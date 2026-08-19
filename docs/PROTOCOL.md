# Fafobot wire protocol v1

The contract between one robot and the backend. Defined in `backend/protocol.py`
(authoritative) and mirrored in `firmware/include/protocol_constants.h`. A parity test
(`tests/test_protocol_parity.py`) fails the build if the two drift.

## Transport

One WebSocket connection per robot, to `/ws/robot`.

| | |
| --- | --- |
| Control messages | JSON **text** frames |
| Audio | raw **binary** frames |
| Audio format | 16 kHz, 16-bit signed little-endian, mono PCM |
| Max text frame | 8192 bytes |
| Max binary frame | 8192 bytes |
| Max one utterance | 960000 bytes (30 s) |

Audio is never base64 inside JSON. Base64 costs 33% more bytes and forces the MCU to
encode on the audio path; binary frames cost neither.

There is no WAV header on the wire in either direction. The format is fixed and agreed
at handshake, so 44 bytes per utterance of redundant header would be pure overhead on a
device counting kilobytes.

## Two rules that govern every change

1. **Unknown message types must not crash either side.** The backend returns an
   `UnknownMessage` and counts it; the firmware ignores the frame. A robot running newer
   firmware than its backend degrades rather than disconnecting.
2. **Version mismatch is refused loudly.** `hello`/`hello_ack` exchange
   `protocol_version`. A mismatch closes the connection with a reason rather than
   risking a silent misparse, which is the single most expensive class of bug to
   diagnose on a bench.

Bump `PROTOCOL_VERSION` for any change to message shape or ordering. Adding an optional
field is not a break: both sides ignore unknown keys.

---

## Handshake

The robot speaks first. Nothing else may be sent until `hello_ack` arrives.

```json
→ {"type": "hello",
   "protocol_version": 1,
   "device_id": "fafobot-dev-01",
   "firmware_version": "1.0.0",
   "auth_token": "",
   "capabilities": {"sample_rate": 16000, "bits_per_sample": 16, "channels": 1}}
```

```json
← {"type": "hello_ack",
   "protocol_version": 1,
   "accepted": true,
   "session_id": "9676b9db2a61",
   "role_id": "social_companion",
   "resting_expression": "neutral"}
```

`resting_expression` comes from the active Role Pack. The device adopts it as the face
it decays to, which is why switching from `social_companion` to `english_teacher`
changes the robot's resting face with no firmware change.

`auth_token` is accepted **without verification** in V1. The field exists so that adding
device authentication later is not a protocol break. Do not mistake it for security.

On refusal:

```json
← {"type": "hello_ack", "accepted": false,
   "reason": "protocol version mismatch: robot speaks v2, backend speaks v1"}
```

---

## Robot → backend

| Message | When | Fields |
| --- | --- | --- |
| `hello` | first frame | see above |
| `utterance_start` | VAD detected speech onset | — |
| *(binary frames)* | between start and end | raw PCM |
| `utterance_end` | VAD detected end of speech | `sample_count` (optional) |
| `cancel` | abandon the current turn | — |
| `playback_done` | finished playing a reply | `turn_id` |
| `interaction` | button/touch, already classified | `event`: `short_touch` \| `long_touch` |
| `device_status` | periodic telemetry | `free_heap`, `rssi`, `uptime_ms`, `dropped_audio_chunks` |

Binary frames arriving outside an utterance window, or while the microphone gate is
closed, are **dropped and counted**. They never accumulate and never start a turn — this
is both the anti-DoS measure and the mechanism that stops the robot transcribing itself.

## Backend → robot

| Message | Meaning | Fields |
| --- | --- | --- |
| `hello_ack` | handshake result | see above |
| `state` | authoritative system state | `state` |
| `expression` | show a social expression | `expression`, `hold_ms` |
| `listen_control` | open/close the microphone gate | `listening` |
| `speak_start` | binary PCM follows | `turn_id`, `sample_rate`, `bits_per_sample`, `channels` |
| *(binary frames)* | reply audio | raw PCM |
| `speak_end` | all audio for this turn sent | `turn_id` |
| `error` | technical detail for the device log | `code`, `message`, `recoverable` |

`error` is **never spoken and never displayed**. By the time it is sent, the backend has
already arranged for the robot to say something human — see `backend/fallbacks.py`.

