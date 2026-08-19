#pragma once
/**
 * Wi-Fi, WebSocket and the protocol codec.
 *
 * Owns every network resource. Audio tasks never touch the socket; they exchange bytes
 * with this module through stream buffers, which is what stops a reconnect from
 * stalling capture or playback.
 */

#include <stdint.h>

#include "robot_state.h"

namespace fafobot {

/** Callbacks into the rest of the firmware. Set before start(). */
struct NetLinkHandlers {
  void (*on_state)(SystemState state) = nullptr;
  void (*on_expression)(Expression expression, uint32_t hold_ms) = nullptr;
  void (*on_resting_expression)(Expression expression) = nullptr;
  void (*on_listen_control)(bool listening) = nullptr;
  void (*on_speak_start)(int32_t turn_id, uint32_t sample_rate) = nullptr;
  void (*on_speak_audio)(const uint8_t* pcm, size_t length) = nullptr;
  void (*on_speak_end)(int32_t turn_id) = nullptr;
  void (*on_connected)() = nullptr;
  void (*on_disconnected)() = nullptr;
};

class NetLink {
 public:
  bool begin(const NetLinkHandlers& handlers);
  bool start();

  bool connected() const { return session_ready_; }
  /** How long the link has been down, in ms. 0 while connected. */
  uint32_t offline_for_ms() const;

  // -- outbound protocol messages --
  void send_utterance_start();
  void send_utterance_end(uint32_t sample_count);
  void send_audio(const uint8_t* pcm, size_t length);
  void send_playback_done(int32_t turn_id);
  void send_interaction(const char* event);
  void send_device_status(uint32_t dropped_audio_chunks);

  int32_t current_turn_id() const { return current_turn_id_; }

  // -- WebSocket library callbacks --
  // The WebSockets library takes a plain function pointer, so these have to be
  // reachable from a free function. They are not part of this class's intended API;
  // nothing outside net_link.cpp should call them.
  void on_socket_connected();
  void on_socket_disconnected();
  void on_socket_text(const char* payload, size_t length);
  void on_socket_binary(const uint8_t* payload, size_t length);

 private:
  static void task_entry(void* argument);
  void run();
  void handle_text(const char* payload, size_t length);
  void send_hello();

  NetLinkHandlers handlers_{};
  volatile bool socket_open_ = false;
  volatile bool session_ready_ = false;
  volatile int32_t current_turn_id_ = -1;
  uint32_t last_inbound_ms_ = 0;
  uint32_t disconnected_since_ms_ = 0;
};

extern NetLink net_link;

}  // namespace fafobot
