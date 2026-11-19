#include <Arduino.h>
#include <AHTxx.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <Wire.h>
#include <driver/gpio.h>
#include <driver/ledc.h>
#include <esp_sntp.h>
#include <esp_attr.h>
#include <esp_system.h>
#include <esp_heap_caps.h>
#include <esp_wifi.h>
#include <cJSON.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <soc/soc_caps.h>
#include <atomic>
#include <cmath>
#include <cstring>
#include <ctime>
#include <memory>
#include <new>
#include <sys/time.h>

#include "schedule.h"
#include "wifi_recovery.h"
#include "cloud_update.h"
#include "maintenance.h"
#include "web_ui.h"
#include "diagnostics.h"
#include "bounded_web_server.h"
#include "board_link.h"
#include "board_policy.h"
#include "usb_status_request.h"
#ifndef LED_BLE_ONLY
#define LED_BLE_ONLY 0
#endif
#if LED_BLE_ONLY
#include "ble_link.h"
#include "ble_config.h"
#else
#include "mesh_config.h"
#endif
#include "web_assets.h"

#ifndef LED_CHANNELS
#define LED_CHANNELS 2
#endif
#if LED_CHANNELS == 1
#include "device_config.h"  // Private generated header, never stored in source control.
#endif
#ifndef LED_MASTER
#define LED_MASTER (LED_CHANNELS == 2)
#endif
constexpr bool MASTER = LED_MASTER;
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

BoundedWebServer server(80);
Preferences preferences;
AHTxx aht20(AHTXX_ADDRESS_X38, AHT2x_SENSOR);
aqua::Settings settings = aqua::defaults();
aqua::Levels actual;
portMUX_TYPE controllerMux = portMUX_INITIALIZER_UNLOCKED;
TaskHandle_t controlTaskHandle = nullptr;
TaskHandle_t sensorTaskHandle = nullptr;
uint32_t controlTicks = 0, lastControlAt = 0, maxControlGapMs = 0;
std::atomic<bool> pwmOk{false};
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
aqua::SensorDiagnostics sensorDiagnostics;
aqua::TimingDiagnostics loopDiagnostics;
aqua::TimingDiagnostics httpDiagnostics;
int lastInitProbeCode = -1;
bool firstErrorSdaHigh = false, firstErrorSclHigh = false;
bool lastErrorSdaHigh = false, lastErrorSclHigh = false;
enum class TimeSource : uint8_t { None, Browser, Ntp, Rtc, Mesh, Ble };
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
aqua::SlaveAccessPointPolicy accessPointPolicy;
aqua::ChannelSearchPolicy channelSearchPolicy;
aqua::MaintenanceButtonPolicy maintenanceButtonPolicy;
bool masterNetworkConnected = false;
bool meshReady = false;
uint32_t configRevision = 0;
uint32_t lastPeerBoot = 0;
#if LED_BLE_ONLY
uint8_t radioChannel = 0;
#else
uint8_t radioChannel = meshconfig::channel;
#endif
uint32_t lastStatePublished = 0, lastApAttempt = 0;
bool haveApAttempt = false;
String peerState;
uint32_t peerStateAt = 0;
bool havePeerState = false;
uint32_t peerConfigRevision = 0;
uint8_t persistedRadioChannel = radioChannel;
uint32_t channelStableSince = 0, lastChannelPersistAttempt = 0;
uint8_t observedRadioChannel = 0;
bool haveChannelPersistAttempt = false;
uint32_t peerStateErrors = 0;
#if LED_BLE_ONLY
uint32_t bleReplyFailures = 0;
bool bleClientValidated = false;
#endif
struct CommandTicket { uint32_t id = 0, submitted = 0; };
CommandTicket commandTickets[5];
unsigned nextCommandTicket = 0;

struct SensorSnapshot {
  bool ready = false, okay = false, sampled = false;
  float temperature = NAN, humidity = NAN;
  uint32_t sampleAt = 0;
  aqua::SensorDiagnostics diagnostics;
  int probe = -1;
  bool sdaHigh = true, sclHigh = true;
  bool firstSdaHigh = false, firstSclHigh = false;
  bool lastSdaHigh = false, lastSclHigh = false;
};
portMUX_TYPE sensorMux = portMUX_INITIALIZER_UNLOCKED;
SensorSnapshot publishedSensor;

SensorSnapshot sensorSnapshot() {
  portENTER_CRITICAL(&sensorMux);
  const SensorSnapshot result = publishedSensor;
  portEXIT_CRITICAL(&sensorMux);
  return result;
}

aqua::Levels outputSnapshot() {
  portENTER_CRITICAL(&controllerMux);
  const auto result = actual;
  portEXIT_CRITICAL(&controllerMux);
  return result;
}

bool setAccessPoint(bool enabled);
void applyOutput();
void requestOutput() {
  if (controlTaskHandle) xTaskNotifyGive(controlTaskHandle);
}

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
  portENTER_CRITICAL(&controllerMux);
  actual = {};
  portEXIT_CRITICAL(&controllerMux);
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
  portENTER_CRITICAL(&controllerMux);
  const auto program = settings;
  const auto previousOutput = actual;
  portEXIT_CRITICAL(&controllerMux);
  aqua::Levels desired = aqua::output(program, valid,
      local.tm_hour * 3600UL + local.tm_min * 60UL + local.tm_sec);
#if LED_CHANNELS == 1
  desired.cool = 0;  // No second output on this board.
#endif
  const float values[] = {desired.warm, desired.cool};
  const float previous[] = {previousOutput.warm, previousOutput.cool};
  for (uint8_t i = 0; i < LED_CHANNELS; ++i) {
    const uint32_t value = outputDuty(values[i], i);
    if (value == outputDuty(previous[i], i)) continue;
    if (!writePwmValue(i, value)) {
      stopPwm();
      Serial.println("[PWM] WRITE FAILED: all outputs held OFF.");
      return;
    }
  }
  portENTER_CRITICAL(&controllerMux);
  actual = desired;
  portEXIT_CRITICAL(&controllerMux);
}

void appendCurve(String &text, const aqua::Curve &curve) {
  for (uint8_t i = 0; i < curve.count; ++i) {
    if (i) text += ';';
    text += curve.points[i].minute;
    text += ','; text += curve.points[i].level;
  }
}

String encodeSettings(const aqua::Settings &value) {
  String text;
  if (!text.reserve(384)) return String();
  text = "2,";
  text += static_cast<unsigned>(value.mode);
  text += ','; text += value.manualWarm;
  text += ','; text += value.manualCool;
  text += '|'; appendCurve(text, value.warm);
  text += '|'; appendCurve(text, value.cool);
  return text;
}

