#pragma once

#include "sensor_history.h"

namespace aqua::history {

bool mount();
bool save(Record record);

struct Csv {
  // The upper and lower cutoffs are fixed for the lifetime of the HTTP response.
  explicit Csv(uint32_t epoch)
      : first(windowStart(epoch) - RETENTION_SECONDS), end(windowStart(epoch)), hour(hourStart(first)) {}
  int read(char *output, size_t capacity);

 private:
  uint32_t first, end, hour;
  bool headerSent = false;
};

}  // namespace aqua::history
