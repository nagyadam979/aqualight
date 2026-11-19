#pragma once

#include <cstdint>

namespace aqua {

// The caller has already validated the peer epoch and authenticated its frame.
// The gateway only borrows a peer clock while its own clock is invalid. The
// slave follows the gateway, with a small tolerance to avoid repeated jumps.
inline bool shouldAdoptPeerClock(bool master, bool localValid, int64_t current, int64_t target) {
  return !localValid || (!master && (current > target + 2 || current < target - 2));
}

class MaintenanceButtonPolicy {
 public:
  bool tick(uint32_t now, bool pressed) {
    if (!pressed) { held_ = false; fired_ = false; return false; }
    if (!held_) { held_ = true; started_ = now; }
    if (!fired_ && static_cast<uint32_t>(now - started_) >= 3000) {
      fired_ = true;
      return true;
    }
    return false;
  }
 private:
  bool held_ = false, fired_ = false;
  uint32_t started_ = 0;
};

// All time comparisons are wrap-safe elapsed times, never waits.
class SlaveAccessPointPolicy {
 public:
  static constexpr uint32_t FAILURE_GRACE_MS = 15000;
  static constexpr uint32_t RECOVERY_GRACE_MS = 30000;
  static constexpr uint32_t MAINTENANCE_MS = 600000;

  void force(uint32_t now) { forced_ = true; forcedAt_ = now; }

  bool desired(uint32_t now, bool masterOnline, bool masterNetworkConnected, unsigned clients) {
    const bool healthy = masterOnline && masterNetworkConnected;
    if (healthy != healthy_ || !observed_) {
      observed_ = true;
      healthy_ = healthy;
      changedAt_ = now;
    }
    if (forced_ && static_cast<uint32_t>(now - forcedAt_) >= MAINTENANCE_MS) forced_ = false;
    if (clients || forced_) return true;
    if (healthy) {
      if (static_cast<uint32_t>(now - changedAt_) >= RECOVERY_GRACE_MS) wasRequired_ = false;
      return wasRequired_;
    }
    if (static_cast<uint32_t>(now - changedAt_) >= FAILURE_GRACE_MS) wasRequired_ = true;
    return wasRequired_;
  }

 private:
  bool observed_ = false, healthy_ = false, forced_ = false, wasRequired_ = false;
  uint32_t changedAt_ = 0, forcedAt_ = 0;
};

class ChannelSearchPolicy {
 public:
  static constexpr uint32_t LOST_MS = 6000;
  static constexpr uint32_t DWELL_MS = 2300;

  uint8_t next(uint32_t now, bool peerOnline, uint8_t current, unsigned apClients) {
    if (peerOnline || apClients) { lastStep_ = now; started_ = true; return 0; }
    if (!started_) { lastStep_ = now; started_ = true; return 0; }
    if (static_cast<uint32_t>(now - lastStep_) < DWELL_MS) return 0;
    lastStep_ = now;
    return current >= 13 || current < 1 ? 1 : static_cast<uint8_t>(current + 1);
  }

 private:
  bool started_ = false;
  uint32_t lastStep_ = 0;
};

}  // namespace aqua