bool saveSettings(const aqua::Settings &value) {
  if (!aqua::valid(value)) {
    storageOk = false;
    Serial.println("[NVS] Invalid settings rejected before serialization.");
    return false;
  }
  const String encoded = encodeSettings(value);
  if (!encoded.length()) {
    storageOk = false;
    Serial.println("[NVS] Settings serialization failed; stored settings unchanged.");
    return false;
  }
  storageOk = preferencesOpen && preferences.putString("settings", encoded) == encoded.length();
  if (storageOk) { lastSaveFailed = false; ++configRevision; }
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
  portENTER_CRITICAL(&controllerMux);
  settings = candidate;
  portEXIT_CRITICAL(&controllerMux);
  settingsDirty = false;
  requestOutput();
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
#if !LED_BLE_ONLY
  WiFi.persistent(false);
  WiFi.setHostname(LED_HOSTNAME);
  WiFi.setAutoReconnect(false);
  WiFi.onEvent([](arduino_event_id_t event, arduino_event_info_t info) {
    if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      lastStaReason.store(info.wifi_sta_disconnected.reason);
      lastStaRssi.store(info.wifi_sta_disconnected.rssi);
      Serial.printf("[NET] STA disconnected, reason=%u rssi=%d dBm.\n",
                    static_cast<unsigned>(info.wifi_sta_disconnected.reason), info.wifi_sta_disconnected.rssi);
    }
  });
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
#ifdef LED_AP_PASSWORD
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
  radioChannel = preferencesOpen ? preferences.getUChar("mesh_ch", meshconfig::channel) : meshconfig::channel;
  if (radioChannel < 1 || radioChannel > 13) radioChannel = meshconfig::channel;
  persistedRadioChannel = radioChannel;
  esp_wifi_set_channel(radioChannel, WIFI_SECOND_CHAN_NONE);
  applyWifiPower();
  boardlink::Config config{};
  config.master = MASTER;
  config.channels = LED_CHANNELS;
  memcpy(config.peerMac, MASTER ? meshconfig::slaveMac : meshconfig::masterMac, 6);
  memcpy(config.pmk, meshconfig::pmk, 16);
  memcpy(config.lmk, meshconfig::lmk, 16);
  meshReady = boardlink::begin(config);
  Serial.printf("[MESH] role=%s radio=%s channel=%u; local control is independent.\n",
                MASTER ? "master" : "slave", meshReady ? "ready" : "unavailable", radioChannel);
  if (MASTER && wifi.ssid[0]) startStaAttempt(millis());
  if (MDNS.begin(LED_HOSTNAME)) MDNS.addService("http", "tcp", 80);
#endif
}

void updateNetwork(uint32_t now) {
  if (!MASTER) return;
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

void captureSensorErrorLines() {
  lastErrorSdaHigh = gpio_get_level(static_cast<gpio_num_t>(I2C_SDA));
  lastErrorSclHigh = gpio_get_level(static_cast<gpio_num_t>(I2C_SCL));
  if (!sensorDiagnostics.haveError) {
    firstErrorSdaHigh = lastErrorSdaHigh;
    firstErrorSclHigh = lastErrorSclHigh;
  }
}

bool startSensor() {
  const uint32_t started = millis();
  const bool ready = aht20.begin(I2C_SDA, I2C_SCL, 100000, 50000);
  // AHTxx sets the Stream timeout; set the ESP32 hardware timeout explicitly too.
  Wire.setTimeOut(50);
  const uint32_t duration = millis() - started;
  // AHTxx::begin/softReset do not update getStatus(); never report its stale
  // measurement code as the cause of an initialization failure.
  const uint8_t status = ready ? AHTXX_NO_ERROR : 253;
  lastInitProbeCode = -1;
  if (!ready) {
    captureSensorErrorLines();
    // Read-only address probe after failure, bounded by the same I2C timeout.
    Wire.beginTransmission(AHTXX_ADDRESS_X38);
    lastInitProbeCode = Wire.endTransmission(true);
  }
  sensorDiagnostics.recordInit(ready, status, duration, millis());
  Serial.println(ready ? "[AHT20] Initialized at 0x38" : "[AHT20] Init failed; retry in 5 s.");
  if (!ready) Serial.printf("[DIAG] sensor init status=%u (%s) duration=%lu ms probe=%d SDA=%d SCL=%d\n",
      status, aqua::sensorStatusText(status), static_cast<unsigned long>(sensorDiagnostics.lastInitMs),
      lastInitProbeCode, lastErrorSdaHigh, lastErrorSclHigh);
  return ready;
}

void updateSensor(uint32_t now) {
  if (!aqua::due(now, lastSensor, 5000)) return;
  lastSensor = now;
  if (!sensorReady) {
    sensorReady = startSensor();
    return;
  }
  const uint32_t started = millis();
  const float nextTemperature = aht20.readTemperature();
  const float nextHumidity = aht20.readHumidity(AHTXX_USE_READ_DATA);
  sensorOk = aht20.getStatus() == AHTXX_NO_ERROR && nextTemperature != AHTXX_ERROR &&
      nextHumidity != AHTXX_ERROR && std::isfinite(nextTemperature) && std::isfinite(nextHumidity) &&
      nextTemperature >= -40 && nextTemperature <= 85 && nextHumidity >= 0 && nextHumidity <= 100;
  const uint8_t status = !sensorOk && aht20.getStatus() == AHTXX_NO_ERROR ? 254 : aht20.getStatus();
  if (!sensorOk) captureSensorErrorLines();
  sensorDiagnostics.recordRead(sensorOk, status, millis() - started, millis());
  if (!sensorOk) {
    temperature = humidity = NAN;
    sensorReady = false;
    Serial.printf("[AHT20] Read failed, status=%u (%s), duration=%lu ms SDA=%d SCL=%d; lamp control continues.\n",
        status, aqua::sensorStatusText(status), static_cast<unsigned long>(sensorDiagnostics.lastReadMs),
        gpio_get_level(static_cast<gpio_num_t>(I2C_SDA)), gpio_get_level(static_cast<gpio_num_t>(I2C_SCL)));
    return;
  }
  temperature = nextTemperature;
  humidity = nextHumidity;
  haveSample = true;
  lastSample = millis();
  Serial.printf("[AHT20] %.2f C | %.2f %%RH\n", temperature, humidity);
}

String jsonString(const String &value) {
  String result;
  if (!result.reserve(value.length() * 6 + 3)) return String("null");
  result = "\"";
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


void appendCurveJson(String &text, const aqua::Curve &curve) {
  text += '[';
  for (uint8_t i = 0; i < curve.count; ++i) {
    if (i) text += ',';
    text += "{\"minute\":"; text += curve.points[i].minute;
    text += ",\"level\":"; text += curve.points[i].level; text += '}';
  }
  text += ']';
}

const char *resetReasonName() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "power_on";
    case ESP_RST_EXT: return "external";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "interrupt_watchdog";
    case ESP_RST_TASK_WDT: return "task_watchdog";
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_DEEPSLEEP: return "deep_sleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    case ESP_RST_USB: return "usb";
    case ESP_RST_JTAG: return "jtag";
    default: return "other";
  }
}

void appendTimingJson(String &text, const aqua::TimingDiagnostics &timing) {
  text += "{\"lastMs\":"; text += timing.lastMs;
  text += ",\"maxMs\":"; text += timing.maxMs;
  text += ",\"slowCount\":"; text += timing.slowCount; text += '}';
}

