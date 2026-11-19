#include "board_link.h"

#include <Arduino.h>
#include <esp_now.h>
#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <atomic>
#include <cstring>

namespace boardlink {
namespace {

constexpr unsigned RX_DEPTH = 4;
constexpr uint32_t TX_CALLBACK_WARNING_MS = 500;
constexpr uint32_t RECOVERY_QUIET_MS = 250;
struct IncomingFrame {
  uint16_t length;
  uint8_t channel;
  uint32_t rxAt;
  uint8_t data[FRAME_LIMIT];
};

Protocol protocol;
QueueHandle_t incoming = nullptr;
uint8_t peerMac[6] = {};
bool ready = false;
bool initialized = false;
bool transmitAllowed = true;
Config configuration;
Frame outgoing;
IncomingFrame received;
std::atomic<bool> sending{false};
std::atomic<bool> callbacksActive{false};
std::atomic<uint8_t> callbackEpoch{0};
std::atomic<uint32_t> rxDrops{0}, macAcked{0}, macFailed{0};
uint32_t sendStarted = 0, callbackWarnings = 0;
bool warned = false;
uint8_t peerChannel = 0;
uint32_t retryAt = 0, radioRecoveries = 0, radioFailures = 0;
uint32_t retryDelay = 1000;
uint32_t staleRx = 0;

void onReceive(const esp_now_recv_info_t* info, const uint8_t* data, int length) {
  if (!callbacksActive.load(std::memory_order_acquire) || !incoming || !info || !info->src_addr || !data || length < static_cast<int>(HEADER_SIZE) ||
      length > static_cast<int>(FRAME_LIMIT) || std::memcmp(info->src_addr, peerMac, 6)) return;
  IncomingFrame frame;
  frame.length = static_cast<uint16_t>(length);
  frame.channel = info->rx_ctrl ? info->rx_ctrl->channel : 0;
  frame.rxAt = millis();
  std::memcpy(frame.data, data, static_cast<size_t>(length));
  if (xQueueSend(incoming, &frame, 0) != pdTRUE) rxDrops.fetch_add(1, std::memory_order_relaxed);
}

template <uint8_t Epoch> void onSent(const esp_now_send_info_t* info, esp_now_send_status_t status) {
  (void)info;
  if (!callbacksActive.load(std::memory_order_acquire) || callbackEpoch.load(std::memory_order_relaxed) != Epoch ||
      !sending.load(std::memory_order_acquire)) return;
  if (status == ESP_NOW_SEND_SUCCESS) macAcked.fetch_add(1, std::memory_order_relaxed);
  else macFailed.fetch_add(1, std::memory_order_relaxed);
  sending.store(false, std::memory_order_release);
}

using SendCallback = void (*)(const esp_now_send_info_t*, esp_now_send_status_t);
constexpr SendCallback sendCallbacks[] = {onSent<0>, onSent<1>, onSent<2>, onSent<3>,
                                         onSent<4>, onSent<5>, onSent<6>, onSent<7>};

void stopDriver() {
  callbacksActive.store(false, std::memory_order_release);
  callbackEpoch.store(static_cast<uint8_t>((callbackEpoch.load(std::memory_order_relaxed) + 1) % 8),
                      std::memory_order_relaxed);
  esp_now_unregister_recv_cb(); esp_now_unregister_send_cb(); esp_now_deinit();
  sending.store(false, std::memory_order_release);
  ready = false;
  xQueueReset(incoming);
}

bool startDriver() {
  if (esp_now_init() != ESP_OK) return false;
  esp_now_peer_info_t peer = {};
  std::memcpy(peer.peer_addr, peerMac, 6); std::memcpy(peer.lmk, configuration.lmk, 16);
  peer.channel = 0; peer.ifidx = WIFI_IF_STA; peer.encrypt = true;
  if (esp_now_set_pmk(configuration.pmk) != ESP_OK || esp_now_add_peer(&peer) != ESP_OK ||
      esp_now_register_recv_cb(onReceive) != ESP_OK ||
      esp_now_register_send_cb(sendCallbacks[callbackEpoch.load(std::memory_order_relaxed)]) != ESP_OK) {
    stopDriver(); return false;
  }
  callbacksActive.store(true, std::memory_order_release);
  ready = true;
  return true;
}

}  // namespace

bool begin(const Config& config) {
  if (initialized) return ready;
  bool validMac = false, validPmk = false, validLmk = false;
  for (uint8_t byte : config.peerMac) validMac |= byte != 0;
  for (uint8_t byte : config.pmk) validPmk |= byte != 0;
  for (uint8_t byte : config.lmk) validLmk |= byte != 0;
  if (!validMac || (config.peerMac[0] & 1) || !validPmk || !validLmk ||
      (config.channels != 1 && config.channels != 2) || config.master != (config.channels == 2)) return false;
  incoming = xQueueCreate(RX_DEPTH, sizeof(IncomingFrame));
  if (!incoming) return false;
  std::memcpy(peerMac, config.peerMac, 6);
  configuration = config;
  uint32_t boot = esp_random(); if (!boot) boot = 1;
  initialized = protocol.begin(boot, config.channels, config.master, millis());
  if (!startDriver()) {
    ++radioFailures; retryAt = millis() + retryDelay; retryDelay = 2000;
    return false;
  }
  return true;
}

bool available() { return ready; }

void tick(uint32_t now, bool allowTransmit) {
  if (!initialized) return;
  // The caller's loop timestamp can predate a callback that just queued data.
  now = millis();
  protocol.tick(now);
  if (allowTransmit && !transmitAllowed && sending.load(std::memory_order_acquire)) {
    // A connection scan may delay the callback legitimately. Do not count the
    // radio owner's exclusive interval against the callback watchdog.
    sendStarted = now;
  }
  transmitAllowed = allowTransmit;
  if (!ready) {
    if (!allowTransmit) return;
    if (static_cast<int32_t>(now - retryAt) < 0) return;
    if (startDriver()) { ++radioRecoveries; retryDelay = 1000; }
    else {
      ++radioFailures; retryAt = now + retryDelay;
      retryDelay = retryDelay < 8000 ? retryDelay * 2 : 8000;
    }
    return;
  }
  for (unsigned budget = 0; budget < 2 && xQueueReceive(incoming, &received, 0) == pdTRUE; ++budget) {
    now = millis();
    if (static_cast<uint32_t>(now - received.rxAt) >= ASSEMBLY_TIMEOUT_MS) { ++staleRx; continue; }
    const uint32_t acceptedBefore = protocol.counters().rxFrames;
    protocol.receive(received.data, received.length, now);
    if (protocol.counters().rxFrames != acceptedBefore && received.channel >= 1 && received.channel <= 14)
      peerChannel = received.channel;
  }
  if (!allowTransmit) return;
  now = millis();
  if (sending.load(std::memory_order_acquire)) {
    // No driver queue accumulates behind a missing callback. Retire this driver
    // generation, wait a scheduled quiet interval, then re-create the encrypted
    // peer. Previous-generation callbacks cannot release a newer send.
    if (!warned && static_cast<uint32_t>(now - sendStarted) >= TX_CALLBACK_WARNING_MS) {
      warned = true; ++callbackWarnings;
      stopDriver(); protocol.transportReset(now); retryAt = now + RECOVERY_QUIET_MS;
    }
    return;
  }
  if (!protocol.prepareFrame(now, outgoing)) return;
  sendStarted = now; warned = false;
  sending.store(true, std::memory_order_release);
  const bool queued = esp_now_send(peerMac, outgoing.data, outgoing.length) == ESP_OK;
  if (!queued) sending.store(false, std::memory_order_release);
  protocol.sent(queued);
}

bool publishState(const char* text, size_t length) {
  if (!initialized) return false;
  protocol.tick(millis()); return protocol.publishState(text, length);
}
uint32_t queueCommand(const char* path, const char* form) {
  if (!ready) return 0;
  protocol.tick(millis()); return protocol.queueCommand(path, form);
}
CommandStatus commandStatus(uint32_t id) { return protocol.commandStatus(id); }
bool receiveCommand(ReceivedCommand& command) { return ready && protocol.receiveCommand(command); }
bool replyCommand(uint32_t id, int status, const char* error, uint32_t revision) { return ready && protocol.replyCommand(id, status, error, revision); }
bool receiveState(char* destination, size_t capacity, size_t* length) { return ready && protocol.receiveState(destination, capacity, length); }
bool online(uint32_t now) { return ready && protocol.online(now); }
uint32_t ageMs(uint32_t now) { return ready ? protocol.ageMs(now) : UINT32_MAX; }
uint8_t lastPeerChannel() { return peerChannel; }
uint32_t peerBootId() { return protocol.peerBoot(); }
uint32_t localBootId() { return protocol.localBoot(); }
Counters counters() {
  Counters result = protocol.counters();
  result.rxQueueDrops = rxDrops.load(std::memory_order_relaxed);
  result.txMacAcked = macAcked.load(std::memory_order_relaxed);
  result.txMacFailed = macFailed.load(std::memory_order_relaxed);
  result.txCallbackTimeouts = callbackWarnings;
  result.radioRecoveries = radioRecoveries;
  result.radioFailures = radioFailures;
  result.rxStaleFrames = staleRx;
  return result;
}

}  // namespace boardlink
