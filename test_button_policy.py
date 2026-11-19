#!/usr/bin/env python3
"""Verify a brief/stuck/wrapping BOOT press cannot repeatedly trigger AP work."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
CODE = r'''
#include <cassert>
#include <cstdint>
#include "board_policy.h"
int main() {
  aqua::MaintenanceButtonPolicy button;
  assert(!button.tick(0, false));
  assert(!button.tick(10, true));
  assert(!button.tick(3009, true));
  assert(!button.tick(3010, false)); // A released short press never opens AP.
  assert(!button.tick(4000, true));
  assert(button.tick(7000, true));
  assert(!button.tick(8000, true));
  assert(!button.tick(1000000, true)); // A stuck switch causes only one trigger.
  assert(!button.tick(1000001, false));
  assert(!button.tick(UINT32_MAX - 1000, true));
  assert(!button.tick(1998, true));
  assert(button.tick(1999, true));
  assert(!button.tick(2000, true));
}
'''
with tempfile.TemporaryDirectory() as directory:
    source, binary = Path(directory) / "check.cpp", Path(directory) / "check"
    source.write_text(CODE)
    subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-I", str(ROOT / "src"), str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print("PASS maintenance button: short/released press, single trigger while stuck, rearm and millis wrap")
