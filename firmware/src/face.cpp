#include "face.h"

#include <string.h>

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>

#include "Arduino.h"
#include "audio_output.h"
#include "config.h"

namespace fafobot {

Face face;

namespace {

Adafruit_SSD1306 g_display(OLED_WIDTH, OLED_HEIGHT, &Wire, -1);

// Face geometry. Centralised so the proportions can be tuned in one place; a face is
// mostly a matter of spacing, and hunting constants through drawing code is miserable.
constexpr int16_t kEyeCenterY = 26;
constexpr int16_t kLeftEyeX = 40;
constexpr int16_t kRightEyeX = 88;
constexpr int16_t kEyeWidth = 26;
constexpr int16_t kEyeHeight = 26;
constexpr int16_t kPupilRadius = 5;
constexpr int16_t kMouthCenterY = 52;
constexpr int16_t kMouthWidth = 34;

uint32_t random_between(uint32_t low, uint32_t high) {
  if (high <= low) {
    return low;
  }
  return low + (esp_random() % (high - low));
}

/** One eye as a rounded rectangle, with a pupil offset by the current gaze. */
void draw_open_eye(int16_t center_x, int16_t center_y, int8_t gaze, int16_t height) {
  const int16_t x = center_x - kEyeWidth / 2;
  const int16_t y = center_y - height / 2;
  g_display.fillRoundRect(x, y, kEyeWidth, height, 6, SSD1306_WHITE);
  if (height > kPupilRadius * 2) {
    g_display.fillCircle(center_x + gaze, center_y, kPupilRadius, SSD1306_BLACK);
  }
}

/** A closed eye: a horizontal bar. Used for blinks and for the sleepy expression. */
void draw_closed_eye(int16_t center_x, int16_t center_y) {
  g_display.fillRoundRect(center_x - kEyeWidth / 2, center_y - 2, kEyeWidth, 4, 2,
                          SSD1306_WHITE);
}

/** A brow above an eye. Angle is in "pixels of tilt": positive lowers the inner end. */
void draw_brow(int16_t center_x, int16_t center_y, int8_t tilt, bool mirrored) {
  const int16_t left_x = center_x - kEyeWidth / 2;
  const int16_t right_x = center_x + kEyeWidth / 2;
  const int16_t y = center_y - kEyeHeight / 2 - 6;
  const int16_t inner_y = mirrored ? y + tilt : y - tilt;
  const int16_t outer_y = mirrored ? y - tilt : y + tilt;
  g_display.drawLine(left_x, mirrored ? outer_y : inner_y, right_x,
                     mirrored ? inner_y : outer_y, SSD1306_WHITE);
  g_display.drawLine(left_x, (mirrored ? outer_y : inner_y) + 1, right_x,
                     (mirrored ? inner_y : outer_y) + 1, SSD1306_WHITE);
}

}  // namespace

bool Face::begin() {
  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN, OLED_I2C_FREQUENCY);
  if (!g_display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDRESS)) {
    return false;
  }
  g_display.clearDisplay();
  g_display.display();
  next_blink_at_ms_ = millis() + FACE_BLINK_MIN_INTERVAL_MS;
  next_glance_at_ms_ = millis() + FACE_IDLE_GLANCE_MIN_MS;
  return true;
}

bool Face::start() {
  const BaseType_t created =
      xTaskCreatePinnedToCore(&Face::task_entry, "face", TASK_FACE_STACK, this,
                              TASK_FACE_PRIORITY, nullptr, TASK_FACE_CORE);
  return created == pdPASS;
}

void Face::set_system_state(SystemState state) {
  message_mode_ = false;
  state_.set_system_state(state);
}

void Face::set_expression(Expression expression, uint32_t hold_ms) {
  message_mode_ = false;
  state_.set_expression(expression, hold_ms, millis());
}

void Face::set_resting_expression(Expression expression) {
  state_.set_resting_expression(expression);
}

void Face::go_offline() {
  state_.clear_temporary();
  state_.set_system_state(SystemState::Offline);
}

void Face::show_message(const char* line1, const char* line2) {
  strncpy(message_line1_, line1 ? line1 : "", sizeof(message_line1_) - 1);
  strncpy(message_line2_, line2 ? line2 : "", sizeof(message_line2_) - 1);
  message_line1_[sizeof(message_line1_) - 1] = '\0';
  message_line2_[sizeof(message_line2_) - 1] = '\0';
  message_mode_ = true;
}

