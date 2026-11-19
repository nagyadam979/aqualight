#pragma once

#include <cstdint>

namespace aqua {

// This measures completed operations, not deadlines or watchdog state.
struct TimingDiagnostics {
  uint32_t lastMs = 0;
  uint32_t maxMs = 0;
  uint32_t slowCount = 0;

  void observe(uint32_t durationMs) {
    lastMs = durationMs;
    if (durationMs > maxMs) maxMs = durationMs;
    if (durationMs > 500) ++slowCount;
  }
};

// AHTxx's pinned status codes; 253/254 are local init/sample failures.
inline const char *sensorStatusText(uint8_t status) {
  switch (status) {
    case 0: return "ok";
    case 1: return "busy";
    case 2: return "ack_error";
    case 3: return "data_error";
    case 4: return "crc_error";
    case 253: return "init_failed";
    case 254: return "invalid_sample";
    default: return "unknown_error";
  }
}

// Boot-local counters and retained failure context. Reinitializing the sensor
// is not evidence of a recovered measurement: only a good read ends the streak.
struct SensorDiagnostics {
  bool ready = false;
  uint8_t lastStatus = 0;
  uint8_t lastErrorStatus = 0;
  bool haveError = false;
  uint8_t firstErrorStatus = 0;
  const char *firstErrorPhase = "";
  uint32_t firstErrorAt = 0;
  const char *lastErrorPhase = "";
  uint32_t lastErrorAt = 0;
  uint32_t initAttempts = 0;
  uint32_t initFailures = 0;
  uint32_t readAttempts = 0;
  uint32_t readFailures = 0;
  uint32_t consecutiveFailures = 0;
  uint32_t lastReadMs = 0;
  uint32_t maxReadMs = 0;
  uint32_t lastInitMs = 0;
  uint32_t maxInitMs = 0;

  void recordInit(bool success, uint8_t status, uint32_t durationMs, uint32_t now) {
    ++initAttempts;
    ready = success;
    lastStatus = status;
    lastInitMs = durationMs;
    if (durationMs > maxInitMs) maxInitMs = durationMs;
    if (!success) {
      ++initFailures;
      recordFailure(status, "init", now);
    }
  }

  void recordRead(bool success, uint8_t status, uint32_t durationMs, uint32_t now) {
    ++readAttempts;
    ready = success;
    lastStatus = status;
    lastReadMs = durationMs;
    if (durationMs > maxReadMs) maxReadMs = durationMs;
    if (success) consecutiveFailures = 0;
    else {
      ++readFailures;
      recordFailure(status, "read", now);
    }
  }

 private:
  void recordFailure(uint8_t status, const char *phase, uint32_t now) {
    ++consecutiveFailures;
    if (!haveError) {
      firstErrorStatus = status;
      firstErrorPhase = phase;
      firstErrorAt = now;
    }
    haveError = true;
    lastErrorStatus = status;
    lastErrorPhase = phase;
    lastErrorAt = now;
  }
};

}  // namespace aqua
