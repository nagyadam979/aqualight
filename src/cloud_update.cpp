#include "cloud_update.h"
#include "ota_contract.h"
#include "ota_public_key.h"

#include <WiFi.h>
#include <cJSON.h>
#include <esp_crt_bundle.h>
#include <esp_http_client.h>
#include <esp_ota_ops.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <mbedtls/base64.h>
#include <mbedtls/pk.h>
#include <mbedtls/sha256.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <ctime>

#if !CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE || !CONFIG_MBEDTLS_CERTIFICATE_BUNDLE
#error "LED OTA requires rollback-enabled bootloader and the trusted CA bundle"
#endif
#ifndef LED_CONTROL_VERSION
#define LED_CONTROL_VERSION "1.1.0"
#endif

namespace ledota {
namespace {
constexpr uint32_t FIRST_CHECK_MS = 60000;
constexpr uint32_t CHECK_INTERVAL_MS = 30 * 60000;
constexpr uint32_t MIN_VALIDATION_MS = 10000;
constexpr uint32_t HEALTH_STABLE_MS = 3000;
constexpr uint32_t VALIDATION_DEADLINE_MS = 60000;
constexpr int HTTP_TIMEOUT_MS = 7000;
SemaphoreHandle_t flashMutex = nullptr;
TaskHandle_t workerTask = nullptr;
portMUX_TYPE stateMux = portMUX_INITIALIZER_UNLOCKED;
struct State {
  char state[24] = "waiting";
  char releaseId[65] = {};
  char availableVersion[65] = {};
  char error[96] = {};
  unsigned progress = 0;
  uint32_t lastCheck = 0;
} state;
std::atomic<bool> queued{false}, running{false}, ready{false}, bootSelected{false};
std::atomic<uint32_t> nextCheck{FIRST_CHECK_MS};
bool configured = false, pendingValidation = false, rollbackFailed = false;
uint32_t startedMs = 0, healthySince = 0;
unsigned failures = 0;

void setState(const char *name, const char *error = "", unsigned progress = 0) {
  portENTER_CRITICAL(&stateMux);
  snprintf(state.state, sizeof(state.state), "%s", name);
  snprintf(state.error, sizeof(state.error), "%s", error);
  state.progress = progress;
  portEXIT_CRITICAL(&stateMux);
}

cJSON *strictJson(const char *text, std::size_t length) {
  if (!text || !length || strlen(text) != length || strstr(text, "\\u0000")) return nullptr;
  const char *end = nullptr;
  cJSON *json = cJSON_ParseWithLengthOpts(text, length + 1, &end, true);
  if (!json || end != text + length) { cJSON_Delete(json); return nullptr; }
  return json;
}

bool exactKeys(cJSON *object, const char *const *keys, std::size_t count) {
  if (!cJSON_IsObject(object) || cJSON_GetArraySize(object) != static_cast<int>(count)) return false;
  for (std::size_t i = 0; i < count; ++i) {
    unsigned matches = 0;
    for (cJSON *item = object->child; item; item = item->next)
      if (item->string && strcmp(item->string, keys[i]) == 0) ++matches;
    if (matches != 1) return false;
  }
  return true;
}

bool jsonString(cJSON *object, const char *name, std::string &out) {
  cJSON *item = cJSON_GetObjectItemCaseSensitive(object, name);
  if (!cJSON_IsString(item) || !item->valuestring) return false;
  out = item->valuestring;
  return true;
}

bool verifySignature(const char *manifest, const char *signature) {
  if (!manifest || !signature || !*manifest || strlen(manifest) > MAX_MANIFEST_SIZE ||
      !*signature || strlen(signature) > 192) return false;
  uint8_t der[128], digest[32];
  size_t derLength = 0;
  if (mbedtls_base64_decode(der, sizeof(der), &derLength,
      reinterpret_cast<const unsigned char *>(signature), strlen(signature)) != 0) return false;
  mbedtls_pk_context key;
  mbedtls_pk_init(&key);
  bool okay = mbedtls_pk_parse_public_key(&key,
      reinterpret_cast<const unsigned char *>(OTA_PUBLIC_KEY_PEM), sizeof(OTA_PUBLIC_KEY_PEM)) == 0 &&
      mbedtls_pk_can_do(&key, MBEDTLS_PK_ECKEY) && mbedtls_pk_get_bitlen(&key) == 256 &&
      mbedtls_sha256(reinterpret_cast<const unsigned char *>(manifest), strlen(manifest), digest, 0) == 0 &&
      mbedtls_pk_verify(&key, MBEDTLS_MD_SHA256, digest, sizeof(digest), der, derLength) == 0;
  mbedtls_pk_free(&key);
  return okay;
}

bool parseEnvelope(const String &body, Manifest &manifest) {
  cJSON *outer = strictJson(body.c_str(), body.length());
  const char *const outerKeys[] = {"manifest", "signature"};
  std::string text, signature;
  bool okay = exactKeys(outer, outerKeys, 2) && jsonString(outer, "manifest", text) &&
      jsonString(outer, "signature", signature) && verifySignature(text.c_str(), signature.c_str());
  cJSON *inner = okay ? strictJson(text.c_str(), text.size()) : nullptr;
  const char *const innerKeys[] = {"schema", "target", "channel", "version", "release_id", "image_path", "size", "sha256"};
  okay = okay && exactKeys(inner, innerKeys, 8);
  if (okay) {
    cJSON *schema = cJSON_GetObjectItemCaseSensitive(inner, "schema");
    cJSON *size = cJSON_GetObjectItemCaseSensitive(inner, "size");
    okay = cJSON_IsNumber(schema) && schema->valuedouble == 1 && cJSON_IsNumber(size) &&
        std::isfinite(size->valuedouble) && size->valuedouble >= 288 &&
        size->valuedouble <= MAX_IMAGE_SIZE && floor(size->valuedouble) == size->valuedouble &&
        jsonString(inner, "target", manifest.target) && jsonString(inner, "channel", manifest.channel) &&
        jsonString(inner, "version", manifest.version) && jsonString(inner, "release_id", manifest.releaseId) &&
        jsonString(inner, "image_path", manifest.imagePath) && jsonString(inner, "sha256", manifest.sha256);
    if (okay) {
      manifest.schema = 1;
      manifest.size = static_cast<uint64_t>(size->valuedouble);
      okay = validManifest(manifest);
    }
  }
  cJSON_Delete(inner);
  cJSON_Delete(outer);
  return okay;
}

esp_http_client_handle_t openHttp(const char *url) {
  esp_http_client_config_t config = {};
  config.url = url;
  config.crt_bundle_attach = esp_crt_bundle_attach;
  config.timeout_ms = HTTP_TIMEOUT_MS;
  config.disable_auto_redirect = true;
  config.max_redirection_count = 0;
  config.buffer_size = 2048;
  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client && esp_http_client_open(client, 0) == ESP_OK) return client;
  if (client) esp_http_client_cleanup(client);
  return nullptr;
}

void closeHttp(esp_http_client_handle_t client) {
  esp_http_client_close(client);
  esp_http_client_cleanup(client);
}

bool fetchEnvelope(String &body) {
  const uint32_t started = millis();
  esp_http_client_handle_t client = openHttp(CHANNEL_URL);
  if (!client) return false;
  const int64_t length = esp_http_client_fetch_headers(client);
  bool okay = esp_http_client_get_status_code(client) == 200 && length > 0 &&
      length <= static_cast<int64_t>(MAX_ENVELOPE_SIZE) && body.reserve(length);
  char chunk[512];
  while (okay && body.length() < static_cast<size_t>(length)) {
    const int wanted = std::min(static_cast<size_t>(length) - body.length(), sizeof(chunk));
    const int received = esp_http_client_read(client, chunk, wanted);
    okay = received > 0 && body.concat(chunk, received) && millis() - started < 20000;
  }
  okay = okay && body.length() == static_cast<size_t>(length) &&
      esp_http_client_is_complete_data_received(client);
  closeHttp(client);
  return okay;
}

bool digestMatches(const uint8_t digest[32], const Manifest &manifest) {
  char hex[65];
  for (unsigned i = 0; i < 32; ++i) snprintf(hex + i * 2, 3, "%02x", digest[i]);
  return manifest.sha256 == hex;
}

bool hashMatches(const esp_partition_t *partition, const Manifest &manifest) {
  if (!partition || manifest.size > partition->size) return false;
  mbedtls_sha256_context hash;
  mbedtls_sha256_init(&hash);
  bool okay = mbedtls_sha256_starts(&hash, 0) == 0;
  uint8_t bytes[2048], digest[32];
  for (uint64_t offset = 0; okay && offset < manifest.size; offset += sizeof(bytes)) {
    const size_t count = std::min(static_cast<uint64_t>(sizeof(bytes)), manifest.size - offset);
    okay = esp_partition_read(partition, offset, bytes, count) == ESP_OK &&
        mbedtls_sha256_update(&hash, bytes, count) == 0;
    taskYIELD();
  }
  okay = okay && mbedtls_sha256_finish(&hash, digest) == 0;
  mbedtls_sha256_free(&hash);
  return okay && digestMatches(digest, manifest);
}

const esp_partition_t *downloadImage(const Manifest &manifest) {
  const uint32_t started = millis();
  const esp_partition_t *target = esp_ota_get_next_update_partition(nullptr);
  if (!target || target == esp_ota_get_running_partition() || manifest.size > target->size) return nullptr;
  const std::string url = std::string(HOST) + manifest.imagePath;
  esp_http_client_handle_t client = openHttp(url.c_str());
  if (!client) return nullptr;
  const int64_t length = esp_http_client_fetch_headers(client);
  bool okay = esp_http_client_get_status_code(client) == 200 && length == static_cast<int64_t>(manifest.size);
  esp_ota_handle_t handle = 0;
  mbedtls_sha256_context hash;
  mbedtls_sha256_init(&hash);
  okay = okay && mbedtls_sha256_starts(&hash, 0) == 0;
  uint8_t bytes[2048];
  uint64_t written = 0;
  // The first response chunk can be short; assemble enough for the actual C3 app descriptor.
  size_t first = 0;
  while (okay && first < 288) {
    const int count = esp_http_client_read(client, reinterpret_cast<char *>(bytes + first), 288 - first);
    okay = count > 0 && millis() - started < 120000;
    if (okay) first += count;
  }
  okay = okay && validImagePrefix(bytes, first) && esp_ota_begin(target, manifest.size, &handle) == ESP_OK;
  if (okay) {
    okay = esp_ota_write(handle, bytes, first) == ESP_OK && mbedtls_sha256_update(&hash, bytes, first) == 0;
    written = first;
  }
  while (okay && written < manifest.size) {
    const int wanted = std::min(static_cast<uint64_t>(sizeof(bytes)), manifest.size - written);
    const int count = esp_http_client_read(client, reinterpret_cast<char *>(bytes), wanted);
    okay = count > 0 && millis() - started < 120000 && esp_ota_write(handle, bytes, count) == ESP_OK &&
        mbedtls_sha256_update(&hash, bytes, count) == 0;
    if (okay) {
      written += count;
      portENTER_CRITICAL(&stateMux);
      state.progress = static_cast<unsigned>(written * 100 / manifest.size);
      portEXIT_CRITICAL(&stateMux);
    }
    taskYIELD();
  }
  uint8_t digest[32];
  okay = okay && written == manifest.size && esp_http_client_is_complete_data_received(client) &&
      mbedtls_sha256_finish(&hash, digest) == 0 && digestMatches(digest, manifest);
  mbedtls_sha256_free(&hash);
  closeHttp(client);
  if (okay) {
    okay = esp_ota_end(handle) == ESP_OK;
    handle = 0;  // esp_ota_end consumes the handle, including validation failure.
  }
  if (handle) esp_ota_abort(handle);
  return okay ? target : nullptr;
}

bool checkAndInstall() {
  const time_t now = time(nullptr);
  if (WiFi.status() != WL_CONNECTED) { setState("error", "Internet Wi-Fi is not connected"); return false; }
  if (now < 1704067200LL || now > 4102444799LL) { setState("error", "Clock is not synchronized"); return false; }
  portENTER_CRITICAL(&stateMux);
  state.lastCheck = static_cast<uint32_t>(now);
  portEXIT_CRITICAL(&stateMux);
  setState("checking");
  String body;
  Manifest manifest;
  if (!fetchEnvelope(body)) { setState("error", "Release could not be downloaded"); return false; }
  if (!parseEnvelope(body, manifest)) { setState("error", "Release signature or manifest is invalid"); return false; }
  portENTER_CRITICAL(&stateMux);
  snprintf(state.releaseId, sizeof(state.releaseId), "%s", manifest.releaseId.c_str());
  snprintf(state.availableVersion, sizeof(state.availableVersion), "%s", manifest.version.c_str());
  portEXIT_CRITICAL(&stateMux);
  if (hashMatches(esp_ota_get_running_partition(), manifest)) { setState("up_to_date", "", 100); return true; }
  setState("downloading");
  const esp_partition_t *target = downloadImage(manifest);
  if (!target) { setState("error", "Image download or verification failed"); return false; }
  setState("ready", "", 100);
  ready.store(true);
  // Only the main loop knows that pending settings have reached NVS.
  ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
  if (esp_ota_set_boot_partition(target) != ESP_OK) { setState("error", "Boot partition could not be selected"); return false; }
  bootSelected.store(true);
  // Retain the mutex until the main loop rechecks freshly saved settings and reboots.
  for (;;) ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
}

void worker(void *) {
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    running.store(true);
    bool okay = false;
    if (xSemaphoreTake(flashMutex, 0) == pdTRUE) {
      okay = checkAndInstall();
      xSemaphoreGive(flashMutex);
    } else setState("error", "Another update is in progress");
    failures = okay ? 0 : std::min(failures + 1, 5u);
    const uint32_t delayMs = okay ? CHECK_INTERVAL_MS :
        std::min<uint32_t>(120000u << (failures - 1), CHECK_INTERVAL_MS);
    nextCheck.store(millis() + delayMs);
    running.store(false);
    queued.store(false);
  }
}
}  // namespace