void Face::task_entry(void* argument) {
  static_cast<Face*>(argument)->run();
}

void Face::run() {
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    const uint32_t now = millis();
    // Local expression recovery. This single call is what guarantees the robot can
    // leave a reactive face without the backend's help.
    state_.tick(now);
    render(now);
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(FACE_FRAME_INTERVAL_MS));
  }
}

void Face::render(uint32_t now_ms) {
  g_display.clearDisplay();

  if (message_mode_) {
    g_display.setTextColor(SSD1306_WHITE);
    g_display.setTextSize(1);
    g_display.setCursor(2, 24);
    g_display.print(message_line1_);
    if (message_line2_[0] != '\0') {
      g_display.setCursor(2, 38);
      g_display.print(message_line2_);
    }
    g_display.display();
    return;
  }

  draw_eyes(now_ms);
  draw_mouth();
  draw_overlay(now_ms);
  g_display.display();
}

void Face::draw_eyes(uint32_t now_ms) {
  const Expression expression = state_.expression();
  const SystemState system_state = state_.system_state();

  // Blink scheduling. Only while awake and connected: a robot that blinks at the
  // offline screen looks like it is ignoring you.
  const bool can_blink =
      system_state != SystemState::Offline && expression != Expression::Sleepy;
  if (can_blink && static_cast<int32_t>(now_ms - next_blink_at_ms_) >= 0) {
    blink_until_ms_ = now_ms + FACE_BLINK_DURATION_MS;
    next_blink_at_ms_ = now_ms + random_between(FACE_BLINK_MIN_INTERVAL_MS,
                                                FACE_BLINK_MAX_INTERVAL_MS);
  }
  const bool blinking = static_cast<int32_t>(now_ms - blink_until_ms_) < 0;

  // Idle gaze drift. Subtle and infrequent: constant motion reads as anxious, and the
  // goal is "alive", not "busy".
  if (system_state == SystemState::Idle || system_state == SystemState::Listening) {
    if (static_cast<int32_t>(now_ms - next_glance_at_ms_) >= 0) {
      gaze_offset_px_ = (esp_random() % 2 == 0) ? -4 : 4;
      glance_until_ms_ = now_ms + FACE_IDLE_GLANCE_HOLD_MS;
      next_glance_at_ms_ =
          now_ms + random_between(FACE_IDLE_GLANCE_MIN_MS, FACE_IDLE_GLANCE_MAX_MS);
    }
    if (static_cast<int32_t>(now_ms - glance_until_ms_) >= 0) {
      gaze_offset_px_ = 0;
    }
  } else {
    gaze_offset_px_ = 0;
  }

  if (system_state == SystemState::Offline || expression == Expression::Sleepy) {
    draw_closed_eye(kLeftEyeX, kEyeCenterY);
    draw_closed_eye(kRightEyeX, kEyeCenterY);
    return;
  }
  if (blinking) {
    draw_closed_eye(kLeftEyeX, kEyeCenterY);
    draw_closed_eye(kRightEyeX, kEyeCenterY);
    return;
  }

  int16_t height = kEyeHeight;
  int8_t brow_tilt = 0;
  bool draw_brows = false;

  switch (expression) {
    case Expression::Happy:
      // Squeezed eyes read as a smile even before the mouth does.
      height = kEyeHeight - 8;
      break;
    case Expression::Curious:
      draw_brows = true;
      brow_tilt = -3;  // raised inner ends
      break;
    case Expression::Confused:
      draw_brows = true;
      brow_tilt = 4;  // one-sided tilt below
      height = kEyeHeight - 2;
      break;
    case Expression::Encouraging:
      draw_brows = true;
      brow_tilt = -2;
      height = kEyeHeight - 4;
      break;
    case Expression::Surprised:
      height = kEyeHeight + 4;
      break;
    case Expression::Neutral:
    case Expression::Sleepy:
    default:
      break;
  }

  draw_open_eye(kLeftEyeX, kEyeCenterY, gaze_offset_px_, height);
  draw_open_eye(kRightEyeX, kEyeCenterY, gaze_offset_px_, height);

  if (draw_brows) {
    // Confusion is asymmetric: one brow up, one down. Symmetry reads as a frown.
    const bool asymmetric = (expression == Expression::Confused);
    draw_brow(kLeftEyeX, kEyeCenterY, brow_tilt, false);
    draw_brow(kRightEyeX, kEyeCenterY, asymmetric ? -brow_tilt : brow_tilt, true);
  }
}

