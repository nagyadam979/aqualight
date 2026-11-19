#!/usr/bin/env python3
"""Exercise the production aggregation and flash record format on the host."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
CHECKS = r'''
#include <cassert>
#include <cmath>
#include "sensor_history.h"

using namespace aqua::history;

bool near(float actual, float expected) {
  return std::fabs(actual - expected) < 0.0001f;
}

int main() {
  constexpr uint32_t hour = hourStart(1704072578);
  constexpr uint32_t start = hour + 4 * WINDOW_SECONDS;
  static_assert(hourSlot(hour) == hourSlot(hour + HOUR_SLOTS * HOUR_SECONDS));
  static_assert(hourSlot(hour) != hourSlot(hour + RETENTION_SECONDS));

  Window window;
  assert(window.advance(start).count == 0);
  window.sample(start + 5, 20, 40);
  window.sample(start + 590, 24, 50);
  assert(window.advance(start + 599).count == 0);
  auto record = window.advance(start + 600);
  assert(record.start == start && record.count == 2);
  assert(near(record.temperatureMean, 22) && near(record.humidityMean, 45));
  assert(near(populationDeviation(record.temperatureM2, record.count), 2));
  assert(near(populationDeviation(record.humidityM2, record.count), 5));
  assert(window.advance(start + 3 * WINDOW_SECONDS).count == 0);

  record.checksum = checksum(record);
  assert(valid(record));
  auto corrupted = record;
  corrupted.temperatureMean += 0.25f;
  assert(!valid(corrupted));

  // After a clock correction or restart, an additional fragment may occupy
  // the same hour slot and window. Combining the aggregates retains variance.
  window.sample(start + 500, 26, 60);
  const auto single = window.advance(start + WINDOW_SECONDS);
  assert(single.count == 1 && populationDeviation(single.temperatureM2, 1) == 0);
  merge(record, single);
  assert(valid(record) && record.count == 3);
  assert(near(record.temperatureMean, (20.f + 24 + 26) / 3));
  assert(near(populationDeviation(record.temperatureM2, record.count), std::sqrt(56.f / 9)));
  assert(near(record.humidityMean, 50));
  assert(near(populationDeviation(record.humidityM2, record.count), std::sqrt(200.f / 3)));

  constexpr uint32_t later = start + 9 * WINDOW_SECONDS;
  window.sample(later, 21, 31);
  assert(!window.advance(later - 1).count);
  window.sample(later - 1, 85, 100);
  const auto afterCorrection = window.advance(later + WINDOW_SECONDS);
  assert(afterCorrection.start == later && afterCorrection.count == 1);
  assert(afterCorrection.temperatureMean == 21 && afterCorrection.humidityMean == 31);
}
'''

STORAGE_CHECKS = r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <LittleFS.h>
#include <esp_partition.h>
#include "sensor_history_storage.h"

using namespace aqua::history;

LittleFSDouble LittleFS;
bool hasData = false, readable = true;
bool LittleFSDouble::begin(bool formatOnFail, const char *, int, const char *) {
  assert(!formatOnFail);
  return formats > 0;
}
bool LittleFSDouble::format() { ++formats; return true; }
const esp_partition_t *esp_partition_find_first(int, int, const char *) {
  static constexpr esp_partition_t partition{128 * 1024};
  return &partition;
}
int esp_partition_read(const esp_partition_t *, size_t offset, void *out, size_t count) {
  if (!readable) return -1;
  std::memset(out, 0xff, count);
  if (hasData && offset + count == 128 * 1024) static_cast<char *>(out)[count - 1] = 0;
  return 0;
}

std::string download(uint32_t epoch) {
  Csv csv(epoch);
  std::string output;
  char bytes[512];
  for (unsigned polls = 0; polls <= HOUR_SLOTS + 1; ++polls) {
    const int size = csv.read(bytes, sizeof(bytes));
    assert(size != -1);
    if (!size) return output;
    if (size > 0) output.append(bytes, size);
  }
  assert(false && "CSV exceeded bounded number of reads");
  return {};
}

int main() {
  readable = false;
  assert(!mount() && LittleFS.formats == 0);
  readable = true;
  hasData = true;
  assert(!mount() && LittleFS.formats == 0);
  hasData = false;
  assert(mount() && LittleFS.formats == 1);
  assert(mount() && LittleFS.formats == 1);

  constexpr uint32_t start = 1704067200;  // 2024-01-01T00:00:00Z
  assert(save({start, 2, 20, 8, 50, 2}));
  assert(save({start + 1200, 1, 35, 0, 75, 0}));
  assert(save({start + 600, 1, 25, 0, 55, 0}));
  const std::string first = "2024-01-01T00:00:00Z,2,20.000,2.000,50.000,1.000\r\n";
  const std::string second = "2024-01-01T00:10:00Z,1,25.000,0.000,55.000,0.000\r\n";
  const std::string third = "2024-01-01T00:20:00Z,1,35.000,0.000,75.000,0.000\r\n";
  std::string output = download(start + 1800);
  assert(output.substr(output.find('\n') + 1) == first + second + third);
  assert(download(start + 1200 + RETENTION_SECONDS).find(first) == std::string::npos);
  assert(download(start + 1200 + RETENTION_SECONDS).find(third) != std::string::npos);

  // An interrupted replacement leaves only a temporary file. A damaged
  // trailing record in an existing slot does not prevent earlier valid rows.
  std::ofstream("./littlefs/sensor-history.tmp") << "interrupted";
  char slot[40];
  std::snprintf(slot, sizeof(slot), "./littlefs/sensor-%02u.bin", static_cast<unsigned>(hourSlot(start)));
  {
    std::fstream file(slot, std::ios::in | std::ios::out | std::ios::binary);
    file.seekg(offsetof(Record, checksum));
    const int checksumByte = file.get();
    assert(checksumByte != std::char_traits<char>::eof());
    file.seekp(offsetof(Record, checksum));
    file.put(static_cast<char>(checksumByte ^ 0xff));  // Invalidate the first CRC.
    assert(file.good());
  }
  std::ofstream(slot, std::ios::binary | std::ios::app) << "partial record";
  output = download(start + 1800);
  assert(output.substr(output.find('\n') + 1) == second + third);
  assert(save({start + 1800, 1, 27, 0, 57, 0}));
  output = download(start + 2400);
  assert(output.find("2024-01-01T00:30:00Z,1,27.000,0.000,57.000,0.000") != std::string::npos);

  constexpr auto later = start + HOUR_SLOTS * HOUR_SECONDS;
  assert(save({later, 1, 29, 0, 59, 0}));
  assert(save({start, 1, 29, 0, 59, 0}));  // Late data cannot evict a newer hour.
  assert(download(later + WINDOW_SECONDS).find("2024-01-03T01:00:00Z,1,29.000") != std::string::npos);
  output = download(start + 2400);
  assert(output.find('\n') + 1 == output.size());
}
'''

with tempfile.TemporaryDirectory(prefix="aqualight-history-") as directory:
    temporary = Path(directory)
    source = temporary / "check.cpp"
    binary = source.with_suffix("")
    source.write_text(CHECKS)
    subprocess.run([
        os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-I", str(ROOT / "src"), str(source), "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)
    print("PASS sensor history: UTC alignment, partial windows, gaps, population statistics, clock correction, CRC and rotation")
    # Use the real POSIX storage operations in an isolated directory. Stub
    # only the ESP partition and LittleFS mounting APIs.
    (temporary / "littlefs").mkdir()
    (temporary / "LittleFS.h").write_text("""
struct LittleFSDouble {
  int formats = 0;
  bool begin(bool, const char *, int, const char *);
  bool format();
};
extern LittleFSDouble LittleFS;
""")
    (temporary / "esp_partition.h").write_text("""
#include <cstddef>
#include <cstdint>
struct esp_partition_t { uint32_t size; };
constexpr int ESP_PARTITION_TYPE_DATA = 0, ESP_PARTITION_SUBTYPE_DATA_SPIFFS = 0, ESP_OK = 0;
const esp_partition_t *esp_partition_find_first(int, int, const char *);
int esp_partition_read(const esp_partition_t *, size_t, void *, size_t);
""")
    storage = temporary / "sensor_history_storage.cpp"
    storage.write_text((ROOT / "src/sensor_history_storage.cpp").read_text().replace('"/littlefs', '"./littlefs'))
    source.write_text(STORAGE_CHECKS)
    subprocess.run([
        os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-I", str(ROOT / "src"), "-I", str(temporary), str(source), str(storage), "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], cwd=temporary, check=True)
    print("PASS sensor history storage: erased-only formatting, chronological CSV, retention boundary, interrupted writes, corrupt records and rollover")
