#pragma once
/**
 * The device's own view of system state and social expression — including the local
 * recovery behaviour that keeps the face honest when the network is not.
 *
 * The critical property here: **the robot can always leave a temporary expression by
 * itself.** The backend sends `expression happy hold_ms=1800`; if the follow-up packet
 * is lost, the device decays back to its resting face on its own clock. A robot frozen
 * mid-grin because a UDP-shaped gap ate a frame looks broken in a way nothing else
 * does.
 *
 * Pure logic, host-tested.
 */

#include <stdint.h>

namespace fafobot {

// Mirrors backend/emotion.py SystemState.
enum class SystemState : uint8_t {
  Idle,
  Listening,
  Processing,
  Thinking,
  Speaking,
  Error,
  Offline,
};

// Mirrors backend/emotion.py Expression.
enum class Expression : uint8_t {
  Neutral,
  Happy,
  Curious,
  Confused,
  Encouraging,
  Surprised,
  Sleepy,
};

/** Parse a state name from the wire. Unknown names fall back to Idle, never crash. */
inline SystemState parse_state(const char* name) {
  if (!name) return SystemState::Idle;
  struct Entry { const char* name; SystemState value; };
  static const Entry table[] = {
      {"idle", SystemState::Idle},         {"listening", SystemState::Listening},
      {"processing", SystemState::Processing}, {"thinking", SystemState::Thinking},
      {"speaking", SystemState::Speaking}, {"error", SystemState::Error},
      {"offline", SystemState::Offline},
  };
  for (const Entry& entry : table) {
    const char* a = entry.name;
    const char* b = name;
    while (*a && *a == *b) { ++a; ++b; }
    if (*a == '\0' && *b == '\0') return entry.value;
  }
  return SystemState::Idle;
}

/** Parse an expression name from the wire. Unknown names fall back to Neutral. */
inline Expression parse_expression(const char* name) {
  if (!name) return Expression::Neutral;
  struct Entry { const char* name; Expression value; };
  static const Entry table[] = {
      {"neutral", Expression::Neutral},       {"happy", Expression::Happy},
      {"curious", Expression::Curious},       {"confused", Expression::Confused},
      {"encouraging", Expression::Encouraging}, {"surprised", Expression::Surprised},
      {"sleepy", Expression::Sleepy},
  };
  for (const Entry& entry : table) {
    const char* a = entry.name;
    const char* b = name;
    while (*a && *a == *b) { ++a; ++b; }
    if (*a == '\0' && *b == '\0') return entry.value;
  }
  return Expression::Neutral;
}

/**
 * Holds the two axes plus expression expiry.
 *
 * `tick(now_ms)` must be called from the face task every frame. It is the only thing
 * standing between a lost packet and a permanently stuck face.
 */
class RobotFaceState {
 public:
  explicit RobotFaceState(uint32_t max_hold_ms = 10000) : max_hold_ms_(max_hold_ms) {}

  void set_system_state(SystemState state) { system_state_ = state; }
  SystemState system_state() const { return system_state_; }

  /** Set the resting face. Sent by the backend at handshake, from the active role. */
  void set_resting_expression(Expression expression) {
    resting_ = expression;
    if (!temporary_active_) {
      expression_ = expression;
    }
  }

  /**
   * Show `expression` for `hold_ms`, then decay to the resting face.
   *
   * `hold_ms` is clamped to `max_hold_ms_`: a backend bug (or a corrupted frame)
   * asking for a ten-minute hold must not be able to freeze the face.
   */
  void set_expression(Expression expression, uint32_t hold_ms, uint32_t now_ms) {
    expression_ = expression;
    if (hold_ms == 0 || expression == resting_) {
      temporary_active_ = false;
      return;
    }
    temporary_active_ = true;
    expires_at_ms_ = now_ms + (hold_ms > max_hold_ms_ ? max_hold_ms_ : hold_ms);
  }

  Expression expression() const { return expression_; }

  /** Advance local recovery. Returns true when the expression decayed this tick. */
  bool tick(uint32_t now_ms) {
    if (!temporary_active_) {
      return false;
    }
    // Subtraction rather than `now >= expires` so a millis() rollover (every ~49 days
    // of uptime) expires the face early instead of holding it for another 49 days.
    if (static_cast<int32_t>(now_ms - expires_at_ms_) >= 0) {
      temporary_active_ = false;
      expression_ = resting_;
      return true;
    }
    return false;
  }

  /** Drop any temporary expression immediately (disconnect, cancel). */
  void clear_temporary() {
    temporary_active_ = false;
    expression_ = resting_;
  }

  bool has_temporary_expression() const { return temporary_active_; }

 private:
  uint32_t max_hold_ms_;
  SystemState system_state_ = SystemState::Offline;
  Expression expression_ = Expression::Neutral;
  Expression resting_ = Expression::Neutral;
  bool temporary_active_ = false;
  uint32_t expires_at_ms_ = 0;
};

}  // namespace fafobot
