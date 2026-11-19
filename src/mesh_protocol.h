#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace boardlink {

constexpr size_t STATE_LIMIT = 4096;
constexpr size_t PATH_LIMIT = 48;
constexpr size_t FORM_LIMIT = 1536;
constexpr size_t ERROR_LIMIT = 192;
constexpr size_t FRAME_LIMIT = 1024;
constexpr size_t HEADER_SIZE = 32;
constexpr size_t CHUNK_SIZE = FRAME_LIMIT - HEADER_SIZE;
constexpr size_t COMMAND_LIMIT = 3 + PATH_LIMIT + FORM_LIMIT;
constexpr size_t ACK_LIMIT = ERROR_LIMIT + 7;
constexpr uint32_t ONLINE_MS = 6000;
constexpr uint32_t COMMAND_TIMEOUT_MS = 3000;
constexpr uint32_t COMMAND_RETRY_MS = 400;
constexpr uint32_t ASSEMBLY_TIMEOUT_MS = 2200;
constexpr uint32_t FRAME_GAP_MS = 25;

enum class CommandPhase : uint8_t { Unknown, Pending, Applied, Error, Timeout, PeerReboot };
struct CommandStatus {
  uint32_t id = 0;
  CommandPhase phase = CommandPhase::Unknown;
  int status = 404;
  uint32_t configRevision = 0;
  char error[ERROR_LIMIT + 1] = {};
};
struct ReceivedCommand {
  uint32_t id = 0;
  char path[PATH_LIMIT + 1] = {};
  char form[FORM_LIMIT + 1] = {};
};
struct Counters {
  uint32_t rxFrames = 0, rxInvalid = 0, rxDuplicate = 0, rxOldSession = 0;
  uint32_t rxStates = 0, rxCommands = 0, rxCommandDuplicates = 0, rxCommandsRejected = 0;
  uint32_t assemblyExpired = 0, txFrames = 0, txRejected = 0, commandTimeouts = 0;
  uint32_t peerReboots = 0, rxQueueDrops = 0, txMacAcked = 0, txMacFailed = 0;
  uint32_t txCallbackTimeouts = 0;
  uint32_t radioRecoveries = 0, radioFailures = 0;
  uint32_t rxStaleFrames = 0, stateExpired = 0;
};
struct Frame { uint8_t data[FRAME_LIMIT] = {}; size_t length = 0; };

// Fixed wire format, independent of struct padding and the ESP-IDF callback ABI.
// Frames are authenticated/encrypted by the paired ESP-NOW transport.
class Protocol {
 public:
  enum class Kind : uint8_t { Hello = 1, State = 2, Command = 3, Ack = 4 };

  bool begin(uint32_t bootId, uint8_t channels, bool master, uint32_t now) {
    if (!bootId || (channels != 1 && channels != 2) || master != (channels == 2)) return false;
    _boot = bootId; _channels = channels; _master = master; _now = now;
    _lastHello = now - 2000; _started = true;
    return true;
  }

  void tick(uint32_t now) {
    _now = now;
    if (_stateQueued && elapsed(now, _statePublishedAt) >= ASSEMBLY_TIMEOUT_MS) {
      _stateQueued = false; ++_counters.stateExpired;
    }
    if (_stateRx.active && elapsed(now, _stateRx.started) >= ASSEMBLY_TIMEOUT_MS) {
      _stateRx.active = false; ++_counters.assemblyExpired;
    }
    if (_commandRx.active && elapsed(now, _commandRx.started) >= ASSEMBLY_TIMEOUT_MS) {
      _commandRx.active = false; ++_counters.assemblyExpired;
    }
    if (_status.phase == CommandPhase::Pending && elapsed(now, _commandStarted) >= COMMAND_TIMEOUT_MS) {
      finish(CommandPhase::Timeout, 504, "peer_timeout"); ++_counters.commandTimeouts;
    }
  }

