# Component references

Engineering facts used to design the pin map and the audio path, summarised from
manufacturer documentation. Blog pinouts were not used as sources; where a fact could not
be confirmed from a primary document it is marked **UNVERIFIED**.

Only the details this project actually depends on are recorded. This is not a datasheet
digest.

---

## ESP32-S3 (Espressif Systems)

**Documents:** *ESP32-S3 Series Datasheet*; *ESP32-S3 Technical Reference Manual*;
*ESP32-S3-DevKitC-1 User Guide* (Espressif, at docs.espressif.com).

| Property | Value |
| --- | --- |
| Supply | 3.0–3.6 V (3.3 V nominal) |
| Logic level | 3.3 V. **Not 5 V tolerant.** |
| Cores | 2 × Xtensa LX7 @ up to 240 MHz |
| Internal SRAM | 512 KB |
| I2S controllers | 2 (I2S0, I2S1) — the reason input and output never share one |
| Touch sensor channels | GPIO1–GPIO14 (TOUCH1–TOUCH14) |
| GPIO range | GPIO0–GPIO21, GPIO26–GPIO48. GPIO22–25 do not exist. |

### Pins that cannot be used freely

| Pin(s) | Function | Consequence of using it |
| --- | --- | --- |
| GPIO0 | Strapping: boot mode select | Wrong level at reset enters download mode; also the devkit's BOOT button |
| GPIO3 | Strapping: JTAG source select | Can misconfigure debug routing |
| GPIO45 | Strapping: VDD_SPI voltage select | Wrong level at reset can brown out the flash rail |
| GPIO46 | Strapping: ROM message print enable | Affects boot logging |
| GPIO19, GPIO20 | USB D−/D+ (USB-Serial-JTAG) | Loses USB flashing and the serial console |
| GPIO26–GPIO32 | SPI flash (SPICS1, SPIHD, SPIWP, SPICS0, SPICLK, SPIQ, SPID) | Crash on boot |
| GPIO33–GPIO37 | Octal PSRAM (SPIIO4–7, SPIDQS) on -R8 modules | Crash on PSRAM-equipped modules |
| GPIO43, GPIO44 | U0TXD / U0RXD | Loses the default console |

**Integration concern:** unlike the original ESP32, the ESP32-S3 has **no input-only
pins** — the classic GPIO34–39 restriction does not apply here. Guides written for the
original ESP32 mislead on this point, which is one reason this project reads the S3
datasheet rather than a tutorial.

**UNVERIFIED:** whether the onboard RGB LED is GPIO38 or GPIO48 depends on the
DevKitC-1 board revision. Because it varies, `STATUS_LED_ENABLED` defaults to 0.

---

## INMP441 (TDK InvenSense)

**Document:** *INMP441 Omnidirectional Microphone with Bottom Port and I2S Digital
Output* datasheet (TDK InvenSense).

| Property | Value |
| --- | --- |
| Supply | 1.8–3.3 V. **3.3 V in this design; 5 V destroys it.** |
| Interface | I2S, 24-bit output |
| Sensitivity | −26 dBFS (typical) |
| SNR | 61 dB (typical) |
| Frequency response | 60 Hz – 15 kHz |
| Sample rate range | 8–48 kHz (we use 16 kHz) |

### Integration concerns

- **Data format.** The INMP441 outputs 24-bit samples **MSB-justified inside a 32-bit
  slot**. The ESP32 must be configured for 32-bit slots and the samples shifted down to
  16 bits. This project does that shift in exactly one place, `audio_input.cpp`, using
  `MIC_SAMPLE_SHIFT`.
- **Channel select.** The `L/R` pin decides which half of the I2S frame the mic drives.
  Tied to **GND** here, so it occupies the **left** slot, and the firmware is configured
  for `I2S_CHANNEL_FMT_ONLY_LEFT`. Getting this backwards yields all-zero samples — the
  single most common INMP441 bring-up failure.
- **Gain.** `MIC_SAMPLE_SHIFT = 11` gives roughly 5 bits of digital gain over a plain
  24→16-bit truncation, because a straight shift is too quiet for desk-distance speech
  at this mic's sensitivity. **NEEDS HARDWARE VALIDATION** — measure with
  `selftest/mic_test` and adjust.
- **Bottom port.** The acoustic port is on the underside of the package. The enclosure
  needs a hole aligned to it, not merely near it.

---

## MAX98357A (Analog Devices, formerly Maxim Integrated)

**Document:** *MAX98357A/MAX98357B PCM Input Class D Audio Power Amplifier* datasheet
(Analog Devices).

| Property | Value |
| --- | --- |
| Supply (VIN) | 2.5–5.5 V. 5 V used here for output power. |
| Logic inputs | 3.3 V tolerant at a 5 V supply — **no level shifter required** |
| Output | 3.2 W into 4 Ω at 5 V (typical) |
| Interface | I2S / PCM input, 16/24/32-bit |
| Sample rates | 8–96 kHz |
| Efficiency | ~92 % (class D) |

### Integration concerns

- **The SD pin does two jobs.** It is both shutdown and gain select, via a resistor to
  ground. Driving it from a GPIO gives clean muting; this project drives it low between
  utterances because the amplifier's idle hiss is audible in a quiet room, and a robot
  that hisses whenever it is on feels cheap.
- **Mono, but expects a stereo frame.** Depending on the SD-pin resistor it either sums
  L+R or selects one channel. This project sends a single-channel frame configured as
  left, which every observed module variant accepts.
- **Class-D switching noise.** The output is a switched waveform. Route speaker leads
  away from the microphone's I2S lines, and keep the amplifier's ground return short —
  this is a common cause of a raised noise floor that looks like a microphone fault.
- **Supply sag.** At volume on 4 Ω the amplifier draws significant current. Crackling or
  a board reset when speech starts is almost always the 5 V supply, not the software.

---

## SSD1306 (Solomon Systech)

**Document:** *SSD1306 Advanced Information: 128 × 64 Dot Matrix OLED/PLED Segment/Common
Driver with Controller* datasheet (Solomon Systech).

| Property | Value |
| --- | --- |
| Supply (module) | typically 3.3 V; many breakouts accept 3.3–5 V |
| Logic | 3.3 V compatible |
| Interface | I2C (used here), SPI, or parallel |
| I2C address | 0x3C (SA0 low) or 0x3D (SA0 high) |
| I2C clock | supports fast mode; 400 kHz used here |
| Resolution | 128 × 64 |
| Framebuffer | 1024 bytes (1 bit per pixel), held in MCU RAM by the driver |

### Integration concerns

- **Address varies by module.** 0x3C is far more common, but 0x3D exists.
  `selftest/oled_test` scans the bus and prints what it finds.
- **I2C is the bottleneck, not the panel.** A full 1024-byte frame at 400 kHz takes
  roughly 25 ms. This is why the face task targets ~30 fps and runs at the lowest
  working priority — pushing it harder buys nothing visible and steals time from audio.
- **SPI variants exist and will not work here.** The module must be the I2C variant
  (4 pins: VCC, GND, SDA, SCL).
- **Burn-in.** OLEDs retain static images over long periods. The idle animation drifts
  the eyes and blinks partly for this reason, not only for character.

---

## Facts this project does not claim

- No power consumption figures have been measured on assembled hardware.
- No acoustic isolation figure is claimed; `selftest/full_io_test` measures it on *your*
  build because it depends entirely on the enclosure.
- No supplier, price, or availability claims are made for any component.
- No thermal characteristics have been measured.
