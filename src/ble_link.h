#pragma once
#include "ble_protocol.h"

namespace blelink {
constexpr const char* SERVICE_UUID = "a9f00001-7e2a-4d76-9c43-819d9b3b6a10";
constexpr const char* RX_UUID = "a9f00002-7e2a-4d76-9c43-819d9b3b6a10";
constexpr const char* TX_UUID = "a9f00003-7e2a-4d76-9c43-819d9b3b6a10";
constexpr const char* INFO_UUID = "a9f00004-7e2a-4d76-9c43-819d9b3b6a10";
struct Config {
  uint8_t channels = 0;
  uint32_t passkey = 0;  // Private six-digit PIN, never logged by this module.
  const char* name = nullptr;
  const char* version = "1";
};
// NimBLE-Arduino 2.5.2, one BLE client, one application owner task. begin()
// starts BLE only; caller owns disabling Wi-Fi/ESP-NOW. Bond keys are managed
// by NimBLE. Runtime callbacks perform no application or filesystem operations.
bool begin(const Config& config);
void tick(uint32_t now);  // At most two RX frames and one notification attempt.
bool receiveRequest(char* destination, size_t capacity, size_t* length, uint16_t* id);
// Only after receiveRequest, on the same connection, before its 10 s deadline.
// Copies JSON immediately; accepted notification is not application delivery.
bool reply(uint16_t id, const char* json, size_t length);
bool connected();  // Physical connection; encrypted/authenticated required for I/O.
bool ready();
Counters counters();
}  // namespace blelink
