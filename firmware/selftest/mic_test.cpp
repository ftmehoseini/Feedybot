/**
 * Self-test 2: INMP441 microphone.
 *
 *   pio run -e mic_test -t upload -t monitor
 *
 * Prints a live RMS level and a bar meter. This is also the tool you use to set
 * VAD_START_RMS, VAD_STOP_RMS and MIC_SAMPLE_SHIFT in config.h, so run it in the actual
 * enclosure and the actual room, not on a bare bench.
 *
 * Procedure:
 *   1. Stay silent for ten seconds. Note the quiet-room RMS -- that is your noise floor.
 *   2. Speak normally at your intended distance. Note the speaking RMS.
 *   3. Set VAD_START_RMS to about 4x the noise floor, VAD_STOP_RMS to about 2x.
 *   4. If speech peaks below ~0.05, raise MIC_SAMPLE_SHIFT by one and repeat.
 *      If "CLIPPING" appears during normal speech, lower it by one.
 */

#include <Arduino.h>

#include "config.h"
#include "driver/i2s.h"
#include "vad.h"

static int32_t raw_frame[AUDIO_FRAME_SAMPLES];
static int16_t pcm_frame[AUDIO_FRAME_SAMPLES];

static float peak_rms = 0.0f;
static float floor_rms = 1.0f;

static bool init_microphone() {
  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
  config.sample_rate = AUDIO_SAMPLE_RATE;
  config.bits_per_sample = static_cast<i2s_bits_per_sample_t>(MIC_BITS_PER_SLOT);
  config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = I2S_DMA_BUF_COUNT;
  config.dma_buf_len = I2S_DMA_BUF_LEN;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = MIC_SCK_PIN;
  pins.ws_io_num = MIC_WS_PIN;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = MIC_SD_PIN;

  return i2s_driver_install(MIC_I2S_PORT, &config, 0, nullptr) == ESP_OK &&
         i2s_set_pin(MIC_I2S_PORT, &pins) == ESP_OK;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Fafobot microphone self-test ===");
  Serial.printf("SCK=GPIO%d WS=GPIO%d SD=GPIO%d shift=%d\n", MIC_SCK_PIN, MIC_WS_PIN,
                MIC_SD_PIN, MIC_SAMPLE_SHIFT);

  if (!init_microphone()) {
    Serial.println("[mic_test] FAIL: I2S driver would not install.");
    for (;;) delay(1000);
  }
  Serial.println("[mic_test] I2S up. Stay silent 10 s, then speak normally.\n");
}

void loop() {
  size_t bytes_read = 0;
  if (i2s_read(MIC_I2S_PORT, raw_frame, sizeof(raw_frame), &bytes_read, portMAX_DELAY) != ESP_OK) {
    Serial.println("[mic_test] read error");
    return;
  }
  const size_t count = bytes_read / sizeof(int32_t);

  bool all_zero = true;
  bool clipping = false;
  for (size_t i = 0; i < count; ++i) {
    if (raw_frame[i] != 0) all_zero = false;
    const int32_t shifted = raw_frame[i] >> MIC_SAMPLE_SHIFT;
    if (shifted > 32000 || shifted < -32000) clipping = true;
    pcm_frame[i] = static_cast<int16_t>(shifted);
  }

  const float rms = fafobot::frame_rms(pcm_frame, count);
  if (rms > peak_rms) peak_rms = rms;
  if (rms < floor_rms && rms > 0.0f) floor_rms = rms;

  static uint32_t last_print = 0;
  if (millis() - last_print < 100) return;
  last_print = millis();

  if (all_zero) {
    // Every sample exactly zero means no data line, not a quiet room: even a silent
    // MEMS mic outputs dither.
    Serial.println("[mic_test] ALL SAMPLES ZERO -- check SD wiring, and that L/R is tied to GND");
    return;
  }

  // 40-character bar, full scale at RMS 0.5.
  char bar[42];
  const int filled = static_cast<int>(rms * 80.0f);
  for (int i = 0; i < 40; ++i) bar[i] = (i < filled) ? '#' : '.';
  bar[40] = '\0';

  Serial.printf("rms %.4f  floor %.4f  peak %.4f  [%s]%s\n", rms, floor_rms, peak_rms, bar,
                clipping ? "  CLIPPING - lower MIC_SAMPLE_SHIFT" : "");
}
