#include <Arduino.h>
#include <Wire.h>
#include <AHTxx.h>
#include <driver/ledc.h>
#include <driver/gpio.h>
#include <cmath>

// GPIO numbers, not physical header positions. Change polarity if needed.
constexpr uint8_t PWM_PINS[] = {0, 4};
constexpr bool PWM_ACTIVE_LOW[] = {false, false};
constexpr uint32_t PWM_FREQUENCY_HZ = 1000;
constexpr uint8_t PWM_BITS = 10;
constexpr int I2C_SDA = 1;
constexpr int I2C_SCL = 3;
constexpr uint32_t PWM_STEP_MS = 1000;
constexpr uint32_t PWM_PAUSE_MS = 2000;
constexpr uint32_t SENSOR_INTERVAL_MS = 5000;

static_assert(PWM_BITS > 0 && PWM_BITS < 14, "Keep resolution below the C3 maximum for full-duty endpoints.");
constexpr uint32_t PWM_MAX = (1UL << PWM_BITS);  // Native LEDC full-on value, not Arduino's 2^N-1.
constexpr uint8_t LEVELS[] = {0, 10, 25, 50, 100, 50, 25, 10, 0};
AHTxx aht20(AHTXX_ADDRESS_X38, AHT2x_SENSOR);
bool pwmReady = false;
bool pwmAttached[2] = {false, false};
bool sensorReady = false;
uint8_t phase = 0;  // GPIO0, GPIO4, both, off pause
uint8_t step = 0;
uint32_t lastPwmMs = 0;
uint32_t lastSensorMs = 0;

uint32_t pwmDuty(uint8_t percent, bool activeLow) {
  if (percent > 100) percent = 100;
  const uint32_t duty = (PWM_MAX * percent + 50) / 100;
  return activeLow ? PWM_MAX - duty : duty;
}

void stopPwm() {
  for (uint8_t i = 0; i < 2; ++i) {
    if (pwmAttached[i]) {
      ledc_stop(LEDC_LOW_SPEED_MODE, static_cast<ledc_channel_t>(i), PWM_ACTIVE_LOW[i] ? 1 : 0);
      pwmAttached[i] = false;
    }
    gpio_set_level(static_cast<gpio_num_t>(PWM_PINS[i]), PWM_ACTIVE_LOW[i] ? 1 : 0);
    pinMode(PWM_PINS[i], OUTPUT);
  }
  pwmReady = false;
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
    // Native init sets OFF duty before attaching the pin, including active-low.
    channel.duty = pwmDuty(0, PWM_ACTIVE_LOW[i]);
    if (ledc_channel_config(&channel) != ESP_OK) {
      stopPwm();
      return false;
    }
    pwmAttached[i] = true;
  }
  return true;
}

void applyPwmStep() {
  const uint8_t level = phase < 3 ? LEVELS[step] : 0;
  const uint8_t first = (phase == 0 || phase == 2) ? level : 0;
  const uint8_t second = (phase == 1 || phase == 2) ? level : 0;
  const uint8_t levels[] = {first, second};
  for (uint8_t i = 0; i < 2; ++i) {
    const auto channel = static_cast<ledc_channel_t>(i);
    if (ledc_set_duty(LEDC_LOW_SPEED_MODE, channel, pwmDuty(levels[i], PWM_ACTIVE_LOW[i])) != ESP_OK ||
        ledc_update_duty(LEDC_LOW_SPEED_MODE, channel) != ESP_OK) {
      stopPwm();
      Serial.println("[PWM] WRITE FAILED: both outputs held logically off.");
      return;
    }
  }
  Serial.printf("[PWM] GPIO0=%u%% GPIO4=%u%% (commanded)\n", first, second);
}

void updatePwm(uint32_t now) {
  if (!pwmReady) return;
  const uint32_t duration = phase == 3 ? PWM_PAUSE_MS : PWM_STEP_MS;
  if (static_cast<uint32_t>(now - lastPwmMs) < duration) return;
  lastPwmMs = now;
  if (phase == 3) {
    phase = 0;
    step = 0;
  } else if (++step == sizeof(LEVELS) / sizeof(LEVELS[0])) {
    step = 0;
    ++phase;
  }
  applyPwmStep();
}

void scanI2c() {
  Serial.printf("[I2C] SDA=GPIO%d SCL=GPIO%d, expected AHT20=0x38\n", I2C_SDA, I2C_SCL);
  unsigned found = 0;
  for (uint8_t address = 8; address < 120; ++address) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[I2C] Found 0x%02X\n", address);
      ++found;
    }
  }
  if (found == 0) Serial.println("[I2C] No device: check power, SDA/SCL and pull-ups.");
}

bool startSensor() {
  const bool ready = aht20.begin(I2C_SDA, I2C_SCL, 100000, 50000);
  Serial.println(ready ? "[AHT20] Initialized at 0x38" :
                       "[AHT20] Init failed; PWM continues, retry in 5 s.");
  return ready;
}

void updateSensor(uint32_t now) {
  if (static_cast<uint32_t>(now - lastSensorMs) < SENSOR_INTERVAL_MS) return;
  lastSensorMs = now;
  if (!sensorReady) {
    sensorReady = startSensor();
    return;
  }
  const float temperature = aht20.readTemperature();
  const float humidity = aht20.readHumidity(AHTXX_USE_READ_DATA);
  const uint8_t status = aht20.getStatus();
  if (status != AHTXX_NO_ERROR || temperature == AHTXX_ERROR ||
      humidity == AHTXX_ERROR || !std::isfinite(temperature) || !std::isfinite(humidity)) {
    Serial.printf("[AHT20] Read failed, status=%u (1=busy, 2=ACK, 3=data, 4=CRC); retry init in 5 s.\n", status);
    sensorReady = false;
    return;
  }
  Serial.printf("[AHT20] %.2f C | %.2f %%RH\n", temperature, humidity);
}

void setup() {
  pwmReady = startPwm();  // Logical off before sensor/USB initialization.
  Serial.begin(115200);
  const uint32_t serialStart = millis();
  while (!Serial && static_cast<uint32_t>(millis() - serialStart) < 1500) delay(10);
  Serial.println("\nESP32-C3 SuperMini smoke test");
  Serial.printf("[PWM] %lu Hz, %u bit; GPIO0 active-%s, GPIO4 active-%s\n",
                static_cast<unsigned long>(PWM_FREQUENCY_HZ), PWM_BITS,
                PWM_ACTIVE_LOW[0] ? "low" : "high", PWM_ACTIVE_LOW[1] ? "low" : "high");
  if (!pwmReady) Serial.println("[PWM] INIT FAILED: both outputs held logically off.");
  Serial.println("[PWM] Sequence: GPIO0, GPIO4, both; 0/10/25/50/100/50/25/10/0%, then off pause.");
  Serial.println("[PWM] Check LEDs visually; commanded duty is not hardware feedback.");
  if (Wire.begin(I2C_SDA, I2C_SCL, 100000)) {
    Wire.setTimeOut(50);
    scanI2c();
    sensorReady = startSensor();
  } else {
    Serial.println("[I2C] Init failed; PWM continues, retry in 5 s.");
  }
  lastPwmMs = millis();
  lastSensorMs = lastPwmMs;
  if (pwmReady) applyPwmStep();
}

void loop() {
  const uint32_t now = millis();
  updatePwm(now);
  updateSensor(now);  // Library and Wire have bounded waits; missing AHT20 cannot stop PWM.
  delay(1);
}
