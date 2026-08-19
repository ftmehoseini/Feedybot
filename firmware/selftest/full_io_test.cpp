/**
 * Self-test 5: everything at once.
 *
 *   pio run -e full_io_test -t upload -t monitor
 *
 * Run this after all four subsystems pass individually and the robot is fully wired.
 * It is the last check before flashing the real firmware, and the one that catches
 * integration faults the individual tests cannot: I2S peripherals fighting over a
 * controller, an amplifier whose ground noise floods the microphone, an I2C bus that
 * only misbehaves while audio DMA is running.
 *
 * Sequence:
 *   1. OLED init and a rendered face
 *   2. Microphone init and a live level meter on the display
 *   3. A test tone through the speaker
 *   4. The acoustic isolation check -- the number that decides your enclosure
 *   5. Interaction: press the button/electrode when prompted
 *   6. A pass/fail summary on the display and on serial
 */

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Arduino.h>
#include <Wire.h>

#include "config.h"
#include "driver/i2s.h"
#include "vad.h"

static Adafruit_SSD1306 display(OLED_WIDTH, OLED_HEIGHT, &Wire, -1);

static int32_t mic_raw[AUDIO_FRAME_SAMPLES];
static int16_t mic_pcm[AUDIO_FRAME_SAMPLES];
static int16_t tone_frame[AUDIO_FRAME_SAMPLES];

struct Results {
  bool oled = false;
  bool microphone = false;
  bool mic_has_signal = false;
  bool speaker = false;
  bool interaction = false;
  float noise_floor = 0.0f;
  float speech_peak = 0.0f;
  float self_hearing = 0.0f;
};

static Results results;

static void banner(const char* line1, const char* line2 = nullptr) {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(2, 6);
  display.print(line1);
  if (line2) {
    display.setCursor(2, 22);
    display.print(line2);
  }
  display.display();
}

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

