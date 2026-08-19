#pragma once
/**
 * Copy this file to firmware/include/secrets.h and fill it in.
 *
 * secrets.h is gitignored. It must never contain a provider API key: the robot talks
 * only to your backend, and the backend holds every credential. If you find yourself
 * wanting to put an OpenAI key here, the architecture has gone wrong.
 */

#define WIFI_SSID     "your-network"
#define WIFI_PASSWORD "your-password"

// Your backend's WebSocket endpoint.
//   ws://  plain, fine on a trusted LAN for V1
//   wss:// TLS; requires a CA bundle in the firmware, not configured in V1
#define BACKEND_HOST "192.168.1.10"
#define BACKEND_PORT 8000
#define BACKEND_PATH "/ws/robot"

// Per-unit identity. Any string; it appears in the backend's logs.
#define FAFOBOT_DEVICE_ID "fafobot-dev-01"

// Reserved for device authentication. V1's backend accepts any value without checking
// it -- the field exists so that adding verification later is not a protocol break.
#define FAFOBOT_AUTH_TOKEN ""
