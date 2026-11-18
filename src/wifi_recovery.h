#pragma once

#include <cstdint>

namespace aqua {

constexpr uint32_t WIFI_RECOVERY_OFFLINE_MS = 5 * 60 * 1000;
constexpr uint32_t WIFI_RECOVERY_STABLE_MS = 10 * 60 * 1000;
constexpr uint8_t WIFI_RECOVERY_MAX_RESTARTS = 2;
constexpr uint32_t WIFI_RECOVERY_CLOCK_MAGIC = 0x57494649;

inline bool resumeRecoveryClock(bool softwareReset, uint32_t marker, int64_t epoch,
                                uint64_t minimum, uint64_t maximum) {
  return softwareReset && marker == WIFI_RECOVERY_CLOCK_MAGIC && epoch >= 0 &&
      static_cast<uint64_t>(epoch) >= minimum && static_cast<uint64_t>(epoch) <= maximum;
}

// The restart count is loaded from NVS and changes only after a verified NVS
// write. A brief reconnection resets the offline timer, never the restart budget.
class WifiRecovery {
 public:
  void begin(uint8_t persisted) {
    used_ = persisted < WIFI_RECOVERY_MAX_RESTARTS ? persisted : WIFI_RECOVERY_MAX_RESTARTS;
    offline_ = online_ = false;
    failed_ = 0;
  }
  void observe(uint32_t now, bool configured, bool connected) {
    if (!configured) { offline_ = online_ = false; failed_ = 0; return; }
    if (connected) {
      offline_ = false; failed_ = 0;
      if (!online_) { online_ = true; onlineSince_ = now; }
    } else {
      online_ = false;
      if (!offline_) { offline_ = true; offlineSince_ = now; failed_ = 0; }
    }
  }
  void failedAttempt() { if (offline_ && failed_ < 2) ++failed_; }
  bool restartDue(uint32_t now) const {
    return offline_ && used_ < WIFI_RECOVERY_MAX_RESTARTS && failed_ >= 2 &&
        static_cast<uint32_t>(now - offlineSince_) >= WIFI_RECOVERY_OFFLINE_MS;
  }
  bool clearDue(uint32_t now) const {
    return online_ && used_ && static_cast<uint32_t>(now - onlineSince_) >= WIFI_RECOVERY_STABLE_MS;
  }
  void restarted(uint32_t now) { ++used_; failed_ = 0; offlineSince_ = now; }
  void cleared() { used_ = 0; }
  uint8_t used() const { return used_; }
  uint8_t remaining() const { return WIFI_RECOVERY_MAX_RESTARTS - used_; }
 private:
  uint8_t used_ = 0, failed_ = 0;
  bool offline_ = false, online_ = false;
  uint32_t offlineSince_ = 0, onlineSince_ = 0;
};

}  // namespace aqua
