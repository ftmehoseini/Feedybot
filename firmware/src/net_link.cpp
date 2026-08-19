#include "net_link.h"

#include <ArduinoJson.h>
#include <WebSocketsClient.h>
#include <WiFi.h>

#include "Arduino.h"
#include "backoff.h"
#include "config.h"
#include "protocol_constants.h"
#include "secrets.h"

namespace fafobot {

NetLink net_link;

namespace {

WebSocketsClient g_socket;
NetLink* g_instance = nullptr;

/** Trampoline from the C-style library callback into the instance. */
void websocket_event(WStype_t type, uint8_t* payload, size_t length);

}  // namespace

bool NetLink::begin(const NetLinkHandlers& handlers) {
  handlers_ = handlers;
  g_instance = this;
  disconnected_since_ms_ = millis();

  WiFi.mode(WIFI_STA);
  // The Arduino core's own auto-reconnect fights with our backoff and reconnects
  // without jitter. We drive reconnection ourselves.
  WiFi.setAutoReconnect(false);
  WiFi.setSleep(false);  // modem sleep adds tens of ms of latency to every frame
  return true;
}

bool NetLink::start() {
  const BaseType_t created =
      xTaskCreatePinnedToCore(&NetLink::task_entry, "net", TASK_NET_STACK, this,
                              TASK_NET_PRIORITY, nullptr, TASK_NET_CORE);
  return created == pdPASS;
}

uint32_t NetLink::offline_for_ms() const {
  if (session_ready_) {
    return 0;
  }
  return millis() - disconnected_since_ms_;
}

void NetLink::task_entry(void* argument) {
  static_cast<NetLink*>(argument)->run();
}

void NetLink::run() {
  Backoff wifi_backoff(WIFI_RECONNECT_MIN_MS, WIFI_RECONNECT_MAX_MS, WS_RECONNECT_JITTER_PCT);
  Backoff socket_backoff(WS_RECONNECT_MIN_MS, WS_RECONNECT_MAX_MS, WS_RECONNECT_JITTER_PCT);
  bool socket_started = false;

  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      if (socket_started) {
        g_socket.disconnect();
        socket_started = false;
        socket_open_ = false;
        session_ready_ = false;
      }
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

      const uint32_t started = millis();
      while (WiFi.status() != WL_CONNECTED && (millis() - started) < WIFI_CONNECT_TIMEOUT_MS) {
        vTaskDelay(pdMS_TO_TICKS(200));
      }
      if (WiFi.status() != WL_CONNECTED) {
        // Capped, jittered backoff. Without the cap the robot appears dead after a
        // long outage; without the jitter a roomful of them stampede a recovering AP.
        const uint32_t delay_ms = wifi_backoff.next_delay_ms(esp_random());
        vTaskDelay(pdMS_TO_TICKS(delay_ms));
        continue;
      }
      wifi_backoff.reset();
    }

    if (!socket_started) {
      g_socket.begin(BACKEND_HOST, BACKEND_PORT, BACKEND_PATH);
      g_socket.onEvent(websocket_event);
      // The library's own retry interval; our backoff governs the outer loop.
      g_socket.setReconnectInterval(WS_RECONNECT_MIN_MS);
      g_socket.enableHeartbeat(WS_PING_INTERVAL_MS, WS_PING_INTERVAL_MS * 2, 2);
      socket_started = true;
      last_inbound_ms_ = millis();
    }

    g_socket.loop();

    // TCP can stay "connected" long after the peer has gone. If nothing has arrived
    // within the inactivity window -- pongs included -- treat the link as dead.
    if (socket_open_ && (millis() - last_inbound_ms_) > WS_INACTIVITY_TIMEOUT_MS) {
      g_socket.disconnect();
      socket_open_ = false;
      session_ready_ = false;
      socket_started = false;
      const uint32_t delay_ms = socket_backoff.next_delay_ms(esp_random());
      vTaskDelay(pdMS_TO_TICKS(delay_ms));
      continue;
    }

    if (session_ready_) {
      socket_backoff.reset();
    }

