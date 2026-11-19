#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace aqua::history {

constexpr uint32_t WINDOW_SECONDS = 600;
constexpr uint32_t HOUR_SECONDS = 3600;
constexpr uint32_t RETENTION_SECONDS = 48 * HOUR_SECONDS;
constexpr size_t WINDOWS_PER_HOUR = HOUR_SECONDS / WINDOW_SECONDS;
constexpr uint32_t HOUR_SLOTS = RETENTION_SECONDS / HOUR_SECONDS + 1;
constexpr uint32_t windowStart(uint32_t epoch) { return epoch - epoch % WINDOW_SECONDS; }
constexpr uint32_t hourStart(uint32_t epoch) { return epoch - epoch % HOUR_SECONDS; }
constexpr uint32_t hourSlot(uint32_t hour) { return hour / HOUR_SECONDS % HOUR_SLOTS; }

struct Record {
  uint32_t start = 0, count = 0;
  float temperatureMean = 0, temperatureM2 = 0, humidityMean = 0, humidityM2 = 0;
  uint32_t checksum = 0;
};
static_assert(sizeof(Record) == 28 && offsetof(Record, checksum) == 24);

inline uint32_t checksum(const Record &record) {
  const auto *bytes = reinterpret_cast<const unsigned char *>(&record);
  uint32_t crc = 0xFFFFFFFF;
  for (size_t i = 0; i < offsetof(Record, checksum); ++i) {
    crc ^= bytes[i];
    for (int bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ ((crc & 1) ? 0xEDB88320 : 0);
  }
  return ~crc;
}

inline bool valid(const Record &record) {
  return record.start && record.start == windowStart(record.start) && record.count &&
         std::isfinite(record.temperatureMean) && std::isfinite(record.humidityMean) &&
         std::isfinite(record.temperatureM2) && record.temperatureM2 >= 0 &&
         std::isfinite(record.humidityM2) && record.humidityM2 >= 0 &&
         record.checksum == checksum(record);
}

inline void accumulate(float value, uint32_t count, float &mean, float &m2) {
  const float delta = value - mean;
  mean += delta / count;
  m2 += delta * (value - mean);
}

inline void combine(float incomingMean, float incomingM2, uint32_t incomingCount,
                    uint32_t totalCount, float &mean, float &m2) {
  const float delta = incomingMean - mean;
  const float fraction = static_cast<float>(incomingCount) / totalCount;
  mean += delta * fraction;
  m2 += incomingM2 + delta * delta * (totalCount - incomingCount) * fraction;
}

inline void merge(Record &record, const Record &incoming) {
  record.count += incoming.count;
  combine(incoming.temperatureMean, incoming.temperatureM2, incoming.count, record.count,
          record.temperatureMean, record.temperatureM2);
  combine(incoming.humidityMean, incoming.humidityM2, incoming.count, record.count,
          record.humidityMean, record.humidityM2);
  record.checksum = checksum(record);
}

inline float populationDeviation(float m2, uint32_t count) { return std::sqrt(std::max(0.0f, m2) / count); }

struct Window {
  Record current;

  // The open window is owned by the sensor task. A backward clock adjustment
  // pauses accumulation until time catches up to that window again.
  Record advance(uint32_t epoch) {
    if (!current.count || windowStart(epoch) <= current.start) return {};
    const auto finished = current;
    current = {};
    return finished;
  }

  void sample(uint32_t epoch, float temperature, float humidity) {
    const uint32_t start = windowStart(epoch);
    if (current.count && start != current.start) return;
    current.start = start;
    ++current.count;
    accumulate(temperature, current.count, current.temperatureMean, current.temperatureM2);
    accumulate(humidity, current.count, current.humidityMean, current.humidityM2);
  }
};

}  // namespace aqua::history
