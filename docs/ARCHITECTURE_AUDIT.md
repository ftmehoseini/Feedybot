# Fafobot — Architecture Audit

Date: 2026-08-19
Auditor: principal-engineer review, performed before any production code was written.

---

## 0. Audit finding zero: there was nothing to audit

The task brief described an existing repository containing `backend/`, `firmware/`,
`robot/`, `tests/` and `docs/`, with automated tests already passing. That is **not**
what this repository contained.

Verified state at audit time:

```
$ git log --oneline
fatal: your current branch 'claude/fafobot-v1-platform-jmxoth' does not have any commits yet

$ git ls-remote origin
(no output — remote has zero refs)

$ find . -path ./.git -prune -o -type f -print | wc -l
0
```

The GitHub API confirms the same: `ftmehoseini/Feedybot` has **no branches and no
commits**. There is no backend, no firmware, no test suite, no protocol definition and
no prompt architecture to inspect.

> **ASSUMPTION:** the described repository either lives somewhere else, was never
> pushed, or was lost. Nothing was silently discarded by this work — there was nothing
> to discard. If the described codebase surfaces later, this document becomes the
> criteria to merge it against rather than a description of it.

Consequently this document changes register. Sections 1–3 below (current architecture /
strengths / risks) cannot describe an existing system, so instead they record:

1. the architecture that was **designed and then built** in this change,
2. the engineering risks that design is deliberately structured against — these are the
   same risk categories the brief asked to audit for, evaluated against the new design
   rather than against absent code,
3. the `MUST FIX FOR V1 / SHOULD FIX / LATER VERSION` classification, which here reads
   as "built now / built as a seam / explicitly deferred".

This is an honest substitution and the reader should treat every claim below as a
design commitment, not as a measurement of pre-existing code.

---

## 1. Architecture as built

### 1.1 System split

```
ESP32-S3 (hard real-time)          Backend (expensive, replaceable)
─────────────────────────          ────────────────────────────────
I2S mic capture                    STT provider
VAD + pre-roll buffering           language policy (role-driven)
utterance framing                  Role Engine + layered prompts
PCM upload (binary WS)             LLM provider
PCM playback + amplitude           TTS provider
OLED face rendering                session history (bounded)
touch/button                       emotion selection
local state recovery               latency instrumentation
Wi-Fi/WS reconnection              secrets
```

The dividing line is the one product principle that matters most: **anything with a
deadline measured in milliseconds runs on the MCU; anything that costs money, holds a
secret, or changes per deployment runs on the backend.** Nothing in the firmware knows
what a "role" is; nothing in the backend knows what a GPIO is.

### 1.2 Firmware architecture

Five FreeRTOS tasks, no more (task count is justified in
`docs/ARCHITECTURE.md#firmware-task-model`, with priorities, stacks and core affinity):

| Task | Core | Prio | Owns |
| --- | --- | --- | --- |
| `audio_in` | 1 | 6 | I2S RX, VAD, pre-roll ring, capture→net stream buffer |
| `audio_out` | 1 | 6 | I2S TX, playback ring, amplitude envelope publication |
| `net` | 0 | 5 | Wi-Fi, WebSocket, protocol codec, backoff |
| `face` | 0 | 3 | SSD1306 rendering, animation, local expression expiry |
| `interaction` | 0 | 4 | button/touch debounce → semantic events |

Audio tasks are pinned to core 1 and are the only tasks that touch I2S. The face task is
the lowest priority that does real work, because a dropped frame is invisible and a
dropped audio block is audible.

Cross-task communication is exclusively FreeRTOS primitives (`StreamBuffer` for PCM,
`Queue` for events, one atomic for the playback amplitude). There are no mutexes on the
audio path and no shared mutable globals other than the amplitude atomic.

### 1.3 Backend architecture

```
backend/
  config.py          validated settings, fail-fast at startup
  protocol.py        the single source of truth for wire messages
  emotion.py         SystemState (machine) vs Expression (social) — separate types
  agent_reply.py     typed AgentReply + tolerant emotion-marker parsing
  session.py         RobotSession: per-connection state, bounded history, turn ids
  pipeline.py        one conversational turn: STT → role → LLM → TTS, with timeouts
  communication.py   WebSocket transport; framing, size limits, dispatch
  roles/             schema, loader, prompt_builder
  providers/         stt/ llm/ tts/ — Protocol interfaces + fake + real adapters
  prompts/core_robot.md
```

