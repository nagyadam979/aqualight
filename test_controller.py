#!/usr/bin/env python3
"""Run production scheduling/parser checks: python3 test_controller.py.

Compiles the actual schedule.h with a native C++17 compiler and sanitizers.
Checks interpolation, midnight wrap, strict parsing and atomic rejection,
mode/clock behavior, PWM endpoints and Budapest DST. No board or packages needed.
Electrical behavior, HTTP, NVS and Wi-Fi require the device integration checks.
"""
from pathlib import Path
from datetime import datetime, timezone
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
source = (ROOT / "src/main.cpp").read_text()
tz = re.search(r'constexpr char TIMEZONE\[\] = "([^"]+)";', source).group(1)

CHECKS = r'''
#include <cassert>
#include <cmath>
#include <cstdlib>
#include <ctime>
#include <limits>
#include <string>
#include "schedule.h"

bool same(const aqua::Settings &a, const aqua::Settings &b) {
  if (a.mode!=b.mode || a.manualWarm!=b.manualWarm || a.manualCool!=b.manualCool || a.count!=b.count) return false;
  for (std::size_t i=0;i<aqua::MAX_POINTS;++i) {
    if (a.points[i].minute!=b.points[i].minute || a.points[i].warm!=b.points[i].warm || a.points[i].cool!=b.points[i].cool) return false;
  }
  return true;
}
void levels(aqua::Levels value, float warm, float cool) {
  assert(std::fabs(value.warm-warm)<0.001f);
  assert(std::fabs(value.cool-cool)<0.001f);
}
void localTime(long long epoch, int hour, int minute, int dst) {
  const time_t value=static_cast<time_t>(epoch);
  tm local{};
  assert(localtime_r(&value,&local));
  assert(local.tm_hour==hour && local.tm_min==minute && local.tm_isdst==dst);
}
int main() {
  auto settings=aqua::defaults();
  assert(aqua::valid(settings) && settings.mode==aqua::Mode::Off);
  levels(aqua::output(settings,true,9*3600),0,0); // No first-boot light program.
  levels(aqua::interpolate(settings,8*3600),0,0);
  levels(aqua::interpolate(settings,8*3600+30*60),25,25);
  levels(aqua::interpolate(settings,8*3600+30),50.0f/120,50.0f/120); // Second-level dawn.
  levels(aqua::interpolate(settings,12*3600),50,50);
  levels(aqua::interpolate(settings,17*3600+30*60),25,25);
  levels(aqua::interpolate(settings,18*3600),0,0);
  levels(aqua::interpolate(settings,0),0,0);
  levels(aqua::interpolate(settings,86399),0,0);
  settings.mode=aqua::Mode::Manual; settings.manualWarm=27; settings.manualCool=83;
  levels(aqua::output(settings,false,0),27,83); // Manual doesn't require a clock.
  settings.mode=aqua::Mode::Auto;
  levels(aqua::output(settings,false,12*3600),0,0); // No stale saved clock after outage.
  levels(aqua::output(settings,true,12*3600),50,50);
  assert(aqua::parseProgram("60,0,100;1380,100,0",settings));
  levels(aqua::interpolate(settings,0),50,50); // Interpolation across midnight.
  levels(aqua::interpolate(settings,60*60),0,100);
  levels(aqua::interpolate(settings,1380*60),100,0);
  levels(aqua::interpolate(settings,86400),50,50);
  for (uint32_t second=0;second<86400;++second) {
    const auto value=aqua::interpolate(settings,second);
    assert(value.warm>=0 && value.warm<=100 && value.cool>=0 && value.cool<=100);
    assert(std::fabs(value.warm+value.cool-100)<0.001f);
  }
  const char *invalid[]={"", "0,1,2", "0,1,2;", "0,1,2;0,3,4", "1,1,2;0,3,4",
    "0,1,2;1440,1,2", "0,101,2;60,1,2", "0,1,101;60,1,2", "-1,1,2;60,1,2",
    "+1,1,2;60,1,2", " 0,1,2;60,1,2", "0,,2;60,1,2", "0,1;60,1,2",
    "0,1,2;60,1,2x", "0,1,2;60,1,2\n", "999999999999999999999,1,2;60,1,2"};
  for (const auto input:invalid) {
    const auto before=settings;
    assert(!aqua::parseProgram(input,settings));
    assert(same(before,settings));
  }
  assert(!aqua::parseProgram(nullptr,settings));
  std::string maximum;
  for (unsigned i=0;i<16;++i) {
    if(i) maximum+=';';
    maximum+=std::to_string(i*60)+",100,0";
  }
  assert(aqua::parseProgram(maximum.c_str(),settings) && settings.count==16);
  const auto before=settings;
  maximum+=";1439,10,20";
  assert(!aqua::parseProgram(maximum.c_str(),settings) && same(before,settings));
  assert(aqua::parseSettings("1,2,27,83|0,0,100;1439,100,0",settings));
  assert(settings.mode==aqua::Mode::Auto && settings.manualWarm==27 && settings.manualCool==83);
  const char *badStored[]={"2,2,27,83|0,0,100;1439,100,0", "1,3,27,83|0,0,100;1439,100,0",
    "1,2,27,101|0,0,100;1439,100,0", "1,2,27,83|", "", "1,2,27,83|0,0,100;1439,100,0;"};
  for(const auto input:badStored) {
    const auto saved=settings;
    assert(!aqua::parseSettings(input,settings) && same(saved,settings));
  }
  uint32_t random=123456789;
  for(unsigned run=0;run<10000;++run) {
    std::string candidate;
    random^=random<<13; random^=random>>17; random^=random<<5;
    const unsigned length=random%80;
    for(unsigned i=0;i<length;++i) {
      random^=random<<13; random^=random>>17; random^=random<<5;
      candidate+="0123456789,;+- \n"[random%16];
    }
    const auto saved=settings;
    if(aqua::parseProgram(candidate.c_str(),settings)) assert(aqua::valid(settings));
    else assert(same(saved,settings));
  }
  assert(aqua::duty(0,false)==0 && aqua::duty(100,false)==1024);
  assert(aqua::duty(0,true)==1024 && aqua::duty(100,true)==0);
  assert(aqua::duty(-50,false)==0 && aqua::duty(999,false)==1024);
  assert(aqua::duty(std::numeric_limits<float>::quiet_NaN(),false)==0);
  for(unsigned i=0;i<=1000;++i) {
    const float value=i/10.0f;
    assert(aqua::duty(value,false)+aqua::duty(value,true)==1024);
  }
  assert(aqua::due(499,UINT32_MAX-500,1000));
  assert(!aqua::due(498,UINT32_MAX-500,1000));
  uint64_t number;
  assert(aqua::integer("4102444799",4102444799ULL,number) && number==4102444799ULL);
  assert(!aqua::integer("4102444800",4102444799ULL,number));
  assert(!aqua::integer("100x",100,number));
  assert(!aqua::integer("-1",100,number));
  assert(!aqua::integer("",100,number));
  setenv("TZ",TEST_TIMEZONE,1); tzset();
  localTime(SPRING_BEFORE,1,59,0);
  localTime(SPRING_AFTER,3,0,1);
  localTime(FALL_BEFORE,2,59,1);
  localTime(FALL_AFTER,2,0,0);
  std::puts("PASS: schedule, second-level transitions, midnight, 16-point limit, parser atomicity, fuzz, modes/clock, PWM endpoints, rollover, DST");
}
'''

cases = {
    "SPRING_BEFORE": datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc),
    "SPRING_AFTER": datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc),
    "FALL_BEFORE": datetime(2026, 10, 25, 0, 59, tzinfo=timezone.utc),
    "FALL_AFTER": datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc),
}
with tempfile.TemporaryDirectory(prefix="aqualight-check-") as directory:
    temp = Path(directory)
    cpp = temp / "check.cpp"
    cpp.write_text(f'#define TEST_TIMEZONE "{tz}"\n' +
                   "".join(f"#define {name} {int(moment.timestamp())}LL\n" for name, moment in cases.items()) + CHECKS)
    executable = temp / "check"
    subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(ROOT / "src"),
                    str(cpp), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
