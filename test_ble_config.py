#!/usr/bin/env python3
"""Test private BLE pairing configuration with isolated synthetic credentials."""
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
SCRIPT = ROOT / "tools/generate_ble_config.py"
spec = importlib.util.spec_from_file_location("ble_config_generator", SCRIPT)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class FakeEnv(dict):
    def __init__(self, project, name="aqualight", defines=(), build_defines=()):
        super().__init__(PIOENV=name, CPPDEFINES=list(defines), BUILD_FLAGS=[])
        self.project = Path(project)
        self.build = self.project / ".pio/build" / name
        self.build_defines = list(build_defines)
        self.includes = []

    def subst(self, expression):
        return {"$PROJECT_DIR": str(self.project), "$BUILD_DIR": str(self.build)}[expression]

    def ParseFlags(self, _flags):
        return {"CPPDEFINES": self.build_defines}

    def Append(self, **values):
        self.includes.extend(values.get("CPPPATH", []))


class BleConfigTests(unittest.TestCase):
    def private(self, path, value=234567):
        if path.exists():
            path.chmod(0o600)
        path.write_text(json.dumps({"passkey": value}), encoding="utf-8")
        path.chmod(0o600)

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=cwd,
                              capture_output=True, text=True, check=False)

    def test_valid_limits_header_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "private.json"
            output = Path(directory) / "generated/ble_config.h"
            for passkey in (100000, 234567, 999999):
                self.private(source, passkey)
                console = io.StringIO()
                with contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
                    generator.generate(source, output)
                self.assertEqual(console.getvalue(), "")
                self.assertEqual(generator.load_config(source), passkey)
                self.assertIn(f"constexpr uint32_t passkey = {passkey};", output.read_text())
                self.assertIn("namespace bleconfig", output.read_text())
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                before = output.stat().st_mtime_ns
                generator.generate(source, output)
                self.assertEqual(output.stat().st_mtime_ns, before)
                output.chmod(0o644)
                generator.generate(source, output)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_invalid_permissions_types_fields_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "private.json", Path(directory) / "header.h"
            for value in (True, False, None, "234567", 234567.0, 99999, 1000000, [], {}):
                self.private(source, value)
                with self.subTest(value=value), self.assertRaises(ValueError):
                    generator.generate(source, output)
                self.assertFalse(output.exists())
            for mode in (0o400, 0o640, 0o644, 0o660, 0o700):
                self.private(source)
                source.chmod(mode)
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    generator.generate(source, output)
            for text in ('{}', '[]', '{"passkey":234567,"extra":0}', '{"passkey":234567,"passkey":345678}', '{bad'):
                source.write_text(text); source.chmod(0o600)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    generator.generate(source, output)
            self.assertFalse(output.exists())

    def test_init_exclusive_uniform_range_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "private.json"
            for offset, expected in ((0, 100000), (899999, 999999)):
                with mock.patch.object(generator.secrets, "randbelow", return_value=offset) as random:
                    generator.init_config(source)
                random.assert_called_once_with(900000)
                self.assertEqual(generator.load_config(source), expected)
                self.assertEqual(source.stat().st_mode & 0o777, 0o600)
                before = source.read_bytes(), source.stat().st_mtime_ns
                with self.assertRaises(FileExistsError):
                    generator.init_config(source)
                self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before)
                source.unlink()

    def test_cli_never_discloses_passkey(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "private.json", Path(directory) / "header.h"
            initialized = self.cli("--init", "--config", source, "--output", output)
            self.assertEqual(initialized.returncode, 0)
            passkey = str(generator.load_config(source))
            self.assertNotIn(passkey, initialized.stdout + initialized.stderr)
            self.assertIn(passkey, output.read_text())
            before = source.read_bytes()
            repeated = self.cli("--init", "--config", source)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertNotIn(passkey, repeated.stdout + repeated.stderr)
            self.assertEqual(before, source.read_bytes())
            generated = self.cli("--config", source, "--output", output)
            self.assertEqual(generated.returncode, 0)
            self.assertNotIn(passkey, generated.stdout + generated.stderr)
            source.chmod(0o644)
            unsafe = self.cli("--config", source, "--output", output)
            self.assertNotEqual(unsafe.returncode, 0)
            self.assertNotIn(passkey, unsafe.stdout + unsafe.stderr)

    def test_default_cli_path_is_separate_and_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            initialized = self.cli("--init", cwd=directory)
            self.assertEqual(initialized.returncode, 0)
            source = Path(directory) / ".ota-credentials-ble.json"
            self.assertTrue(source.exists())
            self.assertNotIn(str(generator.load_config(source)), initialized.stdout + initialized.stderr)
            self.assertFalse((Path(directory) / ".ota-credentials.json").exists())

    def test_normal_build_does_not_require_private_ble_file(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, defines in (("aqualight", ()), ("aqualight1ch", ()), ("ordinary", (("LED_BLE_ONLY", 0),))):
                env = FakeEnv(directory, name, defines)
                generator.configure_platformio(env)
                self.assertFalse(env.build.exists())
                self.assertEqual(env.includes, [])
            env = FakeEnv(directory)
            env["CPPDEFINES"] = {"LED_BLE_ONLY": 0}
            generator.configure_platformio(env)
            self.assertFalse(env.build.exists())

    def test_platformio_ble_env_and_both_define_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / generator.DEFAULT_CONFIG
            self.private(source)
            for name, defines, flags in (("aqualight_ble", (), ()),
                                         ("custom", (("LED_BLE_ONLY", 1),), ()),
                                         ("custom_flags", (), ("LED_BLE_ONLY=1",))):
                env = FakeEnv(directory, name, defines, flags)
                generator.configure_platformio(env)
                output = env.build / "generated/ble_config.h"
                self.assertTrue(output.exists())
                self.assertEqual(env.includes, [str(output.parent)])
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_platformio_import_entrypoint_is_conditional_and_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            imported = []
            ordinary = FakeEnv(directory)
            runpy.run_path(str(SCRIPT), init_globals={"Import": imported.append, "env": ordinary})
            self.assertEqual(imported, ["env"])
            self.assertFalse(ordinary.build.exists())
            source = Path(directory) / generator.DEFAULT_CONFIG
            self.private(source)
            ble = FakeEnv(directory, "aqualight_ble")
            console = io.StringIO()
            with contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
                runpy.run_path(str(SCRIPT), init_globals={"Import": imported.append, "env": ble})
            self.assertEqual(console.getvalue(), "")
            self.assertTrue((ble.build / "generated/ble_config.h").exists())
            self.assertEqual(imported, ["env", "env"])

    def test_output_cannot_replace_private_source_and_symlink_init_is_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            source, link = Path(directory) / "private.json", Path(directory) / "link.json"
            self.private(source)
            before = source.read_bytes()
            with self.assertRaises(ValueError):
                generator.generate(source, source)
            self.assertEqual(source.read_bytes(), before)
            link.symlink_to(source)
            with self.assertRaises(FileExistsError):
                generator.init_config(link)
            with self.assertRaises(OSError):
                generator.load_config(link)
            self.assertEqual(source.read_bytes(), before)
            fifo = Path(directory) / "not-a-regular-file"
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(ValueError):
                generator.load_config(fifo)


if __name__ == "__main__":
    unittest.main()