static bool init_amplifier() {
#if AMP_SD_PIN >= 0
  pinMode(AMP_SD_PIN, OUTPUT);
  digitalWrite(AMP_SD_PIN, !AMP_ENABLE_LEVEL);
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

/** Read one frame and return its RMS. */
static float read_mic_rms() {
  size_t bytes_read = 0;
  if (i2s_read(MIC_I2S_PORT, mic_raw, sizeof(mic_raw), &bytes_read, pdMS_TO_TICKS(200)) != ESP_OK) {
    return -1.0f;
  }
  const size_t count = bytes_read / sizeof(int32_t);
  bool any_nonzero = false;
  for (size_t i = 0; i < count; ++i) {
    if (mic_raw[i] != 0) any_nonzero = true;
    mic_pcm[i] = static_cast<int16_t>(mic_raw[i] >> MIC_SAMPLE_SHIFT);
  }
  if (any_nonzero) {
    results.mic_has_signal = true;
  }
  return fafobot::frame_rms(mic_pcm, count);
}

static void emit_tone(float frequency, uint32_t duration_ms, float amplitude) {
#if AMP_SD_PIN >= 0
  digitalWrite(AMP_SD_PIN, AMP_ENABLE_LEVEL);
#endif
  const uint32_t total = (AUDIO_SAMPLE_RATE * duration_ms) / 1000;
  uint32_t emitted = 0;
  float phase = 0.0f;
  const float step = 2.0f * PI * frequency / AUDIO_SAMPLE_RATE;
  const int16_t peak = static_cast<int16_t>(amplitude * 32767.0f);
  while (emitted < total) {
    const size_t count = min(static_cast<uint32_t>(AUDIO_FRAME_SAMPLES), total - emitted);
    for (size_t i = 0; i < count; ++i) {
      tone_frame[i] = static_cast<int16_t>(peak * sinf(phase));
      phase += step;
      if (phase > 2.0f * PI) phase -= 2.0f * PI;
    }
    size_t written = 0;
    i2s_write(AMP_I2S_PORT, tone_frame, count * sizeof(int16_t), &written, portMAX_DELAY);
    emitted += count;
  }
#if AMP_SD_PIN >= 0
  digitalWrite(AMP_SD_PIN, !AMP_ENABLE_LEVEL);
#endif
}

static bool sample_pressed() {
#if INTERACTION_USE_TOUCH
  return touchRead(INTERACTION_PIN) > TOUCH_THRESHOLD;
#else
  return digitalRead(INTERACTION_PIN) == BUTTON_ACTIVE_LEVEL;
#endif
}

void setup() {
  Serial.begin(115200);
  delay(600);
  Serial.println("\n=== Fafobot FULL IO self-test ===\n");

  // -- 1. OLED ------------------------------------------------------------------------
  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN, OLED_I2C_FREQUENCY);
  results.oled = display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDRESS);
  Serial.printf("[1/5] OLED ............ %s\n", results.oled ? "OK" : "FAIL");
  if (!results.oled) {
    Serial.println("      No display. Everything below still runs; watch serial.");
  } else {
    banner("Fafobot full IO", "starting...");
    delay(900);
  }

  // -- 2. Microphone ---------------------------------------------------------------------
  results.microphone = init_microphone();
  Serial.printf("[2/5] Microphone I2S .. %s\n", results.microphone ? "OK" : "FAIL");

  if (results.microphone) {
    if (results.oled) banner("Step 2: mic", "Stay quiet 3 s");
    Serial.println("      measuring noise floor -- stay quiet for 3 s");
    delay(600);
    float floor_sum = 0.0f;
    int floor_count = 0;
    const uint32_t until = millis() + 3000;
    while (millis() < until) {
      const float rms = read_mic_rms();
      if (rms >= 0.0f) {
        floor_sum += rms;
        ++floor_count;
      }
    }
    results.noise_floor = (floor_count > 0) ? floor_sum / floor_count : 0.0f;
    Serial.printf("      noise floor: %.4f\n", results.noise_floor);

    if (results.oled) banner("Step 2: mic", "SPEAK NOW (5 s)");
    Serial.println("      now speak normally for 5 s");
    const uint32_t speak_until = millis() + 5000;
    while (millis() < speak_until) {
      const float rms = read_mic_rms();
      if (rms > results.speech_peak) results.speech_peak = rms;
      if (results.oled) {
        // Live meter, so a wiring fault is visible without a serial monitor.
        display.clearDisplay();
        display.setCursor(2, 2);
        display.setTextColor(SSD1306_WHITE);
        display.print("SPEAK NOW");
        const int width = min(126, static_cast<int>(rms * 500.0f));
        display.drawRect(0, 24, 128, 16, SSD1306_WHITE);
        if (width > 0) display.fillRect(1, 25, width, 14, SSD1306_WHITE);
        display.setCursor(2, 48);
        display.printf("rms %.4f", rms);
        display.display();
      }
    }
    Serial.printf("      speech peak: %.4f\n", results.speech_peak);
    Serial.printf("      suggested VAD_START_RMS ~ %.4f, VAD_STOP_RMS ~ %.4f\n",
                  results.noise_floor * 4.0f, results.noise_floor * 2.0f);
    if (!results.mic_has_signal) {
      Serial.println("      WARNING: every sample was zero. Check SD wiring and L/R to GND.");
    }
    if (results.speech_peak < results.noise_floor * 3.0f) {
      Serial.println("      WARNING: speech barely above the floor. Raise MIC_SAMPLE_SHIFT.");
    }
  }

  // -- 3. Speaker --------------------------------------------------------------------------
  results.speaker = init_amplifier();
  Serial.printf("[3/5] Amplifier I2S ... %s\n", results.speaker ? "OK" : "FAIL");
  if (results.speaker) {
    if (results.oled) banner("Step 3: speaker", "Listen for tones");
    Serial.println("      playing 440 Hz then 880 Hz -- listen");
    emit_tone(440.0f, 600, 0.20f);
    delay(200);
    emit_tone(880.0f, 600, 0.20f);
    delay(400);
  }

  // -- 4. Acoustic isolation ------------------------------------------------------------------
  // The number that decides the enclosure. If the mic hears the speaker loudly, V1's
  // half-duplex guard will still work, but the robot will feel sluggish because the
  // guard has to be long. See docs/ENCLOSURE_GUIDE.md.
  if (results.microphone && results.speaker) {
    if (results.oled) banner("Step 4: isolation", "Stay quiet");
    Serial.println("[4/5] Acoustic isolation -- stay quiet, the robot will play a tone");
    delay(500);

    // Emit the tone from a separate task so we can sample the mic while it plays.
    xTaskCreatePinnedToCore(
        [](void*) {
          emit_tone(600.0f, 1500, 0.25f);
          vTaskDelete(nullptr);
        },
        "tone", 4096, nullptr, 5, nullptr, 1);

    delay(200);
    float heard = 0.0f;
    const uint32_t until = millis() + 1100;
    while (millis() < until) {
      const float rms = read_mic_rms();
      if (rms > heard) heard = rms;
    }
    results.self_hearing = heard;
    delay(500);

    Serial.printf("      mic heard its own speaker at rms %.4f (floor %.4f)\n", heard,
                  results.noise_floor);
    if (results.noise_floor > 0.0f) {
      const float ratio = heard / results.noise_floor;
      Serial.printf("      that is %.1fx the noise floor\n", ratio);
      if (ratio > 20.0f) {
        Serial.println("      POOR isolation: separate mic and speaker further, or add");
        Serial.println("      foam/gasketing. See docs/ENCLOSURE_GUIDE.md.");
      } else if (ratio > 6.0f) {
        Serial.println("      ACCEPTABLE for half-duplex V1, but worth improving.");
      } else {
        Serial.println("      GOOD isolation.");
      }
    }
  }

  // -- 5. Interaction ---------------------------------------------------------------------
