#!/usr/bin/env python3
"""Run production mutation admission/persistence and board-role policy checks.

Compiles both channel/role variants with real HTTP, scheduling and policy helpers;
NVS, clock, output notifications and Wi-Fi are controlled doubles.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
source = (ROOT / "src/main.cpp").read_text()
role_start = source.index("#ifndef LED_MASTER")
role_definition = source[role_start:source.index("\n#if LED_CHANNELS", role_start)]
settings_helpers = source[source.index("void appendCurve("):source.index("\nvoid loadSettings(")]
mutations = source[source.index("struct MutationResult"):source.index("\nString encodeFormValue(")]

# These integration invariants complement the executable helper checks: the
# embedded scheduler is not modeled by the host doubles. In particular a slow
# network request must never execute the sensor or lighting task synchronously.
control_worker = source[source.index("void controlWorker("):source.index("\nString meshJson(")]
sensor_worker = source[source.index("void sensorWorker("):source.index("\nvoid controlWorker(")]
setup = source[source.index("void setup()"):source.index("\nvoid loop()")]
main_loop = source[source.index("void loop()") :]
network = source[source.index("void updateNetwork("):source.index("\nvoid initializeClock(")]
network_start = source[source.index("void startNetwork()"):source.index("\nvoid updateNetwork(")]
ap_start = source[source.index("bool setAccessPoint(bool enabled) {"):source.index("\nvoid updateRadioPolicy(")]
diagnostics = source[source.index("String diagnosticsJson()"):source.index("\nvoid publishSensor(")]
assert "applyOutput();" in control_worker and "ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100))" in control_worker
for forbidden in ("server.", "WiFi.", "Wire.", "aht20.", "updateSensor(", "startSensor(", "ledota::", "boardlink::"):
    assert forbidden not in control_worker, f"Control worker must not run {forbidden}"
assert "startSensor();" in sensor_worker and "updateSensor(millis());" in sensor_worker and "publishSensor();" in sensor_worker
for forbidden in ("updateSensor(", "startSensor(", "applyOutput(", "pollWifiRecovery("):
    assert forbidden not in main_loop, f"Main network loop must not run {forbidden}"
for forbidden in ("Wire.", "aht20.", "gpio_get_level("):
    assert forbidden not in diagnostics, "HTTP diagnostics must read the published sensor snapshot"
assert setup.index("xTaskCreate(controlWorker") < setup.index("startNetwork();")
assert setup.index("xTaskCreate(sensorWorker") < setup.index("startNetwork();")
assert re.search(r"xTaskCreate\(controlWorker,[^\n]*,\s*2,\s*&controlTaskHandle", setup)
assert re.search(r"xTaskCreate\(sensorWorker,[^\n]*,\s*1,\s*&sensorTaskHandle", setup)
assert re.search(r"void updateNetwork\([^)]*\)\s*\{\s*if\s*\(!MASTER\)\s*return;", network)
assert re.search(r"if\s*\(MASTER\s*&&\s*wifi\.ssid\[0\]\)\s*startStaAttempt", network_start)
assert "WiFi.mode(WIFI_STA)" in network_start and "WiFi.setSleep(false)" in network_start
assert "WiFi.softAP(" not in network_start and "if (MASTER) return !enabled;" in ap_start
assert "ledota::begin(MASTER)" in setup
master_block_start = setup.index("{", re.search(r"if\s*\(MASTER\)", setup).start())
depth = 0
for end in range(master_block_start, len(setup)):
    if setup[end] == "{":
        depth += 1
    elif setup[end] == "}":
        depth -= 1
        if not depth:
            break
assert "configTzTime" in setup[master_block_start:end] and source.count("configTzTime(") == 1

STUBS = r'''
#include <cassert>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <string>
#include <type_traits>
#include <sys/time.h>
#include "schedule.h"
#include "http_request.h"
#include "board_policy.h"
#include "mesh_protocol.h"

bool stringReserveOkay = true;
class String {
 public:
  String() = default;
  String(const char *text) : text_(text ? text : "") {}
  size_t length() const { return text_.size(); }
  const char *c_str() const { return text_.c_str(); }
  bool reserve(size_t capacity) { if (!stringReserveOkay) return false; text_.reserve(capacity); return true; }
  bool startsWith(const char *prefix) const { return text_.rfind(prefix, 0) == 0; }
  bool operator==(const char *text) const { return text_ == text; }
  bool operator!=(const char *text) const { return text_ != text; }
  String &operator+=(const char *text) { text_ += text; return *this; }
  String &operator+=(char value) { text_ += value; return *this; }
  template<typename T, typename = typename std::enable_if<std::is_integral<T>::value>::type>
  String &operator+=(T value) { text_ += std::to_string(value); return *this; }
  void toCharArray(char *destination, size_t capacity) const {
    assert(capacity); std::strncpy(destination, text_.c_str(), capacity - 1); destination[capacity - 1] = 0;
  }
 private:
  std::string text_;
};

uint32_t fakeNow = 0;
uint32_t millis() { return fakeNow; }
constexpr uint64_t MIN_EPOCH = 1704067200ULL, MAX_EPOCH = 4102444799ULL;
aqua::Settings settings = aqua::defaults();
bool pwmOk = true, preferencesOpen = true, storageOk = true, settingsDirty = false, lastSaveFailed = false;
uint32_t lastManualChange = 0, lastSaveAttempt = 0, configRevision = 0;
int controllerMux = 0, criticalDepth = 0;
#define portENTER_CRITICAL(mutex) do { assert((mutex) == &controllerMux && criticalDepth == 0); ++criticalDepth; } while (0)
#define portEXIT_CRITICAL(mutex) do { assert((mutex) == &controllerMux && criticalDepth == 1); --criticalDepth; } while (0)
unsigned outputRequests = 0;
void requestOutput() { assert(criticalDepth == 0); ++outputRequests; }
struct Output { void println(const char *) {} } Serial;
struct WifiCredentials { uint8_t version = 1; char ssid[33] = {}; char password[64] = {}; } wifi;
bool wifiPending = false;
uint32_t wifiRequested = 0;
struct Storage {
  bool okay = true;
  unsigned settingsWrites = 0, wifiWrites = 0;
  std::string persisted;
  WifiCredentials persistedWifi;
  size_t putString(const char *key, const String &encoded) {
    assert(criticalDepth == 0 && std::strcmp(key, "settings") == 0); ++settingsWrites;
    if (!okay) return 0;
    persisted = encoded.c_str(); return encoded.length();
  }
  size_t putBytes(const char *key, const void *data, size_t length) {
    assert(criticalDepth == 0 && std::strcmp(key, "wifi") == 0 && length == sizeof(WifiCredentials)); ++wifiWrites;
    if (!okay) return 0;
    std::memcpy(&persistedWifi, data, length); return length;
  }
} preferences;
enum class TimeSource { None, Browser };
std::atomic<TimeSource> timeSource{TimeSource::None};
unsigned timeWrites = 0;
int clockResult = 0;
time_t assignedEpoch = 0;
int fakeSetTime(const timeval *value, void *) {
  ++timeWrites;
  if (!clockResult) assignedEpoch = value->tv_sec;
  return clockResult;
}
#define settimeofday fakeSetTime
constexpr int WL_CONNECTED = 3;
struct Network { int state = WL_CONNECTED; int status() { return state; } } WiFi;
bool clockValid = true;
bool readClock(time_t &epoch, tm &) { epoch = MIN_EPOCH; return clockValid; }
namespace ledota {
bool occupied = false, accepted = true; unsigned checks = 0;
bool busy() { return occupied; }
bool requestCheck() { ++checks; return accepted; }
}
namespace maintenance { bool pending = false; bool rebootRequested() { return pending; } }
aqua::SlaveAccessPointPolicy accessPointPolicy;
bool apStartOkay = true, apActive = false;
unsigned apStarts = 0;
bool setAccessPoint(bool enabled) { assert(enabled); ++apStarts; if (apStartOkay) apActive = true; return apStartOkay; }
'''

CHECKS = r'''
bool same(const aqua::Settings &a, const aqua::Settings &b) {
  if (a.mode != b.mode || a.manualWarm != b.manualWarm || a.manualCool != b.manualCool || a.warm.count != b.warm.count || a.cool.count != b.cool.count) return false;
  for (size_t i = 0; i < aqua::MAX_POINTS; ++i)
    if (a.warm.points[i].minute != b.warm.points[i].minute || a.warm.points[i].level != b.warm.points[i].level ||
        a.cool.points[i].minute != b.cool.points[i].minute || a.cool.points[i].level != b.cool.points[i].level) return false;
  return true;
}
void reset() {
  settings = aqua::defaults(); preferences = {}; pwmOk = preferencesOpen = storageOk = clockValid = true;
  settingsDirty = lastSaveFailed = wifiPending = false;
  lastManualChange = lastSaveAttempt = configRevision = outputRequests = timeWrites = 0;
  fakeNow = 12345; criticalDepth = 0; clockResult = 0; assignedEpoch = 0;
  stringReserveOkay = true;
  wifi = {}; WiFi = {}; timeSource.store(TimeSource::None);
  ledota::occupied = false; ledota::accepted = true; ledota::checks = 0; maintenance::pending = false;
  accessPointPolicy = {};
  apStartOkay = true; apActive = false; apStarts = 0;
}
std::string wire(const char *path, const std::string &form, const char *marker = "1", const char *type = "application/x-www-form-urlencoded") {
  return std::string("POST ") + path + " HTTP/1.1\r\nHost: local\r\nX-Aqua-Request: " + marker +
    "\r\nContent-Type: " + type + "\r\nContent-Length: " + std::to_string(form.size()) + "\r\n\r\n" + form;
}
MutationResult command(const char *path, const std::string &form, const char *marker = "1", const char *type = "application/x-www-form-urlencoded") {
  aqua::HttpRequest request;
  const auto bytes = wire(path, form, marker, type);
  request.feed(reinterpret_cast<const uint8_t *>(bytes.data()), bytes.size());
  assert(request.complete() && !request.errorStatus());
  return executeMutation(path, request);
}
void policies() {
  constexpr int64_t epoch = 1795032000;
  assert(aqua::shouldAdoptPeerClock(true, false, 0, epoch)); // Gateway reboot can reuse the surviving slave clock.
  assert(!aqua::shouldAdoptPeerClock(true, true, epoch - 10, epoch)); // A valid gateway/NTP clock remains authoritative.
  assert(!aqua::shouldAdoptPeerClock(true, true, epoch + 10, epoch));
  assert(aqua::shouldAdoptPeerClock(false, false, 0, epoch));
  assert(!aqua::shouldAdoptPeerClock(false, true, epoch - 2, epoch));
  assert(!aqua::shouldAdoptPeerClock(false, true, epoch + 2, epoch));
  assert(aqua::shouldAdoptPeerClock(false, true, epoch - 3, epoch));
  assert(aqua::shouldAdoptPeerClock(false, true, epoch + 3, epoch));
  aqua::SlaveAccessPointPolicy ap;
  assert(!ap.desired(0, false, false, 0));
  assert(!ap.desired(14999, false, false, 0));
  assert(ap.desired(15000, false, false, 0));
  assert(ap.desired(16000, true, true, 0));
  assert(ap.desired(45999, true, true, 0));
  assert(ap.desired(46000, true, true, 1)); // Active setup session survives recovery.
  assert(!ap.desired(46001, true, true, 0));
  assert(!ap.desired(47000, true, false, 0)); // Gateway alive, router lost still needs AP after grace.
  assert(ap.desired(62000, true, false, 0));
  ap = {}; assert(!ap.desired(0, true, true, 0)); ap.force(0);
  assert(ap.desired(599999, true, true, 0));
  assert(!ap.desired(600000, true, true, 0));
  ap = {}; const uint32_t start = UINT32_MAX - 10000;
  assert(!ap.desired(start, false, false, 0));
  assert(!ap.desired(start + 14999, false, false, 0));
  assert(ap.desired(start + 15000, false, false, 0));
  ap = {}; assert(!ap.desired(0, false, false, 0));
  assert(!ap.desired(14000, true, true, 0));
  assert(!ap.desired(14001, false, false, 0));
  assert(!ap.desired(29000, false, false, 0));
  assert(ap.desired(29001, false, false, 0)); // Brief healthy transition restarts failure grace.

  aqua::ChannelSearchPolicy search;
  assert(search.next(0, false, 4, 0) == 0);
  assert(search.next(2299, false, 4, 0) == 0);
  assert(search.next(2300, false, 4, 0) == 5);
  assert(search.next(4600, true, 5, 0) == 0);
  assert(search.next(6900, false, 5, 1) == 0); // Don't move a connected setup client's AP.
  assert(search.next(9199, false, 5, 0) == 0);
  assert(search.next(9200, false, 5, 0) == 6);
  uint8_t channel = 13; uint32_t now = 9200;
  for (unsigned i = 0; i < 13; ++i) { now += 2300; channel = search.next(now, false, channel, 0); assert(channel == i + 1); }
  search = {}; now = UINT32_MAX - 1000;
  assert(search.next(now, false, 0, 0) == 0);
  assert(search.next(now + 2299, false, 0, 0) == 0);
  assert(search.next(now + 2300, false, 0, 0) == 1);
}
int main() {
  static_assert(MASTER == (LED_CHANNELS == 2), "Two-channel terrarium is master, one-channel strip is slave");
  policies();
  reset();
  const auto initial = settings;
  aqua::HttpRequest incomplete;
  const std::string incompleteWire = "POST /api/mode?mode=auto HTTP/1.1\r\nHost: local\r\nX-Aqua-Request: 1\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: 1\r\n\r\n";
  incomplete.feed(reinterpret_cast<const uint8_t *>(incompleteWire.data()), incompleteWire.size());
  assert(!incomplete.complete() && incomplete.args() == 1);
  assert(executeMutation("/api/mode", incomplete).status == 400);
  aqua::HttpRequest wrongMethod;
  const std::string getWire = "GET /api/mode?mode=auto HTTP/1.1\r\nHost: local\r\nX-Aqua-Request: 1\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: 0\r\n\r\n";
  wrongMethod.feed(reinterpret_cast<const uint8_t *>(getWire.data()), getWire.size());
  assert(wrongMethod.complete() && executeMutation("/api/mode", wrongMethod).status == 405);
  assert(command("/api/mode", "mode=auto", "0").status == 403);
  assert(command("/api/mode", "mode=auto", "1", "text/plain").status == 415);
  assert(command("/api/mode", "mode=auto&mode=off").status == 400);
  assert(command("/api/unknown", "mode=auto").status == 404);
  assert(same(initial, settings) && preferences.settingsWrites == 0 && outputRequests == 0);

  pwmOk = false;
  assert(command("/api/mode", "mode=manual").status == 503 && preferences.settingsWrites == 0);
  settings.mode = aqua::Mode::Manual; preferences.okay = false;
  assert(command("/api/mode", "mode=off").status == 503);
  assert(settings.mode == aqua::Mode::Off && outputRequests == 1 && !storageOk);

  reset(); preferences.okay = false;
  assert(command("/api/mode", "mode=auto").status == 503);
  assert(settings.mode == aqua::Mode::Off && outputRequests == 0);
  reset(); assert(command("/api/mode", "mode=auto").status == 200);
  const auto beforeOom = settings;
  const auto persistedBeforeOom = preferences.persisted;
  const auto writesBeforeOom = preferences.settingsWrites;
  const auto outputBeforeOom = outputRequests;
  stringReserveOkay = false;
  assert(command("/api/mode", "mode=manual").status == 503);
  assert(same(beforeOom, settings) && preferences.persisted == persistedBeforeOom);
  assert(preferences.settingsWrites == writesBeforeOom && outputRequests == outputBeforeOom && !storageOk);
  // OFF remains physically actionable under OOM, but the old durable program
  // must never be replaced with an empty/partial serialization.
  assert(command("/api/mode", "mode=off").status == 503);
  assert(settings.mode == aqua::Mode::Off && outputRequests == outputBeforeOom + 1);
  assert(preferences.persisted == persistedBeforeOom && preferences.settingsWrites == writesBeforeOom);
  reset();
  for (const auto count : {0, 1, 17, 255}) {
    auto invalidSettings = settings; invalidSettings.warm.count = count;
    assert(!saveSettings(invalidSettings) && preferences.settingsWrites == 0 && !storageOk);
  }
  reset(); assert(command("/api/mode", "mode=manual").status == 200);
  aqua::Settings persisted;
  assert(aqua::parseSettings(preferences.persisted.c_str(), persisted) && same(persisted, settings));
  assert(preferences.settingsWrites == 1 && outputRequests == 1);
  assert(command("/api/manual", "warm=27&cool=83").status == 200);
  assert(settings.manualWarm == 27 && settings.manualCool == (LED_CHANNELS == 1 ? 0 : 83));
  assert(settingsDirty && lastManualChange == fakeNow && preferences.settingsWrites == 1);
  const auto manual = settings;
  assert(command("/api/manual", "warm=101&cool=83").status == 400 && same(manual, settings));
  settings.mode = aqua::Mode::Auto;
  const auto automatic = settings;
  assert(command("/api/manual", "warm=1&cool=1").status == 409 && same(automatic, settings));

  reset();
  assert(command("/api/program", "warmPoints=0,0;720,50&coolPoints=0,0;0,100").status == 400);
  assert(same(initial, settings) && preferences.settingsWrites == 0);
  preferences.okay = false;
  assert(command("/api/program", "warmPoints=0,0;720,50&coolPoints=0,2;720,70").status == 503);
  assert(same(initial, settings) && outputRequests == 0);
  reset();
  assert(command("/api/program", "warmPoints=0,0;720,50&coolPoints=0,2;720,70").status == 200);
  assert(settings.warm.points[1].level == 50 && settings.cool.points[1].level == 70);
  assert(aqua::parseSettings(preferences.persisted.c_str(), persisted) && same(persisted, settings));

  reset();
  assert(command("/api/time", "epoch=0").status == 400 && timeWrites == 0);
  clockResult = -1;
  assert(command("/api/time", "epoch=1795020000").status == 503 && timeSource.load() == TimeSource::None && outputRequests == 0);
  clockResult = 0;
  assert(command("/api/time", "epoch=1795020000").status == 200);
  assert(assignedEpoch == 1795020000 && timeSource.load() == TimeSource::Browser && outputRequests == 1);

  reset();
  auto wifiResult = command("/api/wifi", "ssid=test-network&password=test-only-password");
  if (MASTER) {
    assert(wifiResult.status == 200 && wifiPending && wifiRequested == fakeNow && preferences.wifiWrites == 1);
    assert(std::strcmp(wifi.ssid, "test-network") == 0);
    const auto beforeWifi = wifi; wifiPending = false; preferences.okay = false;
    assert(command("/api/wifi", "ssid=other-network&password=other-password").status == 503);
    assert(std::memcmp(&beforeWifi, &wifi, sizeof(wifi)) == 0 && !wifiPending);
    assert(command("/api/wifi", "ssid=network&password=short").status == 400);
  } else assert(wifiResult.status == 409 && !wifiPending && preferences.wifiWrites == 0 && wifi.ssid[0] == 0);

  reset();
  const auto maintenanceResult = command("/api/maintenance/ap", "");
  assert(maintenanceResult.status == (MASTER ? 409 : 200));
  assert(accessPointPolicy.desired(fakeNow, true, true, 0) == !MASTER);
  assert(apStarts == (MASTER ? 0u : 1u) && apActive == !MASTER);
  assert(preferences.settingsWrites == 0 && preferences.wifiWrites == 0);
  if (!MASTER) {
    reset(); apStartOkay = false;
    assert(command("/api/maintenance/ap", "").status == 503);
    assert(!apActive && configRevision == 0);
  }

  reset();
  const auto updateResult = command("/api/update/check", "");
  assert(updateResult.status == (MASTER ? 202 : 409));
  assert(ledota::checks == (MASTER ? 1u : 0u));
  if (MASTER) {
    ledota::occupied = true; assert(command("/api/update/check", "").status == 409);
    ledota::occupied = false; WiFi.state = 0; assert(command("/api/update/check", "").status == 503);
  }
  assert(criticalDepth == 0);
  std::printf("PASS roles (%u channels, %s): production mutation admission, NVS atomicity/OFF fallback, manual guard, channel isolation, WiFi/maintenance ownership, AP/channel policy deadlines and rollover\n", LED_CHANNELS, MASTER ? "master" : "slave");
}
'''

with tempfile.TemporaryDirectory(prefix="aqualight-roles-") as directory:
    temp = Path(directory)
    cpp = temp / "check.cpp"
    cpp.write_text(STUBS + role_definition + settings_helpers + mutations + CHECKS)
    for channels in (1, 2):
        binary = temp / f"roles-{channels}"
        subprocess.run([
            os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer", f"-DLED_CHANNELS={channels}",
            "-I", str(ROOT / "src"), str(cpp), "-o", str(binary),
        ], check=True)
        subprocess.run([str(binary)], check=True)