String diagnosticsJson() {
  const auto sample = sensorSnapshot();
  const auto &sensor = sample.diagnostics;
  String text; text.reserve(1500);
  text = "{\"resetReason\":\""; text += resetReasonName(); text += '"';
  text += ",\"freeHeap\":"; text += ESP.getFreeHeap();
  text += ",\"minFreeHeap\":"; text += ESP.getMinFreeHeap();
  text += ",\"largestFreeBlock\":"; text += heap_caps_get_largest_free_block(MALLOC_CAP_8BIT);
  text += ",\"loop\":"; appendTimingJson(text, loopDiagnostics);
  text += ",\"http\":"; appendTimingJson(text, httpDiagnostics);
  portENTER_CRITICAL(&controllerMux);
  const uint32_t ticks = controlTicks, lastTick = lastControlAt, maxGap = maxControlGapMs;
  portEXIT_CRITICAL(&controllerMux);
  text += ",\"control\":{\"ticks\":"; text += ticks;
  text += ",\"lastAgeMs\":"; text += ticks ? String(static_cast<uint32_t>(millis() - lastTick)) : "null";
  text += ",\"maxGapMs\":"; text += maxGap; text += '}';
  text += ",\"sensor\":{\"ready\":"; text += sensor.ready ? "true" : "false";
  text += ",\"lastStatus\":"; text += sensor.lastStatus;
  text += ",\"lastStatusText\":\""; text += aqua::sensorStatusText(sensor.lastStatus); text += '"';
  text += ",\"initAttempts\":"; text += sensor.initAttempts;
  text += ",\"initFailures\":"; text += sensor.initFailures;
  text += ",\"readAttempts\":"; text += sensor.readAttempts;
  text += ",\"readFailures\":"; text += sensor.readFailures;
  text += ",\"consecutiveFailures\":"; text += sensor.consecutiveFailures;
  text += ",\"lastReadMs\":"; text += sensor.lastReadMs;
  text += ",\"maxReadMs\":"; text += sensor.maxReadMs;
  text += ",\"lastInitMs\":"; text += sensor.lastInitMs;
  text += ",\"maxInitMs\":"; text += sensor.maxInitMs;
  text += ",\"lastInitProbeCode\":"; text += sample.probe >= 0 ? String(sample.probe) : "null";
  text += ",\"firstErrorStatus\":"; text += sensor.haveError ? String(sensor.firstErrorStatus) : "null";
  text += ",\"firstErrorText\":\""; text += sensor.haveError ? aqua::sensorStatusText(sensor.firstErrorStatus) : ""; text += '"';
  text += ",\"firstErrorPhase\":\""; text += sensor.firstErrorPhase; text += '"';
  text += ",\"firstErrorAgeSec\":"; text += sensor.haveError ? String(static_cast<uint32_t>(millis() - sensor.firstErrorAt) / 1000) : "null";
  text += ",\"firstErrorSdaHigh\":"; text += !sensor.haveError ? "null" : sample.firstSdaHigh ? "true" : "false";
  text += ",\"firstErrorSclHigh\":"; text += !sensor.haveError ? "null" : sample.firstSclHigh ? "true" : "false";
  text += ",\"lastErrorStatus\":"; text += sensor.haveError ? String(sensor.lastErrorStatus) : "null";
  text += ",\"lastErrorText\":\""; text += sensor.haveError ? aqua::sensorStatusText(sensor.lastErrorStatus) : ""; text += '"';
  text += ",\"lastErrorPhase\":\""; text += sensor.lastErrorPhase; text += '"';
  text += ",\"lastErrorAgeSec\":"; text += sensor.haveError ? String(static_cast<uint32_t>(millis() - sensor.lastErrorAt) / 1000) : "null";
  text += ",\"lastErrorSdaHigh\":"; text += !sensor.haveError ? "null" : sample.lastSdaHigh ? "true" : "false";
  text += ",\"lastErrorSclHigh\":"; text += !sensor.haveError ? "null" : sample.lastSclHigh ? "true" : "false";
  text += ",\"sdaHigh\":"; text += sample.sdaHigh ? "true" : "false";
  text += ",\"sclHigh\":"; text += sample.sclHigh ? "true" : "false";
  text += "}}";
  return text;
}


void publishSensor() {
  SensorSnapshot sample;
  sample.ready = sensorReady; sample.okay = sensorOk; sample.sampled = haveSample;
  sample.temperature = temperature; sample.humidity = humidity; sample.sampleAt = lastSample;
  sample.diagnostics = sensorDiagnostics; sample.probe = lastInitProbeCode;
  sample.sdaHigh = gpio_get_level(static_cast<gpio_num_t>(I2C_SDA));
  sample.sclHigh = gpio_get_level(static_cast<gpio_num_t>(I2C_SCL));
  sample.firstSdaHigh = firstErrorSdaHigh; sample.firstSclHigh = firstErrorSclHigh;
  sample.lastSdaHigh = lastErrorSdaHigh; sample.lastSclHigh = lastErrorSclHigh;
  portENTER_CRITICAL(&sensorMux);
  publishedSensor = sample;
  portEXIT_CRITICAL(&sensorMux);
}

void sensorWorker(void *) {
  sensorReady = startSensor();
  publishSensor();
  lastSensor = millis() - 5000;
  for (;;) {
    updateSensor(millis());
    publishSensor();
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

void controlWorker(void *) {
  for (;;) {
    applyOutput();
    const uint32_t now = millis();
    portENTER_CRITICAL(&controllerMux);
    if (controlTicks && static_cast<uint32_t>(now - lastControlAt) > maxControlGapMs) maxControlGapMs = now - lastControlAt;
    lastControlAt = now; ++controlTicks;
    portEXIT_CRITICAL(&controllerMux);
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100));
  }
}

String meshJson() {
#if LED_BLE_ONLY
  return "{\"role\":\"standalone\",\"ready\":false,\"peerOnline\":false}";
#else
  const uint32_t now = millis(), age = boardlink::ageMs(now);
  const auto counts = boardlink::counters();
  String text = "{\"role\":\"";
  text += MASTER ? "master" : "slave";
  text += "\",\"ready\":"; text += boardlink::available() ? "true" : "false";
  text += ",\"peerOnline\":"; text += boardlink::online(now) ? "true" : "false";
  text += ",\"peerAgeSec\":"; text += age == UINT32_MAX ? "null" : String(age / 1000);
  text += ",\"channel\":"; text += radioChannel;
  text += ",\"masterNetworkConnected\":"; text += (MASTER ? WiFi.status() == WL_CONNECTED : masterNetworkConnected) ? "true" : "false";
  text += ",\"rxDropped\":"; text += counts.rxQueueDrops;
  text += ",\"txFailed\":"; text += counts.txMacFailed;
  text += ",\"stateErrors\":"; text += peerStateErrors;
  text += ",\"rxFrames\":"; text += counts.rxFrames;
  text += ",\"rxStates\":"; text += counts.rxStates;
  text += ",\"rxInvalid\":"; text += counts.rxInvalid;
  text += ",\"txFrames\":"; text += counts.txFrames;
  text += ",\"txRejected\":"; text += counts.txRejected;
  text += ",\"radioRecoveries\":"; text += counts.radioRecoveries;
  text += ",\"radioFailures\":"; text += counts.radioFailures;
  text += '}';
  return text;
#endif
}

String buildState() {
  const auto sample = sensorSnapshot();
  const auto output = outputSnapshot();
  time_t epoch;
  tm local = {};
  const bool clockValid = readClock(epoch, local);
  char localTime[24] = {};
  if (clockValid) strftime(localTime, sizeof(localTime), "%Y-%m-%d %H:%M:%S", &local);
  const auto source = timeSource.load();
  String state;
  if (!state.reserve(4400)) return String();
  state = "{\"mode\":"; state += jsonString(modeName(settings.mode));
  state += ",\"warm\":"; state += String(output.warm, 2);
  state += ",\"cool\":"; state += String(output.cool, 2);
  state += ",\"manualWarm\":"; state += settings.manualWarm;
  state += ",\"manualCool\":"; state += settings.manualCool;
  state += ",\"pwmOk\":"; state += pwmOk ? "true" : "false";
  state += ",\"sensorOk\":"; state += sample.okay ? "true" : "false";
  state += ",\"temperature\":"; state += sample.okay ? String(sample.temperature, 2) : "null";
  state += ",\"humidity\":"; state += sample.okay ? String(sample.humidity, 2) : "null";
  state += ",\"sensorAgeSec\":"; state += sample.sampled ? String(static_cast<uint32_t>(millis() - sample.sampleAt) / 1000) : "null";
  state += ",\"clockValid\":"; state += clockValid ? "true" : "false";
  state += ",\"epoch\":"; state += clockValid ? String(static_cast<unsigned long long>(epoch)) : "0";
  state += ",\"localTime\":"; state += jsonString(localTime);
  state += ",\"minuteOfDay\":"; state += clockValid ? local.tm_hour * 60 + local.tm_min : 0;
  state += ",\"timeSource\":"; state += jsonString(!clockValid ? "none" : source == TimeSource::Ntp ? "ntp" : source == TimeSource::Rtc ? "rtc" : source == TimeSource::Mesh ? "mesh" : source == TimeSource::Ble ? "ble" : "browser");
  state += ",\"schedule\":{\"warm\":"; appendCurveJson(state, settings.warm);
  state += ",\"cool\":"; appendCurveJson(state, settings.cool);
#if LED_BLE_ONLY
  state += "},\"network\":{\"connected\":false,\"ssid\":\"\",\"ip\":\"\",\"apActive\":false,\"rssi\":0,\"channel\":0,\"staAttempting\":false,\"recoveryState\":\"ble\",\"automaticRestartsEnabled\":false";
#else
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
  state += jsonString(MASTER ? (connected ? "online" : "reconnecting") : "esp_now");
  state += ",\"automaticRestartsEnabled\":false";
#endif
  state += "},\"channels\":"; state += LED_CHANNELS;
  state += ",\"warmPin\":"; state += WARM_PIN;
  state += ",\"coolPin\":"; state += COOL_PIN;
  state += ",\"storageOk\":"; state += storageOk ? "true" : "false";
  state += ",\"savePending\":"; state += settingsDirty ? "true" : "false";
  state += ",\"uptime\":"; state += String(static_cast<unsigned long long>(uptimeMs / 1000));
  state += ",\"update\":"; state += ledota::stateJson();
  state += ",\"maintenance\":"; state += maintenance::stateJson();
  state += ",\"diagnostics\":"; state += diagnosticsJson();
  state += ",\"configRevision\":"; state += configRevision;
  state += ",\"mesh\":"; state += meshJson();
#if LED_BLE_ONLY
  const auto counts = blelink::counters();
  state += ",\"ble\":{\"ready\":"; state += blelink::ready() ? "true" : "false";
  state += ",\"connected\":"; state += blelink::connected() ? "true" : "false";
  state += ",\"rxFrames\":"; state += counts.rxFrames;
  state += ",\"txFrames\":"; state += counts.txFrames;
  state += ",\"rxInvalid\":"; state += counts.rxInvalid;
  state += ",\"rxDropped\":"; state += counts.rxQueueDrops;
  state += ",\"responseTimeouts\":"; state += counts.responseTimeouts;
  state += ",\"applicationReplyFailures\":"; state += bleReplyFailures; state += '}';
#endif
  state += '}';
  return state;
}