  bool publishState(const char* text, size_t length) {
    if (!_started || !text || !length || length > STATE_LIMIT || std::memchr(text, 0, length)) return false;
    std::memcpy(_stateTx, text, length); _stateTxLength = length; _stateTxOffset = 0;
    _stateTxId = next(_stateSequence); _stateQueued = true; _statePublishedAt = _now;
    return true;
  }

  uint32_t queueCommand(const char* path, const char* form) {
    if (!_started || !online(_now) || _status.phase == CommandPhase::Pending) return 0;
    const size_t pn = boundedLength(path, PATH_LIMIT), fn = boundedLength(form, FORM_LIMIT);
    if (pn == 0 || pn > PATH_LIMIT || fn > FORM_LIMIT || path[0] != '/') return 0;
    for (size_t i = 0; i < pn; ++i) if (path[i] <= 32 || static_cast<uint8_t>(path[i]) >= 127) return 0;
    _commandTx[0] = static_cast<uint8_t>(pn); put16(_commandTx + 1, static_cast<uint16_t>(fn));
    std::memcpy(_commandTx + 3, path, pn); std::memcpy(_commandTx + 3 + pn, form, fn);
    _commandTxLength = 3 + pn + fn; _commandTxOffset = 0; _commandRound = false;
    _commandTargetBoot = _peerBoot; _commandStarted = _now; _commandRetryAt = _now;
    if (_status.id && _status.phase != CommandPhase::Unknown)
      _history[_historyNext++ % 4] = _status;
    _status = CommandStatus(); _status.id = next(_commandSequence);
    _status.phase = CommandPhase::Pending; _status.status = 202;
    return _status.id;
  }

  CommandStatus commandStatus(uint32_t id) const {
    if (id && _status.id == id) return _status;
    for (const auto& previous : _history) if (id && previous.id == id) return previous;
    CommandStatus unknown; unknown.id = id;
    std::strcpy(unknown.error, "unknown_command");
    return unknown;
  }

  bool receiveCommand(ReceivedCommand& out) {
    if (!_incomingActive || _incomingDelivered) return false;
    out = _incoming; _incomingDelivered = true; return true;
  }

  bool replyCommand(uint32_t id, int status, const char* error = "", uint32_t revision = 0) {
    const size_t n = boundedLength(error, ERROR_LIMIT);
    if (!_incomingActive || !_incomingDelivered || id != _incoming.id || n > ERROR_LIMIT ||
        status < 200 || status > 599) return false;
    put16(_cachedAck, static_cast<uint16_t>(status)); put32(_cachedAck + 2, revision);
    _cachedAck[6] = static_cast<uint8_t>(n);
    std::memcpy(_cachedAck + 7, error, n); _cachedAckLength = 7 + n;
    _hasCachedAck = true; _incomingActive = false;
    queueAck(id, _cachedAck, _cachedAckLength);
    return true;
  }

  bool receiveState(char* destination, size_t capacity, size_t* length) {
    if (!_stateReady || !destination || capacity <= _receivedStateLength) return false;
    std::memcpy(destination, _receivedState, _receivedStateLength);
    destination[_receivedStateLength] = 0;
    if (length) *length = _receivedStateLength;
    _stateReady = false; return true;
  }

  bool online(uint32_t now) const { return _peerBoot && elapsed(now, _lastRx) < ONLINE_MS; }
  uint32_t ageMs(uint32_t now) const { return _peerBoot ? elapsed(now, _lastRx) : UINT32_MAX; }
  uint32_t peerBoot() const { return _peerBoot; }
  uint32_t localBoot() const { return _boot; }
  Counters counters() const { return _counters; }
  void transportReset(uint32_t now) {
    tick(now);
    _prepared = Prepared::None;
    _commandRound = false; _commandRetryAt = now;
    if (_stateQueued) _stateTxOffset = 0;
    _lastHello = now - 2000;
  }

