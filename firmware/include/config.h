#pragma once
/**
 * Fafobot firmware configuration: every pin, every timing constant, one file.
 *
 * Nothing else in the firmware may contain a GPIO number. If you find one, that is a
 * bug — move it here. The reason is not tidiness: it is that a wiring change on the
 * bench must be a one-file edit, verifiable against docs/HARDWARE.md, and not a search
 * through .cpp files for a literal.
 *
 * ---------------------------------------------------------------------------------
 * PIN SELECTION RATIONALE (ESP32-S3-DevKitC-1, ESP32-S3-WROOM-1)
 * ---------------------------------------------------------------------------------
 * Pins were chosen by elimination against the Espressif ESP32-S3 datasheet and the
 * DevKitC-1 user guide. Excluded, and why:
 *
 *   GPIO0          strapping (boot mode select); also the BOOT button on the devkit
 *   GPIO3          strapping (JTAG source select)
 *   GPIO45         strapping (VDD_SPI voltage select) - must read low at reset
 *   GPIO46         strapping (ROM message print enable)
 *   GPIO19, GPIO20 USB D-/D+ for the native USB-Serial-JTAG; using them kills flashing
 *                  and the serial console over the USB port
 *   GPIO26-GPIO32  SPI flash (SPICS1, SPIHD, SPIWP, SPICS0, SPICLK, SPIQ, SPID)
 *   GPIO33-GPIO37  octal PSRAM on -R8 modules (SPIIO4..SPIIO7, SPIDQS). Free on
 *                  non-PSRAM parts, but we do not use them so one BOM does both.
 *   GPIO43, GPIO44 U0TXD/U0RXD, the default UART console
 *   GPIO38/GPIO48  onboard addressable RGB LED. Which one depends on the board
 *                  revision (see STATUS_LED_PIN below)
 *   GPIO22-GPIO25  do not exist on the ESP32-S3
 *
 * Everything below is drawn from the remaining free set. Touch is available on
 * GPIO1-GPIO14 only, which is why the interaction pin sits in that range.
 *
 * NEEDS HARDWARE VALIDATION: this map has been checked against documentation, not
 * against a physical board. Run firmware/selftest/full_io_test before trusting it.
 */

// =====================================================================================
// Identity
// =====================================================================================
#define FAFOBOT_FIRMWARE_VERSION "1.0.0"

// Used as the device_id in the protocol handshake when no per-unit id is provisioned.
// Override in secrets.h for a fleet.
#ifndef FAFOBOT_DEVICE_ID
#define FAFOBOT_DEVICE_ID "fafobot-dev-01"
#endif

// =====================================================================================
// I2S microphone - INMP441 (TDK InvenSense)
// =====================================================================================
// The INMP441 is a 24-bit I2S output MEMS mic. It drives data on one half of the frame
// depending on its L/R pin; we tie L/R to GND and read the LEFT channel.
#define MIC_I2S_PORT        I2S_NUM_0   // dedicated controller; never shared with output
#define MIC_SCK_PIN         4           // INMP441 SCK  <- ESP32 bit clock (output)
#define MIC_WS_PIN          5           // INMP441 WS   <- ESP32 word select (output)
#define MIC_SD_PIN          6           // INMP441 SD   -> ESP32 data (input)

// The INMP441 emits 24-bit samples MSB-justified inside a 32-bit slot. We read 32-bit
// slots and shift down to 16 bits. The shift is applied in exactly one place
// (audio_input.cpp); do not scatter it.
#define MIC_BITS_PER_SLOT   32
#define MIC_SAMPLE_SHIFT    11          // 32 -> 16 bit, plus ~5 bits of digital gain
// NEEDS HARDWARE VALIDATION: MIC_SAMPLE_SHIFT sets the noise floor and the clipping
// point. 11 is a starting point (a pure >>16 is too quiet for desk-distance speech at
// this mic's sensitivity). Measure with selftest/mic_test and adjust: you want normal
// speech peaking around half scale, not clipping.

// =====================================================================================
// I2S amplifier - MAX98357A (Analog Devices / Maxim)
// =====================================================================================
// Class-D mono amp. Runs from 5 V for output power; its logic inputs are 3.3 V
// tolerant, so no level shifter is needed. See docs/HARDWARE.md#power.
#define AMP_I2S_PORT        I2S_NUM_1   // second controller: input and output never
                                        // contend for the same peripheral
#define AMP_BCLK_PIN        15          // MAX98357A BCLK <- ESP32
#define AMP_LRC_PIN         16          // MAX98357A LRC  <- ESP32
#define AMP_DIN_PIN         7           // MAX98357A DIN  <- ESP32

// Optional shutdown control. The MAX98357A's SD pin selects gain AND enables the amp.
// Driving it low mutes the amp, which removes idle hiss between utterances.
// Set to -1 if you tied SD to a resistor divider instead of a GPIO.
#define AMP_SD_PIN          17
#define AMP_ENABLE_LEVEL    HIGH

