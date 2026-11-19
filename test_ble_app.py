#!/usr/bin/env python3
"""Check BLE envelope preflight and compiled BLE-only application boundaries.

The native test runs the actual lexical nesting/NUL guard, not a second JSON
implementation. cJSON remains responsible for JSON syntax and envelope schema.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
source = (ROOT / "src/main.cpp").read_text()
compiler = os.environ.get("CXX", "c++")


def region(begin, end=None):
    start = source.index(begin)
    return source[start:source.index(end, start)] if end else source[start:]


def preprocess(text, ble):
    result = subprocess.run([
        compiler, "-E", "-P", "-x", "c++", f"-DLED_BLE_ONLY={int(ble)}",
        "-DARDUINO_USB_MODE=1", "-DARDUINO_USB_CDC_ON_BOOT=1", "-",
    ], input=text, capture_output=True, text=True, check=True)
    return result.stdout


setup = region("void setup()", "\nvoid loop()")
loop = region("void loop()")
ble_setup, ble_loop = preprocess(setup, True), preprocess(loop, True)
normal_setup = preprocess(setup, False)
for token in ("startNetwork(", "configureRoutes(", "WiFi.", "boardlink::", "configTzTime(",
              "maintenance::begin(", "maintenance::poll(", "server.handleClient("):
    assert token not in ble_setup + ble_loop, f"BLE-only runtime must not start {token}"
assert "blelink::begin(" in ble_setup and "ledota::begin(false)" in ble_setup
assert "pollBle(now)" in ble_loop and "ledota::poll(" in ble_loop
deferred_save = ble_loop.index("if (settingsDirty")
assert re.search(r"\bnow\s*=\s*millis\(\)", ble_loop[ble_loop.index("pollBle(now)"):deferred_save]), \
    "Refresh time after BLE mutations before debounce arithmetic can underflow"
assert "startNetwork();" in normal_setup and "configureRoutes();" in normal_setup
assert "ledota::begin(MASTER)" in normal_setup and "maintenance::begin(" in normal_setup
assert ble_setup.index("xTaskCreate(controlWorker") < ble_setup.index("blelink::begin(")
assert ble_setup.index("xTaskCreate(sensorWorker") < ble_setup.index("blelink::begin(")
ble_state = preprocess(region("String buildState()", "\nvoid sendState()"), True)
ble_usb = preprocess(region("void pollUsbStatus(", "\nvoid setup()"), True)
assert "WiFi." not in ble_state + ble_usb and "boardlink::counters()" not in ble_usb
ble_poll = region("void pollBle(", "\n#endif")
for forbidden_path in ("/api/wifi", "/api/peer/", "/api/update/check", "/api/maintenance/ap"):
    assert forbidden_path not in ble_poll, f"Unexpected Bluetooth command admission: {forbidden_path}"
assert re.search(r"flatBleEnvelope\(payload,\s*length\)\s*\?\s*cJSON_ParseWithLengthOpts", ble_poll)
assert "end == payload + length" in ble_poll and "executeMutation(path->valuestring, *request)" in ble_poll


def function(text, signature):
    start = text.index(signature)
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


# A ready BLE stack alone cannot validate the pilot image. The phone must have
# completed an admitted, successful time mutation; reads and rejected edits do
# not demonstrate the phone-to-device command path.
assert re.search(r"bool\s+bleClientValidated\s*=\s*false\s*;", source)
assignments = re.findall(r"\bbleClientValidated\s*=\s*true\s*;", source)
assert len(assignments) == 1
successful_mutation = function(ble_poll, "if (status < 400)")
assert re.search(r'if\s*\(!strcmp\(path->valuestring,\s*"/api/time"\)\)\s*'
                 r'bleClientValidated\s*=\s*true\s*;', successful_mutation)
assert ble_poll.index("executeMutation(path->valuestring, *request)") < ble_poll.index(successful_mutation)
transport_gate = re.search(r"const bool transportHealthy\s*=\s*([^;]+);", ble_loop).group(1)
health_gate = re.search(r"const bool healthy\s*=\s*([^;]+);", ble_loop).group(1)
assert "blelink::ready()" in transport_gate and "bleClientValidated" in transport_gate
assert "ledota::poll(healthy, !settingsDirty && storageOk)" in ble_loop

production = region("bool flatBleEnvelope(", "\nvoid pollBle(")
CHECKS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
'''
TESTS = r'''
bool accepts(const std::string &text) { return flatBleEnvelope(text.c_str(), text.size()); }
int main() {
  assert(accepts(R"({"method":"GET","path":"/api/state","form":""})"));
  assert(accepts(R"({"method":"POST","path":"/api/program","form":"warmPoints=0,0;720,50&coolPoints=0,0;720,50"})"));
  assert(accepts(R"({"form":"literal={}[]\"more\"\\end"})"));
  assert(!flatBleEnvelope(nullptr, 1));
  assert(!flatBleEnvelope("", 0));
  assert(!flatBleEnvelope("{}", 1));
  assert(!accepts(std::string("{}\0{}", 5)));
  assert(!accepts(R"({"method":"GET","path":"/api/state\u0000junk","form":""})"));
  assert(!accepts(R"({"method":"POST","path":"/api/mode","form":{"mode":"off"}})"));
  assert(!accepts(R"({"form":["mode=off"]})"));
  assert(!accepts("[{}]"));
  assert(!accepts("}"));
  assert(!accepts("{{}}"));
  assert(!accepts(R"({"form":"unterminated\)"));
  assert(!accepts(R"({"form":"unterminated})"));
  assert(!accepts(std::string(10000, '{') + std::string(10000, '}')));
  assert(!accepts(std::string(10000, '[') + std::string(10000, ']')));
  const std::string prefix = R"({"method":"POST","path":"/api/program","form":")";
  const std::string suffix = R"("})";
  std::string full = prefix + std::string(2048 - prefix.size() - suffix.size(), 'x') + suffix;
  assert(full.size() == 2048 && accepts(full)); // Lexical guard at transport maximum; route validates form length separately.
  full[2047] = '\0';
  assert(!accepts(full)); // A final-byte NUL must not turn a truncated prefix into a request.
  std::puts("PASS BLE app: production flat-envelope nesting/NUL guard; BLE-only compiled runtime excludes WiFi/ESP-NOW/web/OTA services, preserves independent control/sensor tasks and narrow mutation admission");
}
'''

with tempfile.TemporaryDirectory(prefix="aqualight-ble-app-") as directory:
    temp = Path(directory)
    cpp, binary = temp / "check.cpp", temp / "check"
    cpp.write_text(CHECKS + production + TESTS)
    subprocess.run([
        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-omit-frame-pointer", str(cpp), "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)


# Compile the production OTA poll and its actual conditional constants, rather
# than restating a three-minute deadline in the fixture.
cloud = (ROOT / "src/cloud_update.cpp").read_text()
constants = cloud[cloud.index("constexpr uint32_t MIN_VALIDATION_MS"):
                  cloud.index("constexpr int HTTP_TIMEOUT_MS")]
ota_fixture = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>
constexpr int ESP_OK = 0;
bool pendingValidation = true, rollbackFailed = false, configured = false;
uint32_t startedMs = 0, healthySince = 0, nowMs = 0;
std::atomic<bool> ready{false}, queued{false}, running{false}, bootSelected{false};
std::atomic<uint32_t> nextCheck{0};
void *workerTask = nullptr;
unsigned accepted = 0, rolledBack = 0;
const char *lastState = "";
uint32_t millis() { return nowMs; }
void setState(const char *state, const char * = "", unsigned = 0) { lastState = state; }
int esp_ota_mark_app_valid_cancel_rollback() { ++accepted; return ESP_OK; }
int esp_ota_mark_app_invalid_rollback_and_reboot() { ++rolledBack; return -1; }
struct { void restart() { assert(false); } } ESP;
void xTaskNotifyGive(void *) { assert(false); }
bool requestCheck() { assert(false); return false; }
bool bleReady = true, bleClientValidated = false;
namespace blelink { bool ready() { return bleReady; } }
bool pwmOk = true, storageOk = true, controlTaskHandle = true;
bool sensorTaskHandle = true, controlHealthy = true;
'''
ota_fixture += preprocess(constants, True)
ota_fixture += "\nnamespace normal {\n" + preprocess(constants, False) + "\n}\n"
ota_fixture += "bool appHealthy() { const bool transportHealthy = " + transport_gate
ota_fixture += "; return " + health_gate + "; }\n"
ota_fixture += function(cloud, "void poll(bool applicationHealthy, bool canReboot)")
ota_fixture += r'''
void freshBoot() {
  pendingValidation = true; rollbackFailed = false; startedMs = 0;
  healthySince = 0; nowMs = 0; accepted = rolledBack = 0;
  bleReady = true; bleClientValidated = false;
}
void at(uint32_t time) { nowMs = time; poll(appHealthy(), false); }
int main() {
  static_assert(VALIDATION_DEADLINE_MS == 180000);
  static_assert(normal::VALIDATION_DEADLINE_MS == 60000);
  static_assert(MIN_VALIDATION_MS == 10000 && HEALTH_STABLE_MS == 3000);
  static_assert(normal::MIN_VALIDATION_MS == MIN_VALIDATION_MS && normal::HEALTH_STABLE_MS == HEALTH_STABLE_MS);
  // Evaluate the compiled main-loop gate, including each local health input.
  assert(!appHealthy()); // BLE ready but no successful phone/time command.
  bleClientValidated = true; assert(appHealthy());
  bleReady = false; assert(!appHealthy()); bleReady = true;
  for (bool *input : {&pwmOk, &storageOk, &controlTaskHandle, &sensorTaskHandle, &controlHealthy}) {
    *input = false; assert(!appHealthy()); *input = true;
  }
  freshBoot();
  at(60000); assert(pendingValidation && !accepted && !rolledBack);
  at(179999); assert(pendingValidation && !accepted && !rolledBack);
  at(180000); assert(pendingValidation && !accepted && rolledBack == 1 && rollbackFailed);
  at(200000); assert(rolledBack == 1); // No repeated rollback after a failed restart.

  freshBoot(); bleClientValidated = true;
  at(1); at(3001); at(9999); assert(!accepted && pendingValidation);
  at(10000); assert(accepted == 1 && !pendingValidation && !strcmp(lastState, "healthy"));
  at(180000); assert(accepted == 1 && !rolledBack);
  bleReady = false; bleClientValidated = false;
  at(360000); assert(accepted == 1 && !rolledBack); // Accepted image stays accepted.

  freshBoot();
  at(12000); assert(!accepted); // Boot time alone is insufficient.
  bleClientValidated = true;
  at(12000); at(14999); assert(!accepted && pendingValidation);
  at(15000); assert(accepted == 1 && !pendingValidation && !rolledBack);

  freshBoot(); bleClientValidated = true;
  at(12000); at(14000); bleReady = false; at(14001);
  assert(!healthySince && !accepted);
  bleReady = true; at(15000); at(17999); assert(!accepted);
  at(18000); assert(accepted == 1 && !pendingValidation && !rolledBack);
  std::puts("PASS BLE pilot OTA: actual deadline/health gate, 180s rollback, 10s boot and 3s continuous health, accepted image remains valid");
}
'''
with tempfile.TemporaryDirectory(prefix="aqualight-ble-ota-") as directory:
    binary = Path(directory) / "check"
    subprocess.run([
        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
        "-x", "c++", "-", "-o", str(binary),
    ], input=ota_fixture, text=True, check=True)
    subprocess.run([str(binary)], check=True)
