#!/usr/bin/env python3
"""Check retained sensor failure history and completed-operation timing.

Compiles the production helpers with C++17 and ASan/UBSan. This does not
exercise AHTxx, I2C, Wi-Fi or real operation timings on the board.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
CHECKS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include "diagnostics.h"

int main() {
  aqua::SensorDiagnostics sensor;
  assert(!sensor.ready && !sensor.haveError);
  assert(sensor.initAttempts == 0 && sensor.readAttempts == 0);

  sensor.recordInit(true, 0, 140, 1000);
  assert(sensor.ready && !sensor.haveError);
  sensor.recordRead(true, 0, 80, 6000);
  assert(sensor.ready && sensor.readAttempts == 1 && sensor.readFailures == 0);

  // A bad packet, a failed reinitialization and then a successful init must
  // remain visibly failed until an actual measurement has succeeded.
  sensor.recordRead(false, 4, 85, 11000);
  assert(!sensor.ready && sensor.haveError && sensor.consecutiveFailures == 1);
  assert(sensor.readAttempts == 2 && sensor.readFailures == 1);
  assert(sensor.lastErrorStatus == 4 && sensor.lastErrorAt == 11000);
  assert(std::strcmp(sensor.lastErrorPhase, "read") == 0);
  assert(sensor.firstErrorStatus == 4 && sensor.firstErrorAt == 11000);
  assert(std::strcmp(sensor.firstErrorPhase, "read") == 0);
  sensor.recordInit(false, 2, 210, 16000);
  assert(!sensor.ready && sensor.consecutiveFailures == 2);
  assert(sensor.initAttempts == 2 && sensor.initFailures == 1);
  assert(sensor.lastErrorStatus == 2 && sensor.lastErrorAt == 16000);
  assert(std::strcmp(sensor.lastErrorPhase, "init") == 0);
  assert(sensor.firstErrorStatus == 4 && sensor.firstErrorAt == 11000);
  assert(std::strcmp(sensor.firstErrorPhase, "read") == 0);
  sensor.recordInit(true, 0, 140, 21000);
  assert(sensor.ready && sensor.lastStatus == 0);
  assert(sensor.consecutiveFailures == 2 && sensor.haveError);
  assert(sensor.lastErrorStatus == 2 && sensor.lastErrorAt == 16000);
  assert(sensor.lastInitMs == 140 && sensor.maxInitMs == 210);

  // Recovery clears the current streak but retains the last error for a
  // remote observer whose next poll arrives after the fault has cleared.
  sensor.recordRead(true, 0, 79, 26000);
  assert(sensor.ready && sensor.consecutiveFailures == 0);
  assert(sensor.readAttempts == 3 && sensor.readFailures == 1);
  assert(sensor.haveError && sensor.lastErrorStatus == 2);
  assert(sensor.firstErrorStatus == 4 && sensor.firstErrorAt == 11000);
  assert(std::strcmp(sensor.firstErrorPhase, "read") == 0);
  assert(sensor.lastErrorAt == 16000 && std::strcmp(sensor.lastErrorPhase, "init") == 0);
  assert(sensor.lastReadMs == 79 && sensor.maxReadMs == 85);
  assert(sensor.initAttempts == 3 && sensor.initFailures == 1);

  // Valid library status plus rejected numeric data has its own code.
  sensor.recordRead(false, 254, 90, 31000);
  assert(sensor.consecutiveFailures == 1 && sensor.readFailures == 2);
  assert(sensor.lastStatus == 254 && sensor.lastErrorStatus == 254);
  assert(sensor.maxReadMs == 90 && sensor.lastErrorAt == 31000);
  assert(std::strcmp(sensor.lastErrorPhase, "read") == 0);

  // A first fault at millis()==0 must not be confused with no error yet.
  aqua::SensorDiagnostics bootFailure;
  bootFailure.recordRead(false, 4, 80, 0);
  assert(bootFailure.haveError && bootFailure.lastErrorAt == 0);
  bootFailure.recordInit(false, 2, 50, 5000);
  assert(bootFailure.firstErrorStatus == 4 && bootFailure.firstErrorAt == 0);
  assert(std::strcmp(bootFailure.firstErrorPhase, "read") == 0);
  assert(bootFailure.lastErrorStatus == 2 && bootFailure.lastErrorAt == 5000);
  bootFailure.recordInit(true, 0, 140, 10000);
  bootFailure.recordRead(true, 0, 80, 15000);
  assert(bootFailure.haveError && bootFailure.consecutiveFailures == 0);
  assert(bootFailure.firstErrorStatus == 4 && bootFailure.firstErrorAt == 0);
  assert(std::strcmp(bootFailure.firstErrorPhase, "read") == 0);
  assert(bootFailure.lastErrorStatus == 2 && bootFailure.lastErrorAt == 5000);
  assert(bootFailure.initFailures == 1 && bootFailure.readFailures == 1);

  const char *names[] = {"ok", "busy", "ack_error", "data_error", "crc_error"};
  for (uint8_t status = 0; status < 5; ++status)
    assert(std::strcmp(aqua::sensorStatusText(status), names[status]) == 0);
  assert(std::strcmp(aqua::sensorStatusText(253), "init_failed") == 0);
  assert(std::strcmp(aqua::sensorStatusText(254), "invalid_sample") == 0);
  assert(std::strcmp(aqua::sensorStatusText(255), "unknown_error") == 0);
  assert(std::strcmp(aqua::sensorStatusText(42), "unknown_error") == 0);

  aqua::TimingDiagnostics http;
  for (const uint32_t ms : {0u, 500u, 501u, 34000u, 12u}) http.observe(ms);
  assert(http.lastMs == 12 && http.maxMs == 34000 && http.slowCount == 2);
  aqua::TimingDiagnostics loop;
  loop.observe(100);
  assert(loop.maxMs == 100 && loop.slowCount == 0);
  assert(http.maxMs == 34000); // Instruments do not share their history.
  std::puts("PASS diagnostics: transient failure history, initialization vs measurement recovery, invalid samples, boot-time errors and slow-operation timing");
}
'''

with tempfile.TemporaryDirectory(prefix="aqualight-diagnostics-") as directory:
    temp = Path(directory)
    cpp, binary = temp / "check.cpp", temp / "check"
    cpp.write_text(CHECKS)
    subprocess.run([
        os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(ROOT / "src"),
        str(cpp), "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)
