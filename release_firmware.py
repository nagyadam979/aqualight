#!/usr/bin/env python3
"""Sign a LED OTA release; dry run by default, never deploys anything.

Use --prepare --output /absolute/path/outside/repository to prepare the dedicated
led-control-terrarium1 Hosting site. Keep the private key in ~/.config/led-control.
Only public firmware, signed manifests and firebase.json enter the output tree.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
TARGET = "led-control-esp32c3"
CHANNEL = "stable"
SITE = "led-control-terrarium1"
MAX_IMAGE_SIZE = 0x1E0000
DEFAULT_KEY = Path.home() / ".config/led-control/ota-signing-key.pem"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def identifier(value, version=False):
    pattern = r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}" if version else r"[a-z0-9][a-z0-9._-]{0,63}"
    return isinstance(value, str) and re.fullmatch(pattern, value) and ".." not in value


def openssl(arguments, data=None):
    try:
        result = subprocess.run(["openssl", *arguments], input=data, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("OpenSSL is unavailable or timed out") from error
    if result.returncode:
        raise ValueError("OpenSSL rejected the key or signature operation (OpenSSL 3.2+ required)")
    return result.stdout


def validate_image(image):
    """Check the C3 application header, segment boundaries, checksum and appended hash."""
    if not 288 <= len(image) <= MAX_IMAGE_SIZE or image[0] != 0xE9 or not 1 <= image[1] <= 16:
        raise ValueError("Firmware is not a supported ESP application image")
    if struct.unpack_from("<H", image, 12)[0] != 5 or struct.unpack_from("<I", image, 32)[0] != 0xABCD5432:
        raise ValueError("Firmware must be an ESP32-C3 application, not a merged flash image")
    offset, checksum = 24, 0xEF
    for _ in range(image[1]):
        if offset + 8 > len(image):
            raise ValueError("Firmware segment header is truncated")
        length = struct.unpack_from("<I", image, offset + 4)[0]
        offset += 8
        if not length or offset + length > len(image):
            raise ValueError("Firmware segment is truncated")
        for byte in image[offset:offset + length]:
            checksum ^= byte
        offset += length
    checksum_offset = offset | 15
    if checksum_offset >= len(image) or image[checksum_offset] != checksum:
        raise ValueError("Firmware image checksum is invalid")
    end = checksum_offset + 1
    if image[23] == 1:
        if len(image) != end + 32 or hashlib.sha256(image[:end]).digest() != image[end:]:
            raise ValueError("Firmware appended SHA-256 is invalid")
    elif image[23] != 0 or len(image) != end:
        raise ValueError("Firmware has an unsupported image trailer")


def signing_public_key(private_key):
    public = openssl(["pkey", "-in", str(private_key), "-pubout"])
    description = openssl(["pkey", "-pubin", "-text", "-noout"], public)
    if b"prime256v1" not in description and b"NIST CURVE: P-256" not in description:
        raise ValueError("The signing key must use P-256")
    header = (ROOT / "src/ota_public_key.h").read_text()
    match = re.search(r"-----BEGIN PUBLIC KEY-----\s+.+?-----END PUBLIC KEY-----", header, re.S)
    if not match or b"".join(public.split()) != "".join(match.group().split()).encode("ascii"):
        raise ValueError("Signing key does not match the firmware's public verification key")
    return public


def prepare_release(firmware, private_key, release_id, version):
    if not identifier(release_id) or not identifier(version, True):
        raise ValueError("Release ID or version is invalid")
    image = Path(firmware).read_bytes()
    validate_image(image)
    signing_public_key(private_key)
    manifest = canonical({
        "schema": 1, "target": TARGET, "channel": CHANNEL,
        "release_id": release_id, "version": version,
        "image_path": f"/releases/{release_id}/firmware.bin",
        "size": len(image), "sha256": hashlib.sha256(image).hexdigest(),
    })
    # Deterministic ECDSA makes an identical immutable release safe to retry.
    signature = openssl(["dgst", "-sha256", "-sign", str(private_key), "-sigopt", "nonce-type:1"], manifest)
    envelope = canonical({"manifest": manifest.decode("utf-8"), "signature": base64.b64encode(signature).decode("ascii")})
    return image, envelope


def hosting_config():
    return {"hosting": {
        "site": SITE, "public": "public", "ignore": ["firebase.json", "**/.*"],
        "headers": [
            {"source": "/channels/**", "headers": [{"key": "Cache-Control", "value": "no-store"}]},
            {"source": "/releases/**", "headers": [{"key": "Cache-Control", "value": "public,max-age=31536000,immutable"}]},
        ],
    }}


def write_atomic(path, data, immutable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".led-release-", delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != data:
                    raise ValueError("An immutable release already exists with different contents")
        else:
            if path.is_symlink():
                raise ValueError("Refusing to replace a symlink")
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_release(output, release_id, image, envelope):
    if not identifier(release_id):
        raise ValueError("Release ID is invalid")
    output = Path(output).expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("Release output must be outside the source repository")
    paths = [output / "firebase.json", output / "public/releases" / release_id / "firmware.bin",
             output / "public/releases" / release_id / "manifest.json", output / "public/channels/stable.json"]
    if any(output not in path.resolve().parents for path in paths):
        raise ValueError("Release output contains a path outside its root")
    # Existing releases are retained. Conflicts never advance the channel selector.
    write_atomic(paths[0], canonical(hosting_config()) + b"\n", immutable=True)
    write_atomic(paths[1], image, immutable=True)
    write_atomic(paths[2], envelope, immutable=True)
    write_atomic(paths[3], envelope)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", required=True, type=Path)
    parser.add_argument("--private-key", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--prepare", action="store_true", help="write local Hosting files; never publishes")
    parser.add_argument("--output", type=Path, help="persistent Hosting output outside the source repository")
    args = parser.parse_args(argv)
    if args.prepare != bool(args.output):
        parser.error("--prepare and --output must be used together; omit both for a dry run")
    try:
        image, envelope = prepare_release(args.firmware, args.private_key, args.release_id, args.version)
        if args.prepare:
            write_release(args.output, args.release_id, image, envelope)
            print(f"Prepared {args.release_id}: {len(image)} bytes, site {SITE}; no cloud operation")
        else:
            print(f"Dry run {args.release_id}: {len(image)} bytes; signed with matching key, no files written")
    except (ValueError, OSError) as error:
        print(f"Release rejected: {error}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
