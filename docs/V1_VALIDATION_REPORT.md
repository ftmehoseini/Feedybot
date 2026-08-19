# V1 validation report

Date: 2026-08-19

This document states what has actually been verified and what has not. Read the
evidence-level table first — several things in this repository are **written but never
executed on real hardware or against a real API**, and this report exists so nobody
discovers that on a bench at 2 a.m.

## Evidence levels used here

| Level | Means |
| --- | --- |
| **unit tested** | executed by an automated test in this repository |
| **integration tested** | executed end to end across module boundaries, in-process |
| **simulated** | exercised over a real WebSocket by `robot/simulator.py` |
| **compiled** | the compiler accepted it; it has never run |
| **not compiled** | written, never fed to a compiler |
| **hardware verified** | run on a physical robot |
| **live API verified** | run against a real provider endpoint |

---

## 1. Automated validation

### Python suite — **207 passed, 0 failed, 0 skipped**

```
$ pytest
207 passed in 7.62s
```

| File | Tests | Covers |
| --- | --- | --- |
| `test_agent_reply.py` | 25 | marker variants, markdown stripping, Persian, empty output, neutral fallback |
| `test_config_and_providers.py` | 29 | startup validation, provider selection, adapters via mocked transport, log redaction |
| `test_state_machine.py` | 29 | transitions, turn ids, cancellation, history bounds, session isolation |
| `test_roles.py` | 25 | schema, loading, path traversal, no role branching in core |
| `test_protocol.py` | 23 | encoding, malformed frames, size limits, forward compatibility |
| `test_pipeline.py` | 22 | full turns, every failure mode, every timeout, mid-reply cancellation, latency |
| `test_audio.py` | 17 | PCM conversion, chunk boundaries, format refusals |
| `test_connection.py` | 15 | real WebSocket turns against the real app |
| `test_prompt_builder.py` | 14 | layer order, no cross-role leakage, reserved layers unused |
| `test_protocol_parity.py` | 8 | backend and firmware agree on the protocol |

### C++ host suite — **208 checks, 0 failures**

```
$ make -C tests/firmware test
208 checks, 0 failures
```

Covers `vad.h`, `mouth.h`, `backoff.h`, `robot_state.h` — compiled with
`-std=c++17 -Wall -Wextra -Werror`.

**This suite found a real bug.** `frame_rms` originally used a hand-rolled
Newton-Raphson square root that, seeded far from the root, had not converged after twelve
iterations — full-scale audio measured as RMS 0.008 instead of 1.0. On hardware that
would have presented as a VAD that never triggers at normal speech volume. It is now
`sqrtf`.

### Not measured

No coverage percentage is reported. Nothing in this repository measures it, and quoting a
number would be an invention.

---

## 2. Firmware validation

| Item | Status |
| --- | --- |
| Pure logic (VAD, mouth, backoff, face state, parsing) | **unit tested** (208 host checks) |
| Everything else (`main.cpp`, `audio_input`, `audio_output`, `net_link`, `face`, `interaction`, all 5 self-tests) | **not compiled** |
| Binary size | **not measured** |
| RAM usage | **calculated, not measured** (~110 KB — see `docs/ARCHITECTURE.md#ram-budget`) |
| Flashed to a board | **no** |

### Why the firmware was not compiled

PlatformIO could not install the ESP32 platform in this environment. The package registry
is blocked by the session's network policy:

```
$ pio run -e fafobot
Processing fafobot (platform: espressif32@^6.9.0; board: esp32-s3-devkitc-1)
Platform Manager: Installing espressif32 @ ^6.9.0
HTTPClientError:
```

The proxy recorded the cause: `gateway answered 403 to CONNECT` for
`api.registry.platformio.org:443` — an egress policy denial, not a transient failure.

**Consequence: the device firmware has never been compiled.** Treat a first build as
likely to surface errors. What has been done to reduce that risk:

- All logic that *could* be host-tested was extracted into Arduino-free headers and is
  covered by 208 checks.
- Header include hygiene was audited (this found one real defect: `face.h` used
  `FACE_EXPRESSION_MAX_HOLD_MS` without including `config.h`).
- `tests/test_protocol_parity.py` parses the firmware headers and asserts they match the
  backend, so the protocol constants are verified even though the C++ is not built.

**Anyone with a toolchain should run `pio run -e fafobot` first and expect to fix
compile errors before anything else.**

### API generation risk

The firmware targets the **legacy** `driver/i2s.h` API, which ships with Arduino-ESP32
2.0.x (`platform = espressif32@^6.9.0`, pinned in `platformio.ini`). Arduino-ESP32 3.x
replaces it with `i2s_std.h` — a breaking change. All I2S calls are confined to
`audio_input.cpp` and `audio_output.cpp` so that migration is contained, but a build
against a 3.x core **will fail**. This is a known, deliberate pin, not an oversight.