void sendState() {
  const String state = buildState();
  if (!state.length()) { errorResponse(503, "Nincs elegendő memória az állapothoz."); return; }
  sendJson(200, state);
}

String maintenanceStatus() {
  const auto sample = sensorSnapshot();
  const auto output = outputSnapshot();
  time_t epoch;
  tm local = {};
  const bool clockValid = readClock(epoch, local);
  String status = "mode="; status += modeName(settings.mode);
  status += " warm="; status += String(output.warm, 2);
  status += "% cool="; status += String(output.cool, 2);
  status += "% pwm="; status += pwmOk ? "ok" : "error";
  status += " storage="; status += storageOk ? "ok" : "error";
  status += " savePending="; status += settingsDirty ? "yes" : "no";
  status += " clock="; status += clockValid ? "set" : "unset";
  status += " sensor="; status += sample.okay ? "ok" : "error";
  if (sample.okay) {
    status += " temperature="; status += String(sample.temperature, 2);
    status += "C humidity="; status += String(sample.humidity, 2); status += "%RH";
  }
  status += "\r\nupdate="; status += ledota::stateJson();
  status += "\r\nmaintenance="; status += maintenance::stateJson();
  status += "\r\ndiagnostics="; status += diagnosticsJson();
  return status;
}

struct MutationResult {
  int status;
  String error;
};

uint8_t argumentCount(const char *path) {
  if (!strcmp(path, "/api/mode") || !strcmp(path, "/api/time")) return 1;
  if (!strcmp(path, "/api/manual") || !strcmp(path, "/api/program") || !strcmp(path, "/api/wifi")) return 2;
  if (!strcmp(path, "/api/update/check") || !strcmp(path, "/api/maintenance/ap")) return 0;
  return 255;
}

MutationResult validateMutation(const char *path, const aqua::HttpRequest &request) {
  if (!request.complete() || request.errorStatus()) return {400, "Hiányos vagy hibás kérés."};
  if (request.method() != aqua::HttpRequest::Method::Post) return {405, "A módosításhoz POST kérés szükséges."};
  const uint8_t count = argumentCount(path);
  if (count == 255) return {404, "Nincs ilyen végpont."};
  if (strcmp(request.header("X-Aqua-Request"), "1")) return {403, "Érvénytelen kérés. Frissítsd az oldalt."};
  const String type = request.header("Content-Type");
  if (type != "application/x-www-form-urlencoded" && !type.startsWith("application/x-www-form-urlencoded;"))
    return {415, "Nem támogatott kérésformátum."};
  uint64_t length;
  if (!aqua::integer(request.header("Content-Length"), 3072, length) || request.args() != count)
    return {400, "Hibás vagy túl hosszú kérés."};
  return {200, ""};
}

MutationResult executeMutation(const char *path, const aqua::HttpRequest &request) {
  const auto validation = validateMutation(path, request);
  if (validation.status != 200) return validation;
  if (!strcmp(path, "/api/mode")) {
    const String mode = request.arg("mode");
    auto candidate = settings;
    if (mode == "off") candidate.mode = aqua::Mode::Off;
    else if (mode == "manual") candidate.mode = aqua::Mode::Manual;
    else if (mode == "auto") candidate.mode = aqua::Mode::Auto;
    else return {400, "Ismeretlen üzemmód."};
    if (candidate.mode != aqua::Mode::Off && !pwmOk) return {503, "A PWM-kimenet hibás."};
    if (candidate.mode == aqua::Mode::Off) {
      portENTER_CRITICAL(&controllerMux);
      settings.mode = candidate.mode;
      portEXIT_CRITICAL(&controllerMux);
      requestOutput();
    }
    if (!saveAndApply(candidate)) return {503, "A mentés sikertelen. A kikapcsolás ettől még működik."};
  } else if (!strcmp(path, "/api/manual")) {
    uint64_t warm, cool;
    if (!aqua::integer(request.arg("warm"), 100, warm) || !aqua::integer(request.arg("cool"), 100, cool))
      return {400, "Mindkét fényerő 0–100 közötti egész szám legyen."};
    if (settings.mode != aqua::Mode::Manual) return {409, "Előbb kapcsold be a kézi módot."};
    if (!pwmOk) return {503, "A PWM-kimenet hibás."};
    portENTER_CRITICAL(&controllerMux);
    settings.manualWarm = warm;
    settings.manualCool = LED_CHANNELS == 1 ? 0 : cool;
    portEXIT_CRITICAL(&controllerMux);
    ++configRevision;
    settingsDirty = true;
    lastManualChange = millis();
    requestOutput();
  } else if (!strcmp(path, "/api/program")) {
    const String warm = request.arg("warmPoints"), cool = request.arg("coolPoints");
    auto candidate = settings;
    if (warm.length() > 512 || cool.length() > 512 ||
        !aqua::parseProgram(warm.c_str(), cool.c_str(), candidate))
      return {400, "Mindkét csatornához 2–16 különböző, időrendbe rendezett pont kell; a fényerő 0–100 között legyen."};
    if (!saveAndApply(candidate)) return {503, "A program mentése sikertelen."};
  } else if (!strcmp(path, "/api/time")) {
    uint64_t epoch;
    if (!aqua::integer(request.arg("epoch"), MAX_EPOCH, epoch) || epoch < MIN_EPOCH)
      return {400, "Érvénytelen dátum; 2024–2099 közötti idő állítható be."};
    timeval value = {static_cast<time_t>(epoch), 0};
    if (settimeofday(&value, nullptr)) return {503, "Az óra beállítása sikertelen."};
#if LED_BLE_ONLY
    timeSource.store(TimeSource::Ble);
#else
    timeSource.store(TimeSource::Browser);
#endif
    ++configRevision;
    requestOutput();
  } else if (!strcmp(path, "/api/wifi")) {
    if (!MASTER) return {409, "Az otthoni Wi-Fi beállítását a master boardnak kell küldeni."};
    const String ssid = request.arg("ssid"), password = request.arg("password");
    if (!request.hasArg("ssid") || !request.hasArg("password") || ssid.length() < 1 || ssid.length() > 32 ||
        (password.length() && (password.length() < 8 || password.length() > 63)))
      return {400, "Az SSID 1–32 bájt, a jelszó 8–63 bájt legyen; nyílt hálózatnál üres."};
    WifiCredentials candidate;
    ssid.toCharArray(candidate.ssid, sizeof(candidate.ssid));
    password.toCharArray(candidate.password, sizeof(candidate.password));
    storageOk = preferencesOpen && preferences.putBytes("wifi", &candidate, sizeof(candidate)) == sizeof(candidate);
    if (!storageOk) return {503, "A Wi-Fi-beállítás mentése sikertelen."};
    wifi = candidate;
    wifiPending = true;
    wifiRequested = millis();
    ++configRevision;
  } else if (!strcmp(path, "/api/update/check")) {
    if (!MASTER) return {409, "A slave frissítéséhez indítsd el a karbantartási hotspotját; ott helyi OTA használható."};
    if (WiFi.status() != WL_CONNECTED) return {503, "Az internetes frissítéshez csatlakozz Wi-Fi-hálózathoz."};
    time_t epoch; tm local = {};
    if (!readClock(epoch, local)) return {503, "Előbb szinkronizáld az órát."};
    if (ledota::busy() || maintenance::rebootRequested()) return {409, "Frissítés vagy újraindítás már folyamatban van."};
    if (!ledota::requestCheck()) return {503, "A frissítési keresés most nem indítható."};
    return {202, ""};
  } else if (!strcmp(path, "/api/maintenance/ap")) {
    if (MASTER) return {409, "A karbantartási hotspotot a slave boardon lehet indítani."};
    accessPointPolicy.force(millis());
    if (!setAccessPoint(true)) return {503, "A karbantartási hotspot nem indítható."};
    ++configRevision;
  }
  return {200, ""};
}