// =====================================================================================
// OLED - SSD1306 128x64 over I2C (Solomon Systech)
// =====================================================================================
#define OLED_SDA_PIN        8
#define OLED_SCL_PIN        9
#define OLED_I2C_ADDRESS    0x3C        // 0x3D on some modules; mic_test prints a scan
#define OLED_WIDTH          128
#define OLED_HEIGHT         64
#define OLED_I2C_FREQUENCY  400000UL    // fast mode; the SSD1306 supports it

// =====================================================================================
// Interaction - button or capacitive touch
// =====================================================================================
// GPIO10 is TOUCH10 on the ESP32-S3 (touch is available on GPIO1-GPIO14 only), so the
// same pin serves both build variants without a wiring change.
#define INTERACTION_PIN     10

// Choose one. Button is the default because a bare wire electrode needs calibration and
// V1 should work with parts already in a drawer.
#define INTERACTION_USE_TOUCH 0
#if INTERACTION_USE_TOUCH
// NEEDS HARDWARE VALIDATION: the threshold depends on the electrode's size and what it
// is glued to. selftest/touch_test prints live readings; set this between the resting
// value and the touched value.
#define TOUCH_THRESHOLD     40000
#else
// Wire the button between the pin and GND; the internal pull-up does the rest.
#define BUTTON_ACTIVE_LEVEL LOW
#define BUTTON_USE_PULLUP   1
#endif

#define INTERACTION_DEBOUNCE_MS   30
#define INTERACTION_LONG_PRESS_MS 800   // above this a press means "cancel"
#define INTERACTION_MAX_PRESS_MS  10000 // beyond this, assume a stuck input and ignore

// =====================================================================================
// Status LED (optional)
// =====================================================================================
// The DevKitC-1 onboard RGB LED is GPIO38 on v1.1 boards and GPIO48 on v1.0 boards.
// Because that varies, it is off by default: a wrong guess drives a pin that may be
// wired to something else on your revision.
#define STATUS_LED_ENABLED  0
#define STATUS_LED_PIN      38

// =====================================================================================
// Audio format - must match backend/protocol.py
// =====================================================================================
#define AUDIO_SAMPLE_RATE       16000
#define AUDIO_BITS_PER_SAMPLE   16
#define AUDIO_CHANNELS          1
#define AUDIO_BYTES_PER_SAMPLE  2

// =====================================================================================
// Audio buffering
// =====================================================================================
// Every buffer below is statically sized. There is no dynamic allocation on the audio
// path, so there is no fragmentation and no allocation failure to handle mid-sentence.
// The RAM total is tabulated in docs/ARCHITECTURE.md#ram-budget.

// One I2S read/write unit. 20 ms at 16 kHz: small enough that the VAD reacts quickly,
// large enough that we are not waking tasks thousands of times a second.
#define AUDIO_FRAME_SAMPLES     320
#define AUDIO_FRAME_BYTES       (AUDIO_FRAME_SAMPLES * AUDIO_BYTES_PER_SAMPLE)

// I2S DMA. Four descriptors of one frame each gives 80 ms of slack before an overrun,
// which comfortably covers a WebSocket send stalling on a retransmit.
#define I2S_DMA_BUF_COUNT       4
#define I2S_DMA_BUF_LEN         AUDIO_FRAME_SAMPLES

// Pre-roll: audio kept from *before* the VAD fired, prepended to every utterance.
// Without it the first consonant is always missing, because energy-based VAD cannot
// trigger until the sound already exists. 250 ms covers a plosive onset plus the
// detector's own minimum-speech delay.
#define AUDIO_PREROLL_MS        250
#define AUDIO_PREROLL_BYTES     ((AUDIO_SAMPLE_RATE * AUDIO_PREROLL_MS / 1000) * AUDIO_BYTES_PER_SAMPLE)

// Capture -> network. Holds ~500 ms, so a brief Wi-Fi stall does not drop speech.
#define AUDIO_CAPTURE_STREAM_BYTES  16384

// Network -> speaker. ~1.5 s of audio. Playback does not start until LOW_WATER is
// reached, which is what prevents the stutter at the beginning of every reply.
#define AUDIO_PLAYBACK_STREAM_BYTES 49152
#define AUDIO_PLAYBACK_LOW_WATER    8192   // ~256 ms buffered before the first sample
#define AUDIO_PLAYBACK_HIGH_WATER   40960  // above this, drop incoming chunks and log

// How long after the robot stops speaking before the microphone reopens. Covers the
// speaker's decay and the room's reverb tail so the robot does not hear its own voice.
// V1 is half-duplex; this constant is the crude stand-in for the echo cancellation a
// later version will need.
#define AUDIO_POST_PLAYBACK_GUARD_MS 250

