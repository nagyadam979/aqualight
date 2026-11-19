#!/usr/bin/env python3
"""Private board header tests using only isolated synthetic credentials."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "tools/generate_device_config.py"
spec = importlib.util.spec_from_file_location("device_config_generator", SCRIPT)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class FakeEnv(dict):
    def __init__(self, project, name="aqualight", relative=False):
        super().__init__(PIOENV=name)
        self.project = Path(project)
        self.build = Path(".pio/build") / name
        if not relative:
            self.build = self.project / self.build
        self.includes = []

    def subst(self, name):
        return {"$PROJECT_DIR": str(self.project), "$BUILD_DIR": str(self.build)}[name]

    def Append(self, **values):
        self.includes.extend(values.get("CPPPATH", []))


class DeviceConfigTests(unittest.TestCase):
    def private(self, path, value="synthetic-password"):
        if path.exists():
            path.chmod(0o600)
        path.write_text(json.dumps({"1ch": value, "2ch": "unrelated-synthetic-value"}), encoding="utf-8")
        path.chmod(0o600)

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=cwd,
                              capture_output=True, text=True, check=False)

    def test_valid_limits_printable_bytes_and_unchanged_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "private.json", Path(folder) / "generated/device_config.h"
            for value in ("a" * 8, "z" * 63, ' space~ !"#$%&\'()*+,-./:;<=>?@[\\]^_`{|} '):
                self.private(source, value)
                before = source.read_bytes(), source.stat().st_mtime_ns
                console = io.StringIO()
                with contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
                    generator.generate(source, output)
                self.assertEqual(console.getvalue(), "")
                self.assertEqual(generator.load_config(source), value)
                self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertTrue(output.read_text().startswith("#pragma once\n#define LED_AP_PASSWORD "))

    def test_cpp_escaping_preserves_exact_password_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source, output = directory / "private.json", directory / "device_config.h"
            value = 'quote" slash\\ and ??/ tail\\'
            self.private(source, value)
            generator.generate(source, output)
            expected = ",".join(str(byte) for byte in value.encode("ascii"))
            cpp = '#include "device_config.h"\n#include <cstring>\n#include <cassert>\n'
            cpp += 'int main(){const unsigned char expected[]={' + expected + '};'
            cpp += 'assert(sizeof(LED_AP_PASSWORD)==sizeof(expected)+1);'
            cpp += 'assert(!std::memcmp(LED_AP_PASSWORD,expected,sizeof(expected)));}\n'
            binary = directory / "check"
            subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-x", "c++", "-", "-I", str(directory), "-o", str(binary)],
                           input=cpp, text=True, check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)

    def test_invalid_types_length_nonascii_and_controls(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "private.json", Path(folder) / "header.h"
            values = [True, 12345678, None, [], {}, "", "x" * 7, "x" * 64, "á" * 8]
            values += ["valid-prefix" + chr(byte) for byte in list(range(32)) + [127, 128, 255]]
            for value in values:
                self.private(source, value)
                with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                    generator.generate(source, output)
                self.assertFalse(output.exists())

    def test_invalid_permissions_schema_duplicates_and_json(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "private.json", Path(folder) / "header.h"
            for mode in (0o400, 0o640, 0o644, 0o660, 0o700):
                self.private(source); source.chmod(mode)
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    generator.generate(source, output)
            for text in ('{}', '[]', 'null', '{bad', '{"1ch":"valid-one","1ch":"valid-two"}'):
                source.write_text(text); source.chmod(0o600)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    generator.generate(source, output)
            source.write_bytes(b'\xff'); source.chmod(0o600)
            with self.assertRaises(ValueError):
                generator.generate(source, output)
            self.assertFalse(output.exists())

    def test_regular_file_and_source_destination_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            source, link, fifo = (Path(folder) / name for name in ("private.json", "link", "fifo"))
            self.private(source)
            original = source.read_bytes()
            link.symlink_to(source)
            with self.assertRaises(OSError):
                generator.load_config(link)
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(ValueError):
                generator.load_config(fifo)
            with self.assertRaises(ValueError):
                generator.generate(source, source)
            with self.assertRaises(ValueError):
                generator.generate(source, link)
            self.assertEqual(source.read_bytes(), original)

    def test_atomic_output_idempotence_permissions_and_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "private.json", Path(folder) / "device_config.h"
            self.private(source)
            generator.generate(source, output)
            before = output.read_bytes(), output.stat().st_mtime_ns
            output.chmod(0o644)
            generator.generate(source, output)
            self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), before)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.private(source, "new-synthetic-password")
            with mock.patch.object(generator.os, "replace", side_effect=OSError):
                with self.assertRaises(OSError):
                    generator.generate(source, output)
            self.assertEqual(output.read_bytes(), before[0])
            self.assertEqual(list(Path(folder).glob(".device_config-*.tmp")), [])

    def test_cli_default_board_no_secret_console_or_source_change(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / generator.DEFAULT_CONFIG, Path(folder) / "device_config.h"
            value = "distinct-synthetic-password"
            self.private(source, value)
            before = source.read_bytes()
            for arguments in (("--output", output), ("--config", source, "--output", output, "--board", "1ch")):
                result = self.cli(*arguments, cwd=folder)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout + result.stderr, "")
                self.assertIn(value, output.read_text())
            source.chmod(0o644)
            result = self.cli("--output", output, cwd=folder)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(value, result.stdout + result.stderr)
            self.assertEqual(source.read_bytes(), before)
            self.assertNotEqual(self.cli("--output", output, "--board", "2ch", cwd=folder).returncode, 0)

    def test_normal_and_ble_builds_never_access_config(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("aqualight", "aqualight_ble", "custom", "aqualight1ch_backup"):
                env = FakeEnv(folder, name)
                with mock.patch.object(generator, "generate", side_effect=AssertionError("Unexpected credential access")):
                    generator.configure_platformio(env)
                self.assertEqual(env.includes, [])
                self.assertFalse((Path(folder) / ".pio").exists())

    def test_platformio_entrypoint_relative_build_path_and_silent_header(self):
        with tempfile.TemporaryDirectory() as folder:
            self.private(Path(folder) / generator.DEFAULT_CONFIG)
            for relative in (False, True):
                env = FakeEnv(folder, "aqualight1ch", relative)
                imports, console = [], io.StringIO()
                with contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
                    runpy.run_path(str(SCRIPT), init_globals={"Import": imports.append, "env": env})
                output = Path(folder) / ".pio/build/aqualight1ch/generated/device_config.h"
                self.assertEqual(imports, ["env"])
                self.assertEqual(console.getvalue(), "")
                self.assertEqual(env.includes, [str(output.parent)])
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