String encodeFormValue(const char *value) {
  constexpr char hex[] = "0123456789ABCDEF";
  size_t needed = 0;
  for (const unsigned char *p = reinterpret_cast<const unsigned char *>(value); *p; ++p) {
    const auto c = *p;
    needed += ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
               c == '-' || c == '_' || c == '.' || c == '~') ? 1 : 3;
    if (needed > boardlink::FORM_LIMIT) return String();
  }
  String result;
  if (needed && !result.reserve(needed)) return String();
  while (*value) {
    const uint8_t c = static_cast<uint8_t>(*value++);
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
        c == '-' || c == '_' || c == '.' || c == '~') result += static_cast<char>(c);
    else { result += '%'; result += hex[c >> 4]; result += hex[c & 15]; }
  }
  return result;
}

void publishLocalState() {
  const String state = buildState();
  if (!boardlink::publishState(state.c_str(), state.length())) ++peerStateErrors;
  lastStatePublished = millis();
}

void handleMutation(const char *path, bool remote) {
  const auto &request = server.request();
  const auto validation = validateMutation(path, request);
  if (validation.status != 200) { errorResponse(validation.status, validation.error.c_str()); return; }
  // Router credentials belong to the master. A reachable master also owns
  // shared clock synchronization; without it the slave can set its local clock.
  if (!MASTER && !strcmp(path, "/api/wifi")) remote = true;
  if (!MASTER && !strcmp(path, "/api/time") && boardlink::online(millis())) remote = true;
  if (remote) {
    String form;
    if (!form.reserve(boardlink::FORM_LIMIT)) { errorResponse(503, "Nincs elegendő memória a parancshoz."); return; }
    for (size_t i = 0; i < request.args(); ++i) {
      const String name = encodeFormValue(request.argName(i)), value = encodeFormValue(request.arg(i));
      if ((*request.argName(i) && !name.length()) || (*request.arg(i) && !value.length())) {
        errorResponse(503, "A parancs nem kódolható. Nem került elküldésre."); return;
      }
      if (form.length() + name.length() + value.length() + 1 + (i ? 1 : 0) > boardlink::FORM_LIMIT) {
        errorResponse(413, "A rádiós parancs túl hosszú."); return;
      }
      if (i) form += '&';
      form += name; form += '='; form += value;
    }
    String result;
    if (!result.reserve(96)) { errorResponse(503, "Nincs elegendő memória a parancshoz."); return; }
    const uint32_t id = boardlink::queueCommand(path, form.c_str());
    if (!id) { errorResponse(503, "A másik board nem elérhető, vagy már van függő parancs. Próbáld újra később."); return; }
    commandTickets[nextCommandTicket] = {id, millis()};
    nextCommandTicket = (nextCommandTicket + 1) % 5;
    result = "{\"ok\":true,\"pending\":true,\"commandId\":"; result += id; result += '}';
    sendJson(202, result);
    return;
  }
  const auto result = executeMutation(path, request);
  if (result.status >= 400) { errorResponse(result.status, result.error.c_str()); return; }
  publishLocalState();
  sendJson(result.status, "{\"ok\":true}");
}

cJSON *parsePeerState() {
  if (!havePeerState) return nullptr;
  return cJSON_Parse(peerState.c_str());
}

void sendPeerState() {
  cJSON *state = parsePeerState();
  if (!state) { errorResponse(503, "A másik board még nem küldött állapotot."); return; }
  const uint32_t ageMs = millis() - peerStateAt;
  cJSON *sensorAge = cJSON_GetObjectItemCaseSensitive(state, "sensorAgeSec");
  if (cJSON_IsNumber(sensorAge)) cJSON_SetNumberValue(sensorAge, sensorAge->valuedouble + ageMs / 1000);
  cJSON *transport = cJSON_CreateObject();
  if (!transport) { cJSON_Delete(state); errorResponse(503, "Nincs elegendő memória az állapothoz."); return; }
  cJSON_AddBoolToObject(transport, "remote", true);
  cJSON_AddBoolToObject(transport, "online", boardlink::online(millis()) && ageMs < 15000);
  cJSON_AddNumberToObject(transport, "ageSec", ageMs / 1000);
  cJSON_DeleteItemFromObjectCaseSensitive(state, "transport");
  cJSON_AddItemToObject(state, "transport", transport);
  char *encoded = cJSON_PrintUnformatted(state);
  if (!encoded) { cJSON_Delete(state); errorResponse(503, "Nincs elegendő memória az állapothoz."); return; }
  sendJson(200, String(encoded));
  cJSON_free(encoded);
  cJSON_Delete(state);
}

uint32_t cachedPeerRevision() { return havePeerState ? peerConfigRevision : 0; }

void sendCommandStatus() {
  uint64_t id;
  if (server.args() != 1 || !aqua::integer(server.arg("id").c_str(), UINT32_MAX, id) || !id) {
    errorResponse(400, "Hibás parancsazonosító."); return;
  }
  const auto status = boardlink::commandStatus(id);
  String result = "{\"commandId\":"; result += static_cast<uint32_t>(id);
  const bool statePending = status.phase == boardlink::CommandPhase::Applied && status.configRevision > cachedPeerRevision();
  bool stateExpired = statePending;
  for (const auto &ticket : commandTickets) {
    if (ticket.id == id) stateExpired = statePending && aqua::due(millis(), ticket.submitted, 5000);
  }
  if (stateExpired) {
    result += ",\"ok\":false,\"status\":\"timeout\",\"error\":\"A végrehajtás visszaigazolva, de friss állapot még nem érkezett. Ellenőrizd az állapotot újrapróbálás előtt.\"}";
  } else if (status.phase == boardlink::CommandPhase::Pending || statePending) result += ",\"ok\":true,\"status\":\"pending\"}";
  else if (status.phase == boardlink::CommandPhase::Applied) result += ",\"ok\":true,\"status\":\"applied\"}";
  else {
    result += ",\"ok\":false,\"status\":\"error\",\"error\":";
    String reason = status.error;
    if (status.phase == boardlink::CommandPhase::Timeout)
      reason = "Nem érkezett végrehajtási visszaigazolás. Ellenőrizd az állapotot újrapróbálás előtt.";
    else if (status.phase == boardlink::CommandPhase::PeerReboot)
      reason = "A másik board újraindult; a parancs eredménye nem igazolható.";
    else if (status.phase == boardlink::CommandPhase::Unknown)
      reason = "A parancsazonosító már nem ismert. Ellenőrizd az aktuális állapotot.";
    else if (reason == "command_busy") reason = "A másik board már egy parancsot dolgoz fel.";
    else if (!reason.length()) reason = "A parancs végrehajtása nem igazolható.";
    result += jsonString(reason); result += '}';
  }
  sendJson(200, result);
}

