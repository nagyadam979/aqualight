#include <Arduino.h>
#include <AHTxx.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <Wire.h>
#include <driver/gpio.h>
#include <driver/ledc.h>
#include <esp_sntp.h>
#include <esp_attr.h>
#include <esp_system.h>
#include <soc/soc_caps.h>
#include <atomic>
#include <cmath>
#include <cstring>
#include <ctime>
#include <sys/time.h>

#include "schedule.h"
#include "wifi_recovery.h"
#include "cloud_update.h"
#include "maintenance.h"
#include "web_ui.h"

#ifndef LED_CHANNELS
#define LED_CHANNELS 2
#endif
#if LED_CHANNELS == 2
// Confirmed wiring: full-spectrum purple (blue + red) -> GPIO0, cool 6500 K -> GPIO4, AHT20 SDA 1 / SCL 3.
constexpr uint8_t PWM_PINS[] = {0, 4};
constexpr bool PWM_ACTIVE_LOW[] = {false, false};
constexpr uint32_t PWM_MIN_DUTY[] = {aqua::PWM_AT_FIVE_WARM, aqua::PWM_AT_FIVE_COOL};
constexpr uint32_t PWM_START_DUTY[] = {aqua::PWM_START_WARM, aqua::PWM_START_COOL};
constexpr int I2C_SDA = 1;
constexpr int I2C_SCL = 3;
constexpr int WARM_PIN = 0;
constexpr int COOL_PIN = 4;
#elif LED_CHANNELS == 1
// Single-channel board: one PWM output on GPIO4, AHT20 SDA 1 / SCL 0.
// The data model keeps two curves; only the first ("warm") curve drives the output.
constexpr uint8_t PWM_PINS[] = {4};
constexpr bool PWM_ACTIVE_LOW[] = {false};
constexpr uint32_t PWM_MIN_DUTY[] = {aqua::PWM_MIN_SINGLE};
constexpr int I2C_SDA = 1;
constexpr int I2C_SCL = 0;
constexpr int WARM_PIN = 4;
constexpr int COOL_PIN = -1;
#else
#error "LED_CHANNELS must be 1 or 2"
#endif
constexpr uint32_t PWM_FREQUENCY_HZ = LED_CHANNELS == 2 ? aqua::PWM_TERRARIUM_HZ : aqua::PWM_CALIBRATION_HZ;
constexpr uint8_t PWM_BITS = LED_CHANNELS == 2 ? aqua::PWM_TERRARIUM_BITS : aqua::PWM_BITS;
constexpr uint32_t PWM_FULL = 1UL << PWM_BITS;
static_assert(PWM_BITS <= SOC_LEDC_TIMER_BIT_WIDTH, "PWM resolution exceeds the hardware maximum");
#if LED_CHANNELS == 2
// Startup uses duty=0. A different board with active-low hardware needs a
// separate glitch-free startup path before enabling maximum-width LEDC.
static_assert(PWM_BITS < SOC_LEDC_TIMER_BIT_WIDTH || (!PWM_ACTIVE_LOW[0] && !PWM_ACTIVE_LOW[1]),
              "14-bit startup requires active-high outputs");
#endif
constexpr char TIMEZONE[] = "CET-1CEST,M3.5.0/2,M10.5.0/3";
constexpr uint64_t MIN_EPOCH = 1704067200ULL;  // 2024-01-01 UTC.
constexpr uint64_t MAX_EPOCH = 4102444799ULL;  // 2099-12-31 UTC.
constexpr uint32_t STA_ATTEMPT_MS = 15000;
constexpr uint32_t STA_RETRY_MIN_MS = 30000;
constexpr uint32_t STA_RETRY_MAX_MS = 120000;
constexpr uint32_t RECOVERY_NVS_RETRY_MS = 30000;
#if !CONFIG_ESP_TIME_FUNCS_USE_RTC_TIMER
#error "Wi-Fi recovery clock preservation requires the RTC system timer"
#endif
#ifndef LED_WIFI_TX_POWER
// SuperMini association failed at the driver's default power; lower-power board calibration.
#define LED_WIFI_TX_POWER WIFI_POWER_8_5dBm
#endif

WebServer server(80);
Preferences preferences;
AHTxx aht20(AHTXX_ADDRESS_X38, AHT2x_SENSOR);
aqua::Settings settings = aqua::defaults();
aqua::Levels actual;
bool pwmOk = false;
bool pwmAttached[2] = {false, false};
bool preferencesOpen = false;
bool serverStarted = false;
bool storageOk = false;
bool settingsDirty = false;
uint32_t lastManualChange = 0;
uint32_t lastSaveAttempt = 0;
bool lastSaveFailed = false;
bool sensorReady = false;
bool sensorOk = false;
bool haveSample = false;
float temperature = NAN;
float humidity = NAN;
uint32_t lastSensor = 0;
uint32_t lastSample = 0;
uint32_t lastOutput = 0;
enum class TimeSource : uint8_t { None, Browser, Ntp, Rtc };
std::atomic<TimeSource> timeSource{TimeSource::None};
String apSsid;
String apPassword;
bool apActive = false;
bool wasConnected = false;
bool wifiPending = false;
uint32_t wifiRequested = 0;
uint32_t lastWifiAttempt = 0;
bool staAttemptActive = false;
bool staConfigDisconnecting = false;
uint32_t staAttemptStarted = 0;
uint32_t staRetryDelay = 0;
uint32_t staNextRetryDelay = STA_RETRY_MIN_MS;
std::atomic<int> lastStaReason{-1};
std::atomic<int> lastStaRssi{0};
uint64_t uptimeMs = 0;
uint32_t previousMillis = 0;
aqua::WifiRecovery wifiRecovery;
bool recoveryStorageError = false;
bool recoveryWriteAttempted = false;
uint32_t recoveryLastWrite = 0;
RTC_NOINIT_ATTR uint32_t wifiRecoveryClockMarker;

