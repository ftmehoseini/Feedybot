"""A Fafobot on your desktop, minus the desk.

Connects to the backend exactly as the firmware does — same handshake, same message
types, same binary PCM frames, same `playback_done` — and prints what the robot's face
would be doing. It lets the whole backend be developed and demonstrated before any
hardware exists.

Usage:

    # send a WAV file as one utterance
    python -m robot.simulator --wav hello.wav

    # send synthetic audio (no file needed, works with the fake providers)
    python -m robot.simulator --say "tell me about yourself"

    # interactive: press enter to send a turn, 'q' to quit
    python -m robot.simulator

    # save what the robot said
    python -m robot.simulator --say "hi" --record reply.wav
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import websockets

from backend.audio import chunk_pcm, pcm_duration_ms, pcm_to_wav, tone, wav_to_pcm
from backend.protocol import AUDIO_SAMPLE_RATE, PROTOCOL_VERSION

#: Matches the firmware's WS_AUDIO_CHUNK_BYTES so the backend sees the same frame
#: cadence it will see from hardware.
CHUNK_BYTES = 2048

#: Text renderings of each face, so a terminal can show what the OLED would show.
_FACES = {
    "neutral": "( o  o )",
    "happy": "( ^  ^ )",
    "curious": "( o  O )?",
    "confused": "( o  - )?",
    "encouraging": "( ^  ^ )!",
    "surprised": "( O  O )!",
    "sleepy": "( -  - )z",
}


class SimulatedRobot:
    """The device half of the protocol, in Python."""

    def __init__(self, url: str, *, device_id: str = "simulator-01", verbose: bool = True) -> None:
        self._url = url
        self._device_id = device_id
        self._verbose = verbose
        self._socket: websockets.WebSocketClientProtocol | None = None
        self.listening = False
        self.state = "offline"
        self.expression = "neutral"
        self.resting_expression = "neutral"
        #: Audio received for the current utterance, so it can be saved or measured.
        self.received_pcm = bytearray()
        self.session_id = ""
        self.role_id = ""

    # -- transport ------------------------------------------------------------------

    async def connect(self) -> None:
        self._socket = await websockets.connect(self._url, max_size=2**20)
        await self._send(
            {
                "type": "hello",
                "protocol_version": PROTOCOL_VERSION,
                "device_id": self._device_id,
                "firmware_version": "simulator",
                "capabilities": {"sample_rate": AUDIO_SAMPLE_RATE},
            }
        )
        message = await self._receive_control(expect="hello_ack")
        if not message.get("accepted"):
            raise RuntimeError(f"backend refused the connection: {message.get('reason')}")
        self.session_id = message.get("session_id", "")
        self.role_id = message.get("role_id", "")
        self.resting_expression = message.get("resting_expression", "neutral")
        self.expression = self.resting_expression
        self._log(f"connected  session={self.session_id}  role={self.role_id}")

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()

    async def _send(self, payload: dict) -> None:
        assert self._socket is not None
        await self._socket.send(json.dumps(payload))

    async def _receive_control(self, *, expect: str | None = None) -> dict:
        """Read frames until a control message arrives, applying it to local state."""
        assert self._socket is not None
        while True:
            frame = await self._socket.recv()
            if isinstance(frame, bytes):
                self.received_pcm.extend(frame)
                continue
            message = json.loads(frame)
            self._apply(message)
            if expect is None or message.get("type") == expect:
                return message

    def _apply(self, message: dict) -> None:
        """Mirror the firmware's own state handling, including the local face rules."""
        kind = message.get("type")
        if kind == "state":
            self.state = message["state"]
            self._log(f"state      {self.state}")
        elif kind == "expression":
            self.expression = message["expression"]
            face = _FACES.get(self.expression, "( ?  ? )")
            self._log(f"face       {face}  {self.expression} for {message.get('hold_ms')}ms")
        elif kind == "listen_control":
            self.listening = message["listening"]
            self._log(f"microphone {'open' if self.listening else 'closed'}")
        elif kind == "speak_start":
            self.received_pcm.clear()
            self._log(f"speaking   turn {message['turn_id']} starting")
        elif kind == "error":
            # Shown here because a developer wants to see it. The real robot never
            # displays this and never speaks it.
            self._log(f"backend error: {message.get('code')} {message.get('message', '')}")

    def _log(self, text: str) -> None:
        if self._verbose:
            print(f"  {text}", flush=True)

    # -- robot behaviour ----------------------------------------------------------------

    async def send_utterance(self, pcm: bytes) -> None:
        """Upload one utterance, chunked exactly as the firmware chunks it."""
        await self._send({"type": "utterance_start"})
        assert self._socket is not None
        for chunk in chunk_pcm(pcm, CHUNK_BYTES):
            await self._socket.send(chunk)
            # Real hardware streams at wall-clock speed. Sleeping keeps the backend's
            # timing realistic without making the simulator unusably slow.
            await asyncio.sleep(0.005)
        await self._send(
            {"type": "utterance_end", "sample_count": len(pcm) // 2}
        )
        self._log(f"sent       {pcm_duration_ms(pcm)}ms of audio")

    async def await_reply(self, timeout_s: float = 60.0) -> bytes:
        """Wait for the robot's spoken reply, then confirm playback like the device does."""
        try:
            async with asyncio.timeout(timeout_s):
                message = await self._receive_control(expect="speak_end")
                turn_id = message.get("turn_id")
                audio = bytes(self.received_pcm)
                self._log(f"received   {pcm_duration_ms(audio)}ms of speech")
                # A real robot plays the audio before confirming. Simulating the delay
                # keeps the backend's playback timeout on a realistic path.
                await asyncio.sleep(min(pcm_duration_ms(audio) / 1000, 2.0))
                await self._send({"type": "playback_done", "turn_id": turn_id})
                await self._receive_control(expect="state")
                return audio
        except asyncio.TimeoutError:
            self._log("timed out waiting for a reply")
            return b""

    async def press(self, *, long: bool = False) -> None:
        """Send an interaction event, as the button would."""
        await self._send(
            {"type": "interaction", "event": "long_touch" if long else "short_touch"}
        )

    async def cancel(self) -> None:
        await self._send({"type": "cancel"})


def _load_audio(args: argparse.Namespace) -> bytes:
    """Produce the PCM for one utterance from whatever the user supplied."""
    if args.wav:
        pcm, rate = wav_to_pcm(Path(args.wav).read_bytes())
        if rate != AUDIO_SAMPLE_RATE:
            raise SystemExit(
                f"{args.wav} is {rate} Hz; the robot speaks {AUDIO_SAMPLE_RATE} Hz. "
                "Resample it first (e.g. ffmpeg -i in.wav -ar 16000 -ac 1 out.wav)."
            )
        return pcm
    # Synthetic speech-shaped audio. The fake STT ignores the content entirely, so this
    # exercises every byte of the path without needing a recording.
    duration_ms = max(600, min(6000, len(args.say) * 60)) if args.say else 1500
    return tone(duration_ms, frequency_hz=180.0, amplitude=0.3)


async def _run(args: argparse.Namespace) -> int:
    robot = SimulatedRobot(args.url, device_id=args.device_id)
    await robot.connect()

    try:
        if args.interactive:
            print("\nPress enter to speak a turn, or type 'q' then enter to quit.\n")
            while True:
                line = await asyncio.to_thread(input, "> ")
                if line.strip().lower() in {"q", "quit", "exit"}:
                    break
                args.say = line.strip() or None
                await robot.send_utterance(_load_audio(args))
                await robot.await_reply()
        else:
            for index in range(args.turns):
                if args.turns > 1:
                    print(f"\n--- turn {index + 1} of {args.turns} ---")
                await robot.send_utterance(_load_audio(args))
                audio = await robot.await_reply()
                if args.record and audio:
                    path = Path(args.record)
                    if args.turns > 1:
                        path = path.with_name(f"{path.stem}_{index + 1}{path.suffix}")
                    path.write_bytes(pcm_to_wav(audio, AUDIO_SAMPLE_RATE))
                    print(f"  saved      {path}")
    finally:
        await robot.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fafobot desktop simulator")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/robot")
    parser.add_argument("--device-id", default="simulator-01")
    parser.add_argument("--wav", help="16 kHz mono WAV file to send as one utterance")
    parser.add_argument("--say", help="text label for a synthetic utterance")
    parser.add_argument("--turns", type=int, default=1, help="how many turns to run")
    parser.add_argument("--record", help="save the robot's reply to this WAV path")
    parser.add_argument(
        "-i", "--interactive", action="store_true", help="prompt for each turn"
    )
    args = parser.parse_args()

    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except (OSError, websockets.WebSocketException) as exc:
        print(f"could not talk to the backend at {args.url}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
