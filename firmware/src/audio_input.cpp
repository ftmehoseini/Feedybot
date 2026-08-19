#include "audio_input.h"

#include <string.h>

#include "Arduino.h"
#include "config.h"
#include "driver/i2s.h"
#include "vad.h"

namespace fafobot {

AudioInput audio_input;

namespace {

// Pre-roll ring. Statically allocated: the audio path never calls malloc, so it can
// never fail to allocate mid-sentence and never fragments the heap.
uint8_t g_preroll[AUDIO_PREROLL_BYTES];
size_t g_preroll_write = 0;
bool g_preroll_full = false;

// Scratch for one I2S read. The INMP441 delivers 32-bit slots, which we narrow to 16.
int32_t g_raw_frame[AUDIO_FRAME_SAMPLES];
int16_t g_pcm_frame[AUDIO_FRAME_SAMPLES];

VadConfig make_vad_config() {
  VadConfig config;
  config.start_rms = VAD_START_RMS;
  config.stop_rms = VAD_STOP_RMS;
  config.min_speech_ms = VAD_MIN_SPEECH_MS;
  config.trailing_silence_ms = VAD_TRAILING_SILENCE_MS;
  config.max_utterance_ms = AUDIO_MAX_UTTERANCE_MS;
  config.frame_ms = (AUDIO_FRAME_SAMPLES * 1000) / AUDIO_SAMPLE_RATE;
  return config;
}

}  // namespace

bool AudioInput::begin() {
  capture_stream_ = xStreamBufferCreate(AUDIO_CAPTURE_STREAM_BYTES, AUDIO_FRAME_BYTES);
  if (capture_stream_ == nullptr) {
    return false;
  }

  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
  config.sample_rate = AUDIO_SAMPLE_RATE;
  config.bits_per_sample = static_cast<i2s_bits_per_sample_t>(MIC_BITS_PER_SLOT);
  // The INMP441's L/R pin is tied low, so it drives the left slot only.
  config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = I2S_DMA_BUF_COUNT;
  config.dma_buf_len = I2S_DMA_BUF_LEN;
  config.use_apll = false;
  config.tx_desc_auto_clear = false;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = MIC_SCK_PIN;
  pins.ws_io_num = MIC_WS_PIN;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = MIC_SD_PIN;

  if (i2s_driver_install(MIC_I2S_PORT, &config, 0, nullptr) != ESP_OK) {
    return false;
  }
  if (i2s_set_pin(MIC_I2S_PORT, &pins) != ESP_OK) {
    return false;
  }
  return true;
}

bool AudioInput::start(QueueHandle_t event_queue) {
  event_queue_ = event_queue;
  const BaseType_t created = xTaskCreatePinnedToCore(
      &AudioInput::task_entry, "audio_in", TASK_AUDIO_IN_STACK, this,
      TASK_AUDIO_IN_PRIORITY, nullptr, TASK_AUDIO_IN_CORE);
  return created == pdPASS;
}

void AudioInput::set_gate_open(bool open) {
  gate_open_ = open;
}

size_t AudioInput::read_pcm(uint8_t* destination, size_t capacity, uint32_t timeout_ms) {
  if (capture_stream_ == nullptr) {
    return 0;
  }
  return xStreamBufferReceive(capture_stream_, destination, capacity,
                              pdMS_TO_TICKS(timeout_ms));
}

void AudioInput::push_to_stream(const uint8_t* data, size_t length) {
  // Zero block time. The capture task must never wait on the network task: a stalled
  // WebSocket send would otherwise back up into the I2S DMA and corrupt the audio we
  // are still recording. Dropping is the correct failure here, and it is counted.
  const size_t written = xStreamBufferSend(capture_stream_, data, length, 0);
  if (written < length) {
    dropped_bytes_ += (length - written);
  }
}

void AudioInput::flush_preroll() {
  // Send the oldest part of the ring first so the utterance is in order.
  if (g_preroll_full) {
    push_to_stream(g_preroll + g_preroll_write, AUDIO_PREROLL_BYTES - g_preroll_write);
  }
  push_to_stream(g_preroll, g_preroll_write);
  g_preroll_write = 0;
  g_preroll_full = false;
}

void AudioInput::task_entry(void* argument) {
  static_cast<AudioInput*>(argument)->run();
}

void AudioInput::run() {
  Vad vad(make_vad_config());
  bool uploading = false;

  for (;;) {
    size_t bytes_read = 0;
    const esp_err_t status = i2s_read(MIC_I2S_PORT, g_raw_frame, sizeof(g_raw_frame),
                                      &bytes_read, portMAX_DELAY);
    if (status != ESP_OK || bytes_read == 0) {
      continue;
    }
    const size_t sample_count = bytes_read / sizeof(int32_t);

    // The INMP441 presents 24 bits MSB-justified in a 32-bit slot. This shift is the
    // one place the conversion happens; MIC_SAMPLE_SHIFT also carries our digital gain.
    for (size_t i = 0; i < sample_count; ++i) {
      g_pcm_frame[i] = static_cast<int16_t>(g_raw_frame[i] >> MIC_SAMPLE_SHIFT);
    }

    const float rms = frame_rms(g_pcm_frame, sample_count);
    last_rms_ = rms;

    if (!gate_open_) {
      // Gate closed: the robot is speaking, or the link is down. Drop everything and
      // keep the detector clean so it does not resume mid-utterance when reopened.
      if (uploading) {
        uploading = false;
      }
      vad.reset();
      g_preroll_write = 0;
      g_preroll_full = false;
      continue;
    }

    const size_t frame_bytes = sample_count * sizeof(int16_t);

    if (uploading) {
      push_to_stream(reinterpret_cast<const uint8_t*>(g_pcm_frame), frame_bytes);
    } else {
      // Not yet in an utterance: keep the last AUDIO_PREROLL_MS in the ring so the
      // first consonant survives. Energy VAD cannot fire until the sound exists, so
      // without this the beginning of every word is missing.
      size_t offset = 0;
      size_t remaining = frame_bytes;
      const uint8_t* source = reinterpret_cast<const uint8_t*>(g_pcm_frame);
      while (remaining > 0) {
        const size_t space = AUDIO_PREROLL_BYTES - g_preroll_write;
        const size_t copy = (remaining < space) ? remaining : space;
        memcpy(g_preroll + g_preroll_write, source + offset, copy);
        g_preroll_write += copy;
        offset += copy;
        remaining -= copy;
        if (g_preroll_write >= AUDIO_PREROLL_BYTES) {
          g_preroll_write = 0;
          g_preroll_full = true;
        }
      }
    }

    const VadEvent event = vad.update(rms);
    if (event == VadEvent::SpeechStarted) {
      uploading = true;
      flush_preroll();
      const CaptureEvent notification = CaptureEvent::UtteranceStarted;
      xQueueSend(event_queue_, &notification, 0);
    } else if (event == VadEvent::SpeechEnded || event == VadEvent::MaxLengthHit) {
      uploading = false;
      const CaptureEvent notification = (event == VadEvent::SpeechEnded)
                                            ? CaptureEvent::UtteranceEnded
                                            : CaptureEvent::UtteranceTruncated;
      xQueueSend(event_queue_, &notification, 0);
    }
  }
}

}  // namespace fafobot
