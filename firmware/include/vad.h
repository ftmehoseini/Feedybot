#pragma once
/**
 * Voice activity detection: a pure state machine over frame energy.
 *
 * Deliberately free of Arduino, FreeRTOS and I2S so it can be compiled and tested on a
 * host machine (see tests/firmware/). VAD bugs are miserable to diagnose on hardware —
 * they present as "the robot sometimes cuts me off" — so the logic is tested where
 * tests are cheap, and the hardware only supplies samples and a clock.
 *
 * The design points that matter:
 *
 *  - Two thresholds, not one. Speech starts above `start_rms` and only ends below
 *    `stop_rms`. A single threshold chatters around the boundary and shreds utterances.
 *  - Speech must persist for `min_speech_ms` before an utterance opens, which rejects
 *    clicks and bumps.
 *  - Silence must persist for `trailing_silence_ms` before it closes, so a person
 *    pausing to think is not cut off mid-sentence.
 *  - A hard `max_utterance_ms` ceiling exists so a noisy room cannot produce an endless
 *    upload.
 */

#include <math.h>
#include <stddef.h>
#include <stdint.h>

namespace fafobot {

enum class VadEvent : uint8_t {
  None,           // nothing changed
  SpeechStarted,  // open an utterance and flush the pre-roll
  SpeechEnded,    // close the utterance normally
  MaxLengthHit,   // close the utterance because it hit the ceiling
};

struct VadConfig {
  float start_rms = 0.030f;
  float stop_rms = 0.015f;
  uint32_t min_speech_ms = 200;
  uint32_t trailing_silence_ms = 700;
  uint32_t max_utterance_ms = 20000;
  uint32_t frame_ms = 20;
};

class Vad {
 public:
  explicit Vad(const VadConfig& config) : config_(config) {}

  /** Feed one frame's RMS. Returns the transition it caused, if any. */
  VadEvent update(float rms) {
    if (!active_) {
      if (rms >= config_.start_rms) {
        candidate_ms_ += config_.frame_ms;
        if (candidate_ms_ >= config_.min_speech_ms) {
          active_ = true;
          utterance_ms_ = candidate_ms_;
          candidate_ms_ = 0;
          silence_ms_ = 0;
          return VadEvent::SpeechStarted;
        }
      } else {
        // Reset rather than decay: a single loud frame in a quiet room is a bump, and
        // accumulating credit for it would make the detector trigger on furniture.
        candidate_ms_ = 0;
      }
      return VadEvent::None;
    }

    utterance_ms_ += config_.frame_ms;
    if (utterance_ms_ >= config_.max_utterance_ms) {
      reset();
      return VadEvent::MaxLengthHit;
    }

    if (rms < config_.stop_rms) {
      silence_ms_ += config_.frame_ms;
      if (silence_ms_ >= config_.trailing_silence_ms) {
        reset();
        return VadEvent::SpeechEnded;
      }
    } else {
      silence_ms_ = 0;
    }
    return VadEvent::None;
  }

  /** Abandon any in-progress detection (cancel, disconnect, robot starts speaking). */
  void reset() {
    active_ = false;
    candidate_ms_ = 0;
    silence_ms_ = 0;
    utterance_ms_ = 0;
  }

  bool active() const { return active_; }
  uint32_t utterance_ms() const { return utterance_ms_; }
  /** Trailing silence accumulated so far, in ms. The caller trims this from the tail. */
  uint32_t trailing_silence_ms() const { return silence_ms_; }

 private:
  VadConfig config_;
  bool active_ = false;
  uint32_t candidate_ms_ = 0;
  uint32_t silence_ms_ = 0;
  uint32_t utterance_ms_ = 0;
};

/** RMS of a 16-bit PCM frame, normalised to 0.0 - 1.0. */
inline float frame_rms(const int16_t* samples, size_t count) {
  if (count == 0) {
    return 0.0f;
  }
  // Accumulate in 64-bit: 320 samples of full-scale 16-bit audio overflows int32.
  uint64_t total = 0;
  for (size_t i = 0; i < count; ++i) {
    const int32_t sample = samples[i];
    total += static_cast<uint64_t>(sample * sample);
  }
  const float mean = static_cast<float>(total) / static_cast<float>(count);
  // sqrtf, not a hand-rolled Newton-Raphson: the ESP32-S3 has a single-precision FPU,
  // this runs 50x per second, and an approximation seeded far from the root converges
  // too slowly to be correct at full scale.
  return sqrtf(mean) / 32768.0f;
}

}  // namespace fafobot
