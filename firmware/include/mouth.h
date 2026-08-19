#pragma once
/**
 * Amplitude-driven mouth animation.
 *
 * The mouth follows what actually comes out of the speaker, measured from the PCM as it
 * is handed to I2S. It is never driven by estimated speech duration: a text-timed mouth
 * drifts out of sync within a sentence and is the clearest tell that a robot's face is
 * decoration rather than behaviour.
 *
 * Pure logic, host-tested.
 */

#include <stdint.h>

namespace fafobot {

struct MouthConfig {
  float attack = 0.55f;    // how fast the envelope rises (0-1)
  float release = 0.18f;   // how fast it falls; slower than attack, or it flutters
  float min_rms = 0.010f;  // below this, mouth closed
  float max_rms = 0.250f;  // at/above this, mouth fully open
  uint8_t min_open_px = 2;
  uint8_t max_open_px = 18;
};

class MouthEnvelope {
 public:
  explicit MouthEnvelope(const MouthConfig& config) : config_(config) {}

  /** Feed the RMS of the audio frame just queued for playback. */
  void update(float rms) {
    // Asymmetric smoothing: open quickly so consonants land on time, close slowly so
    // the mouth does not strobe between syllables.
    const float coefficient = (rms > envelope_) ? config_.attack : config_.release;
    envelope_ += coefficient * (rms - envelope_);
    if (envelope_ < 0.0f) {
      envelope_ = 0.0f;
    }
  }

  /** Collapse the mouth immediately. Called when playback stops. */
  void reset() { envelope_ = 0.0f; }

  /** Current mouth opening in pixels. */
  uint8_t opening_px() const {
    if (envelope_ <= config_.min_rms) {
      return 0;
    }
    const float span = config_.max_rms - config_.min_rms;
    float normalised = (span > 0.0f) ? (envelope_ - config_.min_rms) / span : 1.0f;
    if (normalised > 1.0f) {
      normalised = 1.0f;
    }
    const float range = static_cast<float>(config_.max_open_px - config_.min_open_px);
    return static_cast<uint8_t>(config_.min_open_px + normalised * range);
  }

  float envelope() const { return envelope_; }

 private:
  MouthConfig config_;
  float envelope_ = 0.0f;
};

}  // namespace fafobot
