"""Desktop robot simulator.

Speaks the real protocol over a real WebSocket to the real backend. There is no
simulator-only code path anywhere in the backend, which is the only way a simulator
stays honest: the moment it gets its own endpoint or its own message shapes, it starts
passing tests the hardware would fail.
"""
