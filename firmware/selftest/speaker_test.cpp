/**
 * Self-test 3: MAX98357A amplifier and speaker.
 *
 *   pio run -e speaker_test -t upload -t monitor
 *
 * Plays a sequence of tones and a sweep. What to listen for:
 *   - a clean tone, not a buzz  -> I2S clocks are right
 *   - no loud hiss between tones -> the SD/enable pin is working
 *   - no crackle at high volume -> the 5 V supply is adequate; a crackle under load
 *     usually means USB current limiting, not a software fault
 *
 * SAFETY: start at low amplitude. A 4 ohm speaker driven hard from a bench supply is
 * loud enough to be genuinely unpleasant at desk distance.
 */

#include <Arduino.h>

#include "config.h"
#include "driver/i2s.h"

static int16_t frame[AUDIO_FRAME_SAMPLES];

static bool init_amplifier() {
#if AMP_SD_PIN >= 0
  pinMode(AMP_SD_PIN, OUTPUT);
  digitalWrite(AMP_SD_PIN, AMP_ENABLE_LEVEL);
#endif

  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
  config.sample_rate = AUDIO_SAMPLE_RATE;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = I2S_DMA_BUF_COUNT;
  config.dma_buf_len = I2S_DMA_BUF_LEN;
  config.tx_desc_auto_clear = true;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = AMP_BCLK_PIN;
  pins.ws_io_num = AMP_LRC_PIN;
  pins.data_out_num = AMP_DIN_PIN;
  pins.data_in_num = I2S_PIN_NO_CHANGE;

  return i2s_driver_install(AMP_I2S_PORT, &config, 0, nullptr) == ESP_OK &&
         i2s_set_pin(AMP_I2S_PORT, &pins) == ESP_OK;
}

/** Play `frequency` for `duration_ms` at `amplitude` (0.0 - 1.0). */
static void play_tone(float frequency, uint32_t duration_ms, float amplitude) {
  const uint32_t total_samples = (AUDIO_SAMPLE_RATE * duration_ms) / 1000;
  uint32_t emitted = 0;
  float phase = 0.0f;
  const float step = 2.0f * PI * frequency / AUDIO_SAMPLE_RATE;
  const int16_t peak = static_cast<int16_t>(amplitude * 32767.0f);

  while (emitted < total_samples) {
    const size_t count =
        min(static_cast<uint32_t>(AUDIO_FRAME_SAMPLES), total_samples - emitted);
    for (size_t i = 0; i < count; ++i) {
      frame[i] = static_cast<int16_t>(peak * sinf(phase));
      phase += step;
      if (phase > 2.0f * PI) phase -= 2.0f * PI;
    }
    size_t written = 0;
    i2s_write(AMP_I2S_PORT, frame, count * sizeof(int16_t), &written, portMAX_DELAY);
    emitted += count;
  }
}

static void play_silence(uint32_t duration_ms) {
  memset(frame, 0, sizeof(frame));
  const uint32_t total = (AUDIO_SAMPLE_RATE * duration_ms) / 1000;
  uint32_t emitted = 0;
  while (emitted < total) {
    const size_t count = min(static_cast<uint32_t>(AUDIO_FRAME_SAMPLES), total - emitted);
    size_t written = 0;
    i2s_write(AMP_I2S_PORT, frame, count * sizeof(int16_t), &written, portMAX_DELAY);
    emitted += count;
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Fafobot speaker self-test ===");
  Serial.printf("BCLK=GPIO%d LRC=GPIO%d DIN=GPIO%d SD=GPIO%d\n", AMP_BCLK_PIN, AMP_LRC_PIN,
                AMP_DIN_PIN, AMP_SD_PIN);

  if (!init_amplifier()) {
    Serial.println("[speaker_test] FAIL: I2S driver would not install.");
    for (;;) delay(1000);
  }
  Serial.println("[speaker_test] I2S up. Listen for clean tones.\n");
}

void loop() {
  Serial.println("[speaker_test] 220 Hz, quiet (0.10)");
  play_tone(220.0f, 700, 0.10f);
  play_silence(300);

  Serial.println("[speaker_test] 440 Hz, moderate (0.25)");
  play_tone(440.0f, 700, 0.25f);
  play_silence(300);

  Serial.println("[speaker_test] 880 Hz, moderate (0.25)");
  play_tone(880.0f, 700, 0.25f);
  play_silence(300);

  Serial.println("[speaker_test] sweep 200 -> 3000 Hz (listen for rattle and buzz)");
  for (float frequency = 200.0f; frequency < 3000.0f; frequency *= 1.12f) {
    play_tone(frequency, 90, 0.20f);
  }

  Serial.println("[speaker_test] 2 s silence -- listen for hiss (should be near-inaudible)");
#if AMP_SD_PIN >= 0
  digitalWrite(AMP_SD_PIN, !AMP_ENABLE_LEVEL);
  delay(2000);
  digitalWrite(AMP_SD_PIN, AMP_ENABLE_LEVEL);
#else
  play_silence(2000);
#endif
  Serial.println();
}
