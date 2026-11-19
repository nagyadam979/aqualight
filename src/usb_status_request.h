#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace aqua {

// Physical USB receives exactly one read-only command: "status". A malformed
// or overlong line stays invalid until its terminator; no prefix truncation,
// line editing, command execution or dynamic storage is involved.
class UsbStatusRequest {
 public:
  static constexpr size_t MAX_LINE = 16;

  bool feed(uint8_t byte) {
    if (afterCr_) {
      afterCr_ = false;
      if (byte == '\n') return false;
    }
    if (byte == '\r' || byte == '\n') {
      const bool requested = !invalid_ && length_ == 6 && std::memcmp(line_, "status", 6) == 0;
      clearLine();
      afterCr_ = byte == '\r';
      return requested;
    }
    if (byte < 32 || byte > 126 || length_ == MAX_LINE) {
      invalid_ = true;
      return false;
    }
    if (!invalid_) line_[length_++] = static_cast<char>(byte);
    return false;
  }

  void reset() { clearLine(); afterCr_ = false; }

 private:
  char line_[MAX_LINE] = {};
  uint8_t length_ = 0;
  bool invalid_ = false;
  bool afterCr_ = false;

  void clearLine() {
    std::memset(line_, 0, sizeof(line_));
    length_ = 0;
    invalid_ = false;
  }
};

}  // namespace aqua
