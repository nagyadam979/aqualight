#include "ble_link.h"
#if defined(LED_BLE_ONLY) && LED_BLE_ONLY
#include <Arduino.h>
#include <NimBLEDevice.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <atomic>
#include <cstdio>
#include <new>

namespace blelink {
namespace {
constexpr uint16_t NO_HANDLE = 0xffff;
constexpr uint32_t PAIRING_MS = 45000;
struct Incoming { uint32_t generation, at; uint16_t length; uint8_t data[FRAME_LIMIT]; };
Protocol protocol;
QueueHandle_t queue = nullptr;
NimBLEServer* server = nullptr;
NimBLECharacteristic* tx = nullptr;
bool initialized = false;
uint8_t channels = 0;
char version[25] = {};
std::atomic<uint32_t> generation{0}, drops{0}, connects{0}, disconnects{0};
std::atomic<uint32_t> connectedAt{0};
std::atomic<uint16_t> handle{NO_HANDLE}, frameLimit{20}, rejectHandle{NO_HANDLE};
std::atomic<bool> secured{false}, subscribed{false};
uint32_t stale = 0;
uint16_t ownerHandle = NO_HANDLE;
bool pairingDeadlineResolved = false;
bool pairingDisconnectAttempted = false;
uint32_t lastPairingDisconnect = 0;

bool secure(const NimBLEConnInfo& info) {
  return info.isEncrypted() && info.isAuthenticated() && info.getSecKeySize() == 16;
}
bool current(const NimBLEConnInfo& info) { return info.getConnHandle() == handle.load(std::memory_order_acquire); }
uint16_t limitFor(uint16_t mtu) { return static_cast<uint16_t>(std::min<size_t>(FRAME_LIMIT, mtu >= 23 ? mtu - 3 : 20)); }
// Sync before every owner API, including reply(), because a disconnect can
// happen during application work without an intervening tick.
bool sync() {
  const uint32_t epoch = generation.load(std::memory_order_acquire);
  const uint16_t peer = handle.load(std::memory_order_acquire);
  if (epoch != protocol.generation()) {
    protocol.reset(epoch); ownerHandle = peer; pairingDeadlineResolved = false; pairingDisconnectAttempted = false;
  }
  return peer != NO_HANDLE && generation.load(std::memory_order_acquire) == epoch;
}
class ServerCallbacks final : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer*, NimBLEConnInfo& info) override {
    if (handle.load() != NO_HANDLE) { rejectHandle.store(info.getConnHandle()); return; }
    secured.store(false); subscribed.store(false); frameLimit.store(limitFor(info.getMTU()));
    connectedAt.store(millis(), std::memory_order_release);
    handle.store(info.getConnHandle(), std::memory_order_release);
    generation.fetch_add(1, std::memory_order_acq_rel); connects.fetch_add(1);
  }
  void onDisconnect(NimBLEServer*, NimBLEConnInfo& info, int) override {
    if (!current(info)) return;
    handle.store(NO_HANDLE, std::memory_order_release); secured.store(false); subscribed.store(false);
    generation.fetch_add(1, std::memory_order_acq_rel); disconnects.fetch_add(1);
  }
  void onMTUChange(uint16_t mtu, NimBLEConnInfo& info) override { if (current(info)) frameLimit.store(limitFor(mtu)); }
  void onAuthenticationComplete(NimBLEConnInfo& info) override {
    if (!current(info)) return;
    secured.store(secure(info));
    if (!secure(info)) rejectHandle.store(info.getConnHandle());
  }
} serverCallbacks;

// Override the raw write hook supported by NimBLE 2.5.2. Using getValue() in
// onWrite would allocate another NimBLEAttValue copy in the host callback.
class Receiver final : public NimBLECharacteristic {
 public:
  explicit Receiver(NimBLEService* service) : NimBLECharacteristic(RX_UUID,
      NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_ENC | NIMBLE_PROPERTY::WRITE_AUTHEN, FRAME_LIMIT, service) {}
 protected:
  void writeEvent(const uint8_t* data, uint16_t length, NimBLEConnInfo& info) override {
    if (!queue || !current(info) || !secure(info) || !data || length < HEADER_SIZE || length > FRAME_LIMIT) return;
    Incoming frame{}; frame.generation = generation.load(std::memory_order_acquire); frame.at = millis(); frame.length = length;
    std::memcpy(frame.data, data, length);
    if (xQueueSend(queue, &frame, 0) != pdTRUE) drops.fetch_add(1, std::memory_order_relaxed);
  }
};
class TxCallbacks final : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic*, NimBLEConnInfo& info, uint16_t value) override {
    if (current(info)) { subscribed.store((value & 1) != 0); secured.store(secure(info)); }
  }
} txCallbacks;
class InfoCallbacks final : public NimBLECharacteristicCallbacks {
  void onRead(NimBLECharacteristic* characteristic, NimBLEConnInfo& info) override {
    if (!current(info) || !secure(info)) return;
    secured.store(true);
    char metadata[180];
    const int length = std::snprintf(metadata, sizeof(metadata),
        "{\"protocol\":1,\"maxFrame\":%u,\"requestLimit\":2048,\"responseLimit\":6144,\"channels\":%u,\"version\":\"%s\"}",
        limitFor(info.getMTU()), channels, version);
    if (length > 0 && size_t(length) < sizeof(metadata)) characteristic->setValue(reinterpret_cast<const uint8_t*>(metadata), size_t(length));
  }
} infoCallbacks;
}  // namespace