bool acceptsGzip() {
  const String value = server.header("Accept-Encoding");
  size_t offset = 0;
  while (offset < value.length()) {
    int end = value.indexOf(',', offset);
    if (end < 0) end = value.length();
    String token = value.substring(offset, end); token.trim(); token.toLowerCase();
    const int separator = token.indexOf(';');
    String name = separator < 0 ? token : token.substring(0, separator); name.trim();
    if (name == "gzip") {
      if (separator < 0) return true;
      String parameters = token.substring(separator + 1); parameters.trim();
      // Conservative: only explicitly positive standard q-values are accepted.
      if (parameters == "q=1" || parameters == "q=1.0" || parameters == "q=1.00" || parameters == "q=1.000") return true;
      if (parameters.startsWith("q=0.")) {
        for (size_t i = 4; i < parameters.length(); ++i) if (parameters[i] >= '1' && parameters[i] <= '9') return true;
      }
      return false;
    }
    offset = static_cast<size_t>(end) + 1;
  }
  return false;
}

void configureRoutes() {
  const char *headers[] = {"X-Aqua-Request", "Content-Type", "Content-Length", "Accept-Encoding"};
  server.collectHeaders(headers, 4);
  server.on("/", HTTP_GET, [] {
    server.sendHeader("Cache-Control", "no-store");
    server.sendHeader("X-Content-Type-Options", "nosniff");
    server.sendHeader("Vary", "Accept-Encoding");
    if (acceptsGzip()) {
      server.sendHeader("Content-Encoding", "gzip");
      server.send_P(200, "text/html; charset=utf-8", reinterpret_cast<const char *>(HUB_HTML_GZIP), sizeof(HUB_HTML_GZIP));
    } else server.send_P(200, "text/html; charset=utf-8", HUB_HTML);
  });
  server.on("/control", HTTP_GET, [] {
    server.sendHeader("Cache-Control", "no-store");
    server.sendHeader("X-Content-Type-Options", "nosniff");
    server.sendHeader("Vary", "Accept-Encoding");
    if (acceptsGzip()) {
      server.sendHeader("Content-Encoding", "gzip");
      server.send_P(200, "text/html; charset=utf-8", reinterpret_cast<const char *>(INDEX_HTML_GZIP), sizeof(INDEX_HTML_GZIP));
    } else server.send_P(200, "text/html; charset=utf-8", INDEX_HTML);
  });
  server.on("/favicon.ico", HTTP_GET, [] { server.send(204); });
  server.on("/api/state", HTTP_GET, sendState);
  server.on("/api/peer/state", HTTP_GET, sendPeerState);
  server.on("/api/peer/command", HTTP_GET, sendCommandStatus);
  const char *paths[] = {"/api/mode", "/api/manual", "/api/program", "/api/time", "/api/wifi", "/api/update/check", "/api/maintenance/ap"};
  for (const char *path : paths) {
    server.on(path, HTTP_POST, [path] { handleMutation(path, false); });
    const String remote = String("/api/peer/") + (path + 5);
    server.on(remote.c_str(), HTTP_POST, [path] { handleMutation(path, true); });
  }
  server.onNotFound([] { errorResponse(404, "Nincs ilyen oldal vagy végpont."); });
  server.begin();
  serverStarted = static_cast<bool>(server);
}


bool setAccessPoint(bool enabled) {
  if (MASTER) return !enabled;
  if (enabled == apActive) return true;
  bool okay;
  if (enabled) {
    okay = WiFi.softAP(apSsid.c_str(), apPassword.c_str(), radioChannel, false, 4);
    if (okay) apActive = true;
  } else {
    okay = WiFi.softAPdisconnect(true);
    if (okay) apActive = false;
  }
  lastApAttempt = millis(); haveApAttempt = true;
  Serial.printf("[NET] Slave fallback AP %s: %s; channel=%u.\n", enabled ? "start" : "stop", okay ? "okay" : "failed", radioChannel);
  return okay;
}

void updateRadioPolicy(uint32_t now) {
  uint8_t channel = 0;
  wifi_second_chan_t secondary;
  if (esp_wifi_get_channel(&channel, &secondary) == ESP_OK && channel >= 1 && channel <= 13) radioChannel = channel;
  const bool peerOnline = boardlink::online(now);
  if (!MASTER) {
    if (maintenanceButtonPolicy.tick(now, digitalRead(9) == LOW)) {
      accessPointPolicy.force(now);
      setAccessPoint(true);
    }
    const unsigned clients = apActive ? WiFi.softAPgetStationNum() : 0;
    const bool desired = accessPointPolicy.desired(now, peerOnline, masterNetworkConnected, clients);
    if (desired != apActive && (!haveApAttempt || aqua::due(now, lastApAttempt, 5000))) setAccessPoint(desired);
    // An attached maintenance client keeps its channel; no scan may throw it off.
    const uint8_t next = channelSearchPolicy.next(now, peerOnline, radioChannel, clients);
    if (next && !ledota::busy() && esp_wifi_set_channel(next, WIFI_SECOND_CHAN_NONE) == ESP_OK) radioChannel = next;
  }
  if (radioChannel != observedRadioChannel) {
    observedRadioChannel = radioChannel;
    channelStableSince = now;
  }
  if (peerOnline && persistedRadioChannel != radioChannel && aqua::due(now, channelStableSince, 20000) &&
      (!haveChannelPersistAttempt || aqua::due(now, lastChannelPersistAttempt, 30000))) {
    haveChannelPersistAttempt = true; lastChannelPersistAttempt = now;
    if (preferencesOpen && preferences.putUChar("mesh_ch", radioChannel) == 1) persistedRadioChannel = radioChannel;
  }
}

void acceptPeerSnapshot(const char *data, size_t length, uint32_t now) {
  if (!length || length > boardlink::STATE_LIMIT || strlen(data) != length) { ++peerStateErrors; return; }
  cJSON *state = cJSON_Parse(data);
  if (!state) { ++peerStateErrors; return; }
  const cJSON *channels = cJSON_GetObjectItemCaseSensitive(state, "channels");
  const cJSON *mesh = cJSON_GetObjectItemCaseSensitive(state, "mesh");
  const cJSON *role = cJSON_GetObjectItemCaseSensitive(mesh, "role");
  if (!cJSON_IsNumber(channels) || channels->valuedouble != (LED_CHANNELS == 2 ? 1 : 2) ||
      !cJSON_IsString(role) || strcmp(role->valuestring, MASTER ? "slave" : "master")) {
    cJSON_Delete(state); ++peerStateErrors; return;
  }
  const cJSON *revision = cJSON_GetObjectItemCaseSensitive(state, "configRevision");
  if (!cJSON_IsNumber(revision) || revision->valuedouble < 0 || revision->valuedouble > UINT32_MAX) {
    cJSON_Delete(state); ++peerStateErrors; return;
  }
  peerState = data;
  if (peerState.length() != length) { cJSON_Delete(state); ++peerStateErrors; havePeerState = false; return; }
  peerStateAt = now; havePeerState = true;
  peerConfigRevision = static_cast<uint32_t>(revision->valuedouble);
  if (!MASTER) {
    const cJSON *network = cJSON_GetObjectItemCaseSensitive(state, "network");
    masterNetworkConnected = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(network, "connected"));
  }
  const bool clockValid = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(state, "clockValid"));
  const cJSON *epoch = cJSON_GetObjectItemCaseSensitive(state, "epoch");
  if (clockValid && cJSON_IsNumber(epoch) && epoch->valuedouble >= MIN_EPOCH && epoch->valuedouble <= MAX_EPOCH) {
    const time_t target = static_cast<time_t>(epoch->valuedouble), current = time(nullptr);
    const bool localValid = timeSource.load() != TimeSource::None &&
        current >= static_cast<time_t>(MIN_EPOCH) && current <= static_cast<time_t>(MAX_EPOCH);
    if (aqua::shouldAdoptPeerClock(MASTER, localValid, current, target)) {
      timeval value = {target, 0};
      if (settimeofday(&value, nullptr) == 0) { timeSource.store(TimeSource::Mesh); requestOutput(); }
    }
  }
  cJSON_Delete(state);
}