### `state` vs `expression`

These are different axes and must not be conflated.

- `state` is where the machine is: `idle`, `listening`, `processing`, `thinking`,
  `speaking`, `error`, `offline`. It drives the mouth and any overlay.
- `expression` is what the character feels: `neutral`, `happy`, `curious`, `confused`,
  `encouraging`, `surprised`, `sleepy`. It drives the eyes and brows.

A robot can be `speaking` while `encouraging`. Collapsing the two into one enum is how a
robot ends up "looking thoughtful" because the network is slow.

### `hold_ms` and local recovery

`hold_ms` is how long the device shows a reactive expression before decaying to
`resting_expression` **on its own clock**. The device does not wait to be told. If the
next packet is lost, the face still recovers. The device clamps `hold_ms` to
`FACE_EXPRESSION_MAX_HOLD_MS` (10 s), so a corrupted frame cannot freeze the face.

---

## A full turn

```
robot                                   backend
  │                                        │
  │──── hello ────────────────────────────►│
  │◄─── hello_ack ─────────────────────────│
  │◄─── state: idle ───────────────────────│
  │◄─── listen_control: true ──────────────│
  │                                        │
  │  (VAD fires)                           │
  │──── utterance_start ──────────────────►│
  │──── PCM, PCM, PCM ... ────────────────►│
  │──── utterance_end ────────────────────►│
  │                                        │
  │◄─── listen_control: false ─────────────│  half duplex: mic closed
  │◄─── state: processing ─────────────────│  STT running
  │◄─── state: thinking ───────────────────│  LLM running
  │◄─── expression: happy, hold 1800 ──────│  face set before audio arrives
  │◄─── state: speaking ───────────────────│
  │◄─── speak_start (turn_id 1) ───────────│
  │◄─── PCM, PCM, PCM ... ─────────────────│
  │◄─── speak_end (turn_id 1) ─────────────│
  │                                        │
  │  (plays audio to completion)           │
  │──── playback_done (turn_id 1) ────────►│
  │◄─── state: listening ──────────────────│
  │◄─── listen_control: true ──────────────│
```

### Timeouts on this sequence

| Wait | Bound | On expiry |
| --- | --- | --- |
| STT | `STT_TIMEOUT_S` (15 s) | spoken apology, back to `listening` |
| LLM | `LLM_TIMEOUT_S` (20 s) | spoken apology, back to `listening` |
| TTS | `TTS_TIMEOUT_S` (20 s) | confused face, `speak_end`, back to `listening` |
| `playback_done` | audio duration + `PLAYBACK_GRACE_S` (10 s) | forced to `listening` |
| handshake | 10 s | connection closed |

There is no path that leaves the robot in `thinking` or `speaking` indefinitely. That is
asserted by `tests/test_pipeline.py::test_every_stage_times_out_rather_than_hanging`.

---

## Cancellation

`cancel`, or `interaction` with `long_touch`, abandons the current turn. The backend
sets the session's cancel flag; the pipeline checks it before each stage and between
every audio chunk, so a long press stops the audio mid-sentence rather than merely
hiding the rest of it.

The firmware also flushes its playback buffer **locally** on a long press, so the sound
stops the instant the person presses rather than after a network round trip.

## Reconnection

On reconnect the backend resets the session: history cleared, turn ids restarted, state
forced to `idle`. The previous turn is not resumed — after a network gap the person has
moved on, and picking up mid-thought is worse than starting clean.

The robot reconnects with capped exponential backoff plus jitter (1 s → 30 s, ±25%).
The cap stops a long outage looking like a dead robot; the jitter stops a roomful of
them stampeding a recovering access point.

## Half duplex

V1 is half duplex. The backend closes the microphone gate before `speak_start` and
reopens it after `playback_done`. The firmware independently keeps the gate closed while
its playback buffer is draining and for `AUDIO_POST_PLAYBACK_GUARD_MS` (250 ms)
afterwards, covering the speaker's decay and the room's reverb tail.

Both gates exist on purpose. The backend's is the policy; the device's is the one that
still works when the network hiccups.

Barge-in and acoustic echo cancellation are **not** implemented. Adding them means
keeping the gate open during playback and subtracting the known output signal from the
input — a change to the firmware's audio path and a new message for "the user
interrupted", but not a change to any message defined here.
