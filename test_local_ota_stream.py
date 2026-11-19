#!/usr/bin/env python3
"""Exercise the OTA TCP stream with local socket pairs, never a real board."""
import importlib.util
from pathlib import Path
import socket
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ota_stream", ROOT / "tools/upload_local_ota.py")
uploader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uploader)


class StreamTests(unittest.TestCase):
    def run_transfer(self, final):
        content = bytes(range(256)) * 1024
        received = bytearray()
        host, device = socket.socketpair()
        host.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        device.settimeout(3)

        def emulate_device():
            try:
                while len(received) < len(content):
                    chunk = device.recv(1460)
                    if not chunk:
                        return
                    received.extend(chunk)
                    device.sendall(str(len(chunk)).encode())
                device.sendall(final)
            finally:
                device.close()

        thread = threading.Thread(target=emulate_device)
        thread.start()
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "firmware.bin"
            image.write_bytes(content)
            okay, reply = uploader.stream_image(host, image)
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(bytes(received), content)
        return okay, reply

    def test_backpressured_stream_drains_all_device_replies(self):
        okay, reply = self.run_transfer(b"OK")
        self.assertTrue(okay)
        self.assertTrue(reply.endswith(b"OK"))

    def test_complete_transfer_without_device_validation_is_failure(self):
        okay, reply = self.run_transfer(b"ERROR: image rejected")
        self.assertFalse(okay)
        self.assertIn(b"image rejected", reply)


if __name__ == "__main__":
    unittest.main()
