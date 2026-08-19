#pragma once
/**
 * Microphone capture, VAD and utterance framing.
 *
 * Owns I2S_NUM_0, the pre-roll ring, and the capture stream buffer. Nothing else in the
 * firmware touches the input I2S peripheral.
 */

#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"

namespace fafobot {

/** What the capture task is telling the rest of the system. */
enum class CaptureEvent : uint8_t {
  UtteranceStarted,
  UtteranceEnded,
  UtteranceTruncated,  // hit AUDIO_MAX_UTTERANCE_MS
};

class AudioInput {
 public:
  /** Initialise I2S input. Returns false if the driver refused the configuration. */
  bool begin();

  /** Start the capture task. Call once, after begin(). */
  bool start(QueueHandle_t event_queue);

  /**
   * Open or close the microphone gate.
   *
   * Closing discards in-progress detection and stops audio reaching the network
   * buffer. This is the half-duplex mechanism: the gate is closed while the robot
   * speaks, so it cannot transcribe its own voice.
   */
  void set_gate_open(bool open);
  bool gate_open() const { return gate_open_; }

  /** Read captured PCM for upload. Returns bytes read; 0 if none available. */
  size_t read_pcm(uint8_t* destination, size_t capacity, uint32_t timeout_ms);

  /** Bytes dropped because the capture buffer was full. Reported in telemetry. */
  uint32_t dropped_bytes() const { return dropped_bytes_; }

  /** Most recent frame RMS, for the mic self-test and threshold tuning. */
  float last_rms() const { return last_rms_; }

 private:
  static void task_entry(void* argument);
  void run();
  void flush_preroll();
  void push_to_stream(const uint8_t* data, size_t length);

  StreamBufferHandle_t capture_stream_ = nullptr;
  QueueHandle_t event_queue_ = nullptr;
  volatile bool gate_open_ = false;
  volatile uint32_t dropped_bytes_ = 0;
  volatile float last_rms_ = 0.0f;
};

extern AudioInput audio_input;

}  // namespace fafobot
