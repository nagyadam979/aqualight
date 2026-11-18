#!/usr/bin/env python3
"""Check the production LEDC output call path with native driver doubles.

Exercises endpoint forcing and PWM resumption at 13/14 bits under ASan/UBSan.
Does not establish electrical timing, LED brightness or actual peripheral output.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
source = (ROOT / "src/main.cpp").read_text()
start = source.index("bool writePwmValue(")
production = source[start:source.index("\nbool startPwm()", start)]

STUBS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <vector>

constexpr int ESP_OK=0, ESP_FAIL=-1, LEDC_LOW_SPEED_MODE=0;
using ledc_channel_t=int;
uint32_t PWM_FULL=0;
struct Call { char operation; int channel; uint32_t value; };
std::vector<Call> calls;
struct Channel { bool enabled=false; uint32_t idle=0,duty=0; } channels[2];
bool failStop=false,failSet=false,failUpdate=false;
void checkChannel(int mode,int channel) {
  assert(mode==LEDC_LOW_SPEED_MODE && channel>=0 && channel<2);
}
int ledc_stop(int mode,int channel,uint32_t idle) {
  checkChannel(mode,channel); assert(idle<=1);
  calls.push_back({'S',channel,idle});
  if(failStop) return ESP_FAIL;
  channels[channel].enabled=false; channels[channel].idle=idle;
  return ESP_OK;
}
int ledc_set_duty(int mode,int channel,uint32_t value) {
  checkChannel(mode,channel);
  // Especially important at 14 bits: FULL must never reach the duty register.
  assert(value>0 && value<PWM_FULL);
  calls.push_back({'D',channel,value});
  if(failSet) return ESP_FAIL;
  channels[channel].duty=value;
  return ESP_OK;
}
int ledc_update_duty(int mode,int channel) {
  checkChannel(mode,channel);
  calls.push_back({'U',channel,0});
  if(failUpdate) return ESP_FAIL;
  // Matches the pinned ESP-IDF update path: sig_out_en is re-enabled.
  channels[channel].enabled=true;
  return ESP_OK;
}
'''

CHECKS = r'''
void expectCalls(int channel,const char* operations) {
  unsigned index=0;
  for(const auto operation: calls) {
    assert(operations[index] && operation.operation==operations[index++]);
    assert(operation.channel==channel);
  }
  assert(!operations[index]);
}
int main() {
  for(const unsigned bits:{13u,14u}) {
    PWM_FULL=1u<<bits;
    for(uint8_t channel=0;channel<2;++channel) {
      channels[0]={}; channels[1]={}; calls.clear();
      assert(writePwmValue(channel,0));
      assert(!channels[channel].enabled && channels[channel].idle==0);
      assert(writePwmValue(channel,1));
      assert(channels[channel].enabled && channels[channel].duty==1);
      assert(writePwmValue(channel,PWM_FULL));
      assert(!channels[channel].enabled && channels[channel].idle==1);
      assert(writePwmValue(channel,PWM_FULL-1));
      assert(channels[channel].enabled && channels[channel].duty==PWM_FULL-1);
      assert(writePwmValue(channel,0));
      assert(!channels[channel].enabled && channels[channel].idle==0);
      expectCalls(channel,"SDUSDUS");
    }
    for(uint8_t channel=0;channel<2;++channel) {
      calls.clear();
      assert(!writePwmValue(channel,PWM_FULL+1));
      assert(!writePwmValue(channel,UINT32_MAX));
      assert(calls.empty());

      for(const auto endpoint:{0u,PWM_FULL}) {
        calls.clear(); failStop=true;
        assert(!writePwmValue(channel,endpoint));
        expectCalls(channel,"S");
        failStop=false;
      }
      calls.clear(); failSet=true;
      assert(!writePwmValue(channel,1));
      expectCalls(channel,"D"); // A failed register write must not enable output.
      failSet=false;
      calls.clear(); failUpdate=true;
      assert(!writePwmValue(channel,1));
      expectCalls(channel,"DU");
      failUpdate=false;
    }
  }
  std::puts("PASS PWM output: production endpoint forcing, 13/14-bit FULL never written, both channels, stop/resume sequences, failure propagation and out-of-range rejection");
}
'''

with tempfile.TemporaryDirectory(prefix="aqualight-pwm-test-") as directory:
    temp = Path(directory)
    cpp, binary = temp / "check.cpp", temp / "check"
    cpp.write_text(STUBS + production + CHECKS)
    subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                    str(cpp), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
