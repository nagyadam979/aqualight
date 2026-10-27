#!/usr/bin/env python3
"""Run: python3 test_smoke.py (needs a C++ compiler; no board or extra packages).

Runs the actual firmware with fake time/I2C/PWM to catch sequence, rollover,
polarity and sensor-failure regressions. It cannot verify electrical behavior.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
ARDUINO = r'''
#pragma once
#include <cstdint>
#include <cstdio>
#include <string>
constexpr int HIGH=1, LOW=0, OUTPUT=1;
uint32_t fakeTime=0, duties[2]={0,0};
int pins[5]={0}, setupFailure=-1;
bool attached[5]={false};
bool latchWritten[5]={false};
uint32_t millis() { return fakeTime; }
void delay(uint32_t ms) { fakeTime+=ms; }
void pinMode(uint8_t pin, int) { assert(latchWritten[pin]); }
struct SerialMock {
  std::string log;
  void begin(int) {}
  explicit operator bool() const { return false; }
  void println(const char *text) { log+=text; log+='\n'; }
  template<typename... Args> void printf(const char *format, Args... args) {
    char buffer[256]; std::snprintf(buffer,sizeof(buffer),format,args...); log+=buffer;
  }
} Serial;
'''
LEDC = r'''
#pragma once
constexpr int ESP_OK=0, LEDC_LOW_SPEED_MODE=0, LEDC_TIMER_0=0, LEDC_AUTO_CLK=0, LEDC_INTR_DISABLE=0;
using ledc_channel_t=int;
using ledc_timer_bit_t=int;
struct ledc_timer_config_t { int speed_mode, duty_resolution, timer_num; uint32_t freq_hz; int clk_cfg; };
struct ledc_channel_config_t { int gpio_num,speed_mode,channel,intr_type,timer_sel; uint32_t duty; };
int ledc_timer_config(const ledc_timer_config_t*) { return 0; }
int ledc_channel_config(const ledc_channel_config_t* config) {
  if(config->channel==setupFailure) return -1;
  assert(config->duty==static_cast<uint32_t>(pins[config->gpio_num])*1024);
  duties[config->channel]=config->duty; attached[config->gpio_num]=true; return 0;
}
int ledc_set_duty(int, int channel, uint32_t duty) { duties[channel]=duty; return 0; }
int ledc_update_duty(int, int) { return 0; }
int ledc_stop(int, int channel, int) { attached[channel==0 ? 0 : 4]=false; return 0; }
'''
GPIO = r'''
#pragma once
using gpio_num_t=int;
int gpio_set_level(int pin, int value) { pins[pin]=value; latchWritten[pin]=true; return 0; }
'''
WIRE = r'''
#pragma once
struct WireMock {
  bool begin(int, int, int) { return true; }
  void setTimeOut(int) {}
  void beginTransmission(uint8_t) {}
  int endTransmission() { return 2; }
} Wire;
'''
AHT = r'''
#pragma once
constexpr int AHTXX_ADDRESS_X38=0x38, AHT2x_SENSOR=1;
constexpr int AHTXX_NO_ERROR=0, AHTXX_ERROR=255;
constexpr bool AHTXX_USE_READ_DATA=false;
bool sensorPresent=false;
float fakeTemperature=23.5f, fakeHumidity=47.25f;
uint8_t fakeStatus=0;
struct AHTxx {
  AHTxx(int, int) {}
  bool begin(int sda, int scl, int hz, int timeout) {
    if (sda!=1 || scl!=3 || hz!=100000 || timeout!=50000) std::abort();
    return sensorPresent;
  }
  float readTemperature() { return fakeTemperature; }
  float readHumidity(bool refresh) { if(refresh) std::abort(); return fakeHumidity; }
  uint8_t getStatus() { return fakeStatus; }
};
'''
CHECKS = r'''
int main() {
  setup();
  assert(fakeTime==1500); // Disconnected USB monitor never blocks startup indefinitely.
  assert(pwmReady && !sensorReady && attached[0] && attached[4]);
  assert(PWM_MAX==1024); // Native LEDC needs 2^N for constant HIGH, including active-low OFF.
  const uint32_t expected[]={0,102,256,512,1024,512,256,102,0};
  for (unsigned group=0;group<3;++group) {
    for (unsigned index=0;index<9;++index) {
      assert(phase==group && step==index);
      assert(duties[0]==(group==1 ? 0 : expected[index]));
      assert(duties[1]==(group==0 ? 0 : expected[index]));
      fakeTime+=1000;
      loop(); // A missing sensor must not prevent the PWM sequence from advancing.
    }
  }
  assert(phase==3 && duties[0]==0 && duties[1]==0);
  updatePwm(lastPwmMs+1999);
  assert(phase==3);
  updatePwm(lastPwmMs+2000);
  assert(phase==0 && step==0);
  for (unsigned p=0;p<=255;++p) {
    assert(pwmDuty(p,false)<=PWM_MAX);
    assert(pwmDuty(p,true)+pwmDuty(p,false)==PWM_MAX);
  }
  assert(pwmDuty(0,true)==PWM_MAX && pwmDuty(100,true)==0);
  assert(pwmDuty(255,false)==PWM_MAX);
  phase=0;step=0;lastPwmMs=UINT32_MAX-500;
  updatePwm(499);
  assert(step==1); // millis() rollover still advances exactly one step.
  sensorPresent=true;
  updateSensor(lastSensorMs+5000);
  assert(sensorReady);
  updateSensor(lastSensorMs+5000);
  assert(Serial.log.find("23.50 C | 47.25 %RH")!=std::string::npos);
  Serial.log.clear();
  fakeStatus=4;
  updateSensor(lastSensorMs+5000);
  assert(!sensorReady && Serial.log.find("Read failed")!=std::string::npos);
  assert(Serial.log.find("%RH")==std::string::npos); // Never present failed/stale data as a reading.
  sensorReady=true;fakeStatus=0;fakeTemperature=AHTXX_ERROR;
  updateSensor(lastSensorMs+5000);
  assert(!sensorReady);
  setupFailure=1;
  pwmReady=startPwm();
  assert(!pwmReady && !attached[0] && !attached[4] && pins[0]==LOW && pins[4]==LOW);
  const uint32_t before=duties[0];
  updatePwm(lastPwmMs+1000);
  assert(duties[0]==before);
  std::puts("PASS: sequence, pause, polarity, rollover, sensor errors, PWM init failure");
}
'''

with tempfile.TemporaryDirectory(prefix="esp32c3-smoke-") as directory:
    temp = Path(directory)
    (temp / "driver").mkdir()
    for name, content in {"Arduino.h": ARDUINO, "Wire.h": WIRE, "AHTxx.h": AHT,
                          "driver/ledc.h": LEDC, "driver/gpio.h": GPIO}.items():
        (temp / name).write_text(content)
    source = temp / "check.cpp"
    source.write_text('#include <cassert>\n#include <cstdlib>\n'
                      f'#include "{ROOT / "src/main.cpp"}"\n' + CHECKS)
    executable = temp / "check"
    subprocess.run([os.environ.get("CXX", "c++"), "-std=c++11", "-Wall", "-Wextra",
                    "-Werror", "-I", str(temp), str(source), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
