#pragma once

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace aqua {

constexpr std::size_t MAX_POINTS = 16;
constexpr uint32_t DAY_SECONDS = 86400;
// Stay below the C3's 14-bit maximum so 2^PWM_BITS is a valid constant-HIGH endpoint.
constexpr uint8_t PWM_BITS = 13;
constexpr uint32_t PWM_FULL = 1UL << PWM_BITS;
// Measured first visible light on the previous gamma-2 scale (8%, 10%, 9%).
constexpr uint32_t PWM_MIN_WARM = 52;
constexpr uint32_t PWM_MIN_COOL = 82;
constexpr uint32_t PWM_MIN_SINGLE = 66;
constexpr uint32_t PWM_CALIBRATION_HZ = 1000;
constexpr uint32_t PWM_TERRARIUM_HZ = 1000;
constexpr uint8_t PWM_TERRARIUM_BITS = 13;
constexpr uint32_t PWM_TERRARIUM_FULL = 1UL << PWM_TERRARIUM_BITS;
constexpr uint32_t PWM_TERRARIUM_SCALE = PWM_TERRARIUM_FULL / PWM_FULL;
constexpr uint32_t PWM_AT_FIVE_WARM = PWM_MIN_WARM * PWM_TERRARIUM_SCALE;
constexpr uint32_t PWM_AT_FIVE_COOL = PWM_MIN_COOL * PWM_TERRARIUM_SCALE;
// Restore the 1.2.2 curve: the main LEDs visibly started at the 5% anchors.
// Keep the electrical low-level PWM range for the user's auxiliary LED wiring.
constexpr uint32_t PWM_START_WARM = 1;
constexpr uint32_t PWM_START_COOL = 1;
enum class Mode : uint8_t { Off, Manual, Auto };

struct Point { uint16_t minute; uint8_t level; };
struct Curve {
  uint8_t count = 0;
  std::array<Point, MAX_POINTS> points{};
};
struct Settings {
  Mode mode = Mode::Off;
  uint8_t manualWarm = 0;
  uint8_t manualCool = 0;
  Curve warm;
  Curve cool;
};
struct Levels { float warm = 0; float cool = 0; };

inline Settings defaults() {
  Settings result;
  result.warm.count = 4;
  result.warm.points[0] = {480, 0};
  result.warm.points[1] = {540, 50};
  result.warm.points[2] = {1020, 50};
  result.warm.points[3] = {1080, 0};
  result.cool = result.warm;
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
inline bool valid(const Curve &curve) {
  if (curve.count < 2 || curve.count > MAX_POINTS) return false;
  for (uint8_t i = 0; i < curve.count; ++i) {
    const auto &point = curve.points[i];
    if (point.minute >= 1440 || point.level > 100 ||
        (i && point.minute <= curve.points[i - 1].minute)) return false;
  }
  return true;
}
inline bool valid(const Settings &settings) {
  return static_cast<uint8_t>(settings.mode) <= 2 && settings.manualWarm <= 100 &&
         settings.manualCool <= 100 && valid(settings.warm) && valid(settings.cool);
}
inline bool parseCurveUntil(const char *&text, char end, Curve &curve) {
  Curve candidate;
  if (!text) return false;
  do {
    uint64_t minute, level;
    if (candidate.count == MAX_POINTS || !number(text, 1439, minute) || *text++ != ',' ||
        !number(text, 100, level)) return false;
    candidate.points[candidate.count++] = {static_cast<uint16_t>(minute), static_cast<uint8_t>(level)};
    if (*text == end) break;
    if (*text++ != ';' || *text == end) return false;
  } while (true);
  if (!valid(candidate)) return false;
  curve = candidate;
  return true;
}
inline bool parseCurve(const char *text, Curve &curve) {
  return parseCurveUntil(text, '\0', curve);
}
inline bool parseProgram(const char *warm, const char *cool, Settings &settings) {
  Settings candidate = settings;
  if (!parseCurve(warm, candidate.warm) || !parseCurve(cool, candidate.cool)) return false;
  settings = candidate;  // Neither channel changes unless both curves are valid.
  return true;
}
inline bool parseSettings(const char *text, Settings &settings) {
  uint64_t version, mode, warm, cool;
  if (!text || !number(text, 2, version) || version != 2 || *text++ != ',' ||
      !number(text, 2, mode) || *text++ != ',' || !number(text, 100, warm) ||
      *text++ != ',' || !number(text, 100, cool) || *text++ != '|') return false;
  Settings candidate;
  candidate.mode = static_cast<Mode>(mode);
  candidate.manualWarm = static_cast<uint8_t>(warm);
  candidate.manualCool = static_cast<uint8_t>(cool);
  if (!parseCurveUntil(text, '|', candidate.warm) || *text++ != '|' ||
      !parseCurveUntil(text, '\0', candidate.cool)) return false;
  settings = candidate;
  return true;
}
inline float interpolate(const Curve &curve, uint32_t secondOfDay) {
  if (!valid(curve)) return 0;
  secondOfDay %= DAY_SECONDS;
  for (uint8_t i = 0; i < curve.count; ++i) {
    const auto &left = curve.points[i];
    const auto &right = curve.points[(i + 1) % curve.count];
    const uint32_t start = left.minute * 60UL;
    const uint32_t end = right.minute * 60UL + (i + 1 == curve.count ? DAY_SECONDS : 0);
    const uint32_t moment = secondOfDay < start ? secondOfDay + DAY_SECONDS : secondOfDay;
    if (moment < start || moment > end) continue;
    const float fraction = static_cast<float>(moment - start) / (end - start);
    return left.level + fraction * (static_cast<int>(right.level) - left.level);
  }
  return 0;
}
inline Levels output(const Settings &settings, bool clockValid, uint32_t secondOfDay) {
  if (settings.mode == Mode::Manual) {
    return {static_cast<float>(settings.manualWarm), static_cast<float>(settings.manualCool)};
  }
  if (settings.mode == Mode::Auto && clockValid) {
    return {interpolate(settings.warm, secondOfDay), interpolate(settings.cool, secondOfDay)};
  }
  return {};
}
inline uint32_t duty(float percent, bool activeLow) {
  if (!std::isfinite(percent) || percent < 0) percent = 0;
  if (percent > 100) percent = 100;
  // Gamma 2: concentrate the 100 control steps near OFF. Apply after schedule
  // interpolation so manual and automatic levels share the same smooth curve.
  const float normalized = percent / 100.0f;
  const uint32_t value = static_cast<uint32_t>(std::lround(normalized * normalized * PWM_FULL));
  return activeLow ? PWM_FULL - value : value;
}
inline uint32_t calibratedDuty(float percent, bool activeLow, uint32_t minimum) {
  if (!std::isfinite(percent) || percent < 0) percent = 0;
  if (percent > 100) percent = 100;
  if (minimum > PWM_FULL) minimum = PWM_FULL;
  // 0 is genuinely OFF; 1 is the measured first visible level. Fractional
  // automatic levels below 1 ramp toward that threshold instead of jumping.
  const float fraction = percent > 1 ? (percent - 1) / 99.0f : 0;
  const float raw = percent < 1 ? minimum * percent :
      minimum + (PWM_FULL - minimum) * fraction * fraction;
  const uint32_t value = static_cast<uint32_t>(std::lround(raw));
  return activeLow ? PWM_FULL - value : value;
}
inline uint32_t fineLowDuty(float percent, bool activeLow, uint32_t atFive, uint32_t atOne,
                            uint32_t full = PWM_FULL) {
  if (!std::isfinite(percent) || percent < 0) percent = 0;
  if (percent > 100) percent = 100;
  if (atFive < 1) atFive = 1;
  if (atFive > full) atFive = full;
  if (atOne < 1) atOne = 1;
  if (atOne > atFive) atOne = atFive;
  // Map the electrical low range from atOne to atFive. The main LEDs need
  // not emit visible light at every value below the measured 5% anchors.
  float raw;
  if (percent <= 1) raw = atOne * percent;
  else if (percent <= 5) {
    const float fraction = (percent - 1) / 4.0f;
    raw = atOne + (atFive - atOne) * fraction * fraction;
  } else {
    const float fraction = (percent - 5) / 95.0f;
    raw = atFive + (full - atFive) * fraction * fraction;
  }
  const uint32_t value = static_cast<uint32_t>(std::lround(raw));
  return activeLow ? full - value : value;
}
inline bool due(uint32_t now, uint32_t previous, uint32_t interval) {
  return static_cast<uint32_t>(now - previous) >= interval;
}

}  // namespace aqua
