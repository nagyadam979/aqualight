#!/usr/bin/env python3
"""Check private pairing config validation without reading real board keys."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mesh_config_generator", ROOT / "tools/generate_mesh_config.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class MeshConfigTests(unittest.TestCase):
    def config(self):
        return {"masterMac": [2, 0, 0, 0, 0, 1], "slaveMac": [2, 0, 0, 0, 0, 2],
                "pmk": list(range(16)), "lmk": list(range(16, 32)), "channel": 4}

    def test_private_deterministic_header(self):
        with tempfile.TemporaryDirectory() as directory:
            config, header = Path(directory) / "private.json", Path(directory) / "out/mesh_config.h"
            config.write_text(json.dumps(self.config()))
            config.chmod(0o600)
            generator.generate(config, header)
            first = header.read_bytes()
            generator.generate(config, header)
            self.assertEqual(first, header.read_bytes())
            self.assertEqual(header.stat().st_mode & 0o777, 0o600)
            self.assertIn(b"constexpr uint8_t channel = 4;", first)

    def test_invalid_pairs_keys_channel_and_permissions(self):
        bad = []
        for name, value in [("slaveMac", self.config()["masterMac"]), ("masterMac", [1, 0, 0, 0, 0, 1]),
                            ("pmk", [0] * 16), ("lmk", [0] * 15), ("channel", 0), ("channel", 14),
                            ("channel", True), ("pmk", [True] * 16)]:
            config = self.config(); config[name] = value; bad.append(config)
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "private.json", Path(directory) / "header.h"
            for config in bad:
                source.write_text(json.dumps(config)); source.chmod(0o600)
                with self.assertRaises(ValueError): generator.generate(source, output)
                self.assertFalse(output.exists())
            source.write_text(json.dumps(self.config())); source.chmod(0o644)
            with self.assertRaises(ValueError): generator.generate(source, output)

    def test_mac_validation(self):
        self.assertEqual(generator.mac_bytes("02:00:00:00:00:01"), [2, 0, 0, 0, 0, 1])
        for address in ["ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00", "2:00:00:00:00:01", "not-a-mac"]:
            with self.assertRaises(ValueError): generator.mac_bytes(address)


if __name__ == "__main__":
    unittest.main()
