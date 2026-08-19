# Fafobot hardware

Everything needed to build one prototype: parts, pin map, wiring, power, and the order to
assemble it in.

> **NEEDS HARDWARE VALIDATION.** This pin map was derived from manufacturer
> documentation (see `docs/HARDWARE_REFERENCES.md`) and has **not** been verified on a
> physical board. Work through the assembly order below, flashing the named self-test at
> each stage, before trusting any of it.

---

## Bill of materials

| # | Part | Specification | Notes |
| --- | --- | --- | --- |
| 1 | ESP32-S3 dev board | ESP32-S3-DevKitC-1, ESP32-S3-WROOM-1 module | Any flash/PSRAM variant. The pin map avoids GPIO33–37, so the octal-PSRAM (-R8) parts work unchanged. |
| 2 | Microphone | INMP441 I2S MEMS module | Breakout with VDD/GND/SCK/WS/SD/L-R pins. |
| 3 | Amplifier | MAX98357A I2S class-D breakout | 3.2 W mono. Adafruit and generic clones both work. |
| 4 | Speaker | 4 Ω, 3 W, 40–50 mm | 8 Ω also works and is quieter — fine for a desk. |
| 5 | Display | SSD1306 OLED, 128×64, **I2C** | Must be I2C, not SPI. Usually address 0x3C. |
| 6 | Input | Momentary push button (SPST) | Or a copper-tape electrode for capacitive touch. |
| 7 | Power | USB-C cable + 5 V / ≥1 A supply | A phone charger is fine. A laptop port may current-limit under audio load. |
| 8 | Wiring | Dupont jumpers, or 26 AWG wire | Soldered joints are strongly preferred — see below. |
| 9 | Breadboard | half-size, optional | For bring-up only. |

**Not claimed:** no specific supplier, price, or availability. Buy from wherever you
normally source parts; every item above is a commodity.

**A note on jumpers:** I2S is a clocked digital bus running at ~1 MHz here. Long, loose
breadboard jumpers work often enough to be misleading and fail often enough to waste a
day. If the microphone reads noise or the speaker buzzes, shorten and solder the I2S
lines before suspecting the software.

---

## Pin map

| Component | Signal | ESP32-S3 pin | Voltage | Direction | Notes |
| --- | --- | --- | --- | --- | --- |
| INMP441 | VDD | 3V3 | 3.3 V | — | **3.3 V only.** 5 V destroys it. |
| INMP441 | GND | GND | — | — | |
| INMP441 | SCK | GPIO4 | 3.3 V | ESP32 → mic | I2S bit clock |
| INMP441 | WS | GPIO5 | 3.3 V | ESP32 → mic | I2S word select |
| INMP441 | SD | GPIO6 | 3.3 V | mic → ESP32 | I2S data |
| INMP441 | L/R | GND | — | — | Ties the mic to the **left** slot. Firmware reads left. |
| MAX98357A | VIN | 5V (VBUS) | 5 V | — | 5 V for output power; see Power below. |
| MAX98357A | GND | GND | — | — | |
| MAX98357A | BCLK | GPIO15 | 3.3 V | ESP32 → amp | Logic is 3.3 V tolerant; no level shifter. |
| MAX98357A | LRC | GPIO16 | 3.3 V | ESP32 → amp | |
| MAX98357A | DIN | GPIO7 | 3.3 V | ESP32 → amp | |
| MAX98357A | SD | GPIO17 | 3.3 V | ESP32 → amp | Shutdown/gain. Driven low between utterances to kill idle hiss. |
| MAX98357A | +/− | speaker | — | — | Speaker across the two output terminals. |
| SSD1306 | VCC | 3V3 | 3.3 V | — | Most modules accept 3.3 V; check yours. |
| SSD1306 | GND | GND | — | — | |
| SSD1306 | SDA | GPIO8 | 3.3 V | bidirectional | I2C data, 400 kHz |
| SSD1306 | SCL | GPIO9 | 3.3 V | ESP32 → OLED | I2C clock |
| Button | one leg | GPIO10 | 3.3 V | input | Internal pull-up; press pulls to GND. Also TOUCH10. |
| Button | other leg | GND | — | — | |

