#!/usr/bin/env python3
"""Check the production USB-only status request parser with ASan/UBSan."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
CHECKS = r'''
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <string>
#include "usb_status_request.h"

unsigned feed(aqua::UsbStatusRequest &parser, const std::string &bytes) {
  unsigned count = 0;
  for (unsigned char byte : bytes) if (parser.feed(byte)) ++count;
  return count;
}

int main() {
  static_assert(aqua::UsbStatusRequest::MAX_LINE == 16, "Keep physical USB input bounded");
  static_assert(sizeof(aqua::UsbStatusRequest) <= 24, "No hidden dynamic input storage");
  aqua::UsbStatusRequest parser;
  assert(feed(parser, "status") == 0); // Never signal a partial line.
  assert(feed(parser, "\r") == 1);
  assert(feed(parser, "\n") == 0); // CRLF is one request.
  assert(feed(parser, "status\nstatus\rstatus\r\n") == 3);
  assert(feed(parser, "\r\n\n\r") == 0);

  for (const auto *command : {"STATUS", "Status", " status", "status ", "statusstatus", "off", "reboot", "reset", "time", "status now", "help"}) {
    parser.reset();
    assert(feed(parser, std::string(command) + "\n") == 0);
  }

  // No byte may be ignored after a valid prefix and leave it executable.
  // Only CR and LF are actual delimiters; every other suffix invalidates it.
  for (unsigned value = 0; value <= 255; ++value) {
    parser.reset();
    std::string wire = "status";
    wire.push_back(static_cast<char>(value)); wire.push_back('\n');
    assert(feed(parser, wire) == ((value == '\r' || value == '\n') ? 1u : 0u));
    assert(feed(parser, "status\n") == 1); // Terminator fully recovers parser state.
  }

  parser.reset();
  assert(feed(parser, std::string("oops\0status\n", 12)) == 0);
  assert(feed(parser, std::string("status\0\n", 8)) == 0);
  assert(feed(parser, "status\b\nstatus\t\nstatus\x7f\n") == 0);
  assert(feed(parser, std::string(16, 'x') + "\n") == 0);
  assert(feed(parser, std::string(17, 'x') + "status\n") == 0);
  assert(feed(parser, std::string("status") + std::string(65536, 'x') + "\n") == 0);
  assert(feed(parser, "status\n") == 1); // Overflow cannot wrap to a valid prefix.

  parser.reset();
  assert(feed(parser, "sta") == 0);
  parser.reset();
  assert(feed(parser, "tus\n") == 0);
  assert(feed(parser, "status\r") == 1);
  parser.reset();
  assert(feed(parser, "\nstatus\r\n") == 1);

  // Compare arbitrary binary chunks with a complete-line reference model.
  // Deliberate command insertions ensure this exercises positive requests too.
  std::string reference;
  uint32_t random = 0x12abcdef;
  unsigned observed = 0, expected = 0;
  parser.reset();
  auto byte = [&](uint8_t value) {
    bool wanted = false;
    if (value == '\r' || value == '\n') {
      wanted = reference == "status"; reference.clear();
    } else reference.push_back(static_cast<char>(value));
    const bool actual = parser.feed(value);
    assert(actual == wanted);
    observed += actual; expected += wanted;
  };
  for (unsigned i = 0; i < 100000; ++i) {
    random ^= random << 13; random ^= random >> 17; random ^= random << 5;
    byte(static_cast<uint8_t>(random));
    if (i % 71 == 0) for (unsigned char value : std::string("\nstatus\r\n")) byte(value);
    if (i % 997 == 0) { parser.reset(); reference.clear(); }
  }
  assert(observed == expected && observed > 1000);
  std::puts("PASS USB status: exact read-only command, complete CR/LF/CRLF lines, malformed/overflow rejection, reset and random-byte framing");
}
'''

with tempfile.TemporaryDirectory(prefix="aqualight-usb-status-") as directory:
    temp = Path(directory)
    cpp, binary = temp / "check.cpp", temp / "check"
    cpp.write_text(CHECKS)
    subprocess.run([
        os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(ROOT / "src"),
        str(cpp), "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)