#if !INTERACTION_USE_TOUCH
#if BUTTON_USE_PULLUP
  pinMode(INTERACTION_PIN, INPUT_PULLUP);
#else
  pinMode(INTERACTION_PIN, INPUT);
#endif
#endif
  if (results.oled) banner("Step 5: touch", "Press the button");
  Serial.println("[5/5] Interaction -- press the button/electrode within 10 s");
  const uint32_t deadline = millis() + 10000;
  while (millis() < deadline && !results.interaction) {
    if (sample_pressed()) {
      delay(INTERACTION_DEBOUNCE_MS);
      if (sample_pressed()) {
        results.interaction = true;
      }
    }
    delay(10);
  }
  Serial.printf("      interaction ......... %s\n", results.interaction ? "OK" : "FAIL (no press)");

  // -- summary -------------------------------------------------------------------------------
  Serial.println("\n=== SUMMARY ===");
  Serial.printf("  OLED .............. %s\n", results.oled ? "PASS" : "FAIL");
  Serial.printf("  Microphone I2S .... %s\n", results.microphone ? "PASS" : "FAIL");
  Serial.printf("  Microphone signal . %s\n", results.mic_has_signal ? "PASS" : "FAIL");
  Serial.printf("  Speaker I2S ....... %s\n", results.speaker ? "PASS" : "FAIL");
  Serial.printf("  Interaction ....... %s\n", results.interaction ? "PASS" : "FAIL");
  Serial.printf("  noise floor ....... %.4f\n", results.noise_floor);
  Serial.printf("  speech peak ....... %.4f\n", results.speech_peak);
  Serial.printf("  self-hearing ...... %.4f\n", results.self_hearing);
  Serial.println("\nCopy the suggested VAD thresholds into firmware/include/config.h.");
}

void loop() {
  if (!results.oled) {
    delay(1000);
    return;
  }
  const bool all_pass = results.oled && results.microphone && results.mic_has_signal &&
                        results.speaker && results.interaction;

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(2, 0);
  display.print(all_pass ? "ALL SYSTEMS PASS" : "SOME TESTS FAILED");
  display.setCursor(2, 14);
  display.printf("mic %s  spk %s", results.mic_has_signal ? "ok" : "XX",
                 results.speaker ? "ok" : "XX");
  display.setCursor(2, 26);
  display.printf("touch %s", results.interaction ? "ok" : "XX");
  display.setCursor(2, 40);
  display.printf("floor %.3f", results.noise_floor);
  display.setCursor(2, 52);
  display.printf("peak  %.3f", results.speech_peak);
  display.display();
  delay(500);
}
