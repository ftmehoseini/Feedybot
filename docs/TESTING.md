# Testing

Three suites, none of which needs hardware, an API key, or a network connection.

| Suite | What it covers | Command |
| --- | --- | --- |
| Python | backend, roles, prompts, pipeline, protocol, providers, full WebSocket turns | `pytest` |
| C++ host | the firmware's pure logic — VAD, mouth, backoff, face expiry, parsing | `make -C tests/firmware test` |
| Simulator | manual end-to-end against a running backend | `python -m robot.simulator` |

## Running everything

```bash
source .venv/bin/activate
pytest                                 # 207 tests
make -C tests/firmware test            # 208 checks
```

## Why the firmware logic is tested on the host

VAD timing, mouth smoothing, reconnect backoff and expression expiry are exactly the
behaviours that are miserable to debug on a device — they present as *"the robot
sometimes cuts me off"* — and none of them need an ESP32 to be correct.

So they live in Arduino-free headers (`firmware/include/vad.h`, `mouth.h`, `backoff.h`,
`robot_state.h`) and are compiled with a normal host compiler. `-Wall -Wextra -Werror`.

This found a real bug during development: `frame_rms` used a hand-rolled Newton-Raphson
square root that, seeded far from the root, had not converged after twelve iterations. On
hardware that would have presented as a VAD that never triggered at normal speech
levels — a day of oscilloscope time. On the host it was one failing assertion.

What is **not** covered on the host: I2S configuration, DMA behaviour, FreeRTOS
scheduling, I2C timing, Wi-Fi. Those need the self-test firmware and a real board.

## Python suite layout

| File | Covers |
| --- | --- |
| `test_roles.py` | schema validation, loading, path traversal, and that no core module mentions a role id |
| `test_prompt_builder.py` | layer order, core rules present exactly once, no cross-role leakage, reserved layers unused |
| `test_agent_reply.py` | marker variants, markdown stripping, Persian text, empty output, neutral fallback |
| `test_state_machine.py` | valid and invalid transitions, turn ids, cancellation, history bounds, session isolation |
| `test_pipeline.py` | full turns in both languages, every failure mode, every timeout, cancellation mid-reply, latency |
| `test_protocol.py` | encoding, malformed frames, size limits, forward compatibility |
| `test_protocol_parity.py` | backend and firmware agree on the protocol |
| `test_connection.py` | real WebSocket turns against the real app: handshake, gating, reconnect, garbage frames |
| `test_audio.py` | PCM conversion, chunk boundaries, format refusals |
| `test_config_and_providers.py` | startup validation, provider selection, adapters against a mocked transport, log redaction |

## The tests worth knowing about

**`test_core_code_contains_no_role_branching`** reads the core modules and fails if any
of them so much as mentions a role id. A regression there would break no behavioural
test — it would quietly reintroduce `if role == "teacher"` — so it is asserted directly.

**`test_every_stage_times_out_rather_than_hanging`** runs each stage with a slow provider
and asserts the robot ends up in LISTENING. This is the reliability promise: no path
leaves the robot stuck in THINKING or SPEAKING.

**`test_protocol_parity.py`** parses `firmware/include/protocol_constants.h` and
`config.h` and compares them against `backend/protocol.py`. The two define the same
contract in two languages and cannot share a definition. Drift between them shows up in
the field as a robot that connects, does nothing, and gives no clue why.

**`test_cancel_during_synthesis_stops_the_audio_mid_reply`** asserts that *some* audio
went out and the rest did not. Interrupting a speaking robot must actually stop it, not
just discard what was left.

## Provider adapters without a network

`tests/test_config_and_providers.py` drives the real adapters through an
`httpx.MockTransport`, asserting on the request they build and the response they parse —
including the failure paths (500s, rate limits, malformed shapes, Gemini safety blocks,
a TTS sample rate the robot cannot play).

This verifies the **adapter**, not the **API**. See `docs/V1_VALIDATION_REPORT.md` for
what that distinction means for what has actually been proven.

## Manual verification with the simulator

```bash
python -m backend.main                 # terminal 1
python -m robot.simulator --turns 3    # terminal 2
python -m robot.simulator -i           # interactive
python -m robot.simulator --wav utterance.wav --record reply.wav
```

The simulator speaks the identical protocol over a real WebSocket. There is no
simulator-only code path in the backend — the moment a simulator gets its own endpoint,
it starts passing tests the hardware would fail.

## Hardware verification

Software tests cannot cover I2S, acoustics, or wiring. That is what
`firmware/selftest/` is for; the procedure and checklist are in `docs/HARDWARE.md`.

## Writing new tests

- **No network, no keys, no hardware.** Use the fakes.
- **Assert on your code, not the vendor's.** Never assert on what a model says.
- **For roles, assert on the data and on the composed prompt** — not on generated text.
- **Every new failure mode needs a test that ends in LISTENING.** That is the invariant
  the whole reliability story rests on.
