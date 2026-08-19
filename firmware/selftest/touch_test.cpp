/**
 * Self-test 4: button or capacitive touch.
 *
 *   pio run -e touch_test -t upload -t monitor
 *
 * Prints live readings plus the classified gesture, using the same debounce and
 * long-press timing as the real firmware. In touch mode it also prints a suggested
 * TOUCH_THRESHOLD, computed from the resting and touched readings it observes -- which
 * is the only reliable way to set it, since the value depends on your electrode's size
 * and what it is mounted to.
 */

#include <Arduino.h>

#include "config.h"

static uint32_t resting_reading = 0;
static uint32_t peak_reading = 0;

static bool sample_pressed(uint32_t& reading_out) {
#if INTERACTION_USE_TOUCH
  const uint32_t reading = touchRead(INTERACTION_PIN);
  reading_out = reading;
  return reading > TOUCH_THRESHOLD;
#else
  const int level = digitalRead(INTERACTION_PIN);
  reading_out = static_cast<uint32_t>(level);
  return level == BUTTON_ACTIVE_LEVEL;
#endif
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Fafobot interaction self-test ===");
#if INTERACTION_USE_TOUCH
  Serial.printf("mode=TOUCH pin=GPIO%d threshold=%d\n", INTERACTION_PIN, TOUCH_THRESHOLD);
  Serial.println("Leave the electrode alone for 5 s, then touch and release repeatedly.");
#else
  Serial.printf("mode=BUTTON pin=GPIO%d active=%s pullup=%d\n", INTERACTION_PIN,
                BUTTON_ACTIVE_LEVEL == LOW ? "LOW" : "HIGH", BUTTON_USE_PULLUP);
#if BUTTON_USE_PULLUP
  pinMode(INTERACTION_PIN, INPUT_PULLUP);
#else
  pinMode(INTERACTION_PIN, INPUT);
#endif
  Serial.println("Press briefly for SHORT_TOUCH, hold >800 ms for LONG_TOUCH.");
#endif
  Serial.printf("long press threshold = %d ms\n\n", INTERACTION_LONG_PRESS_MS);
}

void loop() {
  static bool stable = false;
  static bool candidate = false;
  static uint32_t candidate_since = 0;
  static uint32_t press_started = 0;
  static uint32_t last_print = 0;

  const uint32_t now = millis();
  uint32_t reading = 0;
  const bool raw = sample_pressed(reading);

  if (!raw) {
    // Track the resting value only while released, so a finger cannot poison it.
    resting_reading = (resting_reading == 0) ? reading : (resting_reading * 15 + reading) / 16;
  } else if (reading > peak_reading) {
    peak_reading = reading;
  }

  if (raw != candidate) {
    candidate = raw;
    candidate_since = now;
  }
  if (candidate != stable && (now - candidate_since) >= INTERACTION_DEBOUNCE_MS) {
    stable = candidate;
    if (stable) {
      press_started = now;
      Serial.println(">> press");
    } else {
      const uint32_t held = now - press_started;
      Serial.printf(">> release after %u ms -> %s\n", held,
                    held >= INTERACTION_LONG_PRESS_MS ? "LONG_TOUCH" : "SHORT_TOUCH");
    }
  }

  if (now - last_print >= 500) {
    last_print = now;
    Serial.printf("reading=%u resting=%u peak=%u pressed=%d\n", reading, resting_reading,
                  peak_reading, stable ? 1 : 0);
#if INTERACTION_USE_TOUCH
    if (peak_reading > resting_reading && resting_reading > 0) {
      // Midpoint between resting and touched: the widest margin against both a missed
      // touch and a false one.
      Serial.printf("   suggested TOUCH_THRESHOLD = %u\n",
                    resting_reading + (peak_reading - resting_reading) / 2);
    }
#endif
  }
  delay(10);
}