    // 2 ms keeps the socket responsive without spinning a core.
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

void NetLink::send_hello() {
  StaticJsonDocument<JSON_DOCUMENT_BYTES> document;
  document["type"] = MSG_HELLO;
  document["protocol_version"] = FAFOBOT_PROTOCOL_VERSION;
  document["device_id"] = FAFOBOT_DEVICE_ID;
  document["firmware_version"] = FAFOBOT_FIRMWARE_VERSION;
  document["auth_token"] = FAFOBOT_AUTH_TOKEN;
  JsonObject capabilities = document.createNestedObject("capabilities");
  capabilities["sample_rate"] = AUDIO_SAMPLE_RATE;
  capabilities["bits_per_sample"] = AUDIO_BITS_PER_SAMPLE;
  capabilities["channels"] = AUDIO_CHANNELS;

  char buffer[JSON_DOCUMENT_BYTES];
  const size_t length = serializeJson(document, buffer, sizeof(buffer));
  g_socket.sendTXT(buffer, length);
}

void NetLink::handle_text(const char* payload, size_t length) {
  last_inbound_ms_ = millis();

  StaticJsonDocument<JSON_DOCUMENT_BYTES> document;
  const DeserializationError error = deserializeJson(document, payload, length);
  if (error) {
    // A malformed frame must never take the device down. Drop it and keep the link.
    return;
  }
  const char* type = document["type"] | "";

  if (strcmp(type, MSG_HELLO_ACK) == 0) {
    const bool accepted = document["accepted"] | false;
    const int version = document["protocol_version"] | 0;
    if (!accepted || version != FAFOBOT_PROTOCOL_VERSION) {
      // Refuse to proceed on a version mismatch rather than misinterpreting frames.
      session_ready_ = false;
      g_socket.disconnect();
      return;
    }
    session_ready_ = true;
    if (handlers_.on_resting_expression) {
      handlers_.on_resting_expression(parse_expression(document["resting_expression"] | "neutral"));
    }
    if (handlers_.on_connected) {
      handlers_.on_connected();
    }
    return;
  }

  if (strcmp(type, MSG_STATE) == 0) {
    if (handlers_.on_state) {
      handlers_.on_state(parse_state(document["state"] | ""));
    }
  } else if (strcmp(type, MSG_EXPRESSION) == 0) {
    if (handlers_.on_expression) {
      const uint32_t hold = document["hold_ms"] | FACE_EXPRESSION_DEFAULT_HOLD_MS;
      handlers_.on_expression(parse_expression(document["expression"] | "neutral"), hold);
    }
  } else if (strcmp(type, MSG_LISTEN_CONTROL) == 0) {
    if (handlers_.on_listen_control) {
      handlers_.on_listen_control(document["listening"] | false);
    }
  } else if (strcmp(type, MSG_SPEAK_START) == 0) {
    current_turn_id_ = document["turn_id"] | -1;
    if (handlers_.on_speak_start) {
      handlers_.on_speak_start(current_turn_id_, document["sample_rate"] | AUDIO_SAMPLE_RATE);
    }
  } else if (strcmp(type, MSG_SPEAK_END) == 0) {
    if (handlers_.on_speak_end) {
      handlers_.on_speak_end(document["turn_id"] | current_turn_id_);
    }
  } else if (strcmp(type, MSG_ERROR) == 0) {
    // Technical detail for the serial log only. It is never spoken and never shown:
    // the backend has already arranged for the robot to say something human.
    Serial.printf("[net] backend error: %s\n", document["code"] | "unknown");
  }
  // Any other type: a newer backend talking to older firmware. Ignore it silently.
}

void NetLink::send_utterance_start() {
  if (!session_ready_) return;
  StaticJsonDocument<JSON_DOCUMENT_BYTES> document;
  document["type"] = MSG_UTTERANCE_START;
  char buffer[JSON_DOCUMENT_BYTES];
  const size_t length = serializeJson(document, buffer, sizeof(buffer));
  g_socket.sendTXT(buffer, length);
}

void NetLink::send_utterance_end(uint32_t sample_count) {
  if (!session_ready_) return;
  StaticJsonDocument<JSON_DOCUMENT_BYTES> document;
  document["type"] = MSG_UTTERANCE_END;
  document["sample_count"] = sample_count;
  char buffer[JSON_DOCUMENT_BYTES];
  const size_t length = serializeJson(document, buffer, sizeof(buffer));
  g_socket.sendTXT(buffer, length);
}

void NetLink::send_audio(const uint8_t* pcm, size_t length) {
  if (!session_ready_ || length == 0) return;
  g_socket.sendBIN(pcm, length);
}

void NetLink::send_playback_done(int32_t turn_id) {
  if (!session_ready_) return;
  StaticJsonDocument<JSON_DOCUMENT_BYTES> document;
  document["type"] = MSG_PLAYBACK_DONE;
  document["turn_id"] = turn_id;
  char buffer[JSON_DOCUMENT_BYTES];
  const size_t length = serializeJson(document, buffer, sizeof(buffer));
  g_socket.sendTXT(buffer, length);
}

void NetLink::send_interaction(const char* event) {
  if (!session_ready_) return;
  StaticJsonDocument<JSON_DOCUMENT_BYTES> document;
  document["type"] = MSG_INTERACTION;
  document["event"] = event;
  char buffer[JSON_DOCUMENT_BYTES];
  const size_t length = serializeJson(document, buffer, sizeof(buffer));
  g_socket.sendTXT(buffer, length);
}

void NetLink::send_device_status(uint32_t dropped_audio_chunks) {
  if (!session_ready_) return;
  StaticJsonDocument<JSON_DOCUMENT_BYTES> document;
  document["type"] = MSG_DEVICE_STATUS;
  document["free_heap"] = ESP.getFreeHeap();
  document["rssi"] = WiFi.RSSI();
  document["uptime_ms"] = millis();
  document["dropped_audio_chunks"] = dropped_audio_chunks;
  char buffer[JSON_DOCUMENT_BYTES];
  const size_t length = serializeJson(document, buffer, sizeof(buffer));
  g_socket.sendTXT(buffer, length);
}

void NetLink::on_socket_connected() {
  socket_open_ = true;
  last_inbound_ms_ = millis();
  // The session is not ready until hello_ack arrives. A TCP connection is not a
  // conversation, and sending audio before the handshake completes would be dropped
  // by the backend anyway.
  send_hello();
}

void NetLink::on_socket_disconnected() {
  socket_open_ = false;
  if (session_ready_) {
    disconnected_since_ms_ = millis();
  }
  session_ready_ = false;
  current_turn_id_ = -1;
  if (handlers_.on_disconnected) {
    handlers_.on_disconnected();
  }
}

void NetLink::on_socket_text(const char* payload, size_t length) {
  handle_text(payload, length);
}

void NetLink::on_socket_binary(const uint8_t* payload, size_t length) {
  last_inbound_ms_ = millis();
  if (handlers_.on_speak_audio) {
    handlers_.on_speak_audio(payload, length);
  }
}

namespace {

void websocket_event(WStype_t type, uint8_t* payload, size_t length) {
  if (g_instance == nullptr) {
    return;
  }
  switch (type) {
    case WStype_CONNECTED:
      g_instance->on_socket_connected();
      break;
    case WStype_DISCONNECTED:
      g_instance->on_socket_disconnected();
      break;
    case WStype_TEXT:
      // Bounded before parsing. An oversized frame is dropped, never buffered.
      if (length <= MAX_TEXT_FRAME_BYTES) {
        g_instance->on_socket_text(reinterpret_cast<const char*>(payload), length);
      }
      break;
    case WStype_BIN:
      // PCM for playback. Bounded by the backend, and bounded again here.
      if (length <= MAX_BINARY_FRAME_BYTES) {
        g_instance->on_socket_binary(payload, length);
      }
      break;
    default:
      break;
  }
}

}  // namespace

}  // namespace fafobot