struct WifiCredentials {
  uint8_t version = 1;
  char ssid[33] = {};
  char password[64] = {};
};
WifiCredentials wifi;

const char *modeName(aqua::Mode mode) {
  if (mode == aqua::Mode::Auto) return "auto";
  if (mode == aqua::Mode::Manual) return "manual";
  return "off";
}

void stopPwm() {
  for (uint8_t i = 0; i < LED_CHANNELS; ++i) {
    if (pwmAttached[i]) {
      ledc_stop(LEDC_LOW_SPEED_MODE, static_cast<ledc_channel_t>(i), PWM_ACTIVE_LOW[i] ? 1 : 0);
      pwmAttached[i] = false;
    }
    // Arduino 3 digitalWrite refuses unregistered GPIOs; preload the latch natively.
    gpio_set_level(static_cast<gpio_num_t>(PWM_PINS[i]), PWM_ACTIVE_LOW[i] ? 1 : 0);
    pinMode(PWM_PINS[i], OUTPUT);
  }
  actual = {};
  pwmOk = false;
}

bool writePwmValue(uint8_t channelIndex, uint32_t value) {
  if (value > PWM_FULL) return false;
  const auto channel = static_cast<ledc_channel_t>(channelIndex);
  // At the C3's maximum resolution FULL is forbidden in the duty register.
  // Force physical endpoints instead; update_duty re-enables PWM afterwards.
  if (value == 0 || value == PWM_FULL) {
    return ledc_stop(LEDC_LOW_SPEED_MODE, channel, value == PWM_FULL ? 1 : 0) == ESP_OK;
  }
  return ledc_set_duty(LEDC_LOW_SPEED_MODE, channel, value) == ESP_OK &&
      ledc_update_duty(LEDC_LOW_SPEED_MODE, channel) == ESP_OK;
}

bool startPwm() {
  stopPwm();
  ledc_timer_config_t timer = {};
  timer.speed_mode = LEDC_LOW_SPEED_MODE;
  timer.duty_resolution = static_cast<ledc_timer_bit_t>(PWM_BITS);
  timer.timer_num = LEDC_TIMER_0;
  timer.freq_hz = PWM_FREQUENCY_HZ;
  timer.clk_cfg = LEDC_AUTO_CLK;
  if (ledc_timer_config(&timer) != ESP_OK) return false;
  for (uint8_t i = 0; i < LED_CHANNELS; ++i) {
    ledc_channel_config_t channel = {};
    channel.gpio_num = PWM_PINS[i];
    channel.speed_mode = LEDC_LOW_SPEED_MODE;
    channel.channel = static_cast<ledc_channel_t>(i);
    channel.intr_type = LEDC_INTR_DISABLE;
    channel.timer_sel = LEDC_TIMER_0;
    channel.duty = aqua::duty(0, PWM_ACTIVE_LOW[i]);
    if (ledc_channel_config(&channel) != ESP_OK) {
      stopPwm();
      return false;
    }
    pwmAttached[i] = true;
  }
  return true;
}

bool readClock(time_t &epoch, tm &local) {
  epoch = time(nullptr);
  return timeSource.load() != TimeSource::None && epoch >= static_cast<time_t>(MIN_EPOCH) &&
         epoch <= static_cast<time_t>(MAX_EPOCH) && localtime_r(&epoch, &local) != nullptr;
}

uint32_t outputDuty(float level, uint8_t channel) {
#if LED_CHANNELS == 2
  return aqua::fineLowDuty(level, PWM_ACTIVE_LOW[channel], PWM_MIN_DUTY[channel], PWM_START_DUTY[channel], PWM_FULL);
#else
  return aqua::calibratedDuty(level, PWM_ACTIVE_LOW[channel], PWM_MIN_DUTY[channel]);
#endif
}

void applyOutput() {
  if (!pwmOk) return;
  time_t epoch;
  tm local = {};
  const bool valid = readClock(epoch, local);
  aqua::Levels desired = aqua::output(settings, valid,
      local.tm_hour * 3600UL + local.tm_min * 60UL + local.tm_sec);
#if LED_CHANNELS == 1
  desired.cool = 0;  // No second output on this board.
#endif
  const float values[] = {desired.warm, desired.cool};
  const float previous[] = {actual.warm, actual.cool};
  for (uint8_t i = 0; i < LED_CHANNELS; ++i) {
    const uint32_t value = outputDuty(values[i], i);
    if (value == outputDuty(previous[i], i)) continue;
    if (!writePwmValue(i, value)) {
      stopPwm();
      Serial.println("[PWM] WRITE FAILED: all outputs held OFF.");
      return;
    }
  }
  actual = desired;
}

