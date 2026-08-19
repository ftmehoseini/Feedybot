#pragma once
/**
 * Wire protocol constants and message type strings.
 *
 * This file mirrors backend/protocol.py across a language boundary. There is no way to
 * share the definition, so instead tests/test_protocol_parity.py reads both files and
 * fails if they drift. If you change one, change the other.
 */

#define FAFOBOT_PROTOCOL_VERSION 1

// ---- robot -> backend ----
#define MSG_HELLO           "hello"
#define MSG_UTTERANCE_START "utterance_start"
#define MSG_UTTERANCE_END   "utterance_end"
#define MSG_CANCEL          "cancel"
#define MSG_PLAYBACK_DONE   "playback_done"
#define MSG_INTERACTION     "interaction"
#define MSG_DEVICE_STATUS   "device_status"

// ---- backend -> robot ----
#define MSG_HELLO_ACK       "hello_ack"
#define MSG_STATE           "state"
#define MSG_EXPRESSION      "expression"
#define MSG_LISTEN_CONTROL  "listen_control"
#define MSG_SPEAK_START     "speak_start"
#define MSG_SPEAK_END       "speak_end"
#define MSG_ERROR           "error"

// ---- interaction events ----
#define EVENT_SHORT_TOUCH   "short_touch"
#define EVENT_LONG_TOUCH    "long_touch"

// ---- frame limits (must match the backend, which enforces them) ----
#define MAX_TEXT_FRAME_BYTES   8192
#define MAX_BINARY_FRAME_BYTES 8192

// JSON document capacity for one control frame. Control messages are small; this is
// sized with headroom rather than tuned, because a too-small document silently
// truncates and that failure is miserable to diagnose on hardware.
#define JSON_DOCUMENT_BYTES 512
