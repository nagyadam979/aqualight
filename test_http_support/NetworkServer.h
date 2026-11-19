#pragma once
#include "NetworkClient.h"
#include <cstdint>
NetworkClient fakeAccept();
class NetworkServer {
  bool started_ = false;
 public:
  explicit NetworkServer(uint16_t = 80, uint8_t = 4) {}
  void setNoDelay(bool) {}
  void begin() { started_ = true; }
  NetworkClient accept() { return fakeAccept(); }
  explicit operator bool() const { return started_; }
};
