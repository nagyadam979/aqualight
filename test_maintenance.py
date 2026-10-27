#!/usr/bin/env python3
"""Check the actual bounded Telnet parser/auth contract without a board.

Needs a C++17 compiler with ASan/UBSan. Does not exercise TCP, native ArduinoOTA,
flash partition switching, restart, or shared-lock/worker integration.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
CHECKS = r'''
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>
#include "maintenance.h"
using namespace maintenance;

std::vector<std::string> decode(const std::vector<uint8_t>& bytes) {
  LineReader reader;
  std::vector<std::string> lines;
  for(const auto byte:bytes) {
    const auto event=reader.feed(byte);
    if(event!=Input::None) {
      assert(event==Input::Line);
      lines.emplace_back(reader.line());
      reader.clearLine();
    }
  }
  return lines;
}
int main() {
  auto lines=decode({'h','e','l','p','\r','\n','s','t','a','t','u','s','\r',0});
  assert((lines==std::vector<std::string>{"help","status"}));
  lines=decode({255,251,1,'s','t',255,253,3,'a','t','u','s','\n'});
  assert((lines==std::vector<std::string>{"status"}));
  lines=decode({255,250,24,'r','e','b','o','o','t',255,255,42,255,240,'h','e','l','p','\r','\n'});
  assert((lines==std::vector<std::string>{"help"})); // Subnegotiation cannot execute reboot.
  lines=decode({'h','e','x',8,'l','p','\n'});
  assert((lines==std::vector<std::string>{"help"}));
  LineReader reader;
  for(unsigned i=0;i<MAX_LINE;++i) assert(reader.feed('a')==Input::None);
  assert(reader.feed('\n')==Input::Line && std::strlen(reader.line())==MAX_LINE);
  reader.clearLine();
  for(unsigned i=0;i<MAX_LINE+1000;++i) reader.feed('x');
  assert(reader.feed('\n')==Input::Invalid);
  reader.clearLine();
  for(const auto byte:std::string("status\n")) {
    if(reader.feed(byte)==Input::Line) assert(command(reader.line())==Command::Status);
  }
  reader.reset(); reader.feed('h'); reader.feed(255); reader.feed(255);
  assert(reader.feed('\n')==Input::Invalid); // Escaped binary IAC isn't silently removed from authentication text.
  reader.reset(); reader.feed('h'); reader.feed(0);
  assert(reader.feed('\n')==Input::Invalid);
  reader.clearLine(); assert(reader.line()[0]==0);
  constexpr const char* password="OnlyATest12!";
  assert(equalPassword(password,password));
  assert(!equalPassword("OnlyATest12",password));
  assert(!equalPassword("OnlyATest12!x",password));
  assert(!equalPassword("",password));
  LoginGate login;
  login.reset(100);
  assert(login.attempt("status",password,101)==Login::Retry && !login.authenticated());
  assert(login.attempt("reboot",password,102)==Login::Retry && !login.authenticated());
  assert(login.attempt("bad",password,103)==Login::Close && !login.authenticated());
  assert(login.attempt(password,password,104)==Login::Close); // Attempts exhausted, even with the correct password.
  login.reset(1000);
  assert(login.attempt(password,password,10999)==Login::Accepted && login.authenticated());
  assert(!login.expired(999999));
  login.reset(1000);
  assert(login.attempt(password,password,11000)==Login::Close && !login.authenticated());
  login.reset(UINT32_MAX-500);
  assert(!login.expired(9498));
  assert(login.expired(9499)); // Authentication timeout survives millis rollover.
  assert(command("help")==Command::Help);
  assert(command(" status  ")==Command::Status);
  assert(command("ota-check")==Command::OtaCheck);
  assert(command("reboot")==Command::Reboot);
  for(const auto invalid:{"", "reboot now", "reboot;ls", "status && reboot", "OTA-check", "wifi", "password", "sh", "help\nreboot"}) {
    assert(command(invalid)==Command::Unknown);
  }
  uint32_t random=987654321;
  for(unsigned run=0;run<10000;++run) {
    reader.reset();
    for(unsigned i=0;i<200;++i) {
      random^=random<<13; random^=random>>17; random^=random<<5;
      if(reader.feed(static_cast<uint8_t>(random))!=Input::None) {
        assert(std::strlen(reader.line())<=MAX_LINE);
        reader.clearLine();
      }
    }
  }
  std::puts("PASS maintenance: bounded lines, CRLF/CR-NUL, IAC negotiations, binary/overflow rejection, password matching, attempt/time limits, rollover, restricted commands, random-byte fuzz");
}
'''
with tempfile.TemporaryDirectory(prefix="led-maintenance-test-") as directory:
    temp = Path(directory)
    cpp = temp / "check.cpp"
    binary = temp / "check"
    cpp.write_text(CHECKS)
    subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(ROOT / "src"),
                    str(cpp), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
