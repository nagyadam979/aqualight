#!/usr/bin/env python3
"""Generate the private BLE pairing header without printing its passkey."""
import argparse
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile


DEFAULT_CONFIG = ".ota-credentials-ble.json"


def init_config(path):
    """Create a fresh private configuration; an existing path is never replaced."""
    passkey = secrets.randbelow(900000) + 100000
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump({"passkey": passkey}, stream, indent=2)
        stream.write("\n")


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate private BLE configuration field")
        result[name] = value
    return result


def load_config(path):
    """Return a validated passkey without including its value in an error."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("The private BLE configuration must be a regular file with mode 0600")
        try:
            config = json.load(stream, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise ValueError("Invalid private BLE configuration JSON") from error
    if not isinstance(config, dict) or set(config) != {"passkey"}:
        raise ValueError("Invalid private BLE configuration fields")
    passkey = config["passkey"]
    if type(passkey) is not int or not 100000 <= passkey <= 999999:
        raise ValueError("The BLE passkey must be a six-digit integer")
    return passkey


def generate(config_path, output_path):
    config_path, output_path = Path(config_path), Path(output_path)
    if config_path.resolve() == output_path.resolve():
        raise ValueError("The generated header must differ from the private configuration")
    passkey = load_config(config_path)
    content = ("#pragma once\n#include <cstdint>\nnamespace bleconfig {\n"
               f"constexpr uint32_t passkey = {passkey};\n" + "}\n")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not output_path.is_symlink():
        if not output_path.is_file():
            raise ValueError("The BLE header output must be a file")
        output_path.chmod(0o600)
        if output_path.read_text(encoding="utf-8") == content:
            return
    fd, temporary = tempfile.mkstemp(prefix=".ble_config-", suffix=".tmp", dir=output_path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
        os.replace(temporary, output_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _enabled_define(value):
    if isinstance(value, (tuple, list)) and value:
        name = str(value[0])
        setting = value[1] if len(value) > 1 else 1
    elif isinstance(value, str):
        name, separator, setting = value.partition("=")
        if not separator:
            setting = 1
    else:
        return False
    return name == "LED_BLE_ONLY" and str(setting).strip() not in ("0", "False", "false", "")


def _definitions(values):
    if isinstance(values, dict):
        return list(values.items())
    if isinstance(values, str):
        return [values]
    result = []
    for value in values or []:
        result.extend(value.items() if isinstance(value, dict) else [value])
    return result


def configure_platformio(env):
    """Normal builds must not read, require or create a BLE credential file."""
    enabled = env.get("PIOENV") == "aqualight_ble"
    definitions = _definitions(env.get("CPPDEFINES", []))
    if hasattr(env, "ParseFlags"):
        definitions.extend(_definitions(env.ParseFlags(env.get("BUILD_FLAGS", [])).get("CPPDEFINES", [])))
    enabled = enabled or any(_enabled_define(value) for value in definitions)
    if not enabled:
        return
    project_root = Path(env.subst("$PROJECT_DIR"))
    build_root = Path(env.subst("$BUILD_DIR"))
    if not build_root.is_absolute():
        build_root = project_root / build_root
    generated = build_root / "generated"
    generate(project_root / DEFAULT_CONFIG, generated / "ble_config.h")
    env.Append(CPPPATH=[str(generated)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output")
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args(argv)
    if not args.init and not args.output:
        parser.error("--output or --init is required")
    try:
        if args.init:
            init_config(args.config)
            print("Private BLE configuration created; the passkey was not printed.")
        if args.output:
            generate(args.config, args.output)
    except FileExistsError:
        parser.exit(2, "The private BLE configuration already exists; nothing was overwritten.\n")
    except ValueError as error:
        parser.exit(2, str(error) + "\n")
    except OSError:
        parser.exit(2, "The private BLE configuration or generated header could not be accessed.\n")


if "Import" in globals():
    Import("env")  # type: ignore[name-defined]  # PlatformIO/SCons entry point
    configure_platformio(env)  # type: ignore[name-defined]
elif __name__ == "__main__":
    main()
