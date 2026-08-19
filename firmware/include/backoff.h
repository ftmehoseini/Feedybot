#pragma once
/**
 * Exponential reconnect backoff with a cap and jitter.
 *
 * The cap stops a long outage becoming an hour-long wait; the jitter stops a room of
 * robots hammering the router in lockstep the moment it comes back. Both matter more
 * than they sound: without the cap the robot appears dead after a lunch break, and
 * without the jitter a fleet reconnecting simultaneously can knock over the very
 * backend it is waiting for.
 *
 * Pure logic, host-tested.
 */

#include <stdint.h>

namespace fafobot {

class Backoff {
 public:
  Backoff(uint32_t min_ms, uint32_t max_ms, uint8_t jitter_pct = 25)
      : min_ms_(min_ms), max_ms_(max_ms), jitter_pct_(jitter_pct), current_ms_(min_ms) {}

  /**
   * The delay to wait before the next attempt, then double the base for the attempt
   * after that.
   *
   * @param random_value any value; only its low bits are used to spread the jitter.
   *        Passed in rather than sampled here so the function stays pure and testable.
   */
  uint32_t next_delay_ms(uint32_t random_value) {
    const uint32_t base = current_ms_;

    uint32_t doubled = current_ms_ * 2;
    if (doubled < current_ms_ || doubled > max_ms_) {  // overflow or over cap
      doubled = max_ms_;
    }
    current_ms_ = doubled;

    if (jitter_pct_ == 0) {
      return base;
    }
    // Symmetric jitter: base +/- jitter_pct%, never below min_ms_.
    const uint32_t spread = (base / 100) * jitter_pct_;
    if (spread == 0) {
      return base;
    }
    const uint32_t offset = random_value % (spread * 2 + 1);
    const int64_t jittered = static_cast<int64_t>(base) + static_cast<int64_t>(offset) -
                             static_cast<int64_t>(spread);
    if (jittered < static_cast<int64_t>(min_ms_)) {
      return min_ms_;
    }
    if (jittered > static_cast<int64_t>(max_ms_)) {
      return max_ms_;
    }
    return static_cast<uint32_t>(jittered);
  }

  /** Call after a successful connection. The next failure starts from the floor again. */
  void reset() { current_ms_ = min_ms_; }

  uint32_t current_base_ms() const { return current_ms_; }

 private:
  uint32_t min_ms_;
  uint32_t max_ms_;
  uint8_t jitter_pct_;
  uint32_t current_ms_;
};

}  // namespace fafobot
