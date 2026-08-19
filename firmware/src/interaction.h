#pragma once
/**
 * Physical interaction, abstracted from the physical sensor.
 *
 * The rest of the firmware sees semantic events (SHORT_TOUCH, LONG_TOUCH) and never
 * learns whether they came from a button or a capacitive electrode. Swapping the two is
 * a compile-time flag in config.h, not a change anywhere else.
 *
 * Gesture vocabulary is deliberately two events. Double-taps and swipes on a single
 * electrode are unreliable and nobody asked for them.
 */

#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

namespace fafobot {

enum class InteractionEvent : uint8_t {
  ShortTouch,
  LongTouch,
};

class InteractionController {
 public:
  bool begin();
  bool start(QueueHandle_t event_queue);

  /** Raw sensor reading, for the touch self-test's threshold calibration. */
  uint32_t raw_reading() const { return last_reading_; }
  bool pressed() const { return pressed_; }

 private:
  static void task_entry(void* argument);
  void run();
  /** Reads the configured sensor and returns whether it is currently activated. */
  bool sample_pressed();

  QueueHandle_t event_queue_ = nullptr;
  volatile uint32_t last_reading_ = 0;
  volatile bool pressed_ = false;
};

extern InteractionController interaction;

}  // namespace fafobot
