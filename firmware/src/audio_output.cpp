#include "audio_output.h"

#include <string.h>

#include "Arduino.h"
#include "config.h"
#include "driver/i2s.h"
#include "mouth.h"
#include "vad.h"  // frame_rms

namespace fafobot {

AudioOutput audio_output;

namespace {

int16_t g_playback_frame[AUDIO_FRAME_SAMPLES];

MouthConfig make_mouth_config() {
  MouthConfig config;
  config.attack = MOUTH_ENVELOPE_ATTACK;
  config.release = MOUTH_ENVELOPE_RELEASE;
  config.min_rms = MOUTH_MIN_RMS;
  config.max_rms = MOUTH_MAX_RMS;
  config.min_open_px = MOUTH_MIN_OPEN_PX;
  config.max_open_px = MOUTH_MAX_OPEN_PX;
  return config;
}

}  // namespace

bool AudioOutput::begin() {
  playback_stream_ = xStreamBufferCreate(AUDIO_PLAYBACK_STREAM_BYTES, 1);
  if (playback_stream_ == nullptr) {
    return false;
  }

#if AMP_SD_PIN >= 0
  pinMode(AMP_SD_PIN, OUTPUT);
  // Start muted. The amplifier's idle hiss is audible in a quiet room, and a robot that
  // hisses whenever it is switched on feels cheap.
  set_amplifier_enabled(false);
#endif

  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
  config.sample_rate = AUDIO_SAMPLE_RATE;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  // The MAX98357A is mono but expects a stereo frame; it sums or selects depending on
  // its SD pin wiring. Sending both slots is the portable choice.
  config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = I2S_DMA_BUF_COUNT;
  config.dma_buf_len = I2S_DMA_BUF_LEN;
  config.use_apll = false;
  // Clears the DMA buffer after it is sent, so an underrun plays silence rather than
  // looping the last fragment -- a stutter is far more noticeable than a gap.
  config.tx_desc_auto_clear = true;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = AMP_BCLK_PIN;
  pins.ws_io_num = AMP_LRC_PIN;
  pins.data_out_num = AMP_DIN_PIN;
  pins.data_in_num = I2S_PIN_NO_CHANGE;

  if (i2s_driver_install(AMP_I2S_PORT, &config, 0, nullptr) != ESP_OK) {
    return false;
  }
  if (i2s_set_pin(AMP_I2S_PORT, &pins) != ESP_OK) {
    return false;
  }
  i2s_zero_dma_buffer(AMP_I2S_PORT);
  return true;
}

bool AudioOutput::start() {
  const BaseType_t created = xTaskCreatePinnedToCore(
      &AudioOutput::task_entry, "audio_out", TASK_AUDIO_OUT_STACK, this,
      TASK_AUDIO_OUT_PRIORITY, nullptr, TASK_AUDIO_OUT_CORE);
  return created == pdPASS;
}

void AudioOutput::set_amplifier_enabled(bool enabled) {
#if AMP_SD_PIN >= 0
  digitalWrite(AMP_SD_PIN, enabled ? AMP_ENABLE_LEVEL : !AMP_ENABLE_LEVEL);
#else
  (void)enabled;
#endif
}

bool AudioOutput::enqueue(const uint8_t* pcm, size_t length) {
  if (playback_stream_ == nullptr) {
    return false;
  }
  if (xStreamBufferBytesAvailable(playback_stream_) > AUDIO_PLAYBACK_HIGH_WATER) {
    dropped_bytes_ += length;
    return false;
  }
  const size_t written = xStreamBufferSend(playback_stream_, pcm, length, 0);
  if (written < length) {
    dropped_bytes_ += (length - written);
    return false;
  }
  return true;
}

void AudioOutput::begin_utterance() {
  utterance_open_ = true;
  finished_ = false;
  flush_requested_ = false;
}

void AudioOutput::end_utterance() {
  // Only marks the end of *incoming* audio. The task keeps playing until the buffer
  // drains, then sets finished_, which is what triggers playback_done.
  utterance_open_ = false;
}

void AudioOutput::flush() {
  flush_requested_ = true;
  utterance_open_ = false;
}

void AudioOutput::task_entry(void* argument) {
  static_cast<AudioOutput*>(argument)->run();
}

void AudioOutput::run() {
  MouthEnvelope mouth(make_mouth_config());
  bool waiting_for_low_water = true;

  for (;;) {
    if (flush_requested_) {
      xStreamBufferReset(playback_stream_);
      i2s_zero_dma_buffer(AMP_I2S_PORT);
      set_amplifier_enabled(false);
      mouth.reset();
      mouth_px_ = 0;
      playing_ = false;
      finished_ = true;
      waiting_for_low_water = true;
      flush_requested_ = false;
      continue;
    }

    const size_t available = xStreamBufferBytesAvailable(playback_stream_);

    // Wait for the low-water mark before the first sample. Starting the moment the
    // first packet lands guarantees an underrun a few milliseconds later, and that
    // stutter at the start of every reply is what makes cheap robots sound cheap.
    if (waiting_for_low_water) {
      if (available >= AUDIO_PLAYBACK_LOW_WATER || (!utterance_open_ && available > 0)) {
        waiting_for_low_water = false;
        set_amplifier_enabled(true);
        playing_ = true;
      } else {
        vTaskDelay(pdMS_TO_TICKS(5));
        continue;
      }
    }

    const size_t wanted = sizeof(g_playback_frame);
    const size_t received = xStreamBufferReceive(playback_stream_, g_playback_frame, wanted,
                                                 pdMS_TO_TICKS(20));

    if (received == 0) {
      if (!utterance_open_) {
        // Buffer drained and the backend said it was done: the utterance is over.
        // Push a little silence so the class-D output settles before muting, which
        // avoids the click a hard mute produces.
        memset(g_playback_frame, 0, sizeof(g_playback_frame));
        size_t written = 0;
        i2s_write(AMP_I2S_PORT, g_playback_frame, sizeof(g_playback_frame), &written,
                  pdMS_TO_TICKS(50));
        set_amplifier_enabled(false);
        mouth.reset();
        mouth_px_ = 0;
        playing_ = false;
        finished_ = true;
        waiting_for_low_water = true;
      } else {
        // Still expecting audio but none arrived: a genuine underrun. Counted so the
        // network buffer sizing can be judged from evidence rather than guessed at.
        ++underruns_;
      }
      continue;
    }

    const size_t sample_count = received / sizeof(int16_t);

    // The mouth is driven from the samples *as they are handed to the DAC*, which is
    // as close to "what the speaker is doing" as software can get.
    mouth.update(frame_rms(g_playback_frame, sample_count));
    mouth_px_ = mouth.opening_px();

    size_t written = 0;
    i2s_write(AMP_I2S_PORT, g_playback_frame, received, &written, portMAX_DELAY);
  }
}

}  // namespace fafobot
