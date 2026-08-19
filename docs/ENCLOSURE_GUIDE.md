# Enclosure guide (prototype)

Not industrial design, and not tooling. This is what a person building a working
prototype needs to know so the enclosure does not undo the electronics.

One number decides most of it: **how loudly the microphone hears the speaker.**
`firmware/selftest/full_io_test` measures it on your build and reports GOOD, ACCEPTABLE
or POOR. Every recommendation below exists to improve that figure.

---

## Why acoustic isolation dominates

V1 is half duplex: the microphone is closed while the robot speaks and for 250 ms after.
That works regardless of isolation — but poor isolation forces a longer guard time, and
guard time is latency the person feels as sluggishness.

It also determines whether the *next* version can do barge-in. Acoustic echo cancellation
subtracts the known output from the input; the more the mic is saturated by the speaker,
the harder that becomes. **Isolation you build in now is the difference between AEC being
tuning work and being a redesign.**

### Layout rules, in priority order

1. **Do not point the microphone at the speaker.** Facing them across a small cavity is
   the worst possible arrangement — direct path plus resonance.
2. **Separate them as far as the case allows.** Sound pressure falls with distance; on a
   desktop robot you have maybe 60–90 mm to work with, and every millimetre helps.
   Aim for **at least 50 mm**, more if the case allows.
3. **Put them on different faces.** Microphone forward and up (toward the person),
   speaker downward or rearward, is the usual answer for a desktop unit.
4. **Break the mechanical path.** Most of what the mic hears in a small plastic box is
   not airborne — it is the case itself vibrating. Foam or rubber under the speaker does
   more than another 10 mm of air gap.
5. **Seal the speaker chamber from the microphone cavity.** A dividing wall with a foam
   gasket turns one resonant box into two quieter ones.

> **NEEDS HARDWARE VALIDATION:** the 50 mm figure is a design starting point from general
> acoustic practice, not a measurement on this hardware. Measure yours with
> `full_io_test` and adjust the geometry until the reported ratio improves.

---

## Microphone

- The INMP441's port is on the **bottom** of the package. The hole must align with the
  port, not merely be nearby.
- **1.5–2 mm** hole, as short a tunnel as the wall allows. A long narrow tunnel is a
  resonator and colours the sound.
- Do not cover it with tape or glue. A single layer of acoustic mesh is acceptable and
  keeps dust out; anything thicker attenuates.
- Mount the module on a compliant pad, not rigidly to the shell — a rigid mount couples
  case vibration straight into the mic.
- Keep it away from the amplifier and from the speaker leads: class-D switching noise
  raises the electrical noise floor, and it looks identical to a microphone fault.

## Speaker

- A speaker with no enclosed volume behind it has almost no bass and sounds thin and
  papery. Give it a **sealed chamber** — even 20–30 cm³ makes speech noticeably fuller.
- Seal the driver's rim to the baffle. A leak around the edge produces a chuffing noise
  on plosives.
- The grille should be open area, not a few small holes. Aim for at least 30 % open
  across the driver's face.
- Mount it on a foam gasket, and do not screw it hard against a thin flat panel — that
  turns the panel into a second, badly-behaved driver.

## OLED

- Recess it slightly behind the front face, with a bezel that hides the glass edge and
  the ribbon. A raw module bonded to a hole reads as unfinished, and this is the part of
  the robot people look at.
- **Tilt the front face back 10–15°.** The robot sits on a desk and the person looks
  down at it; a vertical face is looking at their chest.
- Keep the ribbon cable's bend radius generous — repeated flexing on assembly is a
  common way to lose a display.
- The panel is fragile. A clear window in front of it is worth the small loss in
  contrast.

## Touch electrode

- If using capacitive touch: a copper-tape or foil pad on the **inside** of the shell,
  20–30 mm across, with a short lead to GPIO10.
- Wall thickness matters. Under 2 mm of plastic works well; thicker walls need a bigger
  electrode.
- Keep the lead short and away from the I2S lines — a long electrode lead is an antenna,
  and it picks up the audio clocks as false touches.
- Recalibrate `TOUCH_THRESHOLD` with `selftest/touch_test` **after** mounting. The value
  from a bare bench electrode will not be the value in the case.
- The head or the top surface is the natural place to touch a small robot. Put it where a
  hand lands.

## Ventilation

The ESP32-S3 during sustained Wi-Fi and the class-D amplifier at volume both produce
heat. Neither is dramatic, but a fully sealed small box will warm up.

- Vents low on one side and high on the opposite side give convection a path.
- Do not vent through the speaker chamber — that defeats the sealed volume.
- Do not vent next to the microphone port; airflow across it is audible as noise.

## Cables and strain relief

- The USB connector must be **anchored to the shell**, not left hanging off the devkit.
  Someone will pull the cable, and a devkit's USB connector is a surface-mount part.
- Provide a strain-relief channel or a clamp inside, a few centimetres from the port.
- Route the speaker leads away from the microphone lines and the I2S bus. If they must
  cross, cross at right angles.
- Leave enough slack to open the case for service without unplugging everything.

## Access

Do not seal these away:

| Needs access | Why |
| --- | --- |
| USB port | flashing, serial monitoring, power |
| BOOT button (GPIO0) | manual download mode when auto-reset fails |
| RESET / EN button | recovery |
| The interaction button | it is the user interface |

A small opening at the back for BOOT and RESET is enough. You will need them more often
than you expect during bring-up.

---

## Assembly and verification

1. Build the electronics on the bench and pass every self-test (`docs/HARDWARE.md`).
2. Mount into the enclosure.
3. **Re-run `full_io_test` inside the finished case.** Mounting changes the acoustics
   completely — the isolation figure from the bench is not the one that matters.
4. Re-check the noise floor. A large rise after mounting means either the amplifier is
   coupling in electrically, or the mic is now mechanically bonded to the shell.
5. Re-tune `VAD_START_RMS` and `VAD_STOP_RMS` for the assembled unit.
6. Re-calibrate `TOUCH_THRESHOLD` if using touch.

## If isolation comes back POOR

In the order worth trying:

1. Add a foam gasket under the speaker. Cheapest fix, usually the biggest improvement.
2. Add a divider between the speaker chamber and the microphone cavity.
3. Move the microphone further from the speaker, or to a different face.
4. Decouple the microphone module from the shell with a compliant pad.
5. Reduce playback volume. It is a real fix and costs nothing, but it makes the robot
   quieter — treat it as the last resort, not the first.
6. Lengthen `AUDIO_POST_PLAYBACK_GUARD_MS`. This *works around* the problem rather than
   fixing it, and it makes the robot feel slower. Only if the enclosure cannot be
   changed.
