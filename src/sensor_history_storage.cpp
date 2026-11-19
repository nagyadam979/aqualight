#if !LED_BLE_ONLY && (!defined(LED_CHANNELS) || LED_CHANNELS == 2)

#include "sensor_history_storage.h"

#include <LittleFS.h>
#include <esp_partition.h>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <unistd.h>

namespace aqua::history {
namespace {

constexpr char PARTITION[] = "spiffs";
constexpr char MOUNT_PATH[] = "/littlefs";
constexpr char TEMP_PATH[] = "/littlefs/sensor-history.tmp";
constexpr char CSV_HEADER[] =
    "window_start_utc,sample_count,temperature_mean_c,temperature_stddev_c,humidity_mean_pct,humidity_stddev_pct\r\n";

struct Hour {
  Record windows[WINDOWS_PER_HOUR];
  uint32_t start = 0;
};

bool partitionBlank() {
  const auto *partition = esp_partition_find_first(
      ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS, PARTITION);
  if (!partition) return false;
  uint8_t bytes[512];
  for (size_t offset = 0; offset < partition->size; offset += sizeof(bytes)) {
    const size_t count = std::min<size_t>(sizeof(bytes), partition->size - offset);
    if (esp_partition_read(partition, offset, bytes, count) != ESP_OK) return false;
    for (size_t i = 0; i < count; ++i) if (bytes[i] != 0xFF) return false;
  }
  return true;
}

void slotPath(uint32_t hour, char (&path)[40]) {
  std::snprintf(path, sizeof(path), "%s/sensor-%02u.bin", MOUNT_PATH, static_cast<unsigned>(hourSlot(hour)));
}

bool load(const char *path, uint32_t slot, Hour &data) {
  const int fd = open(path, O_RDONLY);
  if (fd < 0) return errno == ENOENT;
  for (size_t i = 0; i < WINDOWS_PER_HOUR; ++i) {
    Record record;
    const auto size = read(fd, &record, sizeof(record));
    if (size < 0) { close(fd); return false; }
    if (size != sizeof(record)) break;  // Ignore an incomplete trailing record.
    if (!valid(record) || hourSlot(hourStart(record.start)) != slot) continue;
    const uint32_t hour = hourStart(record.start);
    if (data.start != hour) {
      if (data.start > hour) continue;
      data = {};
      data.start = hour;
    }
    data.windows[(record.start - hour) / WINDOW_SECONDS] = record;
  }
  return close(fd) == 0;
}

}  // namespace

bool mount() {
  if (LittleFS.begin(false, MOUNT_PATH, 3, PARTITION)) return true;
  return partitionBlank() && LittleFS.format() && LittleFS.begin(false, MOUNT_PATH, 3, PARTITION);
}

bool save(Record record) {
  const uint32_t hour = hourStart(record.start);
  char path[40];
  slotPath(hour, path);
  Hour data;
  if (!load(path, hourSlot(hour), data)) return false;
  if (data.start > hour) return true;  // Do not evict newer data after a large backward time correction.
  if (data.start != hour) data = {};
  auto &window = data.windows[(record.start - hour) / WINDOW_SECONDS];
  if (window.count) merge(window, record);
  else {
    record.checksum = checksum(record);
    window = record;
  }
  // Each slot is at most six records (168 bytes). Commit the replacement only
  // after writing and syncing it so a reset during a write preserves the old hour.
  const int fd = open(TEMP_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return false;
  bool written = true;
  for (const auto &item : data.windows) {
    if (item.count && write(fd, &item, sizeof(item)) != sizeof(item)) {
      written = false;
      break;
    }
  }
  if (written) written = fsync(fd) == 0;
  if (close(fd) != 0) written = false;
  return written && rename(TEMP_PATH, path) == 0;
}

int Csv::read(char *output, size_t capacity) {
  if (!headerSent) {
    constexpr size_t length = sizeof(CSV_HEADER) - 1;
    if (capacity < length) return -1;
    std::memcpy(output, CSV_HEADER, length);
    headerSent = true;
    return length;
  }
  if (hour >= end) return 0;
  char path[40];
  slotPath(hour, path);
  Hour data;
  const uint32_t nextHour = hour + HOUR_SECONDS;
  if (!load(path, hourSlot(hour), data)) return -1;
  const bool matches = data.start == hour;
  hour = nextHour;
  size_t length = 0;
  if (matches) {
    for (const auto &record : data.windows) {
      if (!record.count || record.start < first || record.start >= end) continue;
      const time_t epoch = record.start;
      tm utc;
      char timestamp[21];
      if (!gmtime_r(&epoch, &utc) || !strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utc))
        return -1;
      const int count = std::snprintf(output + length, capacity - length, "%s,%u,%.3f,%.3f,%.3f,%.3f\r\n",
                                     timestamp, static_cast<unsigned>(record.count), record.temperatureMean,
                                     populationDeviation(record.temperatureM2, record.count), record.humidityMean,
                                     populationDeviation(record.humidityM2, record.count));
      if (count < 0 || static_cast<size_t>(count) >= capacity - length) return -1;
      length += count;
    }
  }
  return length ? static_cast<int>(length) : -2;
}

}  // namespace aqua::history

#endif