void appendCurve(String &text, const aqua::Curve &curve) {
  for (uint8_t i = 0; i < curve.count; ++i) {
    if (i) text += ';';
    text += curve.points[i].minute;
    text += ','; text += curve.points[i].level;
  }
}

String encodeSettings(const aqua::Settings &value) {
  String text = "2,";
  text += static_cast<unsigned>(value.mode);
  text += ','; text += value.manualWarm;
  text += ','; text += value.manualCool;
  text += '|'; appendCurve(text, value.warm);
  text += '|'; appendCurve(text, value.cool);
  return text;
}

bool saveSettings(const aqua::Settings &value) {
  const String encoded = encodeSettings(value);
  storageOk = preferencesOpen && preferences.putString("settings", encoded) == encoded.length();
  if (storageOk) lastSaveFailed = false;
  if (!storageOk) Serial.println("[NVS] Settings save failed.");
  return storageOk;
}

bool flushPendingSettings() {
  if (!settingsDirty) return preferencesOpen && storageOk;
  const uint32_t now = millis();
  if (lastSaveFailed && !aqua::due(now, lastSaveAttempt, 30000)) return false;
  lastSaveAttempt = now;
  lastSaveFailed = !saveSettings(settings);
  if (lastSaveFailed) return false;
  settingsDirty = false;
  return true;
}

bool saveAndApply(const aqua::Settings &candidate) {
  if (!saveSettings(candidate)) return false;
  settings = candidate;
  settingsDirty = false;
  applyOutput();
  return true;
}

void loadSettings() {
  preferencesOpen = preferences.begin("aqualight", false);
  storageOk = preferencesOpen;
  if (!preferencesOpen) {
    wifiRecovery.begin(aqua::WIFI_RECOVERY_MAX_RESTARTS);
    Serial.println("[NVS] Cannot open storage; defaults active, lamp OFF.");
    return;
  }
  const uint8_t restarts = preferences.isKey("net_reboots") ? preferences.getUChar("net_reboots", 255) : 0;
  wifiRecovery.begin(restarts);
  recoveryStorageError = restarts > aqua::WIFI_RECOVERY_MAX_RESTARTS;
  if (preferences.isKey("settings")) {
    const String stored = preferences.getString("settings");
    if (stored.length() > 512 || stored.length() != strlen(stored.c_str()) ||
        !aqua::parseSettings(stored.c_str(), settings)) {
      settings = aqua::defaults();
      storageOk = false;
      Serial.println("[NVS] Invalid settings; defaults active, lamp OFF.");
    }
  }
  if (preferences.isKey("wifi") && preferences.getBytesLength("wifi") == sizeof(wifi)) {
    WifiCredentials stored;
    if (preferences.getBytes("wifi", &stored, sizeof(stored)) == sizeof(stored) && stored.version == 1 &&
        std::memchr(stored.ssid, 0, sizeof(stored.ssid)) &&
        std::memchr(stored.password, 0, sizeof(stored.password))) {
      const size_t ssidLength = strlen(stored.ssid);
      const size_t passwordLength = strlen(stored.password);
      if (ssidLength >= 1 && ssidLength <= 32 &&
          (passwordLength == 0 || (passwordLength >= 8 && passwordLength <= 63))) wifi = stored;
    }
  }
  if (preferences.isKey("ap_pass")) apPassword = preferences.getString("ap_pass");
}

void applyWifiPower() {
  const bool applied = WiFi.setTxPower(LED_WIFI_TX_POWER);
  Serial.printf("[NET] TX power requested=%.2f dBm actual=%.2f dBm (%s).\n",
                static_cast<int>(LED_WIFI_TX_POWER) / 4.0, static_cast<int>(WiFi.getTxPower()) / 4.0,
                applied ? "set" : "FAILED");
}

void startStaAttempt(uint32_t now) {
  staAttemptActive = true;
  wasConnected = false;
  staAttemptStarted = now;
  WiFi.begin(wifi.ssid, wifi.password);  // Async; caller waits until the old connection is down.
  applyWifiPower();
  Serial.printf("[NET] STA attempt, timeout=%lu s; AP channel=%u clients=%u.\n",
                static_cast<unsigned long>(STA_ATTEMPT_MS / 1000), WiFi.channel(), WiFi.softAPgetStationNum());
}

void scheduleStaRetry(uint32_t now) {
  WiFi.disconnectAsync(false, false);  // Leave AP and stored credentials untouched; stop station scanning.
  staAttemptActive = false;
  lastWifiAttempt = now;
  staRetryDelay = staNextRetryDelay;
  staNextRetryDelay = staNextRetryDelay < STA_RETRY_MAX_MS / 2 ? staNextRetryDelay * 2 : STA_RETRY_MAX_MS;
  Serial.printf("[NET] STA paused; retry in %lu s; AP channel=%u clients=%u.\n",
                static_cast<unsigned long>(staRetryDelay / 1000), WiFi.channel(), WiFi.softAPgetStationNum());
}

