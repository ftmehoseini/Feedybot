# Fafobot architecture

## The one-line version

Anything with a millisecond deadline runs on the MCU. Anything that costs money, holds a
secret, or changes per deployment runs on the backend. Nothing in the firmware knows what
a role is; nothing in the backend knows what a GPIO is.

```
        ┌──────────────── ESP32-S3 ────────────────┐
        │                                          │
  mic ──┤  I2S RX → VAD → pre-roll → capture buf   │
        │                      │                   │
        │                      ▼                   │
        │              WebSocket (binary PCM)      │
        └──────────────────────┬───────────────────┘
                               │
        ┌──────────────────────▼───────────────────┐
        │                 Backend                  │
        │  STT → Role Engine → LLM → TTS           │
        │  (each stage timed, bounded, replaceable)│
        └──────────────────────┬───────────────────┘
                               │
        ┌──────────────────────▼───────────────────┐
        │  WebSocket (binary PCM + JSON control)   │
        │              │              │            │
        │              ▼              ▼            │
        │      playback buffer     face state      │
        │              │              │            │
   spk ─┤          I2S TX ────► amplitude          │
        │                             │            │
  OLED ─┤◄──────────── face task ◄────┘            │
        └──────────────────────────────────────────┘
```

---

## Firmware task model

Five tasks. Not four, not eight — the minimum that gives clean real-time behaviour.

| Task | Core | Prio | Stack | Owns | Talks to |
| --- | --- | --- | --- | --- | --- |
| `audio_in` | 1 | 6 | 4096 | I2S_NUM_0, pre-roll ring, capture StreamBuffer | capture events queue |
| `audio_out` | 1 | 6 | 4096 | I2S_NUM_1, playback StreamBuffer, mouth envelope | one atomic (mouth px) |
| `net` | 0 | 5 | 8192 | Wi-Fi, WebSocket, protocol codec | handler callbacks |
| `interaction` | 0 | 4 | 2560 | button/touch GPIO | interaction events queue |
| `face` | 0 | 3 | 4096 | I2C, SSD1306 | reads mouth atomic |
| `loopTask` | 0 | 1 | (Arduino) | event routing, audio upload | everything, briefly |

### Why this split

**Audio owns core 1 alone.** Wi-Fi's driver and the OLED's I2C transactions both take
unpredictable time. On core 0 they cannot preempt an I2S read, because they are not on
its core. This is the whole real-time strategy, and it is why there is no lock on the
audio path.

**Priorities encode audibility.** A dropped OLED frame is invisible; a dropped audio
block is a click. So the face is the lowest-priority task that does real work.

**Two I2S controllers, never shared.** Input uses `I2S_NUM_0`, output uses `I2S_NUM_1`.
The ESP32-S3 has two, so there is no reason to multiplex one and every reason not to.

### Buffer ownership

Each buffer has exactly one writer and one reader. No buffer is touched by three tasks.

| Buffer | Size | Writer | Reader | On overflow |
| --- | --- | --- | --- | --- |
| pre-roll ring | 8000 B (250 ms) | `audio_in` | `audio_in` | overwrites oldest (that is the point) |
| capture stream | 16384 B (~512 ms) | `audio_in` | `loopTask` | drop newest, count it |
| playback stream | 49152 B (~1.5 s) | `net` | `audio_out` | drop newest above high water, count it |
| I2S DMA (each) | 4 × 320 samples | driver | driver | driver |

**Nothing blocks.** `audio_in` writes to the capture stream with a zero tick timeout: if
the network has stalled, audio is dropped and counted rather than backing up into the
DMA and corrupting what is still being recorded. Dropping is the correct failure here.

### Playback water marks

Playback does not start when the first packet arrives — it waits for
`AUDIO_PLAYBACK_LOW_WATER` (8192 B, ~256 ms). Starting immediately guarantees an
underrun milliseconds later, and that stutter at the start of every reply is what makes
cheap robots sound cheap.

Above `AUDIO_PLAYBACK_HIGH_WATER` (40960 B) incoming chunks are dropped. Underruns feed
silence, never stale samples: a gap is far less noticeable than a stutter.

### RAM budget

| Item | Bytes |
| --- | --- |
| pre-roll ring | 8000 |
| capture stream | 16384 |
| playback stream | 49152 |
| I2S DMA in (32-bit slots) | 5120 |
| I2S DMA out (16-bit) | 2560 |
| frame scratch (raw + pcm + playback) | 3200 |
| SSD1306 framebuffer | 1024 |
| upload scratch | 2048 |
| task stacks | ~23000 |
| **total** | **~110 KB** |

The ESP32-S3 has 512 KB of internal SRAM. Wi-Fi and the TCP stack typically take
40–60 KB more. That leaves comfortable headroom, and none of the above is heap-allocated
after boot.

> **NEEDS HARDWARE VALIDATION.** These are computed from the constants in `config.h`,
> not measured on a device. `firmware/src/main.cpp` prints free heap at boot and the
> `device_status` message reports it periodically — check both against this table on
> first bring-up.

---

## Local face recovery

The backend sends `expression: happy, hold_ms: 1800`. The device shows it and, **1800 ms
later, decays to the resting face on its own clock** — `RobotFaceState::tick()`, called
every frame by the face task.

