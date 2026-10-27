#include <Arduino.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <Update.h>
#include <WiFi.h>
#include <esp_ota_ops.h>
#include <lwip/sockets.h>
#include <cerrno>

#include "maintenance.h"
#include "cloud_update.h"

#ifndef LED_CONTROL_VERSION
#define LED_CONTROL_VERSION "1.1.0"
#endif

namespace maintenance {
namespace {
constexpr uint32_t TX_TIMEOUT_MS = 5000;
constexpr uint32_t IDLE_TIMEOUT_MS = 300000;
constexpr uint32_t AUTH_COOLDOWN_MS = 30000;
constexpr std::size_t TX_CAPACITY = 1536;
NetworkServer telnet(23, 1);
NetworkClient client;
LineReader reader;
LoginGate login;
String secret;
SaveCallback saveCallback = nullptr;
StatusCallback statusCallback = nullptr;
bool telnetReady = false;
bool otaReady = false;
bool localInProgress = false;
bool ownsOtaLock = false;
bool pendingReboot = false;
uint32_t requestedRebootAt = 0;
uint32_t lastActivity = 0;
uint32_t lastTxProgress = 0;
bool authBlocked = false;
uint32_t blockedAt = 0;
char tx[TX_CAPACITY] = {};
std::size_t txLength = 0;

bool elapsed(uint32_t now, uint32_t previous, uint32_t duration) {
  return static_cast<uint32_t>(now - previous) >= duration;
}
void disconnect() {
  client.stop();
  reader.reset();
  login.reset(0);
  std::memset(tx, 0, sizeof(tx));
  txLength = 0;
}
bool queueBytes(const void *data, std::size_t length) {
  if (length > TX_CAPACITY - txLength) { disconnect(); return false; }
  if (!txLength) lastTxProgress = millis();
  std::memcpy(tx + txLength, data, length);
  txLength += length;
  return true;
}
void queue(const char *text) { queueBytes(text, std::strlen(text)); }
void queue(const String &text) { queueBytes(text.c_str(), text.length()); }
void sendPending(uint32_t now) {
  if (!txLength || client.fd() < 0) return;
  const std::size_t count = txLength < 256 ? txLength : 256;
  // NetworkClient::write can wait on a non-reading peer; use a bounded socket send.
  const int sent = ::send(client.fd(), tx, count, MSG_DONTWAIT);
  if (sent > 0) {
    txLength -= sent;
    std::memmove(tx, tx + sent, txLength);
    lastTxProgress = now;
  } else if ((sent < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) ||
             elapsed(now, lastTxProgress, TX_TIMEOUT_MS)) disconnect();
}
void requestReboot() {
  pendingReboot = true;
  requestedRebootAt = millis();
}
void handleLine(Input input, uint32_t now) {
  if (!login.authenticated()) {
    const Login result = login.attempt(input == Input::Line ? reader.line() : "", secret.c_str(), now);
    reader.clearLine();  // Never retain or echo the supplied password.
    if (result == Login::Accepted) {
      const uint8_t localEcho[] = {255, 252, 1};  // WONT ECHO: restore client-side command echo.
      queueBytes(localEcho, sizeof(localEcho));
      queue("\r\nAuthenticated. Commands: help, status, ota-check, reboot\r\n> ");
    } else if (result == Login::Close) {
      authBlocked = true;
      blockedAt = now;
      disconnect();
    } else queue("\r\nAuthentication failed. Password: ");
    return;
  }
  if (input == Input::Invalid) {
    reader.clearLine(); queue("ERR invalid or overlong line\r\n> "); return;
  }
  const Command action = command(reader.line());
  reader.clearLine();
  switch (action) {
    case Command::Help:
      queue("help      Commands\r\nstatus    Read-only device/update status\r\nota-check Request signed cloud firmware check\r\nreboot    Restart after pending settings are saved\r\n"); break;
    case Command::Status:
      if (statusCallback) queue(statusCallback());
      queue("\r\n"); break;
    case Command::OtaCheck:
      queue(ledota::requestCheck() ? "OTA check queued\r\n" : "ERR OTA check unavailable, busy or offline\r\n"); break;
    case Command::Reboot:
      requestReboot(); queue("Reboot queued; waiting for safe settings storage\r\n"); break;
    default: queue("ERR unknown command; use help\r\n"); break;
  }
  queue("> ");
}
void pollTelnet(uint32_t now) {
  if (!telnetReady) return;
  NetworkClient incoming = telnet.accept();
  if (incoming) {
    if (authBlocked && elapsed(now, blockedAt, AUTH_COOLDOWN_MS)) authBlocked = false;
    if (client.connected() || authBlocked) incoming.stop();
    else {
      disconnect();
      client = incoming;
      client.setNoDelay(true);
      login.reset(now);
      lastActivity = now;
      const uint8_t hideEcho[] = {255, 251, 1};  // WILL ECHO, but never echo the password.
      queueBytes(hideEcho, sizeof(hideEcho));
      queue("LED Control " LED_CONTROL_VERSION "\r\nPassword: ");
    }
  }
  if (!client.connected()) { if (client.fd() >= 0) disconnect(); return; }
  if (login.expired(now) || elapsed(now, lastActivity, IDLE_TIMEOUT_MS)) { disconnect(); return; }
  unsigned budget = 128;
  while (budget-- && client.connected() && client.available()) {
    const int value = client.read();
    if (value < 0) break;
    lastActivity = now;
    const Input input = reader.feed(static_cast<uint8_t>(value));
    if (input != Input::None) handleLine(input, now);
  }
  sendPending(now);
}
}  // namespace

void begin(const String &password, SaveCallback save, StatusCallback status) {
  saveCallback = save;
  statusCallback = status;
  if (password.length() < 8 || password.length() > 63 || !save) {
    Serial.println("[MAINT] Disabled: missing valid authentication/storage callback.");
    return;
  }
  secret = password;
  telnet.begin();
  telnet.setNoDelay(true);
  telnetReady = static_cast<bool>(telnet);
  if (esp_ota_get_next_update_partition(nullptr)) {
    ArduinoOTA.setHostname("led-control");
    ArduinoOTA.setPort(3232);
    ArduinoOTA.setPassword(password.c_str());
    ArduinoOTA.setMdnsEnabled(false);  // Main already owns the led-control mDNS responder.
    ArduinoOTA.setRebootOnSuccess(false);
    ArduinoOTA.setTimeout(1000);
    ArduinoOTA.onStart([] {
      localInProgress = true;
      if (ArduinoOTA.getCommand() != U_FLASH || !saveCallback()) {
        Update.abort();
        localInProgress = false;
        Serial.println("[OTA] Local update aborted: application image/storage requirement.");
      } else Serial.println("[OTA] Authenticated local update started.");
    });
    ArduinoOTA.onEnd([] {
      localInProgress = false;
      requestReboot();
      Serial.println("[OTA] Local image verified; reboot pending safe storage.");
    });
    ArduinoOTA.onError([](ota_error_t error) {
      localInProgress = false;
      Serial.printf("[OTA] Local update failed, code=%u.\n", static_cast<unsigned>(error));
    });
    ArduinoOTA.begin();  // Native API has no public bind-success result.
    MDNS.enableArduino(3232, true);
    otaReady = true;
  }
  Serial.printf("[MAINT] ArduinoOTA=%s port3232; Telnet=%s port23 (authenticated).\n",
                otaReady ? "enabled" : "unavailable", telnetReady ? "listening" : "FAILED");
}

void poll() {
  pollTelnet(millis());
  if (!otaReady || ownsOtaLock || pendingReboot || !ledota::tryLock()) return;
  ArduinoOTA.handle();  // Update.begin happens before onStart, so lock the entire call.
  if (pendingReboot) ownsOtaLock = true;  // Preserve the selected image until main's safe reboot.
  else ledota::unlock();
}

String stateJson() {
  String json = "{\"version\":\"" LED_CONTROL_VERSION "\",\"localOtaReady\":";
  json += otaReady ? "true" : "false";
  json += ",\"telnetReady\":"; json += telnetReady ? "true" : "false";
  json += ",\"localOtaBusy\":"; json += (localInProgress || ownsOtaLock) ? "true" : "false";
  json += ",\"rebootPending\":"; json += pendingReboot ? "true" : "false";
  json += '}';
  return json;
}

bool rebootRequested() { return pendingReboot; }

void rebootIfSafe(bool canReboot) {
  if (pendingReboot && canReboot && (ownsOtaLock || !ledota::busy()) &&
      elapsed(millis(), requestedRebootAt, 300)) {
    Serial.println("[MAINT] Safe reboot.");
    ESP.restart();
  }
}
}  // namespace maintenance
