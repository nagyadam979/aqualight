#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace blelink {
constexpr size_t FRAME_LIMIT = 180, HEADER_SIZE = 10;
constexpr size_t REQUEST_LIMIT = 2048, RESPONSE_LIMIT = 6144;
constexpr uint32_t ASSEMBLY_MS = 15000, APPLICATION_MS = 10000, RESPONSE_MS = 30000;
struct Counters {
  uint32_t rxFrames = 0, rxInvalid = 0, rxBusy = 0, rxReplay = 0;
  uint32_t requests = 0, assemblyTimeouts = 0, applicationTimeouts = 0;
  uint32_t txFrames = 0, txRejected = 0, responseTimeouts = 0;
  uint32_t rxQueueDrops = 0, rxStale = 0, connections = 0, disconnects = 0;
};
struct Frame { uint8_t data[FRAME_LIMIT] = {}; size_t length = 0; };
inline uint16_t read16(const uint8_t* p) { return static_cast<uint16_t>(p[0] | (uint16_t(p[1]) << 8)); }
inline void write16(uint8_t* p, size_t v) { p[0] = static_cast<uint8_t>(v); p[1] = static_cast<uint8_t>(v >> 8); }

// One owner task, no allocation or application execution. IDs strictly increase
// within a connection (1..65535); reconnect before reuse. Reserving IDs at the
// first fragment prevents expired or rejected requests from being applied twice.
class Protocol {
 public:
  void reset(uint32_t generation) {
    generation_ = generation; highest_ = 0; clear();
  }
  void tick(uint32_t now) {
    if (phase_ == Phase::Assembly && uint32_t(now - started_) >= ASSEMBLY_MS) {
      ++counts_.assemblyTimeouts; clear();
    } else if ((phase_ == Phase::Ready || phase_ == Phase::Application) && uint32_t(now - started_) >= APPLICATION_MS) {
      ++counts_.applicationTimeouts; clear();
    } else if (phase_ == Phase::Response && uint32_t(now - started_) >= RESPONSE_MS) {
      ++counts_.responseTimeouts; clear();
    }
  }
  bool receive(const uint8_t* p, size_t n, uint32_t now) {
    return receive(p, n, now, now);
  }
  bool receive(const uint8_t* p, size_t n, uint32_t now, uint32_t receivedAt) {
    tick(now);
    if (!p || n < HEADER_SIZE || n > FRAME_LIMIT || p[0] != 1 || p[1] != 1) return invalid();
    const uint16_t id = read16(p + 2), offset = read16(p + 4), total = read16(p + 6), chunk = read16(p + 8);
    if (!id || !total || total > REQUEST_LIMIT || !chunk || n != HEADER_SIZE + size_t(chunk) ||
        offset >= total || size_t(chunk) > size_t(total - offset) || std::memchr(p + HEADER_SIZE, 0, chunk)) return invalid();
    if (phase_ != Phase::Idle && phase_ != Phase::Assembly) { ++counts_.rxBusy; return false; }
    if (phase_ == Phase::Idle) {
      if (id <= highest_) { ++counts_.rxReplay; return false; }
      if (offset != 0) return invalid();
      highest_ = id; id_ = id; total_ = total; used_ = 0; started_ = receivedAt; phase_ = Phase::Assembly;
    }
    if (id != id_ || total != total_ || offset != used_) return invalid();
    std::memcpy(request_ + used_, p + HEADER_SIZE, chunk); used_ += chunk; ++counts_.rxFrames;
    if (used_ == total_) { request_[used_] = 0; phase_ = Phase::Ready; started_ = now; }
    return true;
  }
  bool receiveRequest(char* dst, size_t capacity, size_t* length, uint16_t* id) {
    if (phase_ != Phase::Ready || !dst || capacity <= used_ || !length || !id) return false;
    std::memcpy(dst, request_, used_ + 1); *length = used_; *id = id_;
    phase_ = Phase::Application; ++counts_.requests; return true;
  }
  bool reply(uint16_t id, const char* json, size_t length, uint32_t now) {
    tick(now);
    if (phase_ != Phase::Application || id != id_ || !json || !length || length > RESPONSE_LIMIT || std::memchr(json, 0, length)) return false;
    std::memcpy(response_, json, length); total_ = static_cast<uint16_t>(length); used_ = 0;
    phase_ = Phase::Response; started_ = now; attempted_ = false; return true;
  }
  bool prepare(Frame& frame, size_t limit, uint32_t now) {
    tick(now);
    if (phase_ != Phase::Response || limit <= HEADER_SIZE ||
        (attempted_ && uint32_t(now - lastAttempt_) < retryMs_)) return false;
    limit = std::min(limit, FRAME_LIMIT);
    const size_t chunk = std::min(size_t(total_ - used_), limit - HEADER_SIZE);
    frame.data[0] = 1; frame.data[1] = 2;
    write16(frame.data + 2, id_); write16(frame.data + 4, used_);
    write16(frame.data + 6, total_); write16(frame.data + 8, chunk);
    std::memcpy(frame.data + HEADER_SIZE, response_ + used_, chunk);
    frame.length = HEADER_SIZE + chunk; prepared_ = static_cast<uint16_t>(chunk); return true;
  }
  void sent(bool accepted, uint32_t now) {
    if (phase_ != Phase::Response || !prepared_) return;
    attempted_ = true; lastAttempt_ = now; retryMs_ = accepted ? 5 : 20;
    if (accepted) { ++counts_.txFrames; used_ += prepared_; if (used_ == total_) clear(); }
    else ++counts_.txRejected;
    prepared_ = 0;
  }
  uint32_t generation() const { return generation_; }
  Counters counters() const { return counts_; }
 private:
  enum class Phase { Idle, Assembly, Ready, Application, Response };
  void clear() { phase_ = Phase::Idle; id_ = total_ = used_ = prepared_ = 0; attempted_ = false; }
  bool invalid() { ++counts_.rxInvalid; return false; }
  Phase phase_ = Phase::Idle;
  uint32_t generation_ = 0, started_ = 0, lastAttempt_ = 0, retryMs_ = 5;
  uint16_t highest_ = 0, id_ = 0, total_ = 0, used_ = 0, prepared_ = 0;
  bool attempted_ = false;
  char request_[REQUEST_LIMIT + 1] = {}, response_[RESPONSE_LIMIT] = {};
  Counters counts_;
};
}  // namespace blelink