All pin numbers live in `firmware/include/config.h`. There is no GPIO number anywhere
else in the firmware.

### Why these pins

Chosen by elimination against the ESP32-S3 datasheet and the DevKitC-1 user guide.
Excluded:

| Pin(s) | Why |
| --- | --- |
| GPIO0 | Strapping (boot mode); the BOOT button on the devkit |
| GPIO3 | Strapping (JTAG source select) |
| GPIO45 | Strapping (VDD_SPI voltage select) — must read low at reset |
| GPIO46 | Strapping (ROM message print enable) |
| GPIO19, GPIO20 | USB D−/D+ for native USB-Serial-JTAG. Using them breaks flashing. |
| GPIO26–32 | SPI flash |
| GPIO33–37 | Octal PSRAM on -R8 modules |
| GPIO43, GPIO44 | UART0 console |
| GPIO38 / GPIO48 | Onboard RGB LED — which one depends on board revision |
| GPIO22–25 | Do not exist on the ESP32-S3 |

Capacitive touch is available on **GPIO1–GPIO14 only**, which is why the interaction pin
is GPIO10: the same wiring serves both the button build and the touch build.

---

## Wiring diagrams

```
ESP32-S3                    INMP441
--------                    -------
3V3    ────────────────►    VDD
GND    ────────────────►    GND
GPIO4  ────────────────►    SCK
GPIO5  ────────────────►    WS
GPIO6  ◄────────────────    SD
GND    ────────────────►    L/R     (selects the left channel)
```

```
ESP32-S3                    MAX98357A                 speaker
--------                    ---------                 -------
5V     ────────────────►    VIN
GND    ────────────────►    GND
GPIO15 ────────────────►    BCLK
GPIO16 ────────────────►    LRC
GPIO7  ────────────────►    DIN
GPIO17 ────────────────►    SD       (shutdown/gain)
                            +   ──────────────────►   + terminal
                            −   ──────────────────►   − terminal
```

```
ESP32-S3                    SSD1306 (I2C)
--------                    -------------
3V3    ────────────────►    VCC
GND    ────────────────►    GND
GPIO8  ◄──────────────►     SDA
GPIO9  ────────────────►    SCL
```

```
ESP32-S3                    button
--------                    ------
GPIO10 ────────────────►    leg 1
GND    ────────────────►    leg 2

(internal pull-up enabled; pressing pulls the pin LOW)
```

---

## Power

| Rail | Feeds | Source |
| --- | --- | --- |
| 5 V | MAX98357A VIN only | USB VBUS, via the devkit's 5V pin |
| 3.3 V | ESP32-S3, INMP441, SSD1306, all logic | The devkit's onboard regulator |

**The one dangerous mistake:** the INMP441 is a **3.3 V part**. Connecting it to 5 V will
destroy it. Double-check that wire before applying power.

The MAX98357A's *logic* inputs (BCLK, LRC, DIN, SD) are 3.3 V tolerant, so no level
shifter is needed even though its supply is 5 V. Running its VIN from 3.3 V also works
but yields noticeably less output power.

**Current:** the ESP32-S3 draws bursts during Wi-Fi transmission, and the amplifier draws
current proportional to volume. A 4 Ω speaker at moderate volume plus Wi-Fi peaks can
exceed what some laptop USB ports supply. Symptoms of an inadequate supply are crackling
under load, or the board resetting when the robot starts speaking — both look like
software faults and are not. Use a 1 A phone charger.

> **NEEDS HARDWARE VALIDATION:** total system current has not been measured. Measure it
> during a spoken reply at your intended volume.

---

## Assembly order

Build up one subsystem at a time and flash the named self-test at each stage. Adding
everything at once and then debugging is how a weekend disappears.

### Stage 1 — ESP32 alone

Nothing wired. Power over USB.

```bash
cd firmware
pio run -e oled_test -t upload -t monitor
```

Expect: serial output at 115200 and an empty I2C scan. This confirms the toolchain, the
USB port, and the board itself before any peripheral can confuse the picture.

- [ ] board enumerates as a serial device
- [ ] serial output is readable
- [ ] `pio run -t upload` completes

### Stage 2 — OLED

