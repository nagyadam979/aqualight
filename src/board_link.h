#pragma once

#include "mesh_protocol.h"

namespace boardlink {

struct Config {
  bool master = false;
  uint8_t channels = 0;
  uint8_t peerMac[6] = {};
  uint8_t pmk[16] = {};
  uint8_t lmk[16] = {};
};

// Root initializes the Wi-Fi radio in STA or AP+STA mode before begin().
// All public calls run on one owner task. Call tick() even if initial radio
// initialization failed: transient driver errors use scheduled retry/backoff.
// This module never associates, scans, switches channel, waits for its peer,
// touches LED/I2C state or performs flash/filesystem I/O.
bool begin(const Config& config);
bool available();
// During a bounded STA connection attempt the radio owner passes false: RX
// and protocol deadlines continue, but no TX or driver lifecycle calls run.
// Resuming grants an outstanding send a fresh callback-watchdog interval.
void tick(uint32_t now, bool allowTransmit = true);
bool publishState(const char* text, size_t length);
uint32_t queueCommand(const char* path, const char* form);
CommandStatus commandStatus(uint32_t id);
bool receiveCommand(ReceivedCommand& command);
bool replyCommand(uint32_t id, int status, const char* error = "", uint32_t revision = 0);
bool receiveState(char* destination, size_t capacity, size_t* length);
bool online(uint32_t now);
uint32_t ageMs(uint32_t now);
uint8_t lastPeerChannel();
uint32_t peerBootId();
uint32_t localBootId();
Counters counters();

}  // namespace boardlink