void startNetwork() {
  WiFi.persistent(false);
  WiFi.setHostname(LED_HOSTNAME);
  WiFi.setAutoReconnect(false);  // Native reconnect events have no backoff and can starve the shared AP radio.
  WiFi.onEvent([](arduino_event_id_t event, arduino_event_info_t info) {
    if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      lastStaReason.store(info.wifi_sta_disconnected.reason);
      lastStaRssi.store(info.wifi_sta_disconnected.rssi);
      Serial.printf("[NET] STA disconnected, reason=%u rssi=%d dBm.\n",
                    static_cast<unsigned>(info.wifi_sta_disconnected.reason), info.wifi_sta_disconnected.rssi);
    }
  });
  WiFi.mode(WIFI_AP_STA);  // Enable RF entropy before creating the first AP password.
#ifdef LED_AP_PASSWORD
  // Fixed, build-time AP/Telnet/OTA password (WPA2 needs 8-63 characters).
  static_assert(sizeof(LED_AP_PASSWORD) - 1 >= 8 && sizeof(LED_AP_PASSWORD) - 1 <= 63,
                "LED_AP_PASSWORD must be 8-63 characters");
  apPassword = LED_AP_PASSWORD;
#else
  if (apPassword.length() != 12) {
    constexpr char alphabet[] = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
    apPassword = "";
    for (uint8_t i = 0; i < 12; ++i) apPassword += alphabet[esp_random() % (sizeof(alphabet) - 1)];
    if (!preferencesOpen || preferences.putString("ap_pass", apPassword) != apPassword.length()) storageOk = false;
  }
#endif
  const uint64_t mac = ESP.getEfuseMac();
  char suffix[7];
  snprintf(suffix, sizeof(suffix), "%02X%02X%02X", static_cast<unsigned>((mac >> 24) & 0xff),
           static_cast<unsigned>((mac >> 32) & 0xff), static_cast<unsigned>((mac >> 40) & 0xff));
  apSsid = String("AquaLight-") + suffix;
  apActive = WiFi.softAP(apSsid.c_str(), apPassword.c_str(), 1, false, 4);
  Serial.printf("[NET] AP SSID: %s\n", apSsid.c_str());
  Serial.printf("[NET] AP password: %s\n", apPassword.c_str());
  Serial.printf("[NET] AP URL: http://%s/ (%s)\n", WiFi.softAPIP().toString().c_str(), apActive ? "ready" : "FAILED");
  applyWifiPower();
  if (wifi.ssid[0]) startStaAttempt(millis());
  if (MDNS.begin(LED_HOSTNAME)) MDNS.addService("http", "tcp", 80);
}

void updateNetwork(uint32_t now) {
  const bool connected = WiFi.status() == WL_CONNECTED;
  wifiRecovery.observe(now, wifi.ssid[0] && !wifiPending, connected);
  if (wifiPending && aqua::due(now, wifiRequested, 300)) {
    if (!staConfigDisconnecting) {
      WiFi.disconnectAsync(false, false);
      staAttemptActive = false;
      staConfigDisconnecting = true;
      lastWifiAttempt = now;
    } else if (!connected && aqua::due(now, lastWifiAttempt, 250)) {
      wifiPending = false;
      staConfigDisconnecting = false;
      staNextRetryDelay = STA_RETRY_MIN_MS;
      startStaAttempt(now);
    }
  }
  if (connected) {
    staAttemptActive = false;
    staNextRetryDelay = STA_RETRY_MIN_MS;
    if (!wasConnected) {
      Serial.printf("[NET] STA URL: http://%s/ (http://%s.local/) channel=%u rssi=%d dBm.\n",
                    WiFi.localIP().toString().c_str(), LED_HOSTNAME, WiFi.channel(), WiFi.RSSI());
    }
  } else if (!wifiPending && wifi.ssid[0]) {
    if (wasConnected || (staAttemptActive && aqua::due(now, staAttemptStarted, STA_ATTEMPT_MS))) {
      if (staAttemptActive && aqua::due(now, staAttemptStarted, STA_ATTEMPT_MS)) wifiRecovery.failedAttempt();
      scheduleStaRetry(now);
    } else if (!staAttemptActive && aqua::due(now, lastWifiAttempt, staRetryDelay)) {
      startStaAttempt(now);
    }
  }
  wasConnected = connected;
}

void initializeClock(uint32_t recoveryMarker) {
  const time_t retained = time(nullptr);
  if (aqua::resumeRecoveryClock(esp_reset_reason() == ESP_RST_SW, recoveryMarker,
                               retained, MIN_EPOCH, MAX_EPOCH)) {
    timeSource.store(TimeSource::Rtc);
    Serial.println("[TIME] Retained RTC clock after Wi-Fi recovery restart.");
  } else {
    timeval unset = {};
    settimeofday(&unset, nullptr);
    timeSource.store(TimeSource::None);
  }
}

