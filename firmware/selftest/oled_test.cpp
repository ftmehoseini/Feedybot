/**
 * Self-test 1: SSD1306 OLED.
 *
 * Flash this first, with only the OLED wired. It answers three questions in order:
 * is the display on the bus at all, is I2C wired correctly, and does the panel render.
 *
 *   pio run -e oled_test -t upload -t monitor
 *
 * Expected: an I2C scan listing at least one address, then a sequence of test patterns
 * and a rendered face. If the scan is empty, the fault is wiring or power, not software.
 */

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Arduino.h>
#include <Wire.h>

#include "config.h"

static Adafruit_SSD1306 display(OLED_WIDTH, OLED_HEIGHT, &Wire, -1);

static void scan_i2c() {
  Serial.println("[oled_test] scanning I2C bus...");
  uint8_t found = 0;
  for (uint8_t address = 1; address < 127; ++address) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  device at 0x%02X\n", address);
      ++found;
    }
  }
  if (found == 0) {
    Serial.println("  NO DEVICES FOUND.");
    Serial.println("  Check: SDA/SCL not swapped, 3V3 and GND connected, module soldered.");
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Fafobot OLED self-test ===");
  Serial.printf("SDA=GPIO%d SCL=GPIO%d addr=0x%02X\n", OLED_SDA_PIN, OLED_SCL_PIN,
                OLED_I2C_ADDRESS);

  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN, OLED_I2C_FREQUENCY);
  scan_i2c();

  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDRESS)) {
    Serial.println("[oled_test] FAIL: display did not initialise.");
    Serial.println("  If the scan found 0x3D, set OLED_I2C_ADDRESS to 0x3D in config.h.");
    for (;;) delay(1000);
  }
  Serial.println("[oled_test] display initialised.");
}

void loop() {
  // 1. Every pixel on -- finds dead rows and columns.
  display.clearDisplay();
  display.fillRect(0, 0, OLED_WIDTH, OLED_HEIGHT, SSD1306_WHITE);
  display.display();
  Serial.println("[oled_test] all pixels on");
  delay(1200);

  // 2. Border and diagonals -- finds a wrong-size panel or an offset origin.
  display.clearDisplay();
  display.drawRect(0, 0, OLED_WIDTH, OLED_HEIGHT, SSD1306_WHITE);
  display.drawLine(0, 0, OLED_WIDTH - 1, OLED_HEIGHT - 1, SSD1306_WHITE);
  display.drawLine(OLED_WIDTH - 1, 0, 0, OLED_HEIGHT - 1, SSD1306_WHITE);
  display.display();
  Serial.println("[oled_test] border + diagonals (all four corners should be lit)");
  delay(1500);

  // 3. Text -- confirms the panel is legible, not just addressable.
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(2, 2);
  display.print("Fafobot OLED test");
  display.setCursor(2, 20);
  display.printf("SDA %d  SCL %d", OLED_SDA_PIN, OLED_SCL_PIN);
  display.setCursor(2, 34);
  display.printf("addr 0x%02X", OLED_I2C_ADDRESS);
  display.setCursor(2, 50);
  display.print("PASS if readable");
  display.display();
  Serial.println("[oled_test] text");
  delay(2500);

  // 4. A face, so the geometry can be judged at final mounting height.
  display.clearDisplay();
  display.fillRoundRect(27, 13, 26, 26, 6, SSD1306_WHITE);
  display.fillRoundRect(75, 13, 26, 26, 6, SSD1306_WHITE);
  display.fillCircle(40, 26, 5, SSD1306_BLACK);
  display.fillCircle(88, 26, 5, SSD1306_BLACK);
  display.fillRoundRect(47, 50, 34, 6, 3, SSD1306_WHITE);
  display.display();
  Serial.println("[oled_test] face");
  delay(2500);
}