This is not an optimisation. If the follow-up packet is lost, a device that waited to be
told would sit there grinning permanently. The rollover-safe comparison in `tick()`
means it also recovers correctly across the 49-day `millis()` wrap.

Tested in `tests/firmware/test_firmware_logic.cpp` (expiry, hold clamping, role-specific
resting face, rollover).

---

## Mouth animation

```
PCM handed to I2S → frame RMS → asymmetric envelope → pixels
```

Attack is fast (0.55), release is slow (0.18): the mouth opens on the consonant and
closes smoothly rather than strobing between syllables.

The mouth is **never** driven by estimated text duration. A text-timed mouth drifts out
of sync within a sentence and is the clearest tell that a robot's face is decoration.
The backend deliberately does not tell the face how wide to open — only the device knows
what actually reached the speaker.

---

## Backend

```
backend/
  config.py          validated settings; bad config fails at startup, named
  protocol.py        the wire contract, versioned, tolerant of unknown messages
  emotion.py         SystemState (machine) vs Expression (social) — separate types
  agent_reply.py     typed AgentReply; the boundary where raw model text stops
  fallbacks.py       what the robot says when something breaks
  errors.py          typed error categories
  audio.py           PCM utilities (stdlib only — 16-bit mono needs no DSP library)
  session.py         RobotSession: state machine, bounded history, turn ids, metrics
  pipeline.py        one turn: STT → role → LLM → TTS, each stage timed and cancellable
  communication.py   WebSocket transport, framing, limits, half-duplex gate
  logging_setup.py   structured JSON logs, secret redaction, transcripts off by default
  main.py            FastAPI app; startup validation order
  roles/             schema, loader, prompt_builder
  providers/         base.py + stt/ llm/ tts/ + registry.py
  prompts/core_robot.md
```

### The dependency rule

`pipeline.py` imports three `Protocol` types and no vendor SDK. `registry.py` is the
only module allowed to import an adapter. Enforced by review and by
`tests/test_roles.py::test_core_code_contains_no_role_branching`, which fails if any
core module so much as mentions a role id.

### Turn lifecycle

```
utterance_end
    │
    ├─ begin_turn()          new turn_id, fresh cancel event, metrics start
    ├─ PROCESSING → STT      timeout: STT_TIMEOUT_S
    ├─ THINKING   → LLM      timeout: LLM_TIMEOUT_S, prompt composed from role
    ├─ parse into AgentReply markers stripped, emotion clamped by the role
    ├─ expression sent       before audio, so the face is right when the first word lands
    ├─ SPEAKING   → TTS      timeout: TTS_TIMEOUT_S, streamed as chunks
    └─ playback_done         connection layer → LISTENING (or forced, on timeout)
```

Every stage checks the cancel flag first, and the TTS loop checks between every chunk.

### Session model

One `RobotSession` per connection. No module-level conversation state anywhere — V1
ships one robot, but a global history would make the second one a rewrite.

The session carries an `asyncio.Lock` (turns never interleave) and a monotonic
`turn_id`. A result tagged with a stale turn id is discarded, which is how a cancelled
turn's late audio cannot talk over the next one.

History is bounded twice: by turn count (`MAX_HISTORY_TURNS`) and by character budget
(`MAX_HISTORY_CHARS`). Truncation, oldest pair first — not summarisation. A summariser
would be an extra LLM call per turn with its own timeout, failure mode and cost, and V1
does not need it.

### Prompt layers

| # | Layer | Source | V1 |
| --- | --- | --- | --- |
| 1 | Core robot rules | `backend/prompts/core_robot.md` | ✅ |
| 2 | Role Pack | `roles/*.yaml` | ✅ |
| 3 | Deployment config | environment | ✅ |
| 4 | Session context | live session | ✅ |
| 5 | User context | — | reserved, empty |
| 6 | Conversation history | session (as chat messages) | ✅ |
| 7 | Retrieved knowledge | — | **not implemented** |
| 8 | Tool results | — | **not implemented** |
| 9 | Long-term memory | — | **not implemented** |

Layers 7–9 are named in `PromptLayer` so the ordering question is settled before they
arrive. Nothing emits them, and no empty abstraction was built for them.

History is passed as chat messages, not flattened into the system prompt: every provider
handles role-tagged turns better than a transcript embedded in a wall of instructions.

---

## Failure philosophy

Failures are social events. The human hears *"Hmm, I had trouble thinking about that.
Could you try again?"*; the log gets the status code. Fallback strings live in
`backend/fallbacks.py`, per category and per language, and are deliberately **not**
generated by the LLM — the LLM is frequently the thing that just failed.

One special case: when TTS is what broke, the robot shows a confused face and stays
quiet rather than recursing into the broken component to apologise.

---

## What is deliberately not here

RAG, vector storage, tool calling, long-term memory, teacher reports, restaurant
integration, camera, locomotion, wake-word, fleet management, OTA, battery management,
acoustic echo cancellation, barge-in, pronunciation scoring.

Accommodations made for them: prompt layers 7–9 are reserved, `tools.enabled` exists in
the role schema (and is rejected if non-empty), the TTS interface returns chunks so
streaming can be dropped in, and `hello` carries an `auth_token` field. Nothing else.

Good architecture keeps doors open. Bad architecture adds an empty abstraction for every
future idea.
