#pragma once

#include <Arduino.h>

namespace ledota {
void begin();
bool requestCheck();
void poll(bool applicationHealthy, bool canReboot);
String stateJson();
// Protect the complete ArduinoOTA.handle() call with this shared flash mutex.
bool tryLock();
void unlock();
bool busy();
}  // namespace ledota