The pipeline never imports a vendor SDK. It depends on three `Protocol` types and gets
concrete adapters by dependency injection from `providers/registry.py`.

### 1.4 Prompt architecture

Six ordered layers, composed by `roles/prompt_builder.py`:

1. core robot rules (`backend/prompts/core_robot.md`, embodiment + honesty rules)
2. Role Pack (YAML, per deployment)
3. deployment configuration (robot name, locale, venue)
4. session context (turn count, current language)
5. optional user context (not populated in V1 — the seam exists)
6. conversation history (bounded)

Layers 7–9 (retrieved knowledge, tool results, long-term memory) are *named* in the
builder as reserved slots and are **not** implemented.

### 1.5 Protocol

JSON text frames for control, binary frames for PCM. Versioned handshake
(`PROTOCOL_VERSION = 1`). Unknown message types are logged and ignored on both sides.
Full contract in `docs/PROTOCOL.md`.

---

## 2. Strengths to preserve

These are the properties the design is built to keep. Any future change that breaks one
of them should be treated as a regression, not a refactor.

- **The firmware contains zero business logic.** Changing `social_companion` to
  `english_teacher` requires no reflash. This is the whole product thesis.
- **`SystemState` and `Expression` are different Python types.** They cannot be
  accidentally interchanged. The face composes them; the protocol carries them
  separately.
- **Mouth animation is driven by measured PCM amplitude**, not by estimated text
  duration. Text-duration mouths are the single most common tell that a robot is faking
  it.
- **Every temporary expression carries `hold_ms` and expires locally.** A lost packet
  cannot freeze the face.
- **No unbounded buffer anywhere.** Capture, pre-roll, network and playback buffers all
  have compile-time sizes and documented overflow strategies.
- **Providers are injected, never imported by business logic.**
- **Tests run with no hardware, no API keys and no network.**

---

## 3. Risks

Each risk is stated, then the mitigation actually implemented is named. Risks with no
implemented mitigation are marked as such — those are honest gaps, not oversights.

### 3.1 Concurrency

| Risk | Mitigation |
| --- | --- |
| OLED rendering blocking I2S | face task is core 0, prio 3; audio tasks core 1, prio 6; no shared lock between them |
| WebSocket reconnect stalling capture | net task owns the socket; audio_in writes into a StreamBuffer with a 0-tick timeout and drops on full |
| Playback amplitude read torn across tasks | single `std::atomic<uint16_t>`, written by audio_out, read by face |
| Backend: blocking call inside async | every provider adapter is `async`; the one CPU-bound step (PCM/WAV framing) is pure arithmetic on bounded buffers, measured in microseconds |
| Backend: two turns racing on one session | `RobotSession` serialises turns with an `asyncio.Lock` and a monotonic `turn_id`; a late result whose `turn_id` is stale is discarded |

### 3.2 Memory

| Risk | Mitigation |
| --- | --- |
| ESP32 RAM pressure | all audio buffers statically sized; total audio RAM budget documented (~150 KB, see `docs/ARCHITECTURE.md#ram-budget`) — **NEEDS HARDWARE VALIDATION** |
| Unbounded utterance | `AUDIO_MAX_UTTERANCE_MS` hard cap in firmware *and* an independent byte cap in `communication.py`; the device is not trusted |
| Unbounded history | `MAX_HISTORY_TURNS` truncation in `RobotSession`, oldest-first, greeting never counted |
| Unbounded playback queue | fixed ring; overflow drops the newest chunk and logs once per utterance |

### 3.3 Protocol

| Risk | Mitigation |
| --- | --- |
| Version skew | `hello`/`hello_ack` exchange carries `protocol_version`; mismatch is refused with a spoken-safe error state rather than a silent misparse |
| Malformed JSON crashing a side | all decoding is inside try/except; malformed frames increment a counter and are dropped |
| Oversized frame as a DoS | `MAX_TEXT_FRAME_BYTES` and `MAX_BINARY_FRAME_BYTES` enforced before parsing |
| Binary frame arriving outside an utterance | dropped with a counter; does not create a turn |

### 3.4 State machine