bool persistRecoveryCount(uint8_t count, uint32_t now) {
  if (recoveryWriteAttempted && !aqua::due(now, recoveryLastWrite, RECOVERY_NVS_RETRY_MS)) return false;
  recoveryWriteAttempted = true;
  recoveryLastWrite = now;
  const bool okay = preferencesOpen && preferences.putUChar("net_reboots", count) == sizeof(uint8_t) &&
      preferences.getUChar("net_reboots", 255) == count;
  recoveryStorageError = !okay;
  if (!okay) Serial.println("[NET] Recovery counter save failed; automatic restart withheld.");
  return okay;
}

void pollWifiRecovery(uint32_t now) {
  const bool resetBudget = wifiRecovery.clearDue(now);
  const bool restart = wifiRecovery.restartDue(now);
  const bool connected = WiFi.status() == WL_CONNECTED;
  if ((restart && connected) || (resetBudget && !connected)) return;
  if ((!resetBudget && !restart) || !preferencesOpen || !storageOk || wifiPending ||
      maintenance::rebootRequested() || (restart && WiFi.softAPgetStationNum() != 0) || !ledota::tryLock()) return;
  // The OTA lock also excludes cloud image writes and local uploads, and refuses
  // acquisition until the running OTA image has passed startup validation.
  if (!flushPendingSettings() || settingsDirty) { ledota::unlock(); return; }
  const uint8_t next = resetBudget ? 0 : wifiRecovery.used() + 1;
  if (!persistRecoveryCount(next, now)) { ledota::unlock(); return; }
  if (resetBudget) {
    wifiRecovery.cleared();
    Serial.println("[NET] Stable Wi-Fi for 10 min; automatic restart budget restored.");
    ledota::unlock();
    return;
  }
  wifiRecovery.restarted(now);
  time_t epoch;
  tm local = {};
  wifiRecoveryClockMarker = readClock(epoch, local) ? aqua::WIFI_RECOVERY_CLOCK_MAGIC : 0;
  Serial.printf("[NET] Wi-Fi recovery restart %u/%u after persistent reconnect failure.\n",
                wifiRecovery.used(), aqua::WIFI_RECOVERY_MAX_RESTARTS);
  ESP.restart();
  ledota::unlock();  // Only reached by test doubles or if restart unexpectedly returns.
}

bool startSensor() {
  const bool ready = aht20.begin(I2C_SDA, I2C_SCL, 100000, 50000);
  // AHTxx sets the Stream timeout; set the ESP32 hardware timeout explicitly too.
  Wire.setTimeOut(50);
  Serial.println(ready ? "[AHT20] Initialized at 0x38" : "[AHT20] Init failed; retry in 5 s.");
  return ready;
}

void updateSensor(uint32_t now) {
  if (!aqua::due(now, lastSensor, 5000)) return;
  lastSensor = now;
  if (!sensorReady) {
    sensorReady = startSensor();
    return;
  }
  const float nextTemperature = aht20.readTemperature();
  const float nextHumidity = aht20.readHumidity(AHTXX_USE_READ_DATA);
  sensorOk = aht20.getStatus() == AHTXX_NO_ERROR && nextTemperature != AHTXX_ERROR &&
      nextHumidity != AHTXX_ERROR && std::isfinite(nextTemperature) && std::isfinite(nextHumidity) &&
      nextTemperature >= -40 && nextTemperature <= 85 && nextHumidity >= 0 && nextHumidity <= 100;
  if (!sensorOk) {
    temperature = humidity = NAN;
    sensorReady = false;
    Serial.printf("[AHT20] Read failed, status=%u; lamp control continues.\n", aht20.getStatus());
    return;
  }
  temperature = nextTemperature;
  humidity = nextHumidity;
  haveSample = true;
  lastSample = now;
  Serial.printf("[AHT20] %.2f C | %.2f %%RH\n", temperature, humidity);
}

String jsonString(const String &value) {
  String result = "\"";
  for (size_t i = 0; i < value.length(); ++i) {
    const uint8_t ch = static_cast<uint8_t>(value[i]);
    if (ch == '\\' || ch == '"') { result += '\\'; result += static_cast<char>(ch); }
    else if (ch < 0x20) {
      char escaped[7]; snprintf(escaped, sizeof(escaped), "\\u%04x", ch); result += escaped;
    } else result += static_cast<char>(ch);
  }
  result += '"';
  return result;
}

void sendJson(int status, const String &body) {
  server.sendHeader("Cache-Control", "no-store");
  server.sendHeader("X-Content-Type-Options", "nosniff");
  server.send(status, "application/json; charset=utf-8", body);
}

void errorResponse(int status, const char *message) {
  sendJson(status, String("{\"ok\":false,\"error\":") + jsonString(message) + "}");
}

bool writableRequest(uint8_t arguments) {
  if (server.header("X-Aqua-Request") != "1") {
    errorResponse(403, "Érvénytelen kérés. Frissítsd az oldalt."); return false;
  }
  const String type = server.header("Content-Type");
  if (type != "application/x-www-form-urlencoded" && !type.startsWith("application/x-www-form-urlencoded;")) {
    errorResponse(415, "Nem támogatott kérésformátum."); return false;
  }
  uint64_t length;
  if (!aqua::integer(server.header("Content-Length").c_str(), 3072, length) || server.args() != arguments) {
    errorResponse(400, "Hibás vagy túl hosszú kérés."); return false;
  }
  return true;
}

