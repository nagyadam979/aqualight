#!/usr/bin/env python3
"""Generate a private, paired ESP-NOW build header without logging key material."""
import argparse
import json
import os
from pathlib import Path
import secrets


def mac_bytes(value):
    parts = value.split(":")
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        raise ValueError("A MAC address must contain six hexadecimal bytes")
    result = [int(part, 16) for part in parts]
    if result[0] & 1 or not any(result):
        raise ValueError("A unicast MAC address is required")
    return result


def generate(config_path, output_path):
    config_path, output_path = Path(config_path), Path(output_path)
    if config_path.stat().st_mode & 0o077:
        raise ValueError("The private mesh configuration must have mode 0600")
    config = json.loads(config_path.read_text())
    for name, count in [("masterMac", 6), ("slaveMac", 6), ("pmk", 16), ("lmk", 16)]:
        value = config[name]
        if not isinstance(value, list) or len(value) != count or any(type(n) is not int or n < 0 or n > 255 for n in value):
            raise ValueError("Invalid mesh configuration field: " + name)
    if config["masterMac"] == config["slaveMac"]:
        raise ValueError("The paired board addresses must differ")
    for name in ["masterMac", "slaveMac"]:
        if not any(config[name]) or config[name][0] & 1:
            raise ValueError("A unicast board address is required")
    if not any(config["pmk"]) or not any(config["lmk"]):
        raise ValueError("Mesh keys must not be all zero")
    channel = config["channel"]
    if type(channel) is not int or not 1 <= channel <= 13:
        raise ValueError("Invalid initial radio channel")
    lines = ["#pragma once", "#include <cstdint>", "namespace meshconfig {"]
    for name in ["masterMac", "slaveMac", "pmk", "lmk"]:
        value = config[name]
        lines.append("constexpr uint8_t " + name + "[] = {" + ",".join("0x%02x" % n for n in value) + "};")
    lines += [f"constexpr uint8_t channel = {channel};", "}", ""]
    content = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not output_path.exists() or output_path.read_text() != content:
        output_path.write_text(content)
        output_path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=".ota-credentials-mesh.json")
    parser.add_argument("--output")
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--master-mac")
    parser.add_argument("--slave-mac")
    parser.add_argument("--channel", type=int, default=1)
    args = parser.parse_args()
    if args.init:
        if not args.master_mac or not args.slave_mac or not 1 <= args.channel <= 13:
            parser.error("--init requires two MAC addresses and a channel from 1 to 13")
        config = {"masterMac": mac_bytes(args.master_mac), "slaveMac": mac_bytes(args.slave_mac),
                  "pmk": list(secrets.token_bytes(16)), "lmk": list(secrets.token_bytes(16)), "channel": args.channel}
        if config["masterMac"] == config["slaveMac"]:
            parser.error("The paired board addresses must differ")
        fd = os.open(args.config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(config, stream, indent=2)
            stream.write("\n")
        print("Private paired-board configuration created; keys were not printed.")
    if args.output:
        generate(args.config, args.output)
    elif not args.init:
        parser.error("--output or --init is required")


if "Import" in globals():
    Import("env")  # type: ignore[name-defined]  # PlatformIO/SCons entry point
    project_root = Path(env.subst("$PROJECT_DIR"))  # type: ignore[name-defined]
    generated = Path(env.subst("$BUILD_DIR")) / "generated"  # type: ignore[name-defined]
    generate(project_root / ".ota-credentials-mesh.json", generated / "mesh_config.h")
    env.Append(CPPPATH=[str(generated)])  # type: ignore[name-defined]
elif __name__ == "__main__":
    main()
