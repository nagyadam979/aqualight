#!/usr/bin/env python3
"""Run production scheduling/parser checks: python3 test_controller.py.

Compiles the actual schedule.h with a native C++17 compiler and sanitizers.
Checks independent curves, interpolation, midnight wrap and atomic dual rejection,
mode/clock behavior, PWM endpoints, Budapest DST and the actual bounded network retry path.
No board or packages needed.
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

bool same(const aqua::Curve &a, const aqua::Curve &b) {
  if(a.count!=b.count) return false;
  for(std::size_t i=0;i<aqua::MAX_POINTS;++i) {
    if(a.points[i].minute!=b.points[i].minute || a.points[i].level!=b.points[i].level) return false;
  }
  return true;
}
bool same(const aqua::Settings &a, const aqua::Settings &b) {
  return a.mode==b.mode && a.manualWarm==b.manualWarm && a.manualCool==b.manualCool &&
         same(a.warm,b.warm) && same(a.cool,b.cool);
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
  settings.mode=aqua::Mode::Auto;
  levels(aqua::output(settings,true,8*3600),0,0);
  levels(aqua::output(settings,true,8*3600+30*60),25,25);
  levels(aqua::output(settings,true,8*3600+30),50.0f/120,50.0f/120); // Second-level dawn.
  levels(aqua::output(settings,true,12*3600),50,50);
  levels(aqua::output(settings,true,17*3600+30*60),25,25);
  levels(aqua::output(settings,true,18*3600),0,0);
  levels(aqua::output(settings,true,0),0,0);
  levels(aqua::output(settings,true,86399),0,0);
  settings.mode=aqua::Mode::Manual; settings.manualWarm=27; settings.manualCool=83;
  levels(aqua::output(settings,false,0),27,83); // Manual doesn't require a clock.
  settings.mode=aqua::Mode::Auto;
  levels(aqua::output(settings,false,12*3600),0,0); // No stale saved clock after outage.
  levels(aqua::output(settings,true,12*3600),50,50);
  assert(aqua::parseProgram("60,0;1380,100","0,0;360,80;720,20;1080,60",settings));
  assert(settings.warm.count==2 && settings.cool.count==4); // Independent point counts/times.
  levels(aqua::output(settings,true,0),50,0); // Warm crosses midnight; cool has a midnight point.
  levels(aqua::output(settings,true,12*3600),50,20);
  assert(aqua::interpolate(settings.warm,60*60)==0);
  assert(aqua::interpolate(settings.warm,1380*60)==100);
  assert(aqua::interpolate(settings.warm,86400)==50);
  for(uint32_t second=0;second<86400;++second) {
    const auto value=aqua::output(settings,true,second);
    assert(value.warm>=0 && value.warm<=100 && value.cool>=0 && value.cool<=100);
  }
  const auto coolBefore=settings.cool;
  assert(aqua::parseCurve("120,10;300,80;1000,5",settings.warm));
  assert(same(coolBefore,settings.cool)); // Editing warm cannot move or add a cool point.
  const auto warmBefore=settings.warm;
  assert(aqua::parseCurve("0,25;900,75",settings.cool));
  assert(same(warmBefore,settings.warm));
  const char *invalid[]={"", "0,1", "0,1;", "0,1;0,3", "1,1;0,3", "0,1;1440,1",
    "0,101;60,1", "-1,1;60,1", "+1,1;60,1", " 0,1;60,1", "0,;60,1", "0;60,1",
    "0,1;60,1x", "0,1;60,1\n", "999999999999999999999,1;60,1", "0,1,2;60,1,2"};
  for(const auto input:invalid) {
    const auto before=settings;
    assert(!aqua::parseProgram(input,"0,99;1439,1",settings));
    assert(same(before,settings));
    assert(!aqua::parseProgram("0,99;1439,1",input,settings));
    assert(same(before,settings)); // Valid first curve + bad second curve also rolls back BOTH.
    auto curve=settings.warm;
    assert(!aqua::parseCurve(input,curve) && same(curve,settings.warm));
  }
  assert(!aqua::parseProgram(nullptr,"0,0;1,1",settings));
  assert(!aqua::parseProgram("0,0;1,1",nullptr,settings));
  std::string maximum;
  for(unsigned i=0;i<16;++i) {
    if(i) maximum+=';';
    maximum+=std::to_string(i*60)+",100";
  }
  assert(aqua::parseProgram(maximum.c_str(),"0,20;1439,80",settings));
  assert(settings.warm.count==16 && settings.cool.count==2);
  const auto before=settings;
  maximum+=";1439,10";
  assert(!aqua::parseProgram(maximum.c_str(),"0,0;1,1",settings) && same(before,settings));
  assert(!aqua::parseProgram("0,0;1,1",maximum.c_str(),settings) && same(before,settings));
  assert(aqua::parseSettings("2,2,27,83|0,0;1439,100|60,70;720,10;1380,40",settings));
  assert(settings.mode==aqua::Mode::Auto && settings.manualWarm==27 && settings.manualCool==83);
  assert(settings.warm.count==2 && settings.cool.count==3);
  assert(settings.warm.points[1].minute==1439 && settings.cool.points[0].minute==60);
  const char *badStored[]={"1,2,27,83|0,0,100;1439,100,0", "3,2,27,83|0,0;1,1|0,0;1,1",
    "2,3,27,83|0,0;1,1|0,0;1,1", "2,2,27,101|0,0;1,1|0,0;1,1", "2,2,27,83|", "",
    "2,2,27,83|0,0;1,1|0,0;1,1;", "2,2,27,83|0,0;1,1|0,0;1,1|", "2,2,27,83|0,0;1,1"};
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
    if(aqua::parseProgram(candidate.c_str(),"0,0;1439,100",settings)) assert(aqua::valid(settings));
    else assert(same(saved,settings));
  }
  assert(aqua::PWM_FULL==8192);
  assert(aqua::duty(0,false)==0 && aqua::duty(100,false)==aqua::PWM_FULL);
  assert(aqua::duty(0,true)==aqua::PWM_FULL && aqua::duty(100,true)==0);
  assert(aqua::duty(-50,false)==0 && aqua::duty(999,false)==aqua::PWM_FULL);
  assert(aqua::duty(std::numeric_limits<float>::quiet_NaN(),false)==0);
  assert(aqua::duty(std::numeric_limits<float>::infinity(),false)==0);
  assert(aqua::duty(1,false)==1); // First step is nonzero, about 0.0122% duty.
  assert(aqua::duty(10,false)==82);
  assert(aqua::duty(50,false)==2048);
  // Preserve the actual PWM thresholds measured on the deployed gamma-2 build.
  assert(aqua::duty(8,false)==aqua::PWM_MIN_WARM);
  assert(aqua::duty(10,false)==aqua::PWM_MIN_COOL);
  assert(aqua::duty(9,false)==aqua::PWM_MIN_SINGLE);
  assert(aqua::PWM_START_WARM==1 && aqua::PWM_START_COOL==1);
  assert(aqua::PWM_TERRARIUM_HZ==1000 && aqua::PWM_TERRARIUM_BITS==13);
  assert(aqua::PWM_TERRARIUM_FULL==8192);
  for(const auto anchor:{aqua::PWM_AT_FIVE_WARM,aqua::PWM_AT_FIVE_COOL}) {
    const auto start=anchor==aqua::PWM_AT_FIVE_WARM ? aqua::PWM_START_WARM : aqua::PWM_START_COOL;
    assert(aqua::fineLowDuty(0,false,anchor,start,aqua::PWM_TERRARIUM_FULL)==0);
    assert(aqua::fineLowDuty(1,false,anchor,start,aqua::PWM_TERRARIUM_FULL)==start);
    assert(aqua::fineLowDuty(5,false,anchor,start,aqua::PWM_TERRARIUM_FULL)==anchor);
    assert(aqua::fineLowDuty(100,false,anchor,start,aqua::PWM_TERRARIUM_FULL)==aqua::PWM_TERRARIUM_FULL);
    assert(aqua::fineLowDuty(-1,false,anchor,start,aqua::PWM_TERRARIUM_FULL)==0);
    assert(aqua::fineLowDuty(101,false,anchor,start,aqua::PWM_TERRARIUM_FULL)==aqua::PWM_TERRARIUM_FULL);
    assert(aqua::fineLowDuty(std::numeric_limits<float>::quiet_NaN(),false,anchor,start,aqua::PWM_TERRARIUM_FULL)==0);
    assert(aqua::fineLowDuty(std::numeric_limits<float>::infinity(),false,anchor,start,aqua::PWM_TERRARIUM_FULL)==0);
    for(unsigned i=1;i<=100;++i) {
      assert(aqua::fineLowDuty(i,false,anchor,start,aqua::PWM_TERRARIUM_FULL)>aqua::fineLowDuty(i-1,false,anchor,start,aqua::PWM_TERRARIUM_FULL));
    }
    uint32_t previous=0;
    for(unsigned i=0;i<=10000;++i) {
      const float level=i/100.0f;
      const auto current=aqua::fineLowDuty(level,false,anchor,start,aqua::PWM_TERRARIUM_FULL);
      assert(current>=previous && current<=aqua::PWM_TERRARIUM_FULL);
      assert(current+aqua::fineLowDuty(level,true,anchor,start,aqua::PWM_TERRARIUM_FULL)==aqua::PWM_TERRARIUM_FULL);
      previous=current;
    }
  }
  // Exact restoration of the deployed 1.2.2 curve, including the sub-5% range.
  const uint32_t restoredWarm[]={0,1,4,14,30,52};
  const uint32_t restoredCool[]={0,1,6,21,47,82};
  for(unsigned i=0;i<=5;++i) {
    assert(aqua::fineLowDuty(i,false,aqua::PWM_AT_FIVE_WARM,aqua::PWM_START_WARM)==restoredWarm[i]);
    assert(aqua::fineLowDuty(i,false,aqua::PWM_AT_FIVE_COOL,aqua::PWM_START_COOL)==restoredCool[i]);
  }
  for(const auto minimum:{aqua::PWM_MIN_WARM,aqua::PWM_MIN_COOL,aqua::PWM_MIN_SINGLE}) {
    assert(aqua::calibratedDuty(0,false,minimum)==0);
    assert(aqua::calibratedDuty(1,false,minimum)==minimum);
    assert(aqua::calibratedDuty(2,false,minimum)==minimum+1);
    assert(aqua::calibratedDuty(100,false,minimum)==aqua::PWM_FULL);
    assert(aqua::calibratedDuty(-1,false,minimum)==0);
    assert(aqua::calibratedDuty(101,false,minimum)==aqua::PWM_FULL);
    assert(aqua::calibratedDuty(std::numeric_limits<float>::quiet_NaN(),false,minimum)==0);
    assert(aqua::calibratedDuty(std::numeric_limits<float>::infinity(),false,minimum)==0);
    for(unsigned i=1;i<=100;++i) {
      assert(aqua::calibratedDuty(i,false,minimum)>aqua::calibratedDuty(i-1,false,minimum));
    }
    uint32_t previous=0;
    for(unsigned i=0;i<=10000;++i) {
      const float level=i/100.0f;
      const auto current=aqua::calibratedDuty(level,false,minimum);
      assert(current>=previous && current<=aqua::PWM_FULL);
      assert(current+aqua::calibratedDuty(level,true,minimum)==aqua::PWM_FULL);
      previous=current;
    }
    assert(aqua::calibratedDuty(0.5f,false,minimum)==static_cast<uint32_t>(std::lround(minimum*0.5f)));
  }
  for(unsigned i=1;i<=100;++i) {
    assert(aqua::duty(i,false)>aqua::duty(i-1,false)); // All 100 steps are distinct.
    assert(aqua::duty(i,true)<aqua::duty(i-1,true));
  }
  settings.mode=aqua::Mode::Auto;
  assert(aqua::parseProgram("0,0;60,100","0,0;60,100",settings));
  assert(aqua::duty(aqua::output(settings,true,1800).warm,false)==2048);
  uint32_t previousDuty=0;
  for(unsigned i=0;i<=1000;++i) {
    const float value=i/10.0f;
    const auto currentDuty=aqua::duty(value,false);
    assert(currentDuty>=previousDuty && currentDuty<=aqua::PWM_FULL);
    assert(currentDuty+aqua::duty(value,true)==aqua::PWM_FULL);
    previousDuty=currentDuty;
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
  std::puts("PASS: independent warm/cool curves, atomic dual save, interpolation, midnight, per-channel point limits, NVS v2, fuzz, modes/clock, gamma PWM and 100 distinct steps, rollover, DST");
}
'''

NETWORK_STUBS = r'''
#define LED_HOSTNAME "led-control"
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <string>
#include <ctime>
#include "schedule.h"
#include "wifi_recovery.h"
namespace network_test {
constexpr bool MASTER=true;
enum wifi_power_t { WIFI_POWER_8_5dBm=34 };
constexpr int WL_DISCONNECTED=0,WL_CONNECTED=1;
bool wasConnected=false,wifiPending=false,staAttemptActive=false,staConfigDisconnecting=false;
uint32_t wifiRequested=0,lastWifiAttempt=0,staAttemptStarted=0,staRetryDelay=0;
struct Credentials { const char* ssid="test-network"; const char* password="test-only-password"; } wifi;
struct Address { std::string toString() { return "192.0.2.2"; } };
struct Network {
  int state=WL_DISCONNECTED;
  unsigned starts=0,stops=0,powerWrites=0,clients=1;
  wifi_power_t power=WIFI_POWER_8_5dBm;
  int status() { return state; }
  void begin(const char* ssid,const char* password) {
    assert(state!=WL_CONNECTED && std::strcmp(ssid,wifi.ssid)==0 && std::strcmp(password,wifi.password)==0); ++starts;
  }
  void disconnectAsync(bool off,bool erase) { assert(!off && !erase); ++stops; state=WL_DISCONNECTED; }
  bool setTxPower(wifi_power_t value) { power=value; ++powerWrites; return true; }
  wifi_power_t getTxPower() { return power; }
  unsigned channel() { return 1; }
  unsigned softAPgetStationNum() { return clients; }
  int RSSI() { return -42; }
  Address localIP() { return {}; }
} WiFi;
struct Output {
  std::string text;
  void println(const char* line) { text+=line; text.push_back(10); }
  template<typename... Args> void printf(const char* format,Args... args) {
    char buffer[512]; std::snprintf(buffer,sizeof(buffer),format,args...); text+=buffer;
  }
} Serial;
aqua::WifiRecovery wifiRecovery;
bool recoveryStorageError=false,recoveryWriteAttempted=false,preferencesOpen=true,storageOk=true,settingsDirty=false;
uint32_t recoveryLastWrite=0,wifiRecoveryClockMarker=0;
constexpr uint64_t MIN_EPOCH=1704067200ULL,MAX_EPOCH=4102444799ULL;
struct Storage {
  uint8_t value=0;
  bool writeOkay=true,readOkay=true;
  unsigned writes=0;
  size_t putUChar(const char* key,uint8_t next) {
    assert(std::strcmp(key,"net_reboots")==0); ++writes;
    if(!writeOkay) return 0;
    value=next; return 1;
  }
  uint8_t getUChar(const char* key,uint8_t fallback) {
    assert(std::strcmp(key,"net_reboots")==0); return readOkay ? value : fallback;
  }
} preferences;
namespace ledota {
  bool lock=false,allowed=true;
  bool tryLock() { if(lock || !allowed) return false; lock=true; return true; }
  void unlock() { assert(lock); lock=false; }
}
namespace maintenance { bool requested=false; bool rebootRequested() { return requested; } }
bool flushOkay=true,clockValid=true;
bool flushPendingSettings() { if(flushOkay) settingsDirty=false; return flushOkay; }
bool readClock(time_t &epoch,tm &) { epoch=1794860000; return clockValid; }
struct Device { unsigned restarts=0; void restart() { ++restarts; } } ESP;
enum class TimeSource { None,Browser,Ntp,Rtc };
struct TimeState { TimeSource value=TimeSource::None; void store(TimeSource next) { value=next; } } timeSource;
constexpr int ESP_RST_SW=3,ESP_RST_POWERON=1,ESP_RST_BROWNOUT=9;
int resetReason=ESP_RST_SW;
time_t retainedEpoch=1794860000;
int unsetCalls=0;
struct timeval {};
int esp_reset_reason() { return resetReason; }
time_t time(time_t*) { return retainedEpoch; }
void settimeofday(timeval*,void*) { ++unsetCalls; }
'''
NETWORK_CHECKS = r'''
void outage(uint8_t used=0) {
  wifiRecovery.begin(used);
  wifiRecovery.observe(0,true,false);
  wifiRecovery.failedAttempt(); wifiRecovery.failedAttempt();
  WiFi=Network{}; WiFi.clients=0;
  wifiPending=false; wasConnected=false; staAttemptActive=false;
  preferences=Storage{}; preferences.value=used;
  preferencesOpen=storageOk=flushOkay=clockValid=true;
  recoveryStorageError=recoveryWriteAttempted=settingsDirty=false;
  recoveryLastWrite=0; wifiRecoveryClockMarker=0;
  ledota::lock=false; ledota::allowed=true; maintenance::requested=false; ESP.restarts=0;
}
void checkRecovery() {
  outage();
  pollWifiRecovery(aqua::WIFI_RECOVERY_OFFLINE_MS-1); assert(ESP.restarts==0);
  pollWifiRecovery(aqua::WIFI_RECOVERY_OFFLINE_MS);
  assert(ESP.restarts==1 && preferences.value==1 && wifiRecovery.used()==1);
  assert(wifiRecoveryClockMarker==aqua::WIFI_RECOVERY_CLOCK_MAGIC);
  // Simulated startup reloads the NVS count: a third recovery restart is impossible.
  outage(preferences.value); pollWifiRecovery(aqua::WIFI_RECOVERY_OFFLINE_MS);
  assert(ESP.restarts==1 && preferences.value==2);
  outage(preferences.value); pollWifiRecovery(aqua::WIFI_RECOVERY_OFFLINE_MS*10);
  assert(ESP.restarts==0 && wifiRecovery.remaining()==0 && preferences.writes==0);
  // Brief reconnection cannot replenish the persistent budget.
  wifiRecovery.observe(100,true,true); WiFi.state=WL_CONNECTED;
  pollWifiRecovery(100+aqua::WIFI_RECOVERY_STABLE_MS-1); assert(preferences.value==2);
  wifiRecovery.observe(101+aqua::WIFI_RECOVERY_STABLE_MS,true,false); WiFi.state=WL_DISCONNECTED;
  wifiRecovery.failedAttempt(); wifiRecovery.failedAttempt();
  pollWifiRecovery(102+aqua::WIFI_RECOVERY_STABLE_MS+aqua::WIFI_RECOVERY_OFFLINE_MS);
  assert(ESP.restarts==0 && wifiRecovery.remaining()==0);
  wifiRecovery.observe(1000000,true,true); WiFi.state=WL_CONNECTED;
  pollWifiRecovery(1000000+aqua::WIFI_RECOVERY_STABLE_MS);
  assert(preferences.value==0 && wifiRecovery.used()==0 && !ledota::lock);
  // Missing credentials / deliberate credential replacement disable outage timing,
  // but never erase an existing restart count.
  outage(1); wifiRecovery.observe(300000,false,false); pollWifiRecovery(600000);
  assert(ESP.restarts==0 && wifiRecovery.used()==1);
  wifiRecovery.observe(600000,true,false); wifiRecovery.failedAttempt();
  pollWifiRecovery(900000); assert(ESP.restarts==0); // At least two real failed attempts.
  wifiRecovery.failedAttempt(); pollWifiRecovery(900000); assert(ESP.restarts==1);
  // OTA startup validation, cloud/local flash locks, pending maintenance reboot,
  // AP clients and unflushed settings all defer an otherwise due restart.
  for(unsigned gate=0;gate<8;++gate) {
    outage();
    if(gate==0) ledota::allowed=false;
    if(gate==1) ledota::lock=true;
    if(gate==2) maintenance::requested=true;
    if(gate==3) WiFi.clients=1;
    if(gate==4) { settingsDirty=true; flushOkay=false; }
    if(gate==5) preferencesOpen=false;
    if(gate==6) storageOk=false;
    if(gate==7) wifiPending=true;
    pollWifiRecovery(300000); assert(ESP.restarts==0 && preferences.writes==0);
  }
  outage(); preferences.writeOkay=false;
  pollWifiRecovery(300000); assert(ESP.restarts==0 && recoveryStorageError && preferences.writes==1);
  pollWifiRecovery(300001); assert(preferences.writes==1); // Failed writes are throttled.
  preferences.writeOkay=true; pollWifiRecovery(330000);
  assert(ESP.restarts==1 && !recoveryStorageError && preferences.value==1);
  outage(); preferences.readOkay=false;
  pollWifiRecovery(300000); assert(ESP.restarts==0 && wifiRecovery.used()==0 && recoveryStorageError);
  outage(255); assert(wifiRecovery.remaining()==0); // Invalid NVS read fails closed.
  outage(2); wifiRecovery.observe(0,true,true); WiFi.state=WL_CONNECTED; preferences.writeOkay=false;
  pollWifiRecovery(600000); assert(wifiRecovery.used()==2 && ESP.restarts==0);
  // Recheck live status after the main loop's earlier network snapshot.
  outage(); WiFi.state=WL_CONNECTED; pollWifiRecovery(300000); assert(ESP.restarts==0);
  outage(2); wifiRecovery.observe(0,true,true); pollWifiRecovery(600000);
  assert(wifiRecovery.used()==2 && preferences.writes==0);
  outage(); clockValid=false; pollWifiRecovery(300000);
  assert(ESP.restarts==1 && wifiRecoveryClockMarker==0); // Never invent a valid clock.
  outage(); wifiRecovery.begin(0); wifiRecovery.observe(UINT32_MAX-100,true,false);
  wifiRecovery.failedAttempt(); wifiRecovery.failedAttempt();
  assert(!wifiRecovery.restartDue(aqua::WIFI_RECOVERY_OFFLINE_MS-102));
  assert(wifiRecovery.restartDue(aqua::WIFI_RECOVERY_OFFLINE_MS-101));
  wifiRecovery.begin(2); wifiRecovery.observe(UINT32_MAX-100,true,true);
  assert(!wifiRecovery.clearDue(aqua::WIFI_RECOVERY_STABLE_MS-102));
  assert(wifiRecovery.clearDue(aqua::WIFI_RECOVERY_STABLE_MS-101));
  // Exercise the production clock initializer, not only the eligibility helper.
  resetReason=ESP_RST_SW; retainedEpoch=1794860000; unsetCalls=0;
  initializeClock(aqua::WIFI_RECOVERY_CLOCK_MAGIC);
  assert(unsetCalls==0 && timeSource.value==TimeSource::Rtc);
  for(unsigned failure=0;failure<7;++failure) {
    resetReason=failure==0 ? ESP_RST_POWERON : failure==1 ? ESP_RST_BROWNOUT : ESP_RST_SW;
    retainedEpoch=failure==2 ? MIN_EPOCH-1 : failure==3 ? MAX_EPOCH+1 : failure==4 ? -1 : 1794860000;
    const auto marker=failure==5 ? 0 : failure==6 ? aqua::WIFI_RECOVERY_CLOCK_MAGIC+1 : aqua::WIFI_RECOVERY_CLOCK_MAGIC;
    unsetCalls=0; initializeClock(marker);
    assert(unsetCalls==1 && timeSource.value==TimeSource::None);
  }
  assert(aqua::resumeRecoveryClock(true,aqua::WIFI_RECOVERY_CLOCK_MAGIC,MIN_EPOCH,MIN_EPOCH,MAX_EPOCH));
  assert(aqua::resumeRecoveryClock(true,aqua::WIFI_RECOVERY_CLOCK_MAGIC,MAX_EPOCH,MIN_EPOCH,MAX_EPOCH));
  // End-to-end real network timeout -> recovery -> NVS -> simulated reboot.
  // Start without injected failures so only updateNetwork can arm each restart.
  uint8_t persisted=0;
  unsigned totalRestarts=0;
  for(unsigned boot=0;boot<3;++boot) {
    outage(persisted);
    wifiRecovery.begin(preferences.value);
    staConfigDisconnecting=false; lastWifiAttempt=0; staRetryDelay=0;
    staNextRetryDelay=STA_RETRY_MIN_MS;
    startStaAttempt(0);
    for(uint32_t now=0;now<=900000;now+=1000) {
      updateNetwork(now);
      pollWifiRecovery(now);
      if(ESP.restarts) { assert(now>=300000); break; }
    }
    if(boot<2) {
      assert(ESP.restarts==1 && WiFi.stops>=2);
      ++totalRestarts;
      persisted=preferences.value; // The next simulated boot reloads actual stored count.
      assert(persisted==boot+1);
    } else {
      assert(ESP.restarts==0 && WiFi.starts>4 && preferences.writes==0);
      assert(wifiRecovery.remaining()==0); // STA retries still run without more resets.
    }
  }
  assert(totalRestarts==2 && persisted==2);
  std::puts("PASS Wi-Fi recovery: actual reboot/clock path, persistent two-restart cap, stable reset, flapping, safety gates, NVS failure/backoff, rollover, RTC one-shot eligibility");
}
void check() {
  startStaAttempt(0);
  updateNetwork(0); // The production loop starts observing immediately after boot.
  assert(WiFi.starts==1 && staAttemptActive && WiFi.power==WIFI_POWER_8_5dBm);
  updateNetwork(STA_ATTEMPT_MS-1); assert(WiFi.starts==1 && WiFi.stops==0);
  updateNetwork(STA_ATTEMPT_MS); assert(!staAttemptActive && WiFi.stops==1 && staRetryDelay==30000);
  updateNetwork(44999); assert(WiFi.starts==1);
  updateNetwork(45000); assert(WiFi.starts==2 && staAttemptActive);
  updateNetwork(60000); assert(staRetryDelay==60000 && WiFi.stops==2);
  updateNetwork(119999); assert(WiFi.starts==2);
  updateNetwork(120000); assert(WiFi.starts==3);
  updateNetwork(135000); assert(staRetryDelay==120000);
  updateNetwork(255000); assert(WiFi.starts==4);
  updateNetwork(270000); assert(staRetryDelay==120000); // Bounded exponential retry ceiling.
  assert(!wifiRecovery.restartDue(299999));
  assert(wifiRecovery.restartDue(300000)); // Actual timed-out STA attempts arm recovery only after five minutes.
  updateNetwork(390000); assert(WiFi.starts==5);
  WiFi.state=WL_CONNECTED; updateNetwork(390001);
  assert(!staAttemptActive && staNextRetryDelay==STA_RETRY_MIN_MS);
  updateNetwork(500000); assert(WiFi.starts==5 && WiFi.stops==4); // No scans while connected.
  WiFi.state=WL_DISCONNECTED; updateNetwork(500001);
  assert(staRetryDelay==30000 && !staAttemptActive);
  updateNetwork(530001); assert(WiFi.starts==6);
  WiFi.state=WL_CONNECTED; updateNetwork(530002);
  wifiPending=true; wifiRequested=530010;
  updateNetwork(530310); assert(staConfigDisconnecting && WiFi.starts==6);
  updateNetwork(530560); // No intervening fast loop: stale old-connected flag must not cancel the new attempt.
  assert(!wifiPending && !staConfigDisconnecting && staAttemptActive && WiFi.starts==7);
  assert(WiFi.powerWrites==WiFi.starts && Serial.text.find(wifi.password)==std::string::npos);
  wasConnected=false; wifiPending=false; staAttemptActive=false; staConfigDisconnecting=false;
  lastWifiAttempt=0; staRetryDelay=0; staNextRetryDelay=STA_RETRY_MIN_MS; WiFi=Network{};
  startStaAttempt(UINT32_MAX-10000);
  updateNetwork(4998); assert(WiFi.stops==0);
  updateNetwork(4999); assert(WiFi.stops==1 && staRetryDelay==30000);
  updateNetwork(34998); assert(WiFi.starts==1);
  updateNetwork(34999); assert(WiFi.starts==2); // Rollover preserves both deadline and backoff.
  std::puts("PASS network: actual main callpath, bounded STA attempts/backoff, success reset, async credential switch, rollover, TX calibration, no password logging");
  checkRecovery();
}
} // namespace network_test
'''

cases = {
    "SPRING_BEFORE": datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc),
    "SPRING_AFTER": datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc),
    "FALL_BEFORE": datetime(2026, 10, 25, 0, 59, tzinfo=timezone.utc),
    "FALL_AFTER": datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc),
}
network_constants = "\n".join(line for line in source.splitlines() if line.startswith("constexpr uint32_t STA_") or line.startswith("constexpr uint32_t RECOVERY_NVS_"))
power_start = source.index("#ifndef LED_WIFI_TX_POWER")
power_macro = source[power_start:source.index("#endif", power_start) + len("#endif")]
network_helpers = source[source.index("void applyWifiPower()"):source.index("\nvoid startNetwork()")]
network_update = source[source.index("void updateNetwork(uint32_t now)"):source.index("\nvoid initializeClock(")]
recovery_helpers = source[source.index("void initializeClock("):source.index("\nvoid captureSensorErrorLines()") ]
network_code = (NETWORK_STUBS + network_constants + "\n" + power_macro +
                "\nuint32_t staNextRetryDelay=STA_RETRY_MIN_MS;\n" + network_helpers + network_update + recovery_helpers + NETWORK_CHECKS)
assert "WiFi.setAutoReconnect(false)" in source
assert 'preferences.isKey("net_reboots") ? preferences.getUChar("net_reboots", 255) : 0' in source
assert source.index('const uint32_t recoveryMarker = wifiRecoveryClockMarker;', source.index('void setup()')) < source.index('wifiRecoveryClockMarker = 0;', source.index('void setup()')) < source.index('pwmOk = startPwm();', source.index('void setup()'))
with tempfile.TemporaryDirectory(prefix="aqualight-check-") as directory:
    temp = Path(directory)
    cpp = temp / "check.cpp"
    cpp.write_text(f'#define TEST_TIMEZONE "{tz}"\n' +
                   "".join(f"#define {name} {int(moment.timestamp())}LL\n" for name, moment in cases.items()) + network_code + CHECKS.replace("int main() {", "int main() {\n  network_test::check();", 1))
    executable = temp / "check"
    subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(ROOT / "src"),
                    str(cpp), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