bool integerArg(const char *name, uint64_t maximum, uint64_t &value) {
  const String text = server.arg(name);
  return text.length() == strlen(text.c_str()) && aqua::integer(text.c_str(), maximum, value);
}

void appendCurveJson(String &text, const aqua::Curve &curve) {
  text += '[';
  for (uint8_t i = 0; i < curve.count; ++i) {
    if (i) text += ',';
    text += "{\"minute\":"; text += curve.points[i].minute;
    text += ",\"level\":"; text += curve.points[i].level; text += '}';
  }
  text += ']';
}

void sendState() {
  time_t epoch;
  tm local = {};
  const bool clockValid = readClock(epoch, local);
  char localTime[24] = {};
  if (clockValid) strftime(localTime, sizeof(localTime), "%Y-%m-%d %H:%M:%S", &local);
  const auto source = timeSource.load();
  String state; state.reserve(2200);
  state = "{\"mode\":"; state += jsonString(modeName(settings.mode));
  state += ",\"warm\":"; state += String(actual.warm, 2);
  state += ",\"cool\":"; state += String(actual.cool, 2);
  state += ",\"manualWarm\":"; state += settings.manualWarm;
  state += ",\"manualCool\":"; state += settings.manualCool;
  state += ",\"pwmOk\":"; state += pwmOk ? "true" : "false";
  state += ",\"sensorOk\":"; state += sensorOk ? "true" : "false";
  state += ",\"temperature\":"; state += sensorOk ? String(temperature, 2) : "null";
  state += ",\"humidity\":"; state += sensorOk ? String(humidity, 2) : "null";
  state += ",\"sensorAgeSec\":"; state += haveSample ? String(static_cast<uint32_t>(millis() - lastSample) / 1000) : "null";
  state += ",\"clockValid\":"; state += clockValid ? "true" : "false";
  state += ",\"epoch\":"; state += clockValid ? String(static_cast<unsigned long long>(epoch)) : "0";
  state += ",\"localTime\":"; state += jsonString(localTime);
  state += ",\"minuteOfDay\":"; state += clockValid ? local.tm_hour * 60 + local.tm_min : 0;
  state += ",\"timeSource\":"; state += jsonString(!clockValid ? "none" : source == TimeSource::Ntp ? "ntp" : source == TimeSource::Rtc ? "rtc" : "browser");
  state += ",\"schedule\":{\"warm\":"; appendCurveJson(state, settings.warm);
  state += ",\"cool\":"; appendCurveJson(state, settings.cool);
  const bool connected = WiFi.status() == WL_CONNECTED;
  state += "},\"network\":{\"connected\":"; state += connected ? "true" : "false";
  state += ",\"ssid\":"; state += jsonString(wifi.ssid);
  state += ",\"ip\":"; state += jsonString(connected ? WiFi.localIP().toString() : String(""));
  state += ",\"apActive\":"; state += apActive ? "true" : "false";
  state += ",\"apSsid\":"; state += jsonString(apSsid);
  state += ",\"apIp\":"; state += jsonString(WiFi.softAPIP().toString());
  state += ",\"rssi\":"; state += connected ? WiFi.RSSI() : 0;
  state += ",\"channel\":"; state += WiFi.channel();
  state += ",\"staAttempting\":"; state += staAttemptActive ? "true" : "false";
  const uint32_t retryElapsed = static_cast<uint32_t>(millis() - lastWifiAttempt);
  state += ",\"retryInSec\":"; state += (!connected && !staAttemptActive && !wifiPending && retryElapsed < staRetryDelay)
      ? (staRetryDelay - retryElapsed + 999) / 1000 : 0;
  state += ",\"lastDisconnectReason\":"; state += lastStaReason.load();
  state += ",\"lastDisconnectRssi\":"; state += lastStaRssi.load();
  state += ",\"recoveryRestartsUsed\":"; state += wifiRecovery.used();
  state += ",\"recoveryRestartsRemaining\":"; state += wifiRecovery.remaining();
  state += ",\"recoveryState\":";
  state += jsonString(!wifi.ssid[0] ? "unconfigured" : recoveryStorageError ? "storage_error" :
      connected ? (wifiRecovery.used() ? "stabilizing" : "online") : !wifiRecovery.remaining() ? "limited" :
      wifiRecovery.restartDue(millis()) ? "restart_waiting" : "reconnecting");
  state += "},\"channels\":"; state += LED_CHANNELS;
  state += ",\"warmPin\":"; state += WARM_PIN;
  state += ",\"coolPin\":"; state += COOL_PIN;
  state += ",\"storageOk\":"; state += storageOk ? "true" : "false";
  state += ",\"savePending\":"; state += settingsDirty ? "true" : "false";
  state += ",\"uptime\":"; state += String(static_cast<unsigned long long>(uptimeMs / 1000));
  state += ",\"update\":"; state += ledota::stateJson();
  state += ",\"maintenance\":"; state += maintenance::stateJson();
  state += '}';
  sendJson(200, state);
}