void Face::draw_mouth() {
  const SystemState system_state = state_.system_state();
  const Expression expression = state_.expression();
  const int16_t left = OLED_WIDTH / 2 - kMouthWidth / 2;

  if (system_state == SystemState::Speaking) {
    // The one place the mouth's height comes from: measured playback amplitude.
    // Never from a text-length estimate.
    const uint8_t opening = audio_output.mouth_opening_px();
    if (opening == 0) {
      g_display.fillRoundRect(left, kMouthCenterY - 1, kMouthWidth, 3, 1, SSD1306_WHITE);
      return;
    }
    g_display.fillRoundRect(left, kMouthCenterY - opening / 2, kMouthWidth, opening,
                            opening / 2, SSD1306_WHITE);
    return;
  }

  switch (system_state) {
    case SystemState::Offline:
      // A flat, slightly short line: closed, not sad.
      g_display.fillRect(left + 8, kMouthCenterY, kMouthWidth - 16, 2, SSD1306_WHITE);
      return;
    case SystemState::Listening:
      // Small open mouth: attentive, ready.
      g_display.drawRoundRect(left + 10, kMouthCenterY - 3, kMouthWidth - 20, 7, 3,
                              SSD1306_WHITE);
      return;
    default:
      break;
  }

  switch (expression) {
    case Expression::Happy:
    case Expression::Encouraging:
      // An upward arc, drawn as two strokes so it reads at this size.
      g_display.drawLine(left + 4, kMouthCenterY - 2, left + kMouthWidth / 2,
                         kMouthCenterY + 4, SSD1306_WHITE);
      g_display.drawLine(left + kMouthWidth / 2, kMouthCenterY + 4,
                         left + kMouthWidth - 4, kMouthCenterY - 2, SSD1306_WHITE);
      g_display.drawLine(left + 4, kMouthCenterY - 1, left + kMouthWidth / 2,
                         kMouthCenterY + 5, SSD1306_WHITE);
      g_display.drawLine(left + kMouthWidth / 2, kMouthCenterY + 5,
                         left + kMouthWidth - 4, kMouthCenterY - 1, SSD1306_WHITE);
      return;
    case Expression::Confused:
      // A short off-centre line: uncertain rather than unhappy.
      g_display.fillRect(left + 6, kMouthCenterY, kMouthWidth / 2, 2, SSD1306_WHITE);
      return;
    case Expression::Surprised:
      g_display.drawCircle(OLED_WIDTH / 2, kMouthCenterY + 1, 5, SSD1306_WHITE);
      return;
    default:
      g_display.fillRoundRect(left + 6, kMouthCenterY, kMouthWidth - 12, 3, 1,
                              SSD1306_WHITE);
      return;
  }
}

void Face::draw_overlay(uint32_t now_ms) {
  switch (state_.system_state()) {
    case SystemState::Processing:
    case SystemState::Thinking: {
      // Three dots cycling along the top. Cheap, legible, and unmistakably "working".
      const uint8_t active = (now_ms / 260) % 3;
      for (uint8_t i = 0; i < 3; ++i) {
        const int16_t x = OLED_WIDTH / 2 - 10 + i * 10;
        if (i == active) {
          g_display.fillCircle(x, 6, 3, SSD1306_WHITE);
        } else {
          g_display.drawCircle(x, 6, 2, SSD1306_WHITE);
        }
      }
      break;
    }
    case SystemState::Offline: {
      // A struck-through link glyph in the corner. Deliberately not text: an error
      // string on a robot's face is a screenshot of a bug, not a robot.
      g_display.drawLine(4, 4, 14, 14, SSD1306_WHITE);
      g_display.drawLine(14, 4, 4, 14, SSD1306_WHITE);
      break;
    }
    case SystemState::Listening: {
      // A single dot: recording, without a red light's alarm.
      g_display.fillCircle(OLED_WIDTH - 8, 6, 3, SSD1306_WHITE);
      break;
    }
    default:
      break;
  }
}

}  // namespace fafobot