void begin() {
  if (configured) return;
  startedMs = millis();
  nextCheck.store(startedMs + FIRST_CHECK_MS);
  const esp_partition_t *partition = esp_ota_get_running_partition();
  esp_ota_img_states_t imageState;
  pendingValidation = partition && esp_ota_get_state_partition(partition, &imageState) == ESP_OK &&
      imageState == ESP_OTA_IMG_PENDING_VERIFY;
  if (pendingValidation) setState("pending_validation");
  flashMutex = xSemaphoreCreateMutex();
  configured = flashMutex && xTaskCreate(worker, "led-cloud-ota", 12288, nullptr, 1, &workerTask) == pdPASS;
  if (!configured) setState("error", "Update worker could not be started");
}

// Preserve the previous slot until the newly booted image has passed its health gate.
bool tryLock() { return !pendingValidation && flashMutex && xSemaphoreTake(flashMutex, 0) == pdTRUE; }
void unlock() { if (flashMutex) xSemaphoreGive(flashMutex); }
bool busy() { return flashMutex && uxSemaphoreGetCount(flashMutex) == 0; }

bool requestCheck() {
  if (!configured || pendingValidation || running.load() || ready.load()) return false;
  bool expected = false;
  if (!queued.compare_exchange_strong(expected, true)) return false;
  xTaskNotifyGive(workerTask);
  return true;
}

