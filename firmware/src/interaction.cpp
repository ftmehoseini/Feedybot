#include "interaction.h"

#include "Arduino.h"
#include "config.h"

namespace fafobot {

InteractionController interaction;

namespace {
// Poll interval. 10 ms is far faster than a finger and cheap on a 240 MHz core.
constexpr uint32_t kPollIntervalMs = 10;
}  // namespace

bool InteractionController::begin() {
#if INTERACTION_USE_TOUCH
  // Nothing to configure: the ESP32-S3 touch peripheral is read on demand.
#else
#if BUTTON_USE_PULLUP
  pinMode(INTERACTION_PIN, INPUT_PULLUP);
#else
  pinMode(INTERACTION_PIN, INPUT);
#endif
#endif
  return true;
}

bool InteractionController::start(QueueHandle_t event_queue) {
  event_queue_ = event_queue;
  const BaseType_t created = xTaskCreatePinnedToCore(
      &InteractionController::task_entry, "interaction", TASK_INTERACTION_STACK, this,
      TASK_INTERACTION_PRIORITY, nullptr, TASK_INTERACTION_CORE);
  return created == pdPASS;
}

bool InteractionController::sample_pressed() {
#if INTERACTION_USE_TOUCH
  const uint32_t reading = touchRead(INTERACTION_PIN);
  last_reading_ = reading;
  // On the ESP32-S3 the touch reading *rises* when a finger is present (unlike the
  // original ESP32, where it falls). Getting this backwards makes the robot think it is
  // being held constantly.
  return reading > TOUCH_THRESHOLD;
#else
  const int level = digitalRead(INTERACTION_PIN);
  last_reading_ = static_cast<uint32_t>(level);
  return level == BUTTON_ACTIVE_LEVEL;
#endif
}

void InteractionController::task_entry(void* argument) {
  static_cast<InteractionController*>(argument)->run();
}

void InteractionController::run() {
  bool stable_state = false;
  bool candidate_state = false;
  uint32_t candidate_since_ms = 0;
  uint32_t press_started_ms = 0;

  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    const uint32_t now = millis();
    const bool raw = sample_pressed();

    if (raw != candidate_state) {
      candidate_state = raw;
      candidate_since_ms = now;
    }

    // Debounce: a reading must hold steady before it counts.
    if (candidate_state != stable_state && (now - candidate_since_ms) >= INTERACTION_DEBOUNCE_MS) {
      stable_state = candidate_state;
      pressed_ = stable_state;

      if (stable_state) {
        press_started_ms = now;
      } else {
        const uint32_t held_ms = now - press_started_ms;
        // A press longer than the sanity ceiling means a stuck button or a hand resting
        // on the electrode. Emitting "cancel" repeatedly for that would be worse than
        // ignoring it.
        if (held_ms <= INTERACTION_MAX_PRESS_MS) {
          const InteractionEvent event = (held_ms >= INTERACTION_LONG_PRESS_MS)
                                             ? InteractionEvent::LongTouch
                                             : InteractionEvent::ShortTouch;
          xQueueSend(event_queue_, &event, 0);
        }
      }
    }

    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kPollIntervalMs));
  }
}

}  // namespace fafobot
