/**
 * Host-side unit tests for the firmware's pure logic.
 *
 * These compile with a normal host compiler and run on the build machine. That is the
 * whole point: VAD timing, mouth smoothing, backoff and face expiry are exactly the
 * behaviours that are painful to debug on a device, and none of them need an ESP32 to
 * be correct.
 *
 * Build and run:  make -C tests/firmware test
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "../../firmware/include/backoff.h"
#include "../../firmware/include/mouth.h"
#include "../../firmware/include/robot_state.h"
#include "../../firmware/include/vad.h"

using namespace fafobot;

static int failures = 0;
static int checks = 0;

#define CHECK(condition, description)                                            \
  do {                                                                           \
    ++checks;                                                                    \
    if (!(condition)) {                                                          \
      ++failures;                                                                \
      std::printf("  FAIL %s (%s:%d)\n", description, __FILE__, __LINE__);       \
    }                                                                            \
  } while (0)

static void test_vad_requires_sustained_speech() {
  VadConfig config;  // 20 ms frames, 200 ms minimum speech
  Vad vad(config);

  // A single loud frame is a bump, not speech.
  CHECK(vad.update(0.5f) == VadEvent::None, "one loud frame does not open an utterance");
  CHECK(vad.update(0.0f) == VadEvent::None, "silence after a bump stays closed");
  CHECK(!vad.active(), "vad remains inactive after a bump");

  // Ten sustained frames = 200 ms, which reaches the minimum.
  VadEvent event = VadEvent::None;
  for (int i = 0; i < 10; ++i) {
    event = vad.update(0.10f);
  }
  CHECK(event == VadEvent::SpeechStarted, "sustained speech opens an utterance");
  CHECK(vad.active(), "vad reports active after opening");
}

static void test_vad_hysteresis_ignores_the_gap_between_thresholds() {
  VadConfig config;
  Vad vad(config);
  for (int i = 0; i < 10; ++i) vad.update(0.10f);
  CHECK(vad.active(), "utterance is open");

  // 0.020 is below start_rms (0.030) but above stop_rms (0.015): the quiet part of a
  // word. Without hysteresis this would end the utterance mid-sentence.
  for (int i = 0; i < 100; ++i) {
    CHECK(vad.update(0.020f) == VadEvent::None, "mid-threshold audio does not end speech");
  }
  CHECK(vad.active(), "utterance survives 2 s of mid-threshold audio");
}

static void test_vad_closes_after_trailing_silence() {
  VadConfig config;
  Vad vad(config);
  for (int i = 0; i < 10; ++i) vad.update(0.10f);

  // 700 ms of silence at 20 ms per frame = 35 frames.
  for (int i = 0; i < 34; ++i) {
    CHECK(vad.update(0.0f) == VadEvent::None, "silence shorter than the threshold holds");
  }
  CHECK(vad.update(0.0f) == VadEvent::SpeechEnded, "trailing silence closes the utterance");
  CHECK(!vad.active(), "vad resets after closing");
}

static void test_vad_pause_mid_sentence_does_not_split_the_utterance() {
  VadConfig config;
  Vad vad(config);
  for (int i = 0; i < 10; ++i) vad.update(0.10f);

  // 400 ms pause -- someone thinking -- then speech resumes.
  for (int i = 0; i < 20; ++i) {
    CHECK(vad.update(0.0f) == VadEvent::None, "a 400 ms pause does not end the turn");
  }
  vad.update(0.10f);
  CHECK(vad.trailing_silence_ms() == 0, "silence counter resets when speech resumes");
  CHECK(vad.active(), "the utterance is still the same one");
}

static void test_vad_enforces_the_maximum_length() {
  VadConfig config;
  config.max_utterance_ms = 200;  // 10 frames
  Vad vad(config);
  for (int i = 0; i < 10; ++i) vad.update(0.10f);

  VadEvent event = VadEvent::None;
  for (int i = 0; i < 20 && event == VadEvent::None; ++i) {
    event = vad.update(0.10f);
  }
  CHECK(event == VadEvent::MaxLengthHit, "continuous noise hits the ceiling and closes");
  CHECK(!vad.active(), "vad resets after hitting the ceiling");
}

static void test_frame_rms_matches_known_signals() {
  std::vector<int16_t> silence(320, 0);
  CHECK(frame_rms(silence.data(), silence.size()) == 0.0f, "silence has zero rms");

  std::vector<int16_t> full(320, 32767);
  const float loud = frame_rms(full.data(), full.size());
  CHECK(loud > 0.99f && loud <= 1.001f, "full-scale dc has rms ~1.0");

  // A square wave alternating +/- half scale should measure ~0.5.
  std::vector<int16_t> square(320);
  for (size_t i = 0; i < square.size(); ++i) {
    square[i] = (i % 2 == 0) ? 16384 : -16384;
  }
  const float half = frame_rms(square.data(), square.size());
  CHECK(half > 0.49f && half < 0.51f, "half-scale square has rms ~0.5");

  CHECK(frame_rms(nullptr, 0) == 0.0f, "empty frame is safe");
}

static void test_mouth_opens_on_audio_and_closes_on_silence() {
  MouthConfig config;
  MouthEnvelope mouth(config);
  CHECK(mouth.opening_px() == 0, "mouth starts closed");

  for (int i = 0; i < 20; ++i) mouth.update(0.20f);
  const uint8_t open = mouth.opening_px();
  CHECK(open >= config.min_open_px, "loud audio opens the mouth");
  CHECK(open <= config.max_open_px, "mouth never exceeds its maximum");

  for (int i = 0; i < 200; ++i) mouth.update(0.0f);
  CHECK(mouth.opening_px() == 0, "silence closes the mouth");
}

static void test_mouth_attack_is_faster_than_release() {
  MouthConfig config;
  MouthEnvelope rising(config);
  rising.update(1.0f);
  const float after_one_loud = rising.envelope();

  MouthEnvelope falling(config);
  for (int i = 0; i < 50; ++i) falling.update(1.0f);
  const float before = falling.envelope();
  falling.update(0.0f);
  const float dropped = before - falling.envelope();

  CHECK(after_one_loud > dropped, "the mouth opens faster than it closes");
}

static void test_mouth_clamps_above_the_maximum_rms() {
  MouthConfig config;
  MouthEnvelope mouth(config);
  for (int i = 0; i < 100; ++i) mouth.update(5.0f);  // absurd input
  CHECK(mouth.opening_px() == config.max_open_px, "over-range audio clamps, not overflows");
}

static void test_mouth_reset_closes_immediately() {
  MouthConfig config;
  MouthEnvelope mouth(config);
  for (int i = 0; i < 20; ++i) mouth.update(0.2f);
  mouth.reset();
  CHECK(mouth.opening_px() == 0, "reset closes the mouth at once");
}

static void test_backoff_grows_and_caps() {
  Backoff backoff(1000, 30000, 0);  // no jitter, so the sequence is exact
  CHECK(backoff.next_delay_ms(0) == 1000, "first retry waits the minimum");
  CHECK(backoff.next_delay_ms(0) == 2000, "second retry doubles");
  CHECK(backoff.next_delay_ms(0) == 4000, "third retry doubles again");
  for (int i = 0; i < 20; ++i) backoff.next_delay_ms(0);
  CHECK(backoff.next_delay_ms(0) == 30000, "backoff caps at the maximum");
}

static void test_backoff_resets_after_success() {
  Backoff backoff(1000, 30000, 0);
  for (int i = 0; i < 5; ++i) backoff.next_delay_ms(0);
  backoff.reset();
  CHECK(backoff.next_delay_ms(0) == 1000, "a successful connection resets the backoff");
}

static void test_backoff_jitter_stays_in_range() {
  Backoff backoff(1000, 30000, 25);
  uint32_t minimum = 0xFFFFFFFF;
  uint32_t maximum = 0;
  for (uint32_t seed = 0; seed < 500; ++seed) {
    Backoff fresh(1000, 30000, 25);
    const uint32_t delay = fresh.next_delay_ms(seed);
    if (delay < minimum) minimum = delay;
    if (delay > maximum) maximum = delay;
  }
  CHECK(minimum >= 750, "jitter never drops below base - 25%");
  CHECK(maximum <= 1250, "jitter never exceeds base + 25%");
  CHECK(minimum != maximum, "jitter actually varies");
}

static void test_face_expression_expires_locally() {
  RobotFaceState face(10000);
  face.set_resting_expression(Expression::Neutral);
  face.set_expression(Expression::Happy, 1800, 1000);
  CHECK(face.expression() == Expression::Happy, "expression applies immediately");

  CHECK(!face.tick(2000), "expression holds before its deadline");
  CHECK(face.expression() == Expression::Happy, "still happy mid-hold");

  CHECK(face.tick(2800), "expression expires at its deadline");
  CHECK(face.expression() == Expression::Neutral, "face decays to resting, not stuck");
  CHECK(!face.tick(9000), "expiry only fires once");
}

static void test_face_hold_is_clamped() {
  RobotFaceState face(10000);
  face.set_resting_expression(Expression::Neutral);
  // A corrupted or buggy frame asking for an hour.
  face.set_expression(Expression::Surprised, 3600000, 0);
  CHECK(face.tick(10001), "an over-long hold is clamped to the maximum");
  CHECK(face.expression() == Expression::Neutral, "face recovers despite the bad hold");
}

static void test_face_respects_role_resting_expression() {
  RobotFaceState face(10000);
  // The english_teacher role rests on `encouraging`, not `neutral`.
  face.set_resting_expression(Expression::Encouraging);
  CHECK(face.expression() == Expression::Encouraging, "resting face comes from the role");
  face.set_expression(Expression::Happy, 500, 0);
  face.tick(600);
  CHECK(face.expression() == Expression::Encouraging, "decays to the role's face, not neutral");
}

static void test_face_survives_millis_rollover() {
  RobotFaceState face(10000);
  face.set_resting_expression(Expression::Neutral);
  // 40 ms before the 32-bit millisecond counter wraps.
  const uint32_t near_rollover = 0xFFFFFFFF - 40;
  face.set_expression(Expression::Happy, 100, near_rollover);
  // 60 ms later the counter has wrapped to a small number.
  CHECK(face.tick(near_rollover + 100), "expiry works across a millis() rollover");
  CHECK(face.expression() == Expression::Neutral, "face is not stuck for another 49 days");
}

static void test_state_and_expression_parsing_is_total() {
  CHECK(parse_state("thinking") == SystemState::Thinking, "known state parses");
  CHECK(parse_state("speaking") == SystemState::Speaking, "known state parses");
  CHECK(parse_state("teleporting") == SystemState::Idle, "unknown state falls back to idle");
  CHECK(parse_state(nullptr) == SystemState::Idle, "null state is safe");
  CHECK(parse_state("") == SystemState::Idle, "empty state is safe");

  CHECK(parse_expression("encouraging") == Expression::Encouraging, "known expression parses");
  CHECK(parse_expression("smug") == Expression::Neutral, "unknown expression falls back");
  CHECK(parse_expression(nullptr) == Expression::Neutral, "null expression is safe");
  // A prefix must not match: "happ" is not "happy".
  CHECK(parse_expression("happ") == Expression::Neutral, "prefixes do not match");
  CHECK(parse_expression("happyy") == Expression::Neutral, "suffixes do not match");
}

int main() {
  struct Test { const char* name; void (*run)(); };
  const Test tests[] = {
      {"vad requires sustained speech", test_vad_requires_sustained_speech},
      {"vad hysteresis", test_vad_hysteresis_ignores_the_gap_between_thresholds},
      {"vad trailing silence", test_vad_closes_after_trailing_silence},
      {"vad mid-sentence pause", test_vad_pause_mid_sentence_does_not_split_the_utterance},
      {"vad maximum length", test_vad_enforces_the_maximum_length},
      {"frame rms", test_frame_rms_matches_known_signals},
      {"mouth opens and closes", test_mouth_opens_on_audio_and_closes_on_silence},
      {"mouth attack vs release", test_mouth_attack_is_faster_than_release},
      {"mouth clamps", test_mouth_clamps_above_the_maximum_rms},
      {"mouth reset", test_mouth_reset_closes_immediately},
      {"backoff grows and caps", test_backoff_grows_and_caps},
      {"backoff resets", test_backoff_resets_after_success},
      {"backoff jitter range", test_backoff_jitter_stays_in_range},
      {"face expiry", test_face_expression_expires_locally},
      {"face hold clamp", test_face_hold_is_clamped},
      {"face resting from role", test_face_respects_role_resting_expression},
      {"face millis rollover", test_face_survives_millis_rollover},
      {"parsing is total", test_state_and_expression_parsing_is_total},
  };

  for (const Test& test : tests) {
    const int before = failures;
    test.run();
    std::printf("%s %s\n", failures == before ? "ok  " : "FAIL", test.name);
  }
  std::printf("\n%d checks, %d failures\n", checks, failures);
  return failures == 0 ? 0 : 1;
}
