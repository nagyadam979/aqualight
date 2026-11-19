#!/usr/bin/env python3
"""Execute the production sensor path with controlled I2C/AHTxx doubles.

Checks retry cadence, retained physical-line snapshots, invalid sample handling
and stale library status on initialization. Does not establish hardware health.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
source = (ROOT / "src/main.cpp").read_text()
start = source.index("void captureSensorErrorLines()")
production = source[start:source.index("\nString jsonString(", start)]

STUBS = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include "diagnostics.h"
#include "schedule.h"

constexpr uint8_t AHTXX_NO_ERROR = 0, AHTXX_ERROR = 255, AHTXX_ADDRESS_X38 = 0x38;
constexpr bool AHTXX_USE_READ_DATA = false;
constexpr int I2C_SDA = 1, I2C_SCL = 3;
using gpio_num_t = int;
uint32_t fakeNow = 0;
uint32_t millis() { return fakeNow; }
int sdaHigh = 1, sclHigh = 1;
int gpio_get_level(gpio_num_t pin) {
  assert(pin == I2C_SDA || pin == I2C_SCL);
  return pin == I2C_SDA ? sdaHigh : sclHigh;
}
struct SerialDouble {
  void println(const char *) {}
  template <typename... Args> void printf(const char *, Args...) {}
} Serial;
struct WireDouble {
  unsigned timeout = 0, probeCalls = 0;
  uint8_t address = 0, probeCode = 0;
  uint32_t probeDuration = 20;
  bool flipPinsOnProbe = false;
  void setTimeOut(unsigned ms) { timeout = ms; }
  void beginTransmission(uint8_t value) { address = value; }
  uint8_t endTransmission(bool stop) {
    assert(stop && timeout == 50 && address == AHTXX_ADDRESS_X38);
    ++probeCalls;
    fakeNow += probeDuration;
    if (flipPinsOnProbe) { sdaHigh = !sdaHigh; sclHigh = !sclHigh; }
    return probeCode;
  }
} Wire;
struct AhtDouble {
  bool beginReady = true;
  uint8_t status = 0, nextReadStatus = 0;
  uint32_t beginDuration = 140, readDuration = 80;
  unsigned beginCalls = 0, temperatureCalls = 0, humidityCalls = 0;
  float nextTemperature = 24.5f, nextHumidity = 61.0f;
  bool begin(int sda, int scl, unsigned speed, unsigned stretch) {
    assert(sda == I2C_SDA && scl == I2C_SCL && speed == 100000 && stretch == 50000);
    ++beginCalls;
    fakeNow += beginDuration;
    // Match pinned AHTxx: begin/softReset do not update the prior read status.
    return beginReady;
  }
  float readTemperature() {
    ++temperatureCalls;
    fakeNow += readDuration;
    status = nextReadStatus;
    return nextTemperature;
  }
  float readHumidity(bool forceRead) {
    assert(!forceRead && temperatureCalls == humidityCalls + 1);
    ++humidityCalls;
    return nextHumidity;
  }
  uint8_t getStatus() { return status; }
} aht20;
bool sensorReady = false, sensorOk = false, haveSample = false;
float temperature = NAN, humidity = NAN;
uint32_t lastSensor = 0, lastSample = 0;
aqua::SensorDiagnostics sensorDiagnostics;
int lastInitProbeCode = -1;
bool firstErrorSdaHigh = false, firstErrorSclHigh = false;
bool lastErrorSdaHigh = false, lastErrorSclHigh = false;
'''

CHECKS = r'''
void reset() {
  fakeNow = 0; sdaHigh = sclHigh = 1;
  Wire = {}; aht20 = {}; sensorDiagnostics = {};
  sensorReady = sensorOk = haveSample = false;
  temperature = humidity = NAN; lastSensor = lastSample = 0;
  lastInitProbeCode = -1;
  firstErrorSdaHigh = firstErrorSclHigh = lastErrorSdaHigh = lastErrorSclHigh = false;
}
void at(uint32_t now) { fakeNow = now; updateSensor(now); }

int main() {
  // Initial failure must not reuse a stale CRC status as its cause. Capture
  // lines before the diagnostic probe changes the physical bus state.
  reset();
  aht20.status = 4; aht20.beginReady = false;
  sdaHigh = 0; sclHigh = 1; Wire.probeCode = 2; Wire.flipPinsOnProbe = true;
  assert(!startSensor());
  assert(sensorDiagnostics.lastStatus == 253);
  assert(std::strcmp(aqua::sensorStatusText(sensorDiagnostics.lastStatus), "init_failed") == 0);
  assert(sensorDiagnostics.initFailures == 1 && Wire.probeCalls == 1 && lastInitProbeCode == 2);
  assert(!firstErrorSdaHigh && firstErrorSclHigh && !lastErrorSdaHigh && lastErrorSclHigh);
  assert(sdaHigh == 1 && sclHigh == 0); // Probe ran after the captured snapshot.
  aht20.beginReady = true;
  at(5000);
  assert(sensorReady && !sensorOk && !haveSample && aht20.temperatureCalls == 0);
  assert(sensorDiagnostics.lastStatus == 0 && sensorDiagnostics.consecutiveFailures == 1);
  assert(lastInitProbeCode == -1 && Wire.probeCalls == 1);
  at(9999);
  assert(aht20.temperatureCalls == 0);
  at(10000);
  assert(sensorOk && haveSample && temperature == 24.5f && humidity == 61.0f);
  assert(lastSample == 10080 && sensorDiagnostics.lastReadMs == 80);
  assert(sensorDiagnostics.consecutiveFailures == 0 && sensorDiagnostics.firstErrorStatus == 253);
  at(10081); at(14999);
  assert(aht20.temperatureCalls == 1 && aht20.humidityCalls == 1);
  at(15000);
  assert(aht20.temperatureCalls == 2 && sensorDiagnostics.readAttempts == 2);

  // A read failure must remain the first cause when subsequent init failures
  // are observed. No premature retry/read is allowed between 5-second ticks.
  reset();
  sensorReady = startSensor();
  aht20.nextReadStatus = 4; sdaHigh = 1; sclHigh = 0;
  at(5000);
  assert(!sensorOk && !sensorReady && std::isnan(temperature) && std::isnan(humidity));
  assert(sensorDiagnostics.firstErrorStatus == 4 && sensorDiagnostics.firstErrorAt == 5080);
  assert(firstErrorSdaHigh && !firstErrorSclHigh);
  assert(Wire.probeCalls == 0);
  at(9999);
  assert(aht20.beginCalls == 1 && aht20.temperatureCalls == 1);
  aht20.beginReady = false; sdaHigh = 0; sclHigh = 1; Wire.probeCode = 2;
  at(10000);
  assert(sensorDiagnostics.lastErrorStatus == 253 && sensorDiagnostics.firstErrorStatus == 4);
  assert(sensorDiagnostics.firstErrorAt == 5080 && sensorDiagnostics.lastErrorAt == 10160);
  assert(firstErrorSdaHigh && !firstErrorSclHigh && !lastErrorSdaHigh && lastErrorSclHigh);
  assert(std::strcmp(sensorDiagnostics.firstErrorPhase, "read") == 0);
  assert(std::strcmp(sensorDiagnostics.lastErrorPhase, "init") == 0);
  assert(Wire.probeCalls == 1 && aht20.temperatureCalls == 1);
  aht20.beginReady = true; aht20.nextReadStatus = 0;
  at(15000);
  assert(sensorReady && !sensorOk && aht20.temperatureCalls == 1);
  assert(sensorDiagnostics.consecutiveFailures == 2);
  at(20000);
  assert(sensorOk && sensorDiagnostics.consecutiveFailures == 0 && lastSample == 20080);
  assert(sensorDiagnostics.firstErrorStatus == 4 && sensorDiagnostics.lastErrorStatus == 253);
  assert(firstErrorSdaHigh && !firstErrorSclHigh && !lastErrorSdaHigh && lastErrorSclHigh);

  // Library "ok" is insufficient: reject nonfinite, sentinel and physical
  // out-of-range values. Failure must be reported as invalid_sample.
  const float invalid[][2] = {
    {NAN, 50}, {25, NAN}, {std::numeric_limits<float>::infinity(), 50},
    {25, -1}, {25, 101}, {-41, 50}, {86, 50}, {255, 50}, {25, 255}
  };
  for (const auto &sample : invalid) {
    reset(); sensorReady = true;
    aht20.nextTemperature = sample[0]; aht20.nextHumidity = sample[1];
    at(5000);
    assert(!sensorOk && !sensorReady && !haveSample);
    assert(std::isnan(temperature) && std::isnan(humidity));
    assert(sensorDiagnostics.lastStatus == 254 && sensorDiagnostics.readFailures == 1);
    assert(aht20.temperatureCalls == 1 && aht20.humidityCalls == 1 && Wire.probeCalls == 0);
  }
  const float boundaries[][2] = {{-40, 0}, {85, 100}};
  for (const auto &sample : boundaries) {
    reset(); sensorReady = true;
    aht20.nextTemperature = sample[0]; aht20.nextHumidity = sample[1];
    at(5000);
    assert(sensorOk && haveSample && !sensorDiagnostics.haveError);
  }

  // Interval arithmetic must survive millis wrapping between samples.
  reset(); sensorReady = true; lastSensor = UINT32_MAX - 2000;
  at(2998); assert(aht20.temperatureCalls == 0);
  at(2999); assert(aht20.temperatureCalls == 1 && sensorOk);
  std::puts("PASS production sensor path: stale init status, bounded probe, pin snapshots, retry cadence, cached humidity, invalid samples, completion timestamps and millis wrap");
}
'''

with tempfile.TemporaryDirectory(prefix="aqualight-sensor-path-") as directory:
    temp = Path(directory)
    cpp, binary = temp / "check.cpp", temp / "check"
    cpp.write_text(STUBS + production + CHECKS)
    subprocess.run([
        os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(ROOT / "src"),
        str(cpp), "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)