  void receive(const uint8_t* data, size_t length, uint32_t now) {
    tick(now);
    Header h;
    if (!_started || !decode(data, length, h) || h.channels == _channels || h.master == _master) {
      ++_counters.rxInvalid; return;
    }
    if ((h.kind == Kind::Command || h.kind == Kind::Ack) ? h.destinationBoot != _boot : h.destinationBoot != 0) {
      ++_counters.rxOldSession; return;
    }
    if (retired(h.boot)) { ++_counters.rxOldSession; return; }
    if (h.boot != _peerBoot) changePeer(h.boot);
    if (!acceptSequence(h.sequence)) {
      ++_counters.rxDuplicate;
      if (h.kind == Kind::Command && h.id == _lastCommandId && _hasCachedAck)
        queueAck(h.id, _cachedAck, _cachedAckLength);
      return;
    }
    _lastRx = now; ++_counters.rxFrames;
    const uint8_t* payload = data + HEADER_SIZE;
    if (h.kind == Kind::Hello) return;
    if (h.kind == Kind::Ack) { acceptAck(h, payload); return; }
    if (h.kind == Kind::State) {
      if (_stateHaveId && !newer(h.id, _stateLatestId)) {
        if (h.id != _stateLatestId || !_stateRx.active) return;
      } else {
        _stateHaveId = true; _stateLatestId = h.id;
        _stateRx.start(h.id, h.total, now);
      }
      if (!_stateRx.add(h, payload)) { ++_counters.rxInvalid; return; }
      if (_stateRx.complete()) {
        if (!std::memchr(_stateRx.bytes, 0, h.total)) {
          std::memcpy(_receivedState, _stateRx.bytes, h.total); _receivedStateLength = h.total;
          _stateReady = true; ++_counters.rxStates;
        } else ++_counters.rxInvalid;
        _stateRx.active = false;
      }
      return;
    }
    if (_hasCommandId && !newer(h.id, _lastCommandId)) {
      if (h.id == _lastCommandId) {
        ++_counters.rxCommandDuplicates;
        if (_hasCachedAck) queueAck(h.id, _cachedAck, _cachedAckLength);
      } else ++_counters.rxCommandsRejected;
      return;
    }
    if (_incomingActive) {
      uint8_t busy[19]; put16(busy, 409); put32(busy + 2, 0); busy[6] = 12; std::memcpy(busy + 7, "command_busy", 12);
      queueAck(h.id, busy, sizeof(busy)); ++_counters.rxCommandsRejected; return;
    }
    if (!_commandRx.active || newer(h.id, _commandRx.id)) _commandRx.start(h.id, h.total, now);
    if (h.id != _commandRx.id || !_commandRx.add(h, payload)) { ++_counters.rxInvalid; return; }
    if (_commandRx.complete()) acceptCommand(h.id);
  }

  // Produces at most one datagram. Caller must immediately report whether the
  // transport accepted it; MAC completion is a separate transport concern.
  bool prepareFrame(uint32_t now, Frame& frame) {
    tick(now);
    if (!_started || _prepared != Prepared::None || (_haveAttempt && elapsed(now, _lastAttempt) < FRAME_GAP_MS)) return false;
    const uint8_t* body = nullptr; size_t total = 0, offset = 0; uint32_t id = 0, target = 0;
    Kind kind = Kind::Hello;
    if (_ackPending && _peerBoot) {
      _prepared = Prepared::Ack; kind = Kind::Ack; body = _ackTx; total = _ackTxLength;
      id = _ackTxId; target = _peerBoot;
    } else if (_status.phase == CommandPhase::Pending && (_commandRound || reached(now, _commandRetryAt))) {
      if (!_commandRound) { _commandRound = true; _commandTxOffset = 0; }
      _prepared = Prepared::Command; kind = Kind::Command; body = _commandTx;
      total = _commandTxLength; offset = _commandTxOffset; id = _status.id; target = _commandTargetBoot;
    } else if (_stateQueued) {
      _prepared = Prepared::State; kind = Kind::State; body = reinterpret_cast<const uint8_t*>(_stateTx);
      total = _stateTxLength; offset = _stateTxOffset; id = _stateTxId;
    } else if (elapsed(now, _lastHello) >= 2000) {
      _prepared = Prepared::Hello;
    } else return false;
    const size_t count = total - offset < CHUNK_SIZE ? total - offset : CHUNK_SIZE;
    Header h{kind, _boot, target, id, next(_packetSequence), static_cast<uint16_t>(total),
             static_cast<uint16_t>(offset), static_cast<uint16_t>(count), _channels, _master};
    encode(h, frame.data);
    if (count) std::memcpy(frame.data + HEADER_SIZE, body + offset, count);
    frame.length = HEADER_SIZE + count; _preparedCount = count; _preparedId = id;
    _lastAttempt = now; _haveAttempt = true;
    return true;
  }

