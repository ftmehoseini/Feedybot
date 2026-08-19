#pragma once
/**
 * Speaker playback and the amplitude envelope that drives the mouth.
 *
 * Owns I2S_NUM_1 and the playback stream buffer. Publishes the current mouth opening as
 * a single atomic value that the face task reads — no lock on the audio path.
 */

#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"

namespace fafobot {

class AudioOutput {
 public:
  /** Initialise I2S output and the amplifier enable pin. */
  bool begin();

  /** Start the playback task. Call once, after begin(). */
  bool start();

  /**
   * Queue PCM for playback.
   *
   * Returns false when the buffer is above its high-water mark. The caller counts the
   * drop rather than blocking: blocking here would stall the network task and, with
   * it, every control message including the cancel that might be trying to stop this
   * very playback.
   */
  bool enqueue(const uint8_t* pcm, size_t length);

  /** Called when the backend sends speak_start. Resets the envelope and unmutes. */
  void begin_utterance();

  /** Called on speak_end. Playback drains, then playback_finished() goes true. */
  void end_utterance();

  /** Drop all queued audio immediately (cancel, disconnect). */
  void flush();

  /** True once every queued sample has been written to I2S and the tail has drained. */
  bool playback_finished() const { return finished_; }

  /** Mouth opening in pixels, derived from real playback amplitude. */
  uint8_t mouth_opening_px() const { return mouth_px_; }

  bool is_playing() const { return playing_; }
  uint32_t dropped_bytes() const { return dropped_bytes_; }
  uint32_t underruns() const { return underruns_; }

 private:
  static void task_entry(void* argument);
  void run();
  void set_amplifier_enabled(bool enabled);

  StreamBufferHandle_t playback_stream_ = nullptr;
  volatile bool playing_ = false;
  volatile bool utterance_open_ = false;
  volatile bool finished_ = true;
  volatile bool flush_requested_ = false;
  volatile uint8_t mouth_px_ = 0;
  volatile uint32_t dropped_bytes_ = 0;
  volatile uint32_t underruns_ = 0;
};

extern AudioOutput audio_output;

}  // namespace fafobot