String maintenanceStatus() {
  time_t epoch;
  tm local = {};
  const bool clockValid = readClock(epoch, local);
  String status = "mode="; status += modeName(settings.mode);
  status += " warm="; status += String(actual.warm, 2);
  status += "% cool="; status += String(actual.cool, 2);
  status += "% pwm="; status += pwmOk ? "ok" : "error";
  status += " storage="; status += storageOk ? "ok" : "error";
  status += " savePending="; status += settingsDirty ? "yes" : "no";
  status += " clock="; status += clockValid ? "set" : "unset";
  status += " sensor="; status += sensorOk ? "ok" : "error";
  if (sensorOk) {
    status += " temperature="; status += String(temperature, 2);
    status += "C humidity="; status += String(humidity, 2); status += "%RH";
  }
  status += "\r\nupdate="; status += ledota::stateJson();
  status += "\r\nmaintenance="; status += maintenance::stateJson();
  return status;
}

void configureRoutes() {
  const char *headers[] = {"X-Aqua-Request", "Content-Type", "Content-Length"};
  server.collectHeaders(headers, 3);
  server.on("/", HTTP_GET, [] {
    server.sendHeader("Cache-Control", "no-store");
    server.sendHeader("X-Content-Type-Options", "nosniff");
    server.send_P(200, "text/html; charset=utf-8", HUB_HTML);
  });
  server.on("/control", HTTP_GET, [] {
    server.sendHeader("Cache-Control", "no-store");
    server.sendHeader("X-Content-Type-Options", "nosniff");
    server.send_P(200, "text/html; charset=utf-8", INDEX_HTML);
  });
  server.on("/favicon.ico", HTTP_GET, [] { server.send(204); });
  server.on("/api/state", HTTP_GET, sendState);
  server.on("/api/update/check", HTTP_POST, [] {
    if (!writableRequest(0)) return;
    if (WiFi.status() != WL_CONNECTED) {
      errorResponse(503, "Az internetes frissítéshez csatlakozz Wi-Fi-hálózathoz."); return;
    }
    time_t epoch;
    tm local = {};
    if (!readClock(epoch, local)) { errorResponse(503, "Előbb szinkronizáld az órát."); return; }
    if (ledota::busy() || maintenance::rebootRequested()) {
      errorResponse(409, "Frissítés vagy újraindítás már folyamatban van."); return;
    }
    if (!ledota::requestCheck()) { errorResponse(503, "A frissítési keresés most nem indítható."); return; }
    sendJson(202, "{\"ok\":true}");
  });
  server.on("/api/mode", HTTP_POST, [] {
    if (!writableRequest(1)) return;
    const String mode = server.arg("mode");
    aqua::Settings candidate = settings;
    if (mode == "off") candidate.mode = aqua::Mode::Off;
    else if (mode == "manual") candidate.mode = aqua::Mode::Manual;
    else if (mode == "auto") candidate.mode = aqua::Mode::Auto;
    else { errorResponse(400, "Ismeretlen üzemmód."); return; }
    if (candidate.mode != aqua::Mode::Off && !pwmOk) { errorResponse(503, "A PWM-kimenet hibás."); return; }
    // OFF must work even when flash storage is unavailable.
    if (candidate.mode == aqua::Mode::Off) { settings.mode = candidate.mode; applyOutput(); }
    if (!saveAndApply(candidate)) { errorResponse(503, "A mentés sikertelen. A kikapcsolás ettől még működik."); return; }
    sendJson(200, "{\"ok\":true}");
  });
  server.on("/api/manual", HTTP_POST, [] {
    if (!writableRequest(2)) return;
    uint64_t warm, cool;
    if (!integerArg("warm", 100, warm) || !integerArg("cool", 100, cool)) {
      errorResponse(400, "Mindkét fényerő 0–100 közötti egész szám legyen."); return;
    }
    if (settings.mode != aqua::Mode::Manual) {
      errorResponse(409, "Előbb kapcsold be a kézi módot."); return;
    }
    if (!pwmOk) { errorResponse(503, "A PWM-kimenet hibás."); return; }
    settings.manualWarm = warm;
    settings.manualCool = cool;
    settingsDirty = true;
    lastManualChange = millis();
    applyOutput();
    sendJson(200, "{\"ok\":true}");
  });
  server.on("/api/program", HTTP_POST, [] {
    if (!writableRequest(2)) return;
    const String warm = server.arg("warmPoints");
    const String cool = server.arg("coolPoints");
    aqua::Settings candidate = settings;
    if (warm.length() > 512 || cool.length() > 512 ||
        warm.length() != strlen(warm.c_str()) || cool.length() != strlen(cool.c_str()) ||
        !aqua::parseProgram(warm.c_str(), cool.c_str(), candidate)) {
      errorResponse(400, "Mindkét csatornához 2–16 különböző, időrendbe rendezett pont kell; a fényerő 0–100 között legyen."); return;
    }
    if (!saveAndApply(candidate)) { errorResponse(503, "A program mentése sikertelen."); return; }
    sendJson(200, "{\"ok\":true}");
  });
  server.on("/api/time", HTTP_POST, [] {
    if (!writableRequest(1)) return;
    uint64_t epoch;
    if (!integerArg("epoch", MAX_EPOCH, epoch) || epoch < MIN_EPOCH) {
      errorResponse(400, "Érvénytelen dátum; 2024–2099 közötti idő állítható be."); return;
    }
    timeval value = {static_cast<time_t>(epoch), 0};
    if (settimeofday(&value, nullptr) != 0) { errorResponse(503, "Az óra beállítása sikertelen."); return; }
    timeSource.store(TimeSource::Browser);
    applyOutput();
    sendJson(200, "{\"ok\":true}");
  });
  server.on("/api/wifi", HTTP_POST, [] {
    if (!writableRequest(2)) return;
    const String ssid = server.arg("ssid");
    const String password = server.arg("password");
    if (!server.hasArg("ssid") || !server.hasArg("password") ||
        ssid.length() < 1 || ssid.length() > 32 || ssid.length() != strlen(ssid.c_str()) ||
        password.length() != strlen(password.c_str()) ||
        (password.length() != 0 && (password.length() < 8 || password.length() > 63))) {
      errorResponse(400, "Az SSID 1–32 bájt, a jelszó 8–63 bájt legyen; nyílt hálózatnál üres."); return;
    }
    WifiCredentials candidate;
    ssid.toCharArray(candidate.ssid, sizeof(candidate.ssid));
    password.toCharArray(candidate.password, sizeof(candidate.password));
    storageOk = preferencesOpen && preferences.putBytes("wifi", &candidate, sizeof(candidate)) == sizeof(candidate);
    if (!storageOk) { errorResponse(503, "A Wi-Fi-beállítás mentése sikertelen."); return; }
    wifi = candidate;
    wifiPending = true;
    wifiRequested = millis();
    sendJson(200, "{\"ok\":true}");
  });
  server.onNotFound([] { errorResponse(404, "Nincs ilyen oldal vagy végpont."); });
  server.begin();
  serverStarted = true;
}

