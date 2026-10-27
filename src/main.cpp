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
#include <atomic>
#include <cmath>
#include <cstring>
#include <ctime>
#include <sys/time.h>

#include "schedule.h"
#include "web_ui.h"

// Confirmed wiring: warm 2700 K -> GPIO0, cool 6500 K -> GPIO4.
constexpr uint8_t PWM_PINS[] = {0, 4};
constexpr bool PWM_ACTIVE_LOW[] = {false, false};
constexpr uint32_t PWM_FREQUENCY_HZ = 1000;
constexpr uint8_t PWM_BITS = 10;
constexpr int I2C_SDA = 1;
constexpr int I2C_SCL = 3;
constexpr char TIMEZONE[] = "CET-1CEST,M3.5.0/2,M10.5.0/3";
constexpr uint64_t MIN_EPOCH = 1704067200ULL;  // 2024-01-01 UTC.
constexpr uint64_t MAX_EPOCH = 4102444799ULL;  // 2099-12-31 UTC.

WebServer server(80);
Preferences preferences;
AHTxx aht20(AHTXX_ADDRESS_X38, AHT2x_SENSOR);
aqua::Settings settings = aqua::defaults();
aqua::Levels actual;
bool pwmOk = false;
bool pwmAttached[2] = {false, false};
bool preferencesOpen = false;
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
enum class TimeSource : uint8_t { None, Browser, Ntp };
std::atomic<TimeSource> timeSource{TimeSource::None};
String apSsid;
String apPassword;
bool apActive = false;
bool wasConnected = false;
bool wifiPending = false;
uint32_t wifiRequested = 0;
uint32_t lastWifiAttempt = 0;
uint64_t uptimeMs = 0;
uint32_t previousMillis = 0;

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
  for (uint8_t i = 0; i < 2; ++i) {
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

bool startPwm() {
  stopPwm();
  ledc_timer_config_t timer = {};
  timer.speed_mode = LEDC_LOW_SPEED_MODE;
  timer.duty_resolution = static_cast<ledc_timer_bit_t>(PWM_BITS);
  timer.timer_num = LEDC_TIMER_0;
  timer.freq_hz = PWM_FREQUENCY_HZ;
  timer.clk_cfg = LEDC_AUTO_CLK;
  if (ledc_timer_config(&timer) != ESP_OK) return false;
  for (uint8_t i = 0; i < 2; ++i) {
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

void applyOutput() {
  if (!pwmOk) return;
  time_t epoch;
  tm local = {};
  const bool valid = readClock(epoch, local);
  const aqua::Levels desired = aqua::output(settings, valid,
      local.tm_hour * 3600UL + local.tm_min * 60UL + local.tm_sec);
  const float values[] = {desired.warm, desired.cool};
  const float previous[] = {actual.warm, actual.cool};
  for (uint8_t i = 0; i < 2; ++i) {
    const uint32_t value = aqua::duty(values[i], PWM_ACTIVE_LOW[i]);
    if (value == aqua::duty(previous[i], PWM_ACTIVE_LOW[i])) continue;
    const auto channel = static_cast<ledc_channel_t>(i);
    if (ledc_set_duty(LEDC_LOW_SPEED_MODE, channel, value) != ESP_OK ||
        ledc_update_duty(LEDC_LOW_SPEED_MODE, channel) != ESP_OK) {
      stopPwm();
      Serial.println("[PWM] WRITE FAILED: both channels held OFF.");
      return;
    }
  }
  actual = desired;
}

String encodeSettings(const aqua::Settings &value) {
  String text = "1,";
  text += static_cast<unsigned>(value.mode);
  text += ','; text += value.manualWarm;
  text += ','; text += value.manualCool;
  text += '|';
  for (uint8_t i = 0; i < value.count; ++i) {
    if (i) text += ';';
    text += value.points[i].minute;
    text += ','; text += value.points[i].warm;
    text += ','; text += value.points[i].cool;
  }
  return text;
}

bool saveSettings(const aqua::Settings &value) {
  const String encoded = encodeSettings(value);
  storageOk = preferencesOpen && preferences.putString("settings", encoded) == encoded.length();
  if (!storageOk) Serial.println("[NVS] Settings save failed.");
  return storageOk;
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
    Serial.println("[NVS] Cannot open storage; defaults active, lamp OFF.");
    return;
  }
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

void startNetwork() {
  WiFi.persistent(false);
  WiFi.setHostname("akvarium");
  WiFi.mode(WIFI_AP_STA);  // Enable RF entropy before creating the first AP password.
  if (apPassword.length() != 12) {
    constexpr char alphabet[] = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
    apPassword = "";
    for (uint8_t i = 0; i < 12; ++i) apPassword += alphabet[esp_random() % (sizeof(alphabet) - 1)];
    if (!preferencesOpen || preferences.putString("ap_pass", apPassword) != apPassword.length()) storageOk = false;
  }
  const uint64_t mac = ESP.getEfuseMac();
  char suffix[7];
  snprintf(suffix, sizeof(suffix), "%02X%02X%02X", static_cast<unsigned>((mac >> 24) & 0xff),
           static_cast<unsigned>((mac >> 32) & 0xff), static_cast<unsigned>((mac >> 40) & 0xff));
  apSsid = String("AquaLight-") + suffix;
  apActive = WiFi.softAP(apSsid.c_str(), apPassword.c_str(), 1, false, 4);
  Serial.printf("[NET] AP SSID: %s\n", apSsid.c_str());
  Serial.printf("[NET] AP password: %s\n", apPassword.c_str());
  Serial.printf("[NET] AP URL: http://%s/ (%s)\n", WiFi.softAPIP().toString().c_str(), apActive ? "ready" : "FAILED");
  WiFi.setAutoReconnect(true);
  if (wifi.ssid[0]) {
    WiFi.begin(wifi.ssid, wifi.password);
    lastWifiAttempt = millis();
  }
  if (MDNS.begin("akvarium")) MDNS.addService("http", "tcp", 80);
}

void updateNetwork(uint32_t now) {
  if (wifiPending && aqua::due(now, wifiRequested, 300)) {
    wifiPending = false;
    WiFi.disconnect(false, false, 50);
    WiFi.begin(wifi.ssid, wifi.password);
    lastWifiAttempt = now;
  }
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !wasConnected) {
    Serial.printf("[NET] STA URL: http://%s/ (http://akvarium.local/)\n", WiFi.localIP().toString().c_str());
  }
  if (!connected && wifi.ssid[0] && !wifiPending && aqua::due(now, lastWifiAttempt, 30000)) {
    WiFi.reconnect();
    lastWifiAttempt = now;
  }
  wasConnected = connected;
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
  state += ",\"timeSource\":"; state += jsonString(!clockValid ? "none" : source == TimeSource::Ntp ? "ntp" : "browser");
  state += ",\"schedule\":[";
  for (uint8_t i = 0; i < settings.count; ++i) {
    if (i) state += ',';
    state += "{\"minute\":"; state += settings.points[i].minute;
    state += ",\"warm\":"; state += settings.points[i].warm;
    state += ",\"cool\":"; state += settings.points[i].cool; state += '}';
  }
  const bool connected = WiFi.status() == WL_CONNECTED;
  state += "],\"network\":{\"connected\":"; state += connected ? "true" : "false";
  state += ",\"ssid\":"; state += jsonString(wifi.ssid);
  state += ",\"ip\":"; state += jsonString(connected ? WiFi.localIP().toString() : String(""));
  state += ",\"apActive\":"; state += apActive ? "true" : "false";
  state += ",\"apSsid\":"; state += jsonString(apSsid);
  state += ",\"apIp\":"; state += jsonString(WiFi.softAPIP().toString());
  state += ",\"rssi\":"; state += connected ? WiFi.RSSI() : 0;
  state += "},\"warmPin\":0,\"coolPin\":4,\"storageOk\":"; state += storageOk ? "true" : "false";
  state += ",\"savePending\":"; state += settingsDirty ? "true" : "false";
  state += ",\"uptime\":"; state += String(static_cast<unsigned long long>(uptimeMs / 1000));
  state += '}';
  sendJson(200, state);
}

void configureRoutes() {
  const char *headers[] = {"X-Aqua-Request", "Content-Type", "Content-Length"};
  server.collectHeaders(headers, 3);
  server.on("/", HTTP_GET, [] {
    server.sendHeader("Cache-Control", "no-store");
    server.sendHeader("X-Content-Type-Options", "nosniff");
    server.send_P(200, "text/html; charset=utf-8", INDEX_HTML);
  });
  server.on("/favicon.ico", HTTP_GET, [] { server.send(204); });
  server.on("/api/state", HTTP_GET, sendState);
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
    if (!pwmOk) { errorResponse(503, "A PWM-kimenet hibás."); return; }
    settings.mode = aqua::Mode::Manual;
    settings.manualWarm = warm;
    settings.manualCool = cool;
    settingsDirty = true;
    lastManualChange = millis();
    applyOutput();
    sendJson(200, "{\"ok\":true}");
  });
  server.on("/api/program", HTTP_POST, [] {
    if (!writableRequest(1)) return;
    const String points = server.arg("points");
    aqua::Settings candidate = settings;
    if (points.length() > 512 || points.length() != strlen(points.c_str()) ||
        !aqua::parseProgram(points.c_str(), candidate)) {
      errorResponse(400, "2–16 különböző, időrendbe rendezett pont kell; a fényerők 0–100 között legyenek."); return;
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
}

void setup() {
  pwmOk = startPwm();
  Serial.begin(115200);
  const uint32_t started = millis();
  while (!Serial && !aqua::due(millis(), started, 1500)) delay(10);
  Serial.println("\nAquaLight ESP32-C3 controller");
  Serial.println("[PWM] Warm GPIO0 (2700 K), cool GPIO4 (6500 K), 1000 Hz, 10 bit.");
  if (!pwmOk) Serial.println("[PWM] INIT FAILED; both channels held OFF.");
  loadSettings();
  // Never restore a saved wall-clock timestamp: after a power outage it would be stale.
  timeval unset = {};
  settimeofday(&unset, nullptr);
  sntp_set_time_sync_notification_cb([](timeval *) { timeSource.store(TimeSource::Ntp); });
  configTzTime(TIMEZONE, "pool.ntp.org", "time.cloudflare.com", "time.google.com");
  startNetwork();
  configureRoutes();
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
  delay(1);
}
