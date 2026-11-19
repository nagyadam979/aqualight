#!/usr/bin/env python3
"""Authenticated local OTA with a continuous TCP stream and drained replies.

Uses the installed Arduino espota.py invitation/authentication implementation.
The device still validates its complete application image before selecting it.
Passwords are read from the ignored credential file, never command arguments.
"""
import argparse
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import socket
import threading
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def stream_image(connection, firmware, progress=lambda value: None):
    """Keep the firmware receiver fed while separately draining byte-count ACKs."""
    replies = bytearray()
    errors = []
    connection.settimeout(10)

    def receive():
        try:
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                replies.extend(chunk)
                if len(replies) > 8192:
                    del replies[:-4096]
        except OSError as error:
            errors.append(type(error).__name__)

    reader = threading.Thread(target=receive, daemon=True)
    reader.start()
    sent = 0
    try:
        with Path(firmware).open("rb") as source:
            size = Path(firmware).stat().st_size
            while True:
                chunk = source.read(65536)
                if not chunk:
                    break
                connection.sendall(chunk)
                sent += len(chunk)
                progress(int(sent * 100 / size))
        reader.join(20)
        if reader.is_alive():
            errors.append("final_reply_timeout")
    except OSError as error:
        errors.append(type(error).__name__)
    finally:
        connection.close()
        reader.join(1)
    return not errors and sent == Path(firmware).stat().st_size and b"OK" in replies, bytes(replies[-256:])


def authenticated_upload(ip, firmware, password):
    """Transfer an application image after the caller has verified target/storage readiness."""
    firmware = Path(firmware).resolve()
    path = Path.home() / ".platformio/packages/framework-arduinoespressif32/tools/espota.py"
    spec = importlib.util.spec_from_file_location("arduino_espota", path)
    ota = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ota)
    ota.TIMEOUT = 4
    logging.basicConfig(level=logging.WARNING)
    digest = hashlib.md5(firmware.read_bytes()).hexdigest()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("0.0.0.0", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        okay, reply, error = ota.send_invitation_and_get_auth_challenge(ip, 3232, f"0 {port} {firmware.stat().st_size} {digest}\n")
        if not okay:
            raise SystemExit("OTA invitation failed: " + str(error))
        if not reply.startswith("AUTH "):
            raise SystemExit("Expected authenticated OTA challenge; refusing unauthenticated transfer")
        nonce = reply.split()[1]
        if len(nonce) not in (32, 64):
            raise SystemExit("Unsupported OTA authentication challenge")
        okay, error = ota.authenticate(ip, 3232, password, False, len(nonce) == 32,
                                       str(firmware), firmware.stat().st_size, digest, nonce)
        if not okay:
            raise SystemExit("OTA authentication failed: " + str(error))
        print("Authenticated; waiting for device TCP connection.", flush=True)
        listener.settimeout(10)
        connection, address = listener.accept()
        if address[0] != ip:
            connection.close()
            raise SystemExit("Unexpected OTA TCP peer")
        previous = [-10]

        def progress(value):
            if value >= previous[0] + 10 or value == 100:
                print("Upload:", value, "%", flush=True)
                previous[0] = value

        okay, reply = stream_image(connection, firmware, progress)
        if not okay:
            raise SystemExit("OTA transfer was not confirmed: " + reply.decode("ascii", "replace"))
        print("Device confirmed the complete image; reboot pending.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, choices=("2ch", "1ch"))
    parser.add_argument("--ip", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--upload", action="store_true", help="Perform the authenticated update; default is preflight only")
    args = parser.parse_args()
    ip = socket.gethostbyname(args.ip)
    with urllib.request.urlopen("http://" + ip + "/api/state", timeout=30) as response:
        state = json.load(response)
    expected_channels = 2 if args.board == "2ch" else 1
    if state.get("channels") != expected_channels:
        raise SystemExit("Wrong board: refusing upload")
    maintenance = state.get("maintenance", {})
    if not state.get("storageOk") or state.get("savePending") or maintenance.get("localOtaBusy") or not maintenance.get("localOtaReady"):
        raise SystemExit("Board storage or OTA is not ready")
    args.snapshot.parent.mkdir(parents=True, exist_ok=True)
    args.snapshot.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    firmware = args.image.resolve()
    content = firmware.read_bytes()
    if (len(content) < 288 or len(content) > 0x1E0000 or content[0] != 0xE9 or
            int.from_bytes(content[32:36], "little") != 0xABCD5432):
        raise SystemExit("Not a supported application image")
    print("Target:", args.board, ip, "previous version:", maintenance.get("version"), flush=True)
    print("Image bytes:", len(content), "SHA256:", hashlib.sha256(content).hexdigest(), flush=True)
    if not args.upload:
        print("Preflight only; no update was started.")
        return
    credentials_path = ROOT / ".ota-credentials.json"
    if credentials_path.stat().st_mode & 0o077:
        raise SystemExit("Credential file must have mode 0600")
    password = json.loads(credentials_path.read_text())[args.board]
    authenticated_upload(ip, firmware, password)


if __name__ == "__main__":
    main()