Wire VCC, GND, SDA, SCL.

```bash
pio run -e oled_test -t upload -t monitor
```

- [ ] I2C scan finds a device (0x3C, or 0x3D — set `OLED_I2C_ADDRESS` if 0x3D)
- [ ] all-pixels-on shows no dead rows or columns
- [ ] border shows all four corners
- [ ] text is legible
- [ ] the test face renders

### Stage 3 — Microphone

Wire VDD, GND, SCK, WS, SD, and **L/R to GND**.

```bash
pio run -e mic_test -t upload -t monitor
```

- [ ] samples are not all zero (all-zero means the SD line or L/R is wrong)
- [ ] the bar meter responds to your voice
- [ ] quiet-room RMS noted → this is your noise floor
- [ ] speaking RMS noted, and no `CLIPPING` warning at normal volume
- [ ] `VAD_START_RMS` set to ≈4× the floor, `VAD_STOP_RMS` to ≈2×, in `config.h`

### Stage 4 — Amplifier and speaker

Wire VIN, GND, BCLK, LRC, DIN, SD, and the speaker.

```bash
pio run -e speaker_test -t upload -t monitor
```

- [ ] tones are clean, not buzzing (buzzing usually means an I2S clock line)
- [ ] the sweep has no rattle at any frequency
- [ ] no crackle at volume (crackle under load usually means the 5 V supply)
- [ ] near-silence during the muted section

### Stage 5 — Button or touch

Wire the button between GPIO10 and GND.

```bash
pio run -e touch_test -t upload -t monitor
```

- [ ] a short press reports `SHORT_TOUCH`
- [ ] a held press (>800 ms) reports `LONG_TOUCH`
- [ ] no phantom presses while untouched
- [ ] (touch build only) `TOUCH_THRESHOLD` set from the suggested value

### Stage 6 — Everything together

Everything wired, ideally in its enclosure.

```bash
pio run -e full_io_test -t upload -t monitor
```

This is the stage that catches integration faults the individual tests cannot: I2S
peripherals conflicting, amplifier ground noise reaching the microphone, an I2C bus that
only misbehaves while audio DMA runs.

- [ ] all five subsystems report PASS
- [ ] noise floor is similar to Stage 3 (a big rise means the amplifier is coupling in)
- [ ] the acoustic isolation figure is GOOD or ACCEPTABLE — if POOR, see
      `docs/ENCLOSURE_GUIDE.md` before continuing

### Stage 7 — The real firmware

```bash
cp include/secrets_example.h include/secrets.h
$EDITOR include/secrets.h        # Wi-Fi + your backend's IP
pio run -e fafobot -t upload -t monitor
```

- [ ] Wi-Fi connects
- [ ] WebSocket connects; the backend logs `robot connected`
- [ ] speaking produces `utterance_start` in the backend log
- [ ] the robot replies through the speaker
- [ ] the mouth moves with the audio
- [ ] the face returns to resting after a reaction
- [ ] pulling the backend down shows the offline face; restarting it reconnects

### Stage 8 — Enclosure

See `docs/ENCLOSURE_GUIDE.md`. Re-run `full_io_test` **inside the finished enclosure**:
mounting changes the acoustics, and the isolation figure that mattered on the bench is
not the one that matters in the case.

---

## Full validation checklist

Work top to bottom on first bring-up.

```
[ ] ESP32 powers safely
[ ] serial output works
[ ] OLED detected on I2C
[ ] face renders
[ ] microphone clock present (mic_test runs without an I2S error)
[ ] microphone samples non-zero
[ ] noise floor measured and written into config.h
[ ] speaker test tone plays cleanly
[ ] no severe hiss when idle
[ ] touch/button detected, short and long presses distinguished
[ ] full_io_test reports acceptable acoustic isolation
[ ] Wi-Fi connects
[ ] WebSocket connects
[ ] PCM uploads (backend logs a turn with a plausible audio_ms)
[ ] backend transcribes
[ ] LLM responds
[ ] TTS returns audio
[ ] audio plays through the speaker
[ ] mouth animates with the audio
[ ] state returns to idle after the reply
[ ] a 10-turn conversation completes without a wedge or a restart
```