  void sent(bool accepted) {
    if (_prepared == Prepared::None) return;
    if (!accepted) ++_counters.txRejected;
    else {
      ++_counters.txFrames;
      if (_prepared == Prepared::Ack && _ackTxId == _preparedId) _ackPending = false;
      if (_prepared == Prepared::Hello) _lastHello = _now;
      if (_prepared == Prepared::State && _stateTxId == _preparedId) {
        _stateTxOffset += _preparedCount;
        if (_stateTxOffset == _stateTxLength) _stateQueued = false;
      }
      if (_prepared == Prepared::Command && _status.phase == CommandPhase::Pending && _status.id == _preparedId) {
        _commandTxOffset += _preparedCount;
        if (_commandTxOffset == _commandTxLength) { _commandRound = false; _commandRetryAt = _now + COMMAND_RETRY_MS; }
      }
    }
    _prepared = Prepared::None;
  }

 private:
  struct Header {
    Kind kind; uint32_t boot, destinationBoot, id, sequence;
    uint16_t total, offset, size; uint8_t channels; bool master;
  };
  enum class Prepared : uint8_t { None, Hello, State, Command, Ack };
  template <size_t Capacity> struct Assembly {
    bool active = false; uint32_t id = 0, started = 0; uint16_t total = 0; uint8_t mask = 0;
    uint8_t bytes[Capacity] = {};
    void start(uint32_t messageId, uint16_t length, uint32_t now) {
      active = true; id = messageId; total = length; started = now; mask = 0;
    }
    bool add(const Header& h, const uint8_t* payload) {
      if (!active || h.id != id || h.total != total || h.offset + h.size > Capacity) return false;
      const uint8_t bit = static_cast<uint8_t>(1u << (h.offset / CHUNK_SIZE));
      if (mask & bit) return std::memcmp(bytes + h.offset, payload, h.size) == 0;
      std::memcpy(bytes + h.offset, payload, h.size); mask |= bit; return true;
    }
    bool complete() const {
      const unsigned chunks = (total + CHUNK_SIZE - 1) / CHUNK_SIZE;
      return active && mask == static_cast<uint8_t>((1u << chunks) - 1);
    }
  };
  static uint32_t elapsed(uint32_t now, uint32_t before) { return now - before; }
  static bool reached(uint32_t now, uint32_t deadline) { return static_cast<int32_t>(now - deadline) >= 0; }
  static bool newer(uint32_t a, uint32_t b) { return static_cast<int32_t>(a - b) > 0; }
  static uint32_t next(uint32_t& value) { if (!++value) ++value; return value; }
  static size_t boundedLength(const char* text, size_t limit) {
    if (!text) return limit + 1;
    size_t n = 0; while (n <= limit && text[n]) ++n; return n;
  }
  static uint16_t get16(const uint8_t* p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }
  static uint32_t get32(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
        (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
  }
  static void put16(uint8_t* p, uint16_t v) { p[0] = v; p[1] = v >> 8; }
  static void put32(uint8_t* p, uint32_t v) { for (unsigned i = 0; i < 4; ++i) p[i] = v >> (8 * i); }
  static void encode(const Header& h, uint8_t* p) {
    std::memcpy(p, "AQLK", 4); p[4] = 1; p[5] = static_cast<uint8_t>(h.kind); p[6] = p[7] = 0;
    put32(p + 8, h.boot); put32(p + 12, h.destinationBoot); put32(p + 16, h.id); put32(p + 20, h.sequence);
    put16(p + 24, h.total); put16(p + 26, h.offset); put16(p + 28, h.size); p[30] = h.channels; p[31] = h.master;
  }
  static bool decode(const uint8_t* p, size_t length, Header& h) {
    if (!p || length < HEADER_SIZE || length > FRAME_LIMIT || std::memcmp(p, "AQLK", 4) ||
        p[4] != 1 || p[5] < 1 || p[5] > 4 || p[6] || p[7] || (p[30] != 1 && p[30] != 2) || p[31] > 1) return false;
    h = Header{static_cast<Kind>(p[5]), get32(p + 8), get32(p + 12), get32(p + 16), get32(p + 20),
               get16(p + 24), get16(p + 26), get16(p + 28), p[30], p[31] != 0};
    if (!h.boot || !h.sequence || h.size + HEADER_SIZE != length || h.offset % CHUNK_SIZE) return false;
    if (h.kind == Kind::Hello) return !h.id && !h.total && !h.offset && !h.size;
    const size_t limit = h.kind == Kind::State ? STATE_LIMIT : h.kind == Kind::Command ? COMMAND_LIMIT : ACK_LIMIT;
    if (!h.id || !h.total || h.total > limit || h.offset >= h.total || !h.size) return false;
    const size_t remaining = h.total - h.offset;
    if (h.size != (remaining < CHUNK_SIZE ? remaining : CHUNK_SIZE)) return false;
    return h.kind != Kind::Ack || (!h.offset && h.total >= 7);
  }
  bool retired(uint32_t boot) const {
    for (uint32_t previous : _retiredBoots) if (previous && previous == boot) return true;
    return false;
  }
  void changePeer(uint32_t boot) {
    if (_peerBoot) {
      _retiredBoots[_retiredIndex++ % 4] = _peerBoot; ++_counters.peerReboots;
      // An application ACK may have arrived before the caller received the
      // matching state snapshot. Its revision belongs to the old boot too.
      if (_status.phase == CommandPhase::Pending || _status.phase == CommandPhase::Applied)
        finish(CommandPhase::PeerReboot, 409, "peer_reboot");
      for (auto& previous : _history) {
        if (previous.phase == CommandPhase::Applied) {
          previous.phase = CommandPhase::PeerReboot; previous.status = 409; previous.configRevision = 0;
          std::strcpy(previous.error, "peer_reboot");
        }
      }
    }
    _peerBoot = boot; _haveRxSequence = false; _rxSeen = 0;
    _stateRx.active = _commandRx.active = false; _stateHaveId = false; _stateReady = false;
    _hasCommandId = _incomingActive = _incomingDelivered = _hasCachedAck = _ackPending = false;
  }
  bool acceptSequence(uint32_t sequence) {
    if (!_haveRxSequence) { _haveRxSequence = true; _rxHighest = sequence; _rxSeen = 1; return true; }
    const int32_t ahead = static_cast<int32_t>(sequence - _rxHighest);
    if (ahead > 0) {
      _rxSeen = ahead >= 64 ? 1 : (_rxSeen << ahead) | 1; _rxHighest = sequence; return true;
    }
    const uint32_t behind = _rxHighest - sequence;
    if (behind >= 64 || (_rxSeen & (UINT64_C(1) << behind))) return false;
    _rxSeen |= UINT64_C(1) << behind; return true;
  }
  void finish(CommandPhase phase, int status, const char* error) {
    _status.phase = phase; _status.status = status;
    _status.configRevision = 0;
    std::strncpy(_status.error, error, ERROR_LIMIT); _status.error[ERROR_LIMIT] = 0;
    _commandRound = false;
  }
  void queueAck(uint32_t id, const uint8_t* bytes, size_t length) {
    _ackTxId = id; _ackTxLength = length; std::memcpy(_ackTx, bytes, length); _ackPending = true;
  }
  void acceptAck(const Header& h, const uint8_t* body) {
    if (h.total < 7 || body[6] != h.total - 7 || std::memchr(body + 7, 0, body[6])) { ++_counters.rxInvalid; return; }
    const int status = get16(body);
    if (status < 200 || status > 599) { ++_counters.rxInvalid; return; }
    if (_status.phase != CommandPhase::Pending || h.id != _status.id || h.boot != _commandTargetBoot) return;
    char error[ERROR_LIMIT + 1]; std::memcpy(error, body + 7, body[6]); error[body[6]] = 0;
    finish(status < 300 ? CommandPhase::Applied : CommandPhase::Error, status, error);
    _status.configRevision = get32(body + 2);
  }
  void acceptCommand(uint32_t id) {
    _commandRx.active = false;
    const uint8_t* body = _commandRx.bytes;
    const size_t pn = body[0], fn = _commandRx.total >= 3 ? get16(body + 1) : FORM_LIMIT + 1;
    if (_commandRx.total < 3 || !pn || pn > PATH_LIMIT || fn > FORM_LIMIT || 3 + pn + fn != _commandRx.total ||
        body[3] != '/' || std::memchr(body + 3, 0, pn + fn)) { ++_counters.rxInvalid; return; }
    for (size_t i = 0; i < pn; ++i) if (body[3 + i] <= 32 || body[3 + i] >= 127) { ++_counters.rxInvalid; return; }
    _incoming = ReceivedCommand(); _incoming.id = id;
    std::memcpy(_incoming.path, body + 3, pn); std::memcpy(_incoming.form, body + 3 + pn, fn);
    _lastCommandId = id; _hasCommandId = true; _incomingActive = true; _incomingDelivered = false;
    _hasCachedAck = false; ++_counters.rxCommands;
  }

  bool _started = false, _master = false; uint8_t _channels = 0;
  uint32_t _boot = 0, _peerBoot = 0, _now = 0, _lastRx = 0, _lastHello = 0;
  uint32_t _retiredBoots[4] = {}; uint8_t _retiredIndex = 0;
  uint32_t _packetSequence = 0, _stateSequence = 0, _commandSequence = 0;
  bool _haveRxSequence = false; uint32_t _rxHighest = 0; uint64_t _rxSeen = 0;
  char _stateTx[STATE_LIMIT] = {}; size_t _stateTxLength = 0, _stateTxOffset = 0;
  uint32_t _stateTxId = 0, _statePublishedAt = 0; bool _stateQueued = false;
  Assembly<STATE_LIMIT> _stateRx; bool _stateHaveId = false; uint32_t _stateLatestId = 0;
  char _receivedState[STATE_LIMIT] = {}; size_t _receivedStateLength = 0; bool _stateReady = false;
  uint8_t _commandTx[COMMAND_LIMIT] = {}; size_t _commandTxLength = 0, _commandTxOffset = 0;
  uint32_t _commandTargetBoot = 0, _commandStarted = 0, _commandRetryAt = 0; bool _commandRound = false;
  CommandStatus _status, _history[4]; uint8_t _historyNext = 0; Assembly<COMMAND_LIMIT> _commandRx;
  ReceivedCommand _incoming; bool _incomingActive = false, _incomingDelivered = false;
  uint32_t _lastCommandId = 0; bool _hasCommandId = false, _hasCachedAck = false;
  uint8_t _cachedAck[ACK_LIMIT] = {}; size_t _cachedAckLength = 0;
  uint8_t _ackTx[ACK_LIMIT] = {}; size_t _ackTxLength = 0; uint32_t _ackTxId = 0; bool _ackPending = false;
  Prepared _prepared = Prepared::None; size_t _preparedCount = 0; uint32_t _preparedId = 0;
  bool _haveAttempt = false; uint32_t _lastAttempt = 0;
  Counters _counters;
};

}  // namespace boardlink
