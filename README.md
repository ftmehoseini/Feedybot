# Fafobot

A small desktop social robot you can talk to — and, more importantly, a **reusable
embodied conversational platform**. The same hardware and the same firmware become a
companion, an English tutor, a receptionist or a museum guide by changing one YAML file.

```
  ┌──────────────┐
  │   ( ^   ^ )  │   ESP32-S3 · INMP441 mic · MAX98357A + speaker
  │    ▁▁▁▁▁▁    │   SSD1306 OLED face · touch/button · USB powered
  └──────────────┘   Persian and English · no camera · no wheels
```

**V1 exists to answer one question:** can a person talk to this robot for five to ten
minutes and feel they are interacting with a responsive character, rather than with
ChatGPT inside a plastic box?

---

## Quick start

You do not need hardware, and you do not need an API key. Both are optional.

```bash
git clone <this repo> && cd Feedybot

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env          # defaults use the fake providers — no keys needed

python -m backend.main        # terminal 1
python -m robot.simulator -i  # terminal 2 — press enter to speak a turn
```

You now have a full conversation loop running: VAD-framed audio in, speech recognition,
a role-driven prompt, a model reply, synthesis, and a face reacting to it. With the
`fake` providers everything is local and deterministic, which is exactly what you want
while building.

Point it at real providers when you are ready:

```bash
# .env
LLM_PROVIDER=openai_compatible
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...

STT_PROVIDER=openai_compatible
STT_MODEL=whisper-1
STT_API_KEY=sk-...

TTS_PROVIDER=openai_compatible
TTS_MODEL=tts-1
TTS_API_KEY=sk-...
```

### Change what the robot *is*

```bash
ROLE_ID=english_teacher       # in .env, then restart the backend
```

That is the whole procedure. No firmware change, no code change, no reflash — the robot
even adopts the new role's resting facial expression, because it learns that from the
handshake. See [`docs/ROLE_SYSTEM.md`](docs/ROLE_SYSTEM.md).

---

## Build the robot

Parts, verified pin map, wiring diagrams, power notes and a stage-by-stage assembly
procedure with a self-test at every step: [`docs/HARDWARE.md`](docs/HARDWARE.md).

```bash
cd firmware
cp include/secrets_example.h include/secrets.h
$EDITOR include/secrets.h                    # Wi-Fi + your backend's IP

pio run -e full_io_test -t upload -t monitor # verify the wiring first
pio run -e fafobot -t upload -t monitor      # then the real firmware
```

The robot never receives a provider API key. It holds Wi-Fi credentials and a backend
URL, and nothing else.

---

## Run the tests

```bash
pytest                              # 207 tests: backend, roles, protocol, pipeline
make -C tests/firmware test         # 208 checks: the firmware's pure logic, on the host
```

No hardware, no API keys, no network required by either.

---

## How it fits together

```
mic → VAD → WebSocket → STT → Role Engine → LLM → TTS → WebSocket → speaker
                                                                  ↘ amplitude → mouth
```

The ESP32 owns everything with a millisecond deadline: capture, VAD, playback, the face,
reconnection. The backend owns everything expensive, secret, or deployment-specific:
recognition, the role, the model, synthesis.

Nothing in the firmware knows what a role is. Nothing in the backend knows what a GPIO
is. That separation is the product.

---

## Repository layout

```
backend/          FastAPI backend
  roles/            Role Engine: schema, loader, layered prompt builder
  providers/        STT/LLM/TTS interfaces + fake, OpenAI-compatible, Gemini adapters
  prompts/          core_robot.md — the rules true of every deployment
roles/            Role Packs (YAML). This is where behaviour lives.
firmware/         ESP32-S3 firmware (PlatformIO)
  include/          config.h — every pin and tuning constant, in one file
  src/              five FreeRTOS tasks
  selftest/         standalone per-subsystem tests + a combined full-IO test
robot/            desktop simulator, speaking the identical protocol
tests/            pytest suite + host-compiled C++ tests for firmware logic
docs/             see below
```

## Documentation

| Document | What it answers |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | how it works: tasks, buffers, RAM budget, prompt layers |
| [ARCHITECTURE_AUDIT.md](docs/ARCHITECTURE_AUDIT.md) | the engineering audit, risks, and what was deferred |
| [HARDWARE.md](docs/HARDWARE.md) | BOM, pin map, wiring, power, assembly order |
| [HARDWARE_REFERENCES.md](docs/HARDWARE_REFERENCES.md) | component facts, from manufacturer documentation |
| [PROTOCOL.md](docs/PROTOCOL.md) | the wire contract between robot and backend |
| [ROLE_SYSTEM.md](docs/ROLE_SYSTEM.md) | Role Packs: what belongs in a role, what belongs in core |
| [ENCLOSURE_GUIDE.md](docs/ENCLOSURE_GUIDE.md) | acoustics, mounting, ventilation, strain relief |
| [TESTING.md](docs/TESTING.md) | the three test suites and how to add to them |
| [V1_VALIDATION_REPORT.md](docs/V1_VALIDATION_REPORT.md) | **what has actually been verified, and what has not** |

Read the validation report before trusting anything. It states plainly which claims are
unit-tested, which are only compiled, and which have never touched hardware or a live
API.

---

## What V1 does not do

No RAG, no vector database, no tool calling, no long-term memory, no camera, no
locomotion, no wake word, no fleet management, no OTA, no acoustic echo cancellation or
barge-in, and no pronunciation scoring.

Some of those are deliberate absences rather than missing features. The English teacher
role, for instance, is explicitly forbidden from scoring pronunciation — the model
receives a transcript, not audio, so any such score would be invented. A robot that
fakes a capability is worse than one that admits its limits.

## Principles

1. The physical robot is a reusable platform.
2. Roles are software configuration.
3. Real-time functionality stays on the MCU.
4. Expensive AI functionality stays on the backend.
5. Conversation should feel embodied, not like a smart speaker.
6. Reliability beats features.
7. Do not fake robot capabilities.
8. Business facts come from tools and databases, not from model imagination.
9. RAG, memory and integrations come *after* the conversation works well.
10. Optimise everything for the five-minute conversation test.
