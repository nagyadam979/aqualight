#!/usr/bin/env python3
"""Local OTA security/packaging regressions; no device, Firebase or private production key.

Runs the actual native contract, real P-256 signatures, ESP image verification,
immutable release retries and selector ordering. Does not prove TLS, flash writes,
rollback or responsiveness on hardware; build the firmware and exercise those there.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import release_firmware as release

ROOT = Path(__file__).resolve().parent


def firmware_function(signature):
    text = (ROOT / "src/cloud_update.cpp").read_text()
    start = text.index(signature)
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def fixture_image():
    header = bytearray(24)
    header[0:2] = bytes([0xE9, 1])
    header[12] = 5
    header[23] = 1
    payload = bytearray(256)
    struct.pack_into("<I", payload, 0, 0xABCD5432)
    image = header + struct.pack("<II", 0x3C000020, len(payload)) + payload
    checksum = 0xEF
    for byte in payload:
        checksum ^= byte
    image.extend(bytes((len(image) | 15) - len(image)))
    image.append(checksum)
    image.extend(hashlib.sha256(image).digest())
    return bytes(image)


class OtaTests(unittest.TestCase):
    def test_native_contract_and_image_prefix(self):
        source = r'''
#include "ota_contract.h"
#include <cassert>
#include <array>
int main() {
  using namespace ledota;
  Manifest original{1,"led-control-esp32c3","stable","1.1.0","release-1",
    "/releases/release-1/firmware.bin",std::string(64,'a'),1024};
  assert(validManifest(original));
  auto m=original;
  m.schema=2; assert(!validManifest(m)); m=original;
  m.target="esp32"; assert(!validManifest(m)); m=original;
  m.channel="testing"; assert(!validManifest(m)); m=original;
  for(const auto bad:{"", "../release", "release..1", "release/1", "A", "a%2f", "a?x", "a#x"}) {
    m=original; m.releaseId=bad; assert(!validManifest(m));
  }
  m=original; m.imagePath="https://evil.invalid/firmware.bin"; assert(!validManifest(m));
  m=original; m.imagePath="/releases/other/firmware.bin"; assert(!validManifest(m));
  m=original; m.sha256[0]='A'; assert(!validManifest(m));
  m=original; m.sha256.pop_back(); assert(!validManifest(m));
  m=original; m.version="x\"injected"; assert(!validManifest(m));
  m=original; m.version=std::string(65,'a'); assert(!validManifest(m));
  for(auto size:{0ULL,287ULL,static_cast<unsigned long long>(MAX_IMAGE_SIZE)+1}) {
    m=original; m.size=size; assert(!validManifest(m));
  }
  std::array<uint8_t,288> prefix{};
  prefix[0]=0xe9; prefix[1]=1; prefix[12]=5;
  prefix[32]=0x32; prefix[33]=0x54; prefix[34]=0xcd; prefix[35]=0xab;
  assert(validImagePrefix(prefix.data(),prefix.size()));
  assert(!validImagePrefix(prefix.data(),287));
  assert(!validImagePrefix(nullptr,288));
  for(auto index:{0,1,12,13,32,33,34,35}) {
    auto bad=prefix; bad[index]^=0xff; assert(!validImagePrefix(bad.data(),bad.size()));
  }
}
'''
        with tempfile.TemporaryDirectory(prefix="led-ota-native-") as folder:
            binary = Path(folder) / "contract"
            subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-I", str(ROOT / "src"), "-x", "c++", "-",
                            "-o", str(binary)], input=source, text=True, check=True)
            subprocess.run([str(binary)], check=True)

    def test_board_targets_cannot_cross_over(self):
        source = r'''
#include "ota_contract.h"
#include <cassert>
int main() {
  using namespace ledota;
  Manifest one{1,"led-control-esp32c3-1ch","stable","1.1.0","1ch-1",
    "/releases/1ch-1/firmware.bin",std::string(64,'a'),1024};
  Manifest two{1,"led-control-esp32c3","stable","1.1.0","release-1",
    "/releases/release-1/firmware.bin",std::string(64,'a'),1024};
#if LED_CHANNELS == 1
  assert(validManifest(one) && !validManifest(two));
  assert(std::strstr(CHANNEL_URL, "/channels/stable-1ch.json"));
#else
  assert(validManifest(two) && !validManifest(one));
  assert(std::strstr(CHANNEL_URL, "/channels/stable.json"));
#endif
}
'''
        for define in ("-DLED_CHANNELS=1", "-DLED_CHANNELS=2"):
            with tempfile.TemporaryDirectory(prefix="led-ota-board-") as folder:
                binary = Path(folder) / "board"
                subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror", define,
                                "-I", str(ROOT / "src"), "-x", "c++", "-", "-o", str(binary)],
                               input=source, text=True, check=True)
                subprocess.run([str(binary)], check=True)

    def test_single_channel_release_target_and_channel(self):
        with tempfile.TemporaryDirectory(prefix="led-ota-1ch-") as folder:
            temp = Path(folder)
            key, public = temp / "key.pem", temp / "public.pem"
            key.write_bytes(release.openssl(["genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"]))
            public.write_bytes(release.openssl(["pkey", "-in", str(key), "-pubout"]))
            (temp / "src").mkdir()
            (temp / "src/ota_public_key.h").write_bytes(public.read_bytes())
            firmware = temp / "firmware.bin"
            firmware.write_bytes(fixture_image())
            with patch.object(release, "ROOT", temp):
                with self.assertRaises(ValueError):
                    release.prepare_release(firmware, key, "release-1", "1.1.0", "1ch")
                with self.assertRaises(ValueError):
                    release.prepare_release(firmware, key, "1ch-1.1.0", "1.1.0", "nope")
                image, envelope = release.prepare_release(firmware, key, "1ch-1.1.0", "1.1.0", "1ch")
                self.assertEqual(json.loads(json.loads(envelope)["manifest"])["target"], "led-control-esp32c3-1ch")
                output = (temp / "hosting").resolve()
                release.write_release(output, "1ch-1.1.0", image, envelope, "1ch")
                self.assertEqual((output / "public/channels/stable-1ch.json").read_bytes(), envelope)
                self.assertFalse((output / "public/channels/stable.json").exists())

    def test_actual_poll_health_deadline_and_reboot_gate(self):
        implementation = firmware_function("void poll(bool applicationHealthy, bool canReboot)")
        source = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstring>
constexpr int ESP_OK=0;
constexpr uint32_t MIN_VALIDATION_MS=10000,HEALTH_STABLE_MS=3000,VALIDATION_DEADLINE_MS=60000;
bool pendingValidation=true,rollbackFailed=false,configured=false;
uint32_t startedMs=0,healthySince=1,nowMs=10001;
std::atomic<bool> ready{false},queued{false},running{false},bootSelected{false};
std::atomic<uint32_t> nextCheck{0};
void *workerTask=nullptr;
int accepted=0,rolledBack=0,acceptResult=-1,notified=0;
int rebooted=0;
struct Esp {void restart(){++rebooted;}} ESP;
const char *lastState="";
uint32_t millis(){return nowMs;}
void setState(const char *state,const char * ="",unsigned=0){lastState=state;}
int esp_ota_mark_app_valid_cancel_rollback(){++accepted;return acceptResult;}
int esp_ota_mark_app_invalid_rollback_and_reboot(){++rolledBack;return -1;}
void xTaskNotifyGive(void *){++notified;}
bool requestCheck(){return true;}
'''
        source += implementation + r'''
int main(){
  poll(true,false); assert(accepted==1 && rolledBack==0 && pendingValidation);
  nowMs=60000; poll(true,false);
  assert(accepted==2 && rolledBack==1 && rollbackFailed);
  nowMs=70000; poll(true,false); assert(accepted==2 && rolledBack==1);
  rollbackFailed=false; pendingValidation=true; nowMs=10001; healthySince=1;
  acceptResult=0; poll(true,false); assert(!pendingValidation && !strcmp(lastState,"healthy"));
  pendingValidation=true; healthySince=1; acceptResult=-1; nowMs=10001;
  poll(false,false); assert(healthySince==0 && pendingValidation);
  nowMs=60000; poll(false,false); assert(rolledBack==2);
  pendingValidation=false; ready=true;
  poll(true,false); assert(notified==0 && ready);
  poll(true,true); assert(notified==1 && !ready && !strcmp(lastState,"rebooting"));
  // An HTTP edit after the first ready notification must postpone the actual reboot.
  bootSelected=true;
  poll(true,false); assert(rebooted==0);
  poll(true,true); assert(rebooted==1);
}
'''
        with tempfile.TemporaryDirectory(prefix="led-ota-health-") as folder:
            binary = Path(folder) / "health"
            subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-x", "c++", "-", "-o", str(binary)],
                           input=source, text=True, check=True)
            subprocess.run([str(binary)], check=True)

    def test_local_ota_cannot_erase_the_rollback_slot_during_validation(self):
        source = r'''
#include <cassert>
#include <cstdint>
bool pendingValidation=true;
void *flashMutex=reinterpret_cast<void *>(1);
int takes=0;
constexpr int pdTRUE=1;
int xSemaphoreTake(void *,uint32_t){++takes;return pdTRUE;}
'''
        source += firmware_function("bool tryLock()") + r'''
int main(){
  assert(!tryLock() && takes==0);
  pendingValidation=false; assert(tryLock() && takes==1);
  flashMutex=nullptr; assert(!tryLock() && takes==1);
}
'''
        with tempfile.TemporaryDirectory(prefix="led-ota-lock-") as folder:
            binary = Path(folder) / "lock"
            subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-x", "c++", "-", "-o", str(binary)],
                           input=source, text=True, check=True)
            subprocess.run([str(binary)], check=True)

    def test_image_checksum_hash_chip_and_bounds(self):
        image = fixture_image()
        release.validate_image(image)
        for offset in [0, 1, 12, 32, 64, len(image) - 33, len(image) - 1]:
            damaged = bytearray(image)
            damaged[offset] ^= 0xFF
            with self.assertRaises(ValueError):
                release.validate_image(bytes(damaged))
        for image in [b"", image[:280], image[:-1], image + b"extra", image * 10000]:
            with self.assertRaises(ValueError):
                release.validate_image(image)

    def test_real_signatures_dry_run_and_immutable_release(self):
        with tempfile.TemporaryDirectory(prefix="led-ota-release-") as folder:
            temp = Path(folder)
            key, public = temp / "key.pem", temp / "public.pem"
            key.write_bytes(release.openssl(["genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"]))
            public.write_bytes(release.openssl(["pkey", "-in", str(key), "-pubout"]))
            (temp / "src").mkdir()
            (temp / "src/ota_public_key.h").write_bytes(public.read_bytes())
            firmware = temp / "firmware.bin"
            firmware.write_bytes(fixture_image())
            with patch.object(release, "ROOT", temp):
                # Public-key matching uses this isolated fixture key, never the production private key.
                image, envelope = release.prepare_release(firmware, key, "release-1", "1.1.0")
                image2, envelope2 = release.prepare_release(firmware, key, "release-2", "1.1.0")
                self.assertEqual(envelope, release.prepare_release(firmware, key, "release-1", "1.1.0")[1])
                before = sorted(path.relative_to(temp) for path in temp.rglob("*"))
                self.assertEqual(release.main(["--firmware", str(firmware), "--private-key", str(key),
                                              "--release-id", "release-1", "--version", "1.1.0"]), 0)
                self.assertEqual(before, sorted(path.relative_to(temp) for path in temp.rglob("*")))
                other = temp / "other-key.pem"
                other.write_bytes(release.openssl(["genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"]))
                with self.assertRaises(ValueError):
                    release.prepare_release(firmware, other, "release-1", "1.1.0")
            outer = json.loads(envelope)
            manifest = outer["manifest"].encode()
            signature = temp / "signature.der"
            signature.write_bytes(base64.b64decode(outer["signature"], validate=True))
            verify = ["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(signature)]
            self.assertEqual(subprocess.run(verify, input=manifest, capture_output=True).returncode, 0)
            self.assertNotEqual(subprocess.run(verify, input=manifest.replace(b"stable", b"unsafe"), capture_output=True).returncode, 0)
            signed = json.loads(manifest)
            self.assertEqual(signed["sha256"], hashlib.sha256(image).hexdigest())
            self.assertNotEqual(signed["sha256"], hashlib.sha256(image[:-1] + b"X").hexdigest())
            output = (temp / "hosting").resolve()
            # This output is outside the real source repository and has no cloud operation.
            release.write_release(output, "release-1", image, envelope)
            release.write_release(output, "release-1", image, envelope)
            selector = output / "public/channels/stable.json"
            config = json.loads((output / "firebase.json").read_bytes())
            self.assertEqual(config["hosting"]["site"], "led-control-terrarium1")
            self.assertEqual(selector.read_bytes(), envelope)
            with self.assertRaises(ValueError):
                release.write_release(output, "release-1", image + b"X", envelope)
            self.assertEqual(selector.read_bytes(), envelope)
            calls = []
            write = release.write_atomic
            def recording(path, data, immutable=False):
                calls.append(str(path.relative_to(output)))
                return write(path, data, immutable)
            with patch.object(release, "write_atomic", recording):
                release.write_release(output, "release-2", image2, envelope2)
            self.assertEqual(calls[-1], "public/channels/stable.json")
            self.assertTrue((output / "public/releases/release-1/firmware.bin").exists())
            with self.assertRaises(ValueError):
                release.write_release(ROOT / "not-allowed", "release-1", image, envelope)


if __name__ == "__main__":
    unittest.main()