// =====================================================================================
// Voice activity detection
// =====================================================================================
// Energy-based with hysteresis. Two thresholds, not one: a single threshold chatters
// on and off around the boundary and slices utterances into fragments.
//
// Values are RMS in normalised units (0.0 - 1.0).
// NEEDS HARDWARE VALIDATION: these depend on mic gain, enclosure and room. Run
// selftest/mic_test, note the quiet-room RMS and the speaking RMS, then set START
// around 4x the noise floor and STOP around 2x.
#define VAD_START_RMS           0.030f
#define VAD_STOP_RMS            0.015f

// Speech must exceed VAD_START_RMS for this long before an utterance begins. Rejects
// key clicks, door bumps and chair scrapes.
#define VAD_MIN_SPEECH_MS       200

// Silence this long ends the utterance. Long enough to survive the pause mid-sentence
// when someone is thinking; short enough that the robot does not feel slow.
#define VAD_TRAILING_SILENCE_MS 700

// Hard ceiling on one utterance. Protects the backend and stops a noisy room from
// producing a 10-minute upload.
#define AUDIO_MAX_UTTERANCE_MS  20000

// Utterances shorter than this after trimming are discarded without upload.
#define AUDIO_MIN_UTTERANCE_MS  300

// =====================================================================================
// Mouth animation
// =====================================================================================
// The mouth follows measured playback amplitude, never estimated text duration.
#define MOUTH_ENVELOPE_ATTACK   0.55f   // 0-1, higher tracks transients faster
#define MOUTH_ENVELOPE_RELEASE  0.18f   // lower = smoother close, less twitch
#define MOUTH_MIN_RMS           0.010f  // below this the mouth is shut
#define MOUTH_MAX_RMS           0.250f  // at or above this the mouth is fully open
#define MOUTH_MIN_OPEN_PX       2
#define MOUTH_MAX_OPEN_PX       18

// =====================================================================================
// Face timing
// =====================================================================================
#define FACE_FRAME_INTERVAL_MS      33      // ~30 fps; the SSD1306 cannot usefully do more
#define FACE_BLINK_MIN_INTERVAL_MS  2600
#define FACE_BLINK_MAX_INTERVAL_MS  6800
#define FACE_BLINK_DURATION_MS      130
// Idle eye drift. Subtle on purpose: a face that moves constantly reads as nervous,
// not alive.
#define FACE_IDLE_GLANCE_MIN_MS     4000
#define FACE_IDLE_GLANCE_MAX_MS     11000
#define FACE_IDLE_GLANCE_HOLD_MS    900

// Local expiry for a reactive expression when the backend never sends the next one.
// The device must be able to leave any temporary face on its own.
#define FACE_EXPRESSION_DEFAULT_HOLD_MS 1800
#define FACE_EXPRESSION_MAX_HOLD_MS     10000

// =====================================================================================
// Networking
// =====================================================================================
#define WIFI_CONNECT_TIMEOUT_MS     20000
#define WIFI_RECONNECT_MIN_MS       1000
#define WIFI_RECONNECT_MAX_MS       30000

#define WS_RECONNECT_MIN_MS         1000
#define WS_RECONNECT_MAX_MS         30000
// Multiplier per failed attempt, capped at the max above. Jitter is added at runtime so
// a room full of robots does not reconnect in lockstep after a router reboot.
#define WS_RECONNECT_BACKOFF_NUM    2
#define WS_RECONNECT_BACKOFF_DEN    1
#define WS_RECONNECT_JITTER_PCT     25

// Silence from the backend for this long means the link is dead even if TCP disagrees.
#define WS_INACTIVITY_TIMEOUT_MS    45000
#define WS_PING_INTERVAL_MS         15000

// Bytes per outbound audio frame. Matches MAX_BINARY_FRAME_BYTES on the backend.
#define WS_AUDIO_CHUNK_BYTES        2048

// =====================================================================================
// FreeRTOS task configuration
// =====================================================================================
// Audio tasks live on core 1 with everything else on core 0. The split is the whole
// real-time strategy: Wi-Fi and the OLED cannot preempt audio, because they are not on
// its core.
//
// Priorities are relative to configMAX_PRIORITIES (25 under Arduino-ESP32). Audio is
// highest because a late sample is audible; the face is lowest because a late frame
// is not.
#define TASK_AUDIO_IN_CORE       1
#define TASK_AUDIO_IN_PRIORITY   6
#define TASK_AUDIO_IN_STACK      4096

#define TASK_AUDIO_OUT_CORE      1
#define TASK_AUDIO_OUT_PRIORITY  6
#define TASK_AUDIO_OUT_STACK     4096

#define TASK_NET_CORE            0
#define TASK_NET_PRIORITY        5
#define TASK_NET_STACK           8192

#define TASK_FACE_CORE           0
#define TASK_FACE_PRIORITY       3
#define TASK_FACE_STACK          4096

#define TASK_INTERACTION_CORE    0
#define TASK_INTERACTION_PRIORITY 4
#define TASK_INTERACTION_STACK   2560

// =====================================================================================
// Offline behaviour
// =====================================================================================
// How long the link may be down before the face switches to the offline expression.
// Short blips should not visibly disturb the robot.
#define OFFLINE_FACE_DELAY_MS    4000
