#!/usr/bin/env python3
"""Generate a private board configuration header without printing credentials."""
import argparse
import json
import os
from pathlib import Path
import stat
import tempfile


DEFAULT_CONFIG = ".ota-credentials.json"


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate private device configuration field")
        result[name] = value
    return result


def load_config(path, board="1ch"):
    """Read the existing board password without exposing its value in errors."""
    if board != "1ch":
        raise ValueError("Unsupported private device configuration board")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("The private device configuration must be a regular file with mode 0600")
        try:
            config = json.load(stream, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, UnicodeError):
            raise ValueError("Invalid private device configuration JSON") from None
    if not isinstance(config, dict) or board not in config:
        raise ValueError("Missing private device configuration board")
    password = config[board]
    if (type(password) is not str or not 8 <= len(password) <= 63 or
            any(ord(character) < 32 or ord(character) > 126 for character in password)):
        raise ValueError("The device password must contain 8 to 63 printable ASCII bytes")
    return password


def generate(config_path, output_path, board="1ch"):
    config_path, output_path = Path(config_path), Path(output_path)
    if config_path.resolve() == output_path.resolve():
        raise ValueError("The generated header must differ from the private configuration")
    password = load_config(config_path, board)
    quoted = password.replace("\\", "\\\\").replace('"', '\\"').replace("?", "\\?")
    content = '#pragma once\n#define LED_AP_PASSWORD "' + quoted + '"\n'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not output_path.is_symlink():
        if not output_path.is_file():
            raise ValueError("The device header output must be a file")
        output_path.chmod(0o600)
        if output_path.read_text(encoding="utf-8") == content:
            return
    fd, temporary = tempfile.mkstemp(prefix=".device_config-", suffix=".tmp", dir=output_path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def configure_platformio(env):
    """Master and BLE builds must not access the legacy board credential file."""
    if env.get("PIOENV") != "aqualight1ch":
        return
    project_root = Path(env.subst("$PROJECT_DIR"))
    build_root = Path(env.subst("$BUILD_DIR"))
    if not build_root.is_absolute():
        build_root = project_root / build_root
    generated = build_root / "generated"
    generate(project_root / DEFAULT_CONFIG, generated / "device_config.h")
    env.Append(CPPPATH=[str(generated)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output", required=True)
    parser.add_argument("--board", choices=("1ch",), default="1ch")
    args = parser.parse_args(argv)
    try:
        generate(args.config, args.output, args.board)
    except ValueError as error:
        parser.exit(2, str(error) + "\n")
    except OSError:
        parser.exit(2, "The private device configuration or generated header could not be accessed.\n")


if "Import" in globals():
    Import("env")  # type: ignore[name-defined]  # PlatformIO/SCons entry point
    configure_platformio(env)  # type: ignore[name-defined]
elif __name__ == "__main__":
    main()
