#!/usr/bin/env python3
"""Verify gzip assets against the bytes emitted by the actual C++ UI header."""
from pathlib import Path
import gzip
import importlib.util
import os
import re
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("generate_web_assets", ROOT / "tools" / "generate_web_assets.py")
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def emitted_assets(header):
    result = {}
    for name in generator.ASSETS:
        match = re.search(r"const unsigned char " + name + r"_GZIP\[\] PROGMEM = \{([^}]+)\};", header)
        if not match:
            raise AssertionError("Generated array missing: " + name)
        result[name] = bytes(int(token, 16) for token in re.findall(r"0x[0-9a-f]{2}", match.group(1)))
    return result


def compiled_assets(directory, channels, hostname):
    source = directory / "actual.cpp"
    executable = directory / "actual"
    source.write_text('''#include <cstdio>
#include <cstdint>
#include <initializer_list>
#include "''' + str(ROOT / "src" / "web_ui.h") + '''"
int main() {
  for (const auto name : {0, 1}) {
    const char *data = name == 0 ? INDEX_HTML : HUB_HTML;
    const std::uint32_t length = name == 0 ? sizeof(INDEX_HTML)-1 : sizeof(HUB_HTML)-1;
    if (std::fwrite(&length, sizeof(length), 1, stdout) != 1 ||
        std::fwrite(data, 1, length, stdout) != length) return 1;
  }
}
''')
    subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-I", str(directory), f"-DLED_CHANNELS={channels}", f'-DLED_HOSTNAME="{hostname}"',
                    str(source), "-o", str(executable)], check=True)
    output = subprocess.check_output([str(executable)])
    result = {}
    for name in generator.ASSETS:
        length = struct.unpack("=I", output[:4])[0]
        result[name], output = output[4:4 + length], output[4 + length:]
    if output:
        raise AssertionError("Unexpected compiled asset output")
    return result


class AssetTests(unittest.TestCase):
    def test_both_variants_match_real_compiled_cpp_exactly(self):
        with tempfile.TemporaryDirectory(prefix="aqualight-web-assets-") as temporary:
            directory = Path(temporary)
            (directory / "Arduino.h").write_text("#pragma once\n#define PROGMEM\n")
            for channels, hostname in [(1, "led-control-1ch"), (2, "led-control")]:
                with self.subTest(channels=channels):
                    output = directory / "generated" / "web_assets.h"
                    sizes = generator.generate(ROOT / "src/web_ui.h", output, channels, hostname)
                    emitted = emitted_assets(output.read_text())
                    actual = compiled_assets(directory, channels, hostname)
                    for name in generator.ASSETS:
                        self.assertEqual(gzip.decompress(emitted[name]), actual[name])
                        self.assertEqual(sizes[name], (len(actual[name]), len(emitted[name])))
                        self.assertLess(len(emitted[name]), len(actual[name]) * 0.5)
                        self.assertEqual(emitted[name][4:8], b"\0\0\0\0")
                        self.assertEqual(emitted[name][9], 255)
                        self.assertIn(f'data-channels="{channels}"'.encode(), actual[name])
                    self.assertIn(hostname.encode() + b".local:3232", actual["INDEX_HTML"])
                    self.assertIn(("led-szallag" if channels == 1 else "terrárium").encode(), actual["INDEX_HTML"])
                    old_content, old_time = output.read_bytes(), output.stat().st_mtime_ns
                    generator.generate(ROOT / "src/web_ui.h", output, channels, hostname)
                    self.assertEqual(output.read_bytes(), old_content)
                    self.assertEqual(output.stat().st_mtime_ns, old_time)

    def test_fails_closed_for_unrecognized_cpp_or_configuration(self):
        source = (ROOT / "src/web_ui.h").read_text()
        for invalid in [source.replace('" LED_UI_CHANNELS R"', '" UNKNOWN_MACRO R"', 1),
                        source.replace('" LED_UI_CHANNELS R"', '" LED_UI_CHANNELS + R"', 1),
                        source.replace('</html>)AQUAHTML";', '</html>)MISSING";', 1)]:
            with self.assertRaises(ValueError):
                generator.render_asset(invalid, "INDEX_HTML", 2, "led-control")
        with self.assertRaises(ValueError):
            generator.render_asset(source, "INDEX_HTML", 3, "led-control")
        for invalid in ['<script>', 'bad hostname', '../path', 'x"y', '']:
            with self.assertRaises(ValueError):
                generator.render_asset(source, "INDEX_HTML", 2, invalid)

    def test_platformio_pre_hook_uses_environment_variant_and_tracks_inputs(self):
        with tempfile.TemporaryDirectory(prefix="aqualight-web-env-") as temporary:
            directory = Path(temporary)

            class Environment:
                def __init__(self):
                    self.appended, self.dependencies = None, None

                def get(self, name, fallback=None):
                    return {"BUILD_FLAGS": ["resolved-build-flags"], "CPPDEFINES": []}.get(name, fallback)

                def ParseFlags(self, flags):
                    self.flags = flags
                    return {"CPPDEFINES": [("LED_CHANNELS", 1), ("LED_HOSTNAME", '\\"custom-slave\\"')]}

                def subst(self, value):
                    return {"$BUILD_DIR": str(directory), "$PROJECT_DIR": str(ROOT)}[value]

                def Append(self, **values):
                    self.appended = values

                def Depends(self, target, sources):
                    self.dependencies = (target, sources)

            environment = Environment()
            generator.install(environment)
            generated = directory / "generated" / "web_assets.h"
            self.assertEqual(environment.appended, {"CPPPATH": [str(directory / "generated")]})
            self.assertIn(str(ROOT / "src/web_ui.h"), environment.dependencies[1])
            self.assertEqual(environment.flags, ["resolved-build-flags"])
            page = gzip.decompress(emitted_assets(generated.read_text())["INDEX_HTML"])
            self.assertIn(b'data-channels="1"', page)
            self.assertIn(b"custom-slave.local:3232", page)


if __name__ == "__main__":
    unittest.main()