void pollMesh(uint32_t now) {
  // Association scans temporarily own the shared radio. Keep RX and protocol
  // deadlines alive, but do not transmit or reset ESP-NOW during that window.
  boardlink::tick(now, !MASTER || !staAttemptActive);
  const uint32_t peerBoot = boardlink::peerBootId();
  if (peerBoot != lastPeerBoot) {
    lastPeerBoot = peerBoot;
    havePeerState = false;
    peerState = "";
    peerConfigRevision = 0;
    masterNetworkConnected = false;
  }
  static char receivedState[boardlink::STATE_LIMIT + 1];
  size_t length = 0;
  if (boardlink::receiveState(receivedState, sizeof(receivedState), &length)) acceptPeerSnapshot(receivedState, length, now);
  static boardlink::ReceivedCommand command;
  if (boardlink::receiveCommand(command)) {
    MutationResult result{503, "Nincs elegendő memória a parancshoz."};
    // A temporary parser is released before the response/state is generated.
    // No HTTP client or radio callback owns application state.
    {
      std::unique_ptr<aqua::HttpRequest> request(new (std::nothrow) aqua::HttpRequest());
      if (request) {
        String header = String("POST ") + command.path + " HTTP/1.1\r\nHost: local\r\nX-Aqua-Request: 1\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: ";
        header += strlen(command.form); header += "\r\n\r\n";
        request->feed(reinterpret_cast<const uint8_t *>(header.c_str()), header.length());
        request->feed(reinterpret_cast<const uint8_t *>(command.form), strlen(command.form));
        if (request->errorStatus() || !request->complete()) result = {400, "Hibás rádiós parancs."};
        else result = executeMutation(command.path, *request);
        request->reset();
      }
    }
    if (result.error.length() > boardlink::ERROR_LIMIT) result.error = "A parancs nem hajtható végre.";
    if (!boardlink::replyCommand(command.id, result.status, result.error.c_str(), configRevision)) ++peerStateErrors;
    memset(&command, 0, sizeof(command));
    publishLocalState();
  }
  if (aqua::due(now, lastStatePublished, 2000)) publishLocalState();
}

#if LED_BLE_ONLY
// BLE carries one flat envelope with three string fields, not arbitrary JSON.
// Reject nesting before cJSON so a malformed peer cannot grow its parse stack.
bool flatBleEnvelope(const char *data, size_t length) {
  if (!data || !length || strlen(data) != length || strstr(data, "\\u0000")) return false;
  bool quoted = false, escaped = false;
  unsigned depth = 0;
  for (size_t i = 0; i < length; ++i) {
    const unsigned char c = data[i];
    if (quoted) {
      if (escaped) escaped = false;
      else if (c == '\\') escaped = true;
      else if (c == '"') quoted = false;
    } else if (c == '"') quoted = true;
    else if (c == '[' || c == ']') return false;
    else if (c == '{') { if (++depth > 1) return false; }
    else if (c == '}') { if (!depth) return false; --depth; }
  }
  return !quoted && !depth;
}

void pollBle(uint32_t now) {
  blelink::tick(now);
  static char payload[blelink::REQUEST_LIMIT + 1];
  size_t length = 0; uint16_t id = 0;
  if (!blelink::receiveRequest(payload, sizeof(payload), &length, &id)) return;
  int status = 400;
  String body;
  const auto memoryFailure = [id]() {
    ++bleReplyFailures;
    constexpr char failure[] = "{\"status\":503,\"body\":{\"error\":\"Memóriahiány.\"}}";
    blelink::reply(id, failure, sizeof(failure) - 1);
  };
  if (!body.reserve(128)) { memset(payload, 0, sizeof(payload)); memoryFailure(); return; }
  body = "{\"error\":\"Hibás Bluetooth-kérés.\"}";
  const char *end = nullptr;
  cJSON *envelope = flatBleEnvelope(payload, length)
      ? cJSON_ParseWithLengthOpts(payload, length + 1, &end, true) : nullptr;
  if (envelope && end == payload + length && cJSON_IsObject(envelope) && cJSON_GetArraySize(envelope) == 3) {
    cJSON *method = cJSON_GetObjectItemCaseSensitive(envelope, "method");
    cJSON *path = cJSON_GetObjectItemCaseSensitive(envelope, "path");
    cJSON *form = cJSON_GetObjectItemCaseSensitive(envelope, "form");
    if (cJSON_IsString(method) && cJSON_IsString(path) && cJSON_IsString(form)) {
      if (!strcmp(method->valuestring, "GET") && !strcmp(path->valuestring, "/api/state") && !*form->valuestring) {
        body = buildState(); status = body.length() ? 200 : 503;
        if (!body.length()) body = "{\"error\":\"Nincs elegendő memória az állapothoz.\"}";
      } else if (!strcmp(method->valuestring, "POST") &&
          (!strcmp(path->valuestring, "/api/mode") || !strcmp(path->valuestring, "/api/manual") ||
           !strcmp(path->valuestring, "/api/program") || !strcmp(path->valuestring, "/api/time")) &&
          strlen(form->valuestring) <= 1536) {
        MutationResult result{503, "Nincs elegendő memória a parancshoz."};
        std::unique_ptr<aqua::HttpRequest> request(new (std::nothrow) aqua::HttpRequest());
        if (request) {
          String header = String("POST ") + path->valuestring + " HTTP/1.1\r\nHost: local\r\nX-Aqua-Request: 1\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: ";
          header += strlen(form->valuestring); header += "\r\n\r\n";
          request->feed(reinterpret_cast<const uint8_t *>(header.c_str()), header.length());
          request->feed(reinterpret_cast<const uint8_t *>(form->valuestring), strlen(form->valuestring));
          result = executeMutation(path->valuestring, *request);
          request->reset();
        }
        status = result.status;
        if (status < 400) {
          body = "{\"ok\":true}";
          if (!strcmp(path->valuestring, "/api/time")) bleClientValidated = true;
        }
        else {
          const String error = jsonString(result.error);
          if (!error.length() || !body.reserve(error.length() + 12)) body = String();
          else { body = "{\"error\":"; body += error; body += '}'; }
        }
      }
    }
  }
  cJSON_Delete(envelope);
  memset(payload, 0, sizeof(payload));
  if (!body.length()) { memoryFailure(); return; }
  String reply;
  if (!reply.reserve(body.length() + 40)) {
    memoryFailure();
    return;
  }
  reply = "{\"status\":"; reply += status; reply += ",\"body\":"; reply += body; reply += '}';
  if (reply.length() > blelink::RESPONSE_LIMIT) {
    ++bleReplyFailures;
    constexpr char failure[] = "{\"status\":503,\"body\":{\"error\":\"Az állapot túl hosszú.\"}}";
    blelink::reply(id, failure, sizeof(failure) - 1);
  } else if (!blelink::reply(id, reply.c_str(), reply.length())) {
    ++bleReplyFailures;
  }
}
#endif