void setup() {
  const uint32_t recoveryMarker = wifiRecoveryClockMarker;
  wifiRecoveryClockMarker = 0;  // One shot, consumed before any possible startup failure.
  pwmOk = startPwm();
  Serial.begin(115200);
  const uint32_t started = millis();
  while (!Serial && !aqua::due(millis(), started, 1500)) delay(10);
  Serial.println("\nAquaLight ESP32-C3 controller");
#if LED_CHANNELS == 2
  Serial.println("[PWM] Full-spectrum purple GPIO0 (blue + red), cool GPIO4 (6500 K), 1000 Hz, 13 bit, restored 5-percent turn-on curve.");
#else
  Serial.println("[PWM] Single channel GPIO4, I2C SDA GPIO1 / SCL GPIO0, 1000 Hz, 13 bit, gamma 2.");
#endif
  if (!pwmOk) Serial.println("[PWM] INIT FAILED; all outputs held OFF.");
  loadSettings();
  // Only a marked Wi-Fi recovery soft reset may retain the live RTC clock.
  // Every power outage and unrelated reset still requires fresh synchronization.
  initializeClock(recoveryMarker);
  sntp_set_time_sync_notification_cb([](timeval *) { timeSource.store(TimeSource::Ntp); });
  configTzTime(TIMEZONE, "pool.ntp.org", "time.cloudflare.com", "time.google.com");
  startNetwork();
  configureRoutes();
  ledota::begin();
  maintenance::begin(apPassword, flushPendingSettings, maintenanceStatus);
  sensorReady = startSensor();
  lastSensor = millis() - 5000;
  previousMillis = millis();
  applyOutput();
  Serial.printf("[READY] mode=%s; auto waits for NTP or browser time.\n", modeName(settings.mode));
}

void loop() {
  uint32_t now = millis();
  uptimeMs += static_cast<uint32_t>(now - previousMillis);
  previousMillis = now;
  server.handleClient();
  // Request handlers record millis(); comparing against the older loop timestamp
  // would underflow and bypass the manual-save debounce and deferred Wi-Fi update.
  now = millis();
  updateNetwork(now);
  if (aqua::due(now, lastOutput, 100)) { lastOutput = now; applyOutput(); }
  updateSensor(now);
  if (settingsDirty && aqua::due(now, lastManualChange, 2000) &&
      (!lastSaveFailed || aqua::due(now, lastSaveAttempt, 30000))) {
    lastSaveAttempt = now;
    lastSaveFailed = !saveSettings(settings);
    if (!lastSaveFailed) settingsDirty = false;
  }
  if (maintenance::rebootRequested() && settingsDirty) flushPendingSettings();
  ledota::poll(pwmOk && storageOk && serverStarted, !settingsDirty && storageOk);
  maintenance::poll();
  if (maintenance::rebootRequested()) maintenance::rebootIfSafe(flushPendingSettings() && !settingsDirty);
  pollWifiRecovery(millis());
  delay(1);
}
