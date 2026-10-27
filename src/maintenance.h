#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

class String;

namespace maintenance {

constexpr std::size_t MAX_LINE = 96;
constexpr uint32_t AUTH_TIMEOUT_MS = 10000;
constexpr uint8_t AUTH_ATTEMPTS = 3;

enum class Input { None, Line, Invalid };

// Bounded Telnet line decoder; negotiations/subnegotiations never become commands.
class LineReader {
 public:
  Input feed(uint8_t byte) {
    if (state_ == State::Option) { state_ = State::Data; return Input::None; }
    if (state_ == State::Sub) { if (byte == 255) state_ = State::SubIac; return Input::None; }
    if (state_ == State::SubIac) { state_ = byte == 240 ? State::Data : State::Sub; return Input::None; }
    if (state_ == State::Iac) {
      state_ = State::Data;
      if (byte >= 251 && byte <= 254) state_ = State::Option;
      else if (byte == 250) state_ = State::Sub;
      else if (byte == 255) invalid_ = true;  // No binary bytes in passwords/commands.
      return Input::None;
    }
    if (byte == 255) { state_ = State::Iac; return Input::None; }
    if (afterCr_) {
      afterCr_ = false;
      if (byte == '\n' || byte == 0) return Input::None;
    }
    if (byte == '\r' || byte == '\n') {
      afterCr_ = byte == '\r';
      line_[length_] = 0;
      return invalid_ ? Input::Invalid : Input::Line;
    }
    if (byte == 8 || byte == 127) {
      if (length_) line_[--length_] = 0;
    } else if (byte >= 32 && byte <= 126) {
      if (length_ < MAX_LINE) line_[length_++] = static_cast<char>(byte);
      else invalid_ = true;
    } else invalid_ = true;
    return Input::None;
  }
  const char *line() const { return line_; }
  void clearLine() { std::memset(line_, 0, sizeof(line_)); length_ = 0; invalid_ = false; }
  void reset() { clearLine(); afterCr_ = false; state_ = State::Data; }
 private:
  enum class State : uint8_t { Data, Iac, Option, Sub, SubIac };
  char line_[MAX_LINE + 1] = {};
  std::size_t length_ = 0;
  bool invalid_ = false;
  bool afterCr_ = false;
  State state_ = State::Data;
};

inline bool equalPassword(const char *line, const char *password) {
  const std::size_t a = std::strlen(line), b = std::strlen(password);
  std::size_t difference = a ^ b;
  for (std::size_t i = 0; i < MAX_LINE; ++i) {
    difference |= (i < a ? static_cast<uint8_t>(line[i]) : 0) ^
                  (i < b ? static_cast<uint8_t>(password[i]) : 0);
  }
  return difference == 0;
}

enum class Login { Accepted, Retry, Close };
class LoginGate {
 public:
  void reset(uint32_t now) { started_ = now; attempts_ = 0; authenticated_ = false; }
  bool authenticated() const { return authenticated_; }
  bool expired(uint32_t now) const {
    return !authenticated_ && static_cast<uint32_t>(now - started_) >= AUTH_TIMEOUT_MS;
  }
  Login attempt(const char *line, const char *password, uint32_t now) {
    if (expired(now) || attempts_ >= AUTH_ATTEMPTS) return Login::Close;
    if (equalPassword(line, password)) { authenticated_ = true; return Login::Accepted; }
    return ++attempts_ >= AUTH_ATTEMPTS ? Login::Close : Login::Retry;
  }
 private:
  uint32_t started_ = 0;
  uint8_t attempts_ = 0;
  bool authenticated_ = false;
};

enum class Command { Help, Status, OtaCheck, Reboot, Unknown };
inline Command command(const char *line) {
  while (*line == ' ') ++line;
  std::size_t length = std::strlen(line);
  while (length && line[length - 1] == ' ') --length;
  if (length == 4 && std::memcmp(line, "help", 4) == 0) return Command::Help;
  if (length == 6 && std::memcmp(line, "status", 6) == 0) return Command::Status;
  if (length == 9 && std::memcmp(line, "ota-check", 9) == 0) return Command::OtaCheck;
  if (length == 6 && std::memcmp(line, "reboot", 6) == 0) return Command::Reboot;
  return Command::Unknown;
}

using SaveCallback = bool (*)();
using StatusCallback = String (*)();
void begin(const String &password, SaveCallback save, StatusCallback status);
void poll();
String stateJson();
bool rebootRequested();
void rebootIfSafe(bool canReboot);

}  // namespace maintenance