---

## 3. Simulator validation — **passed**

`robot/simulator.py` against a live `python -m backend.main`, over a real WebSocket, with
the fake providers.

| Scenario | Result |
| --- | --- |
| Handshake and session establishment | pass — role and resting expression received |
| Single turn, audio uploaded and reply received | pass — 1800 ms in, 1210 ms of speech back |
| Multi-turn (2 and 3 consecutive turns) | pass — turn ids increment, no wedge |
| State sequence | pass — `idle → processing → thinking → speaking → listening` |
| Expression before audio | pass — `happy` arrives before `speak_start` |
| Microphone gating | pass — closed before speaking, reopened after `playback_done` |
| `playback_done` acknowledgement | pass — backend returns to listening |
| Reply saved as WAV | pass |
| Latency instrumentation | pass — all four stage timings logged, `total_ms` populated after playback |

Observed with the fake providers (i.e. measuring the plumbing, not any AI):
`stt_ms 1, llm_ms 0, tts_first_audio_ms 6, time_to_first_audio_ms 8, total_ms 16`.

**These numbers say nothing about real-world latency.** Real STT+LLM+TTS will dominate by
orders of magnitude. What they do establish is that the pipeline itself adds single-digit
milliseconds, so any latency measured later belongs to the providers.

---

## 4. Live API validation

| Provider | Adapter | Status |
| --- | --- | --- |
| STT — OpenAI-compatible | `providers/stt/openai_compatible.py` | **NOT VERIFIED** |
| LLM — OpenAI-compatible | `providers/llm/openai_compatible.py` | **NOT VERIFIED** |
| LLM — Gemini | `providers/llm/gemini.py` | **NOT VERIFIED** |
| TTS — OpenAI-compatible | `providers/tts/openai_compatible.py` | **NOT VERIFIED** |

No request has been made to any real provider endpoint from this environment. Every
adapter is **unit tested against `httpx.MockTransport`** — which verifies the request the
adapter builds and the response it parses, including failure paths, but proves nothing
about whether the real API behaves as its documentation says.

Request and response shapes follow published documentation for the OpenAI audio,
chat-completions and speech APIs, and Google's `generateContent`.

**Most likely to break on first contact with a real endpoint:**

1. **TTS sample rate.** `OpenAICompatibleTTS` requests WAV and *refuses* audio that is
   not 16 kHz, rather than resampling. If your provider returns 24 kHz, you will get a
   clear `ProviderError` naming the rate — that is the designed behaviour, but it means
   the first real call may fail until a suitable voice or model is configured.
2. **Whisper language names.** The adapter maps `"persian"`/`"farsi"` → `fa` and
   `"english"` → `en`, and returns `None` for anything else rather than guessing.
   Verify against actual responses.
3. **Gemini model naming.** `LLM_MODEL` is interpolated straight into the URL path.

---

## 5. Physical hardware validation

**Nothing has been tested on physical hardware. No board was present in this
environment.**

Explicitly *not* verified:

- the pin map (derived from documentation, never wired)
- I2S microphone capture, and whether `MIC_SAMPLE_SHIFT = 11` gives usable gain
- I2S playback, amplifier behaviour, speaker quality
- SSD1306 rendering, and whether the face is legible at desk distance
- VAD thresholds — `VAD_START_RMS`/`VAD_STOP_RMS` are placeholders that **must** be
  measured per unit with `selftest/mic_test`
- acoustic isolation between speaker and microphone
- whether 250 ms of post-playback guard is enough
- RAM headroom, task stack sizing, timing under Wi-Fi load
- Wi-Fi and WebSocket reconnection on real hardware
- power draw, and whether a given USB supply is adequate
- capacitive touch thresholds

`docs/HARDWARE.md` contains the full bring-up checklist. `selftest/full_io_test`
measures the noise floor and suggests VAD thresholds specifically because they cannot be
known in advance.

---

## 6. Acceptance criteria