void pollUsbStatus(uint32_t now) {
#if ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT
  static aqua::UsbStatusRequest parser;
  static bool pending = false;
  static uint32_t requestedAt = 0;
  for (unsigned budget = 0; budget < 32 && Serial.available() > 0; ++budget) {
    const int input = Serial.read();
    if (input < 0) break;
    if (parser.feed(static_cast<uint8_t>(input))) { pending = true; requestedAt = now; }
  }
  if (!pending) return;
  if (aqua::due(now, requestedAt, 2000)) { pending = false; return; }
  const auto sample = sensorSnapshot();
  const auto output = outputSnapshot();
  portENTER_CRITICAL(&controllerMux);
  const uint32_t ticks = controlTicks, gap = maxControlGapMs, lastTick = lastControlAt;
  portEXIT_CRITICAL(&controllerMux);
#if LED_BLE_ONLY
  const boardlink::Counters counters{};
  const bool networkConnected = false, peerConnected = false;
  const IPAddress ip;
#else
  const auto counters = boardlink::counters();
  const bool networkConnected = WiFi.status() == WL_CONNECTED, peerConnected = boardlink::online(now);
  const IPAddress ip = WiFi.localIP();
#endif
  time_t epoch; tm local = {};
  const bool clock = readClock(epoch, local);
  char line[352];
  const int length = snprintf(line, sizeof(line),
    "{\"usbStatus\":1,\"version\":\"%s\",\"up\":%lu,\"net\":%u,\"ip\":\"%u.%u.%u.%u\",\"sta\":%u,\"reason\":%d,\"ch\":%u,\"peer\":%u,\"ap\":%u,\"clock\":%u,\"ticks\":%lu,\"age\":%lu,\"gap\":%lu,\"http\":%lu,\"reads\":%lu,\"errors\":%lu,\"heap\":%lu,\"radio\":%lu,\"warm\":%.2f,\"cool\":%.2f}\n",
    LED_CONTROL_VERSION, static_cast<unsigned long>(uptimeMs / 1000),
    networkConnected ? 1u : 0u,
    static_cast<unsigned>(ip[0]), static_cast<unsigned>(ip[1]), static_cast<unsigned>(ip[2]), static_cast<unsigned>(ip[3]),
    staAttemptActive ? 1u : 0u,
    static_cast<int>(lastStaReason.load()), static_cast<unsigned>(radioChannel),
    peerConnected ? 1u : 0u, apActive ? 1u : 0u, clock ? 1u : 0u,
    static_cast<unsigned long>(ticks), static_cast<unsigned long>(ticks ? static_cast<uint32_t>(millis() - lastTick) : UINT32_MAX),
    static_cast<unsigned long>(gap),
    static_cast<unsigned long>(httpDiagnostics.maxMs),
    static_cast<unsigned long>(sample.diagnostics.readAttempts),
    static_cast<unsigned long>(sample.diagnostics.readFailures),
    static_cast<unsigned long>(ESP.getFreeHeap()), static_cast<unsigned long>(counters.radioRecoveries),
    static_cast<double>(output.warm), static_cast<double>(output.cool));
  if (length > 0 && static_cast<size_t>(length) < sizeof(line) && Serial.availableForWrite() >= length) {
    // One small write keeps the JSON line together. A congested USB host cannot
    // retain requests indefinitely. USB lock/ring-buffer waits use a 1 ms budget.
    Serial.write(reinterpret_cast<const uint8_t *>(line), static_cast<size_t>(length));
    pending = false;
  }
#endif
}

void setup() {
  const uint32_t recoveryMarker = wifiRecoveryClockMarker;
  wifiRecoveryClockMarker = 0;
  pwmOk = startPwm();
#if ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxBufferSize(512);  // Fit a complete compact status line without splitting it.
  Serial.setTxTimeoutMs(1);  // Keep USB logging waits short when the host stops reading.
#endif
  Serial.begin(115200);
  const uint32_t started = millis();
  while (!Serial && !aqua::due(millis(), started, 1500)) delay(10);
  Serial.println("\nAquaLight master/slave controller " LED_CONTROL_VERSION);
  loadSettings();
  initializeClock(recoveryMarker);
  setenv("TZ", TIMEZONE, 1); tzset();
  if (!MASTER) pinMode(9, INPUT_PULLUP);  // SuperMini BOOT: hold 3 s for maintenance AP, no reset.
  if (xTaskCreate(controlWorker, "led-control", 3072, nullptr, 2, &controlTaskHandle) != pdPASS) {
    controlTaskHandle = nullptr; stopPwm(); Serial.println("[CONTROL] Worker could not start; outputs OFF.");
  }
  if (xTaskCreate(sensorWorker, "aht20", 4096, nullptr, 1, &sensorTaskHandle) != pdPASS) {
    sensorTaskHandle = nullptr; Serial.println("[AHT20] Worker could not start.");
  }
#if LED_BLE_ONLY
  const bool bleReady = blelink::begin({LED_CHANNELS, bleconfig::passkey, "AquaLight terrarium", LED_CONTROL_VERSION});
  ledota::begin(false);
  Serial.printf("[BLE] %s; Wi-Fi/ESP-NOW are inactive.\n", bleReady ? "ready" : "initialization failed");
#else
  startNetwork();  // ESP-NOW starts before any router connection can succeed/fail.
  if (MASTER) {
    sntp_set_time_sync_notification_cb([](timeval *) { timeSource.store(TimeSource::Ntp); requestOutput(); });
    configTzTime(TIMEZONE, "pool.ntp.org", "time.cloudflare.com", "time.google.com");
  }
  configureRoutes();
  ledota::begin(MASTER);
  maintenance::begin(apPassword, flushPendingSettings, maintenanceStatus);
#endif
  previousMillis = millis();
  requestOutput();
  Serial.printf("[READY] role=%s mode=%s; slave fallback AP only on setup/failure.\n", MASTER ? "master" : "slave", modeName(settings.mode));
}

void loop() {
  uint32_t now = millis();
  const uint32_t loopStarted = now;
  uptimeMs += static_cast<uint32_t>(now - previousMillis);
  previousMillis = now;
#if LED_BLE_ONLY
  pollBle(now);
#else
  pollMesh(now);
  const uint32_t httpStarted = millis();
  server.handleClient();
  httpDiagnostics.observe(millis() - httpStarted);
  now = millis();
  updateNetwork(now);
  updateRadioPolicy(now);
#endif
  now = millis();  // BLE mutations may stamp a newer millis() than the loop entry.
  pollUsbStatus(now);
  if (settingsDirty && aqua::due(now, lastManualChange, 2000) &&
      (!lastSaveFailed || aqua::due(now, lastSaveAttempt, 30000))) {
    lastSaveAttempt = now;
    lastSaveFailed = !saveSettings(settings);
    if (!lastSaveFailed) settingsDirty = false;
  }
  if (maintenance::rebootRequested() && settingsDirty) flushPendingSettings();
  portENTER_CRITICAL(&controllerMux);
  const bool controlHealthy = controlTicks && static_cast<uint32_t>(millis() - lastControlAt) < 500;
  portEXIT_CRITICAL(&controllerMux);
  const bool transportHealthy =
#if LED_BLE_ONLY
      blelink::ready() && bleClientValidated;
#else
      serverStarted;
#endif
  const bool healthy = pwmOk && storageOk && transportHealthy && controlTaskHandle && sensorTaskHandle && controlHealthy;
  ledota::poll(healthy, !settingsDirty && storageOk);
#if !LED_BLE_ONLY
  maintenance::poll();
  if (maintenance::rebootRequested()) maintenance::rebootIfSafe(flushPendingSettings() && !settingsDirty);
#endif
  // Network loss is a degraded state, never a reason to reboot local lighting.
  delay(1);
  loopDiagnostics.observe(millis() - loopStarted);
}