bool begin(const Config& config) {
  if (initialized) return true;
  if ((config.channels != 1 && config.channels != 2) || config.passkey < 100000 || config.passkey > 999999 ||
      !config.name || !*config.name || std::strlen(config.name) > 24 || !config.version || std::strlen(config.version) > 24) return false;
  for (const char* p = config.version; *p; ++p)
    if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') || (*p >= '0' && *p <= '9') || *p == '.' || *p == '-' || *p == '_')) return false;
  channels = config.channels; std::strcpy(version, config.version);
  queue = xQueueCreate(4, sizeof(Incoming)); if (!queue) return false;
  if (!NimBLEDevice::init(config.name)) { vQueueDelete(queue); queue = nullptr; return false; }
  NimBLEDevice::setSecurityAuth(true, true, true);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_DISPLAY_ONLY);
  NimBLEDevice::setSecurityPasskey(config.passkey);
  ble_hs_cfg.sm_sc_only = 1;  // No legacy-pairing downgrade.
  NimBLEDevice::setMTU(183);
  server = NimBLEDevice::createServer();
  if (server) {
    server->setCallbacks(&serverCallbacks, false); server->advertiseOnDisconnect(true);
    auto* service = server->createService(SERVICE_UUID);
    if (service) {
      auto* rx = new (std::nothrow) Receiver(service);
      if (rx) service->addCharacteristic(rx);
      tx = service->createCharacteristic(TX_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::READ_ENC |
          NIMBLE_PROPERTY::READ_AUTHEN | NIMBLE_PROPERTY::NOTIFY, FRAME_LIMIT);
      auto* info = service->createCharacteristic(INFO_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::READ_ENC |
          NIMBLE_PROPERTY::READ_AUTHEN, 180);
      if (rx && tx && info) {
        tx->setValue(""); tx->setCallbacks(&txCallbacks); info->setCallbacks(&infoCallbacks);
        auto* advertising = NimBLEDevice::getAdvertising();
        if (server->start() && advertising) {
          advertising->enableScanResponse(true);
          if (advertising->addServiceUUID(SERVICE_UUID) && advertising->setName(config.name) && advertising->start()) initialized = true;
        }
      }
    }
  }
  if (!initialized) { NimBLEDevice::deinit(true); server = nullptr; tx = nullptr; vQueueDelete(queue); queue = nullptr; }
  return initialized;
}
bool ready() { return initialized; }
bool connected() { return initialized && handle.load(std::memory_order_acquire) != NO_HANDLE; }
void tick(uint32_t now) {
  if (!initialized) return;
  now = millis(); const bool live = sync(); protocol.tick(now);
  const uint16_t rejected = rejectHandle.exchange(NO_HANDLE);
  if (rejected != NO_HANDLE) server->disconnect(rejected);
  if (live && !pairingDeadlineResolved && uint32_t(now - connectedAt.load(std::memory_order_acquire)) >= PAIRING_MS) {
    const auto peer = server->getPeerInfoByHandle(ownerHandle);
    if (generation.load() == protocol.generation()) {
      if (peer.getConnHandle() == ownerHandle && secure(peer)) pairingDeadlineResolved = true;
      else if (!pairingDisconnectAttempted || uint32_t(now - lastPairingDisconnect) >= 1000) {
        pairingDisconnectAttempted = true; lastPairingDisconnect = now;
        pairingDeadlineResolved = server->disconnect(ownerHandle);
      }
    }
  }
  Incoming frame;
  for (unsigned budget = 0; budget < 2 && xQueueReceive(queue, &frame, 0) == pdTRUE; ++budget) {
    if (!live || frame.generation != protocol.generation() || uint32_t(now - frame.at) >= ASSEMBLY_MS) { ++stale; continue; }
    protocol.receive(frame.data, frame.length, now, frame.at);
  }
  protocol.tick(now);
  if (!live || !secured.load() || !subscribed.load() || generation.load() != protocol.generation()) return;
  // Callback state can become stale after a disconnect or a handle reuse.
  // Query NimBLE's current connection security before emitting any response.
  const auto peer = server->getPeerInfoByHandle(ownerHandle);
  if (peer.getConnHandle() != ownerHandle || !secure(peer) || generation.load() != protocol.generation()) return;
  Frame frameOut;
  if (protocol.prepare(frameOut, frameLimit.load(), now) && generation.load() == protocol.generation())
    protocol.sent(tx->notify(frameOut.data, frameOut.length, ownerHandle), now);
}
bool receiveRequest(char* dst, size_t capacity, size_t* length, uint16_t* id) {
  if (!initialized || !sync()) return false;
  protocol.tick(millis());
  return protocol.receiveRequest(dst, capacity, length, id) && generation.load() == protocol.generation();
}
bool reply(uint16_t id, const char* json, size_t length) {
  if (!initialized || !sync()) return false;
  return protocol.reply(id, json, length, millis()) && generation.load() == protocol.generation();
}
Counters counters() {
  Counters result = protocol.counters(); result.rxQueueDrops = drops.load(); result.rxStale = stale;
  result.connections = connects.load(); result.disconnects = disconnects.load(); return result;
}
}  // namespace blelink
#endif  // LED_BLE_ONLY
