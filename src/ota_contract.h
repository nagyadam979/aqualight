#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

namespace ledota {
constexpr char HOST[] = "https://led-control-terrarium1.web.app";
constexpr char CHANNEL_URL[] = "https://led-control-terrarium1.web.app/channels/stable.json";
constexpr std::size_t MAX_IMAGE_SIZE = 0x1e0000;
constexpr std::size_t MAX_MANIFEST_SIZE = 2048;
constexpr std::size_t MAX_ENVELOPE_SIZE = 4096;

struct Manifest {
  unsigned schema = 0;
  std::string target, channel, version, releaseId, imagePath, sha256;
  uint64_t size = 0;
};

inline bool validIdentifier(const std::string &value, bool version = false) {
  if (value.empty() || value.size() > 64 || value.find("..") != std::string::npos) return false;
  for (std::size_t i = 0; i < value.size(); ++i) {
    const char c = value[i];
    const bool letter = (c >= 'a' && c <= 'z') || (version && c >= 'A' && c <= 'Z');
    if (!letter && !(c >= '0' && c <= '9') &&
        (i == 0 || (c != '.' && c != '_' && c != '-' && !(version && c == '+')))) return false;
  }
  return true;
}

inline bool validManifest(const Manifest &m) {
  if (m.schema != 1 || m.target != "led-control-esp32c3" || m.channel != "stable" ||
      !validIdentifier(m.version, true) || !validIdentifier(m.releaseId) ||
      m.imagePath != "/releases/" + m.releaseId + "/firmware.bin" ||
      m.size < 288 || m.size > MAX_IMAGE_SIZE || m.sha256.size() != 64) return false;
  for (char c : m.sha256) if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')) return false;
  return true;
}

// Validate the actual ESP32-C3 app prefix before erasing the inactive slot.
inline bool validImagePrefix(const uint8_t *bytes, std::size_t length) {
  return bytes && length >= 288 && bytes[0] == 0xe9 && bytes[1] >= 1 && bytes[1] <= 16 &&
      bytes[12] == 5 && bytes[13] == 0 && bytes[32] == 0x32 && bytes[33] == 0x54 &&
      bytes[34] == 0xcd && bytes[35] == 0xab;
}
}  // namespace ledota
