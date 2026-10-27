#pragma once

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace aqua {

constexpr std::size_t MAX_POINTS = 16;
constexpr uint32_t DAY_SECONDS = 86400;
constexpr uint32_t PWM_FULL = 1024;  // Native 10-bit LEDC's constant-HIGH endpoint.
enum class Mode : uint8_t { Off, Manual, Auto };

struct Point { uint16_t minute; uint8_t warm; uint8_t cool; };
struct Settings {
  Mode mode = Mode::Off;
  uint8_t manualWarm = 0;
  uint8_t manualCool = 0;
  uint8_t count = 0;
  std::array<Point, MAX_POINTS> points{};
};
struct Levels { float warm = 0; float cool = 0; };

inline Settings defaults() {
  Settings result;
  result.count = 4;
  result.points[0] = {480, 0, 0};
  result.points[1] = {540, 50, 50};
  result.points[2] = {1020, 50, 50};
  result.points[3] = {1080, 0, 0};
  return result;
}

// Decimal only: reject signs, whitespace, overflow and incomplete fields.
inline bool number(const char *&text, uint64_t maximum, uint64_t &value) {
  if (!text || *text < '0' || *text > '9') return false;
  value = 0;
  while (*text >= '0' && *text <= '9') {
    const uint8_t digit = static_cast<uint8_t>(*text++ - '0');
    if (value > maximum / 10 || (value == maximum / 10 && digit > maximum % 10)) return false;
    value = value * 10 + digit;
  }
  return true;
}
inline bool integer(const char *text, uint64_t maximum, uint64_t &value) {
  return number(text, maximum, value) && *text == '\0';
}
inline bool valid(const Settings &settings) {
  if (static_cast<uint8_t>(settings.mode) > 2 || settings.manualWarm > 100 ||
      settings.manualCool > 100 || settings.count < 2 || settings.count > MAX_POINTS) return false;
  for (uint8_t i = 0; i < settings.count; ++i) {
    const auto &point = settings.points[i];
    if (point.minute >= 1440 || point.warm > 100 || point.cool > 100 ||
        (i && point.minute <= settings.points[i - 1].minute)) return false;
  }
  return true;
}
inline bool parseProgram(const char *text, Settings &settings) {
  Settings candidate = settings;
  candidate.count = 0;
  if (!text) return false;
  do {
    uint64_t minute, warm, cool;
    if (candidate.count == MAX_POINTS || !number(text, 1439, minute) || *text++ != ',' ||
        !number(text, 100, warm) || *text++ != ',' || !number(text, 100, cool)) return false;
    candidate.points[candidate.count++] = {static_cast<uint16_t>(minute),
                                         static_cast<uint8_t>(warm), static_cast<uint8_t>(cool)};
    if (*text == '\0') break;
    if (*text++ != ';' || *text == '\0') return false;
  } while (true);
  if (!valid(candidate)) return false;
  settings = candidate;  // Invalid input never partially changes the active program.
  return true;
}
inline bool parseSettings(const char *text, Settings &settings) {
  uint64_t version, mode, warm, cool;
  if (!text || !number(text, 1, version) || version != 1 || *text++ != ',' ||
      !number(text, 2, mode) || *text++ != ',' || !number(text, 100, warm) ||
      *text++ != ',' || !number(text, 100, cool) || *text++ != '|') return false;
  Settings candidate;
  candidate.mode = static_cast<Mode>(mode);
  candidate.manualWarm = static_cast<uint8_t>(warm);
  candidate.manualCool = static_cast<uint8_t>(cool);
  if (!parseProgram(text, candidate)) return false;
  settings = candidate;
  return true;
}
inline Levels interpolate(const Settings &settings, uint32_t secondOfDay) {
  if (!valid(settings)) return {};
  secondOfDay %= DAY_SECONDS;
  for (uint8_t i = 0; i < settings.count; ++i) {
    const auto &left = settings.points[i];
    const auto &right = settings.points[(i + 1) % settings.count];
    const uint32_t start = left.minute * 60UL;
    const uint32_t end = right.minute * 60UL + (i + 1 == settings.count ? DAY_SECONDS : 0);
    const uint32_t moment = secondOfDay < start ? secondOfDay + DAY_SECONDS : secondOfDay;
    if (moment < start || moment > end) continue;
    const float fraction = static_cast<float>(moment - start) / (end - start);
    return {left.warm + fraction * (static_cast<int>(right.warm) - left.warm),
            left.cool + fraction * (static_cast<int>(right.cool) - left.cool)};
  }
  return {};
}
inline Levels output(const Settings &settings, bool clockValid, uint32_t secondOfDay) {
  if (settings.mode == Mode::Manual) {
    return {static_cast<float>(settings.manualWarm), static_cast<float>(settings.manualCool)};
  }
  if (settings.mode == Mode::Auto && clockValid) return interpolate(settings, secondOfDay);
  return {};
}
inline uint32_t duty(float percent, bool activeLow) {
  if (!std::isfinite(percent) || percent < 0) percent = 0;
  if (percent > 100) percent = 100;
  const uint32_t value = static_cast<uint32_t>(std::lround(percent * PWM_FULL / 100));
  return activeLow ? PWM_FULL - value : value;
}
inline bool due(uint32_t now, uint32_t previous, uint32_t interval) {
  return static_cast<uint32_t>(now - previous) >= interval;
}

}  // namespace aqua
