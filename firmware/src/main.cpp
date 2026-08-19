/**
 * Fafobot firmware entry point.
 *
 * This file wires the five tasks together and owns nothing else. All the real work
 * happens inside the modules; `loop()` is a thin coordinator that moves events between
 * them. Keeping it thin is deliberate — a fat main loop is where real-time behaviour
 * goes to die, because everything ends up serialised behind whatever it is doing.
 *
 * Task map (priorities, stacks and cores are all in config.h):
 *
 *   core 1, prio 6   audio_in     I2S RX, VAD, pre-roll, capture buffer
 *   core 1, prio 6   audio_out    I2S TX, playback buffer, mouth envelope
 *   core 0, prio 5   net          Wi-Fi, WebSocket, protocol
 *   core 0, prio 4   interaction  button/touch debounce
 *   core 0, prio 3   face         OLED rendering
 *   core 0, prio 1   loopTask     this file: event routing and audio upload
 */

#include <Arduino.h>

#include "audio_input.h"
#include "audio_output.h"
#include "config.h"
#include "face.h"
#include "interaction.h"
#include "net_link.h"
#include "protocol_constants.h"
#include "robot_state.h"

using namespace fafobot;

namespace {

QueueHandle_t g_capture_events = nullptr;
QueueHandle_t g_interaction_events = nullptr;

// Upload scratch. One buffer, owned solely by loop(); no other task touches it.
uint8_t g_upload_buffer[WS_AUDIO_CHUNK_BYTES];

uint32_t g_utterance_samples = 0;
bool g_utterance_open = false;
bool g_speak_window_open = false;
int32_t g_speaking_turn_id = -1;
uint32_t g_playback_stopped_at_ms = 0;
uint32_t g_last_status_sent_ms = 0;

// The gate the backend asked for, kept separate from the gate we actually apply. The
// device also closes the mic locally while playing and during the post-playback guard,
// and it must not reopen just because an old listen_control said it could.
bool g_backend_wants_listening = false;

constexpr uint32_t kStatusIntervalMs = 30000;

void apply_microphone_gate() {
  const bool speaking = audio_output.is_playing() || g_speak_window_open;
  const bool in_guard =
      g_playback_stopped_at_ms != 0 &&
      (millis() - g_playback_stopped_at_ms) < AUDIO_POST_PLAYBACK_GUARD_MS;
  const bool open = g_backend_wants_listening && net_link.connected() && !speaking && !in_guard;
  audio_input.set_gate_open(open);
}

// -- network handlers ---------------------------------------------------------------

void handle_state(SystemState state) {
  face.set_system_state(state);
}

void handle_expression(Expression expression, uint32_t hold_ms) {
  face.set_expression(expression, hold_ms);
}

void handle_resting_expression(Expression expression) {
  // Comes from the active Role Pack at handshake. The english_teacher role rests on
  // `encouraging`; the companion on `neutral`. The firmware neither knows nor cares
  // which role is loaded -- it just adopts the face it is told to rest in.
  face.set_resting_expression(expression);
}

void handle_listen_control(bool listening) {
  g_backend_wants_listening = listening;
  apply_microphone_gate();
}

void handle_speak_start(int32_t turn_id, uint32_t sample_rate) {
  (void)sample_rate;  // fixed by the protocol; the handshake already agreed it
  g_speaking_turn_id = turn_id;
  g_speak_window_open = true;
  g_playback_stopped_at_ms = 0;
  audio_output.begin_utterance();
  apply_microphone_gate();
}

void handle_speak_audio(const uint8_t* pcm, size_t length) {
  audio_output.enqueue(pcm, length);
}

void handle_speak_end(int32_t turn_id) {
  (void)turn_id;
  g_speak_window_open = false;
  // Marks the end of *incoming* audio only. The output task keeps playing until the
  // buffer drains, and loop() sends playback_done when it does.
  audio_output.end_utterance();
}

void handle_connected() {
  face.set_system_state(SystemState::Idle);
}

void handle_disconnected() {
  // Stop everything that assumes a backend. Audio in flight is abandoned rather than
  // played into a void, and the face stops pretending to converse.
  g_backend_wants_listening = false;
  g_utterance_open = false;
  g_speak_window_open = false;
  audio_output.flush();
  apply_microphone_gate();
  face.go_offline();
}

// -- fatal errors ---------------------------------------------------------------------

/**
 * Halt with a message on the display and on serial.
 *
 * Only for failures that make the robot pointless: no display, no microphone, no
 * speaker. Everything else degrades instead of stopping.
 */
[[noreturn]] void fail(const char* what) {
  Serial.printf("[fatal] %s\n", what);
  face.show_message("Hardware fault", what);
  for (;;) {
    delay(1000);
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("\nFafobot firmware %s, protocol v%d\n", FAFOBOT_FIRMWARE_VERSION,
                FAFOBOT_PROTOCOL_VERSION);

  g_capture_events = xQueueCreate(8, sizeof(CaptureEvent));
  g_interaction_events = xQueueCreate(8, sizeof(InteractionEvent));
  if (g_capture_events == nullptr || g_interaction_events == nullptr) {
    fail("queue alloc");
  }

  // Display first: with it up, every later failure has somewhere to be reported.
  if (!face.begin()) {
    Serial.println("[fatal] SSD1306 not found on I2C");
    for (;;) {
      delay(1000);
    }
  }
  face.show_message("Fafobot", "starting...");
  if (!face.start()) {
    fail("face task");
  }

  if (!audio_output.begin()) {
    fail("i2s output");
  }
  if (!audio_input.begin()) {
    fail("i2s input");
  }
  if (!interaction.begin()) {
    fail("interaction");
  }

  NetLinkHandlers handlers;
  handlers.on_state = handle_state;
  handlers.on_expression = handle_expression;
  handlers.on_resting_expression = handle_resting_expression;
  handlers.on_listen_control = handle_listen_control;
  handlers.on_speak_start = handle_speak_start;
  handlers.on_speak_audio = handle_speak_audio;
  handlers.on_speak_end = handle_speak_end;
  handlers.on_connected = handle_connected;
  handlers.on_disconnected = handle_disconnected;
  net_link.begin(handlers);

  if (!audio_output.start()) {
    fail("audio_out task");
  }
  if (!audio_input.start(g_capture_events)) {
    fail("audio_in task");
  }
  if (!interaction.start(g_interaction_events)) {
    fail("interaction task");
  }
  if (!net_link.start()) {
    fail("net task");
  }

  face.set_system_state(SystemState::Offline);
  Serial.printf("[boot] free heap %u bytes\n", ESP.getFreeHeap());
}

void loop() {
  const uint32_t now = millis();

  // -- capture events -> protocol ----------------------------------------------------
  CaptureEvent capture_event;
  while (xQueueReceive(g_capture_events, &capture_event, 0) == pdTRUE) {
    switch (capture_event) {
      case CaptureEvent::UtteranceStarted:
        if (net_link.connected()) {
          g_utterance_open = true;
          g_utterance_samples = 0;
          net_link.send_utterance_start();
        }
        break;
      case CaptureEvent::UtteranceEnded:
      case CaptureEvent::UtteranceTruncated:
        if (g_utterance_open) {
          g_utterance_open = false;
          net_link.send_utterance_end(g_utterance_samples);
        }
        break;
    }
  }

  // -- drain captured audio to the socket ---------------------------------------------
  // Always drained, even when no utterance is open, so the capture buffer cannot fill
  // with stale audio and stall the input task.
  for (int i = 0; i < 4; ++i) {
    const size_t read = audio_input.read_pcm(g_upload_buffer, sizeof(g_upload_buffer), 0);
    if (read == 0) {
      break;
    }
    if (g_utterance_open && net_link.connected()) {
      net_link.send_audio(g_upload_buffer, read);
      g_utterance_samples += read / AUDIO_BYTES_PER_SAMPLE;
    }
  }

  // -- playback completion ------------------------------------------------------------
  if (g_speaking_turn_id >= 0 && !g_speak_window_open && audio_output.playback_finished()) {
    net_link.send_playback_done(g_speaking_turn_id);
    g_speaking_turn_id = -1;
    g_playback_stopped_at_ms = now;
  }

  // -- interaction events ---------------------------------------------------------------
  InteractionEvent interaction_event;
  while (xQueueReceive(g_interaction_events, &interaction_event, 0) == pdTRUE) {
    if (interaction_event == InteractionEvent::LongTouch) {
      // Cancel is handled locally *as well as* remotely: the audio should stop the
      // instant the person presses, not after a network round trip.
      audio_output.flush();
      g_speak_window_open = false;
      g_speaking_turn_id = -1;
      g_playback_stopped_at_ms = now;
      net_link.send_interaction(EVENT_LONG_TOUCH);
    } else {
      net_link.send_interaction(EVENT_SHORT_TOUCH);
    }
  }

  // -- offline face ---------------------------------------------------------------------
  // Only after a delay: a brief blip should not visibly disturb the robot.
  if (!net_link.connected() && net_link.offline_for_ms() > OFFLINE_FACE_DELAY_MS) {
    face.set_system_state(SystemState::Offline);
  }

  apply_microphone_gate();

  if (net_link.connected() && (now - g_last_status_sent_ms) > kStatusIntervalMs) {
    g_last_status_sent_ms = now;
    net_link.send_device_status(audio_input.dropped_bytes() + audio_output.dropped_bytes());
  }

  // 5 ms: fast enough that audio upload keeps up with a 20 ms capture cadence, slow
  // enough to leave the core to the tasks that matter.
  delay(5);
}
