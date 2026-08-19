#pragma once
/**
 * The OLED face.
 *
 * Runs on core 0 at the lowest working priority. A dropped frame is invisible; a
 * dropped audio block is audible, so the face never competes with audio for anything —
 * not a core, not a lock, not a buffer.
 *
 * The face reads two independent inputs and composes them:
 *   - `SystemState` decides the mouth and the overlay (thinking dots, offline icon)
 *   - `Expression` decides the eyes and brows
 * A robot can be SPEAKING while ENCOURAGING; those are not the same axis and the face
 * is where they finally meet.
 */

#include <stdint.h>

#include "config.h"
#include "robot_state.h"

namespace fafobot {

class Face {
 public:
  /** Initialise the display. Returns false if the SSD1306 did not answer on I2C. */
  bool begin();

  /** Start the render task. */
  bool start();

  void set_system_state(SystemState state);
  void set_expression(Expression expression, uint32_t hold_ms);
  void set_resting_expression(Expression expression);
  /** Called on disconnect: drop reactive expressions and show the offline face. */
  void go_offline();

  /** Display a short line of text instead of the face. Used for boot and fatal errors. */
  void show_message(const char* line1, const char* line2 = nullptr);

 private:
  static void task_entry(void* argument);
  void run();
  void render(uint32_t now_ms);

  void draw_eyes(uint32_t now_ms);
  void draw_mouth();
  void draw_overlay(uint32_t now_ms);

  RobotFaceState state_{FACE_EXPRESSION_MAX_HOLD_MS};

  // Blink and idle-glance timing, advanced in the render loop.
  uint32_t next_blink_at_ms_ = 0;
  uint32_t blink_until_ms_ = 0;
  int8_t gaze_offset_px_ = 0;
  uint32_t next_glance_at_ms_ = 0;
  uint32_t glance_until_ms_ = 0;

  bool message_mode_ = false;
  char message_line1_[24] = {0};
  char message_line2_[24] = {0};
};

extern Face face;

}  // namespace fafobot