void poll(bool applicationHealthy, bool canReboot) {
  const uint32_t now = millis();
  if (pendingValidation) {
    if (rollbackFailed) return;
    if (applicationHealthy) { if (!healthySince) healthySince = now ? now : 1; }
    else healthySince = 0;
    if (now - startedMs >= MIN_VALIDATION_MS && healthySince && now - healthySince >= HEALTH_STABLE_MS) {
      if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
        pendingValidation = false;
        setState("healthy");
      } else setState("pending_validation", "Startup image confirmation failed");
    }
    if (pendingValidation && now - startedMs >= VALIDATION_DEADLINE_MS) {
      setState("error", "New firmware failed its startup health check");
      if (esp_ota_mark_app_invalid_rollback_and_reboot() != ESP_OK) {
        rollbackFailed = true;
        setState("error", "Startup failed; no bootable rollback image");
      }
    }
    return;
  }
  if (canReboot && bootSelected.load()) {
    setState("rebooting", "", 100);
    ESP.restart();
    return;
  }
  if (canReboot && ready.exchange(false)) {
    setState("rebooting", "", 100);
    xTaskNotifyGive(workerTask);
  }
  if (configured && !queued.load() && !running.load() &&
      static_cast<int32_t>(now - nextCheck.load()) >= 0) requestCheck();
}

String stateJson() {
  State copy;
  portENTER_CRITICAL(&stateMux);
  copy = state;
  portEXIT_CRITICAL(&stateMux);
  // Every variable string is either a validated ASCII identifier or a fixed internal message.
  String json = "{\"configured\":";
  json += configured ? "true" : "false";
  json += ",\"state\":\""; json += copy.state;
  json += "\",\"version\":\"" LED_CONTROL_VERSION "\",\"release_id\":\""; json += copy.releaseId;
  json += "\",\"available_version\":\""; json += copy.availableVersion;
  json += "\",\"progress\":"; json += copy.progress;
  json += ",\"last_check\":"; json += copy.lastCheck;
  json += ",\"error\":\""; json += copy.error;
  json += "\"}";
  return json;
}
}  // namespace ledota

// Arduino's default early acceptance would bypass the application's health gate.
extern "C" bool verifyRollbackLater() { return true; }