| Criterion | Status |
| --- | --- |
| **Software** | |
| backend starts from a documented command | ✅ verified |
| firmware builds with PlatformIO | ❌ **not verified — toolchain unavailable** |
| simulator works | ✅ verified |
| tests pass | ✅ 207 Python + 208 C++ |
| configuration validated at startup | ✅ unit tested |
| no committed secrets | ✅ `.gitignore` covers `.env`, `secrets.h`, `*.pem`, `*.key` |
| **Conversation** | |
| multiple conversational turns | ✅ simulated and integration tested |
| Persian and English per the active role | ✅ integration tested (fake STT detects script) |
| LLM provider replaceable | ✅ unit tested (3 adapters) |
| STT provider replaceable | ✅ unit tested (2 adapters) |
| TTS provider replaceable | ✅ unit tested (2 adapters) |
| conversation history bounded | ✅ unit tested (both bounds) |
| **Role system** | |
| social companion works | ✅ integration tested |
| English teacher works | ✅ integration tested |
| changing role needs no firmware change | ✅ by construction; resting face arrives at handshake |
| no teacher-specific logic in the pipeline | ✅ asserted by test |
| **Robot behaviour** | |
| listening expression | ⚠️ implemented, **not hardware verified** |
| thinking expression | ⚠️ implemented, **not hardware verified** |
| speaking animation | ⚠️ implemented, **not hardware verified** |
| emotional reaction | ✅ protocol verified; rendering not hardware verified |
| mouth responds to actual audio | ⚠️ logic unit tested; **not hardware verified** |
| temporary expressions recover locally | ✅ unit tested (incl. `millis()` rollover) |
| **Reliability** | |
| Wi-Fi disconnect does not crash | ⚠️ implemented, **not hardware verified** |
| WebSocket reconnect supported | ✅ backend side tested; device side not verified |
| AI timeout returns to a usable state | ✅ unit tested, all three stages |
| malformed packet does not crash | ✅ backend unit tested; device side not verified |
| audio buffer bounded | ✅ backend tested; firmware statically sized, not verified |
| **Hardware** | |
| OLED standalone test | ⚠️ written, **not compiled** |
| microphone standalone test | ⚠️ written, **not compiled** |
| amplifier/speaker standalone test | ⚠️ written, **not compiled** |
| interaction standalone test | ⚠️ written, **not compiled** |
| combined hardware test | ⚠️ written, **not compiled** |
| **Documentation** | |
| a competent engineer can buy, wire, flash, run, configure and talk to it | ✅ complete — pending the hardware caveats above |

---

## 7. Known limitations

**Verification gaps** (the important ones)

1. Firmware has never been compiled. Expect to fix build errors.
2. Nothing has been run on hardware.
3. No live API call has ever been made.

**Design limitations** (deliberate)

4. Half duplex only. The robot cannot be interrupted while speaking except by the
   button; there is no barge-in and no echo cancellation.
5. No device authentication. Any client reaching the WebSocket gets a session. Fine on a
   trusted LAN, not fine on the open internet.
6. `ws://` only. TLS would need a CA bundle in the firmware; not configured.
7. Complete-response pipeline. TTS begins after the full LLM reply, so time-to-first-audio
   is the sum of all three stages. The interfaces are shaped for streaming; the
   implementation is not streaming.
8. History truncation, not summarisation. Long conversations lose their early context.
9. One robot per backend process in practice. The session model supports many; nothing
   has been tested beyond one.
10. VAD is energy-based. It will trigger on a television, a loud fan, or another person's
    conversation.
11. TTS sample-rate mismatch is a hard failure, not a resample.
12. Persian TTS quality is entirely the provider's; nothing here improves it.
13. No wake word. The robot listens whenever its gate is open.
14. OLED face is 1-bit at 128×64 with roughly 30 fps — animation is coarse by
    construction.

---

## 8. Recommended next experiments, in priority order

1. **Compile the firmware.** `pio run -e fafobot` on a machine with registry access. Fix
   what it finds. Nothing else can start until this does.
2. **Bring up the hardware stage by stage** per `docs/HARDWARE.md`, flashing each
   self-test in order. Do not skip to the full firmware.
3. **Measure and set the VAD thresholds** with `selftest/mic_test` in the real room. The
   shipped values are placeholders and word-clipping is the most likely first complaint.
4. **Run `full_io_test` and record the acoustic isolation figure.** It determines the
   guard time and whether AEC is feasible later.
5. **Make one live API call per provider** and confirm the response shapes — especially
   the TTS sample rate, which is the most likely first failure.
6. **Measure real end-to-end latency** with the instrumentation already in place. Do not
   optimise before this; the intuition that the LLM dominates is usually wrong, and it is
   usually TTS.
7. **Run the ten-turn conversation test** with a person who has not seen the code, and
   watch where they hesitate.

---

## 9. The question V1 exists to answer

> Can one person naturally talk to this physical robot for 5–10 minutes and feel they are
> interacting with a responsive character rather than ChatGPT inside a plastic box?

**Not yet answerable.** It requires a physical robot, and none has been built. Everything
between the microphone and the speaker is implemented and tested; the two ends of that
sentence are not.

What can be said: the conversation loop is complete and exercised end to end over a real
protocol, the failure paths all return the robot to a usable state, and the things that
most often break the illusion — a text-timed mouth, a face frozen by a lost packet, a
robot that hangs in "thinking", a robot claiming it can see you — have each been
specifically designed against and tested.
