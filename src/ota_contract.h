#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

namespace ledota {
#ifndef LED_CHANNELS
#define LED_CHANNELS 2
#endif
constexpr char HOST[] = "https://led-control-terrarium1.web.app";
#if LED_CHANNELS == 1
// The single-channel board has its own target and channel file so firmware images cannot cross over.
constexpr char CHANNEL_URL[] = "https://led-control-terrarium1.web.app/channels/stable-1ch.json";
constexpr char TARGET[] = "led-control-esp32c3-1ch";
#else
constexpr char CHANNEL_URL[] = "https://led-control-terrarium1.web.app/channels/stable.json";
constexpr char TARGET[] = "led-control-esp32c3";
#endif
constexpr std::size_t MAX_IMAGE_SIZE = 0x1e0000;
constexpr std::size_t MAX_MANIFEST_SIZE = 2048;
constexpr std::size_t MAX_ENVELOPE_SIZE = 4096;

struct Manifest {
  unsigned schema = 0;
  std::string target, channel, version, releaseId, imagePath, sha256;
  uint64_t size = 0;
};

enum class VersionOrder { Invalid, Older, Equal, Newer };

// OTA releases use exactly three unsigned decimal components. Fail closed for
// prerelease labels, incomplete versions, overflow and ambiguous leading zeros.
inline bool parseVersion(const std::string &text, uint32_t (&parts)[3]) {
  std::size_t offset = 0;
  for (unsigned component = 0; component < 3; ++component) {
    const std::size_t start = offset;
    uint32_t value = 0;
    while (offset < text.size() && text[offset] >= '0' && text[offset] <= '9') {
      const unsigned digit = static_cast<unsigned>(text[offset++] - '0');
      if (value > UINT32_MAX / 10 || (value == UINT32_MAX / 10 && digit > UINT32_MAX % 10)) return false;
      value = value * 10 + digit;
    }
    if (offset == start || (offset - start > 1 && text[start] == '0')) return false;
    parts[component] = value;
    if (component < 2) {
      if (offset == text.size() || text[offset++] != '.') return false;
    } else if (offset != text.size()) return false;
  }
  return true;
}

inline VersionOrder compareVersions(const std::string &candidate, const std::string &current) {
  uint32_t next[3], running[3];
  if (!parseVersion(candidate, next) || !parseVersion(current, running)) return VersionOrder::Invalid;
  for (unsigned component = 0; component < 3; ++component) {
    if (next[component] < running[component]) return VersionOrder::Older;
    if (next[component] > running[component]) return VersionOrder::Newer;
  }
  return VersionOrder::Equal;
}

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
  if (m.schema != 1 || m.target != TARGET || m.channel != "stable" ||
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
