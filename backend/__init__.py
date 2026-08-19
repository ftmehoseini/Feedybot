"""Fafobot backend: an embodied conversational AI platform.

The backend owns everything expensive, secret, or deployment-specific. The robot owns
everything with a millisecond deadline. Nothing in this package knows about GPIO pins,
and nothing in the firmware knows about roles or prompts.
"""

__version__ = "1.0.0"