| Risk | Mitigation |
| --- | --- |
| Stuck in THINKING forever | every provider call has an explicit `asyncio.timeout`; on expiry the turn fails into a social error reply and the state returns to LISTENING |
| Stuck in SPEAKING forever | `playback_done` has a timeout derived from audio duration + margin; on expiry the backend force-returns to LISTENING |
| Mic hearing the robot's own TTS | firmware gates capture while `SPEAKING` and for `AUDIO_POST_PLAYBACK_GUARD_MS` afterwards (half-duplex, by design in V1) |
| Reconnect resuming a dead turn | on reconnect the backend resets the session to IDLE and cancels any in-flight turn; the robot re-syncs from the `state` message |

### 3.5 Security

| Risk | Mitigation |
| --- | --- |
| API keys on the device | the device never receives a provider key; it holds only Wi-Fi credentials and a backend URL |
| Secrets in the repo | `.env.example` only; `.gitignore` covers `.env`, `secrets.h`, `*.pem` |
| Log leakage | logging helper redacts any value whose key matches a secret pattern; transcripts are logged only when `LOG_TRANSCRIPTS=true`, default false |
| No device auth | **not mitigated in V1.** A `device_id`/`auth_token` field exists in the `hello` frame and is currently accepted without verification. Documented as the first security milestone. |

### 3.6 Audio and timing

| Risk | Mitigation |
| --- | --- |
| Clipped word onsets | 250 ms pre-roll ring, always prepended to the utterance |
| VAD chattering on the threshold | separate start/stop thresholds (hysteresis) plus minimum-speech and trailing-silence timers |
| INMP441 sample format assumptions | 24-bit-in-32-bit MSB-justified, shifted to 16-bit in one documented place — **NEEDS HARDWARE VALIDATION** of gain/headroom on real hardware |
| Playback underrun clicking | ring has low-water mark; playback starts only after the low-water mark is reached, and underrun feeds silence rather than stale samples |

---

## 4. Recommendations, classified

### MUST FIX FOR V1 — implemented in this change

- Role Engine with YAML Role Packs; zero role branching in core code.
- Layered prompt composition with the core robot rules in a Markdown file, not a Python
  string literal.
- Typed `AgentReply` (speech + emotion) with tolerant parsing and neutral fallback;
  emotion markers stripped before TTS.
- `SystemState` vs `Expression` separation end to end, including the wire protocol.
- Provider abstraction for STT/LLM/TTS with fakes and at least one real adapter each.
- Explicit timeouts on every network/AI operation, with a social (not technical) failure
  reply.
- Bounded conversation history, bounded frames, bounded audio buffers.
- Per-connection `RobotSession`; no global conversation state.
- Wi-Fi/WebSocket reconnection with capped exponential backoff and jitter.
- Local expression expiry on the device.
- Amplitude-driven mouth animation.
- Centralised firmware pin configuration verified against strapping/USB/PSRAM
  constraints.
- Self-test firmware for every subsystem plus a combined full-IO test.
- Test suite that needs no hardware, keys or network.
- Simulator speaking the identical protocol.

### SHOULD FIX — seams built, work deferred

- Streaming LLM → sentence segmentation → streaming TTS. The provider interfaces expose
  `generate()` today; a `stream()` method can be added without touching the pipeline's
  callers, and `pipeline.py` already emits audio as chunks rather than one blob.
- Device authentication. The `hello` frame carries the fields; verification is a
  no-op.
- Per-role voice selection in TTS (the Role Pack has a `voice` hint; the OpenAI-compatible
  adapter honours it, the fake ignores it).
- OTA. Not started; the firmware's partition table leaves room for it.
- Metrics export. Latency is measured and logged structurally; nothing scrapes it.

### LATER VERSION — deliberately not designed now

RAG and vector storage; tool calling and structured business data; long-term memory;
teacher reports and student accounts; restaurant/POS integration; camera; locomotion;
wake-word; multi-robot fleet management; custom PCB; battery management; acoustic echo
cancellation and barge-in; pronunciation scoring; cloud dashboard.

The only accommodation made for these is that prompt layers 7–9 are reserved and the
Role Pack has an empty `tools.enabled` list. No empty abstractions were created for
them.

---

## 5. Baseline

There was no pre-existing test suite, so there is no "before" baseline to record. The
"after" measurement is in `docs/V1_VALIDATION_REPORT.md`, which states explicitly which
claims are unit-tested, which are compiled, which are simulated, and which remain
unverified on hardware or against live APIs.
