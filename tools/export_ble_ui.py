#!/usr/bin/env python3
"""Export the existing terrarium UI as an offline-capable BLE-only web app.

This writes static UI assets only. Pairing PINs, device identifiers, hardware
state and credentials are never read by the exporter or cached by the worker.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("generate_web_assets", ROOT / "tools/generate_web_assets.py")
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)

ICON = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect width="192" height="192" rx="38" fill="#0b1116"/><g fill="none" stroke-width="10" stroke-linecap="round"><path d="M48 88a48 48 0 0 1 96 0" stroke="#d898f4"/><path d="M30 120c26-24 44 24 70 0s44 24 70 0M48 151c20-18 38 18 58 0s38 18 58 0" stroke="#81cdec"/></g></svg>'''


def export(output: Path) -> dict:
    source = (ROOT / "src/web_ui.h").read_text(encoding="utf-8")
    page = assets.render_asset(source, "INDEX_HTML", 2, "led-control").decode("utf-8")
    head = '''<link rel="manifest" href="./manifest.webmanifest">
<link rel="icon" href="./icon.svg" type="image/svg+xml">
<meta name="mobile-web-app-capable" content="yes">
<script>window.AQUA_BLE_MODE = true; document.documentElement.dataset.transport = 'ble';</script>
<script src="./ble_transport.js"></script>
<style>html[data-transport="ble"] #networkSettings,html[data-transport="ble"] #firmwareUpdateSection,html[data-transport="ble"] #localAccessSection,html[data-transport="ble"] #meshSection{display:none!important}</style>
'''
    page = page.replace("</head>", head + "</head>", 1)
    worker_registration = '''<script>
if ('serviceWorker' in navigator) window.addEventListener('load', () => {
  navigator.serviceWorker.register('./sw.js').then(async () => {
    await navigator.serviceWorker.ready;
    const status = document.getElementById('bleOfflineReady');
    if (status) status.textContent = 'A felület az első betöltés után internet nélkül is megnyitható. A vezérléshez Bluetooth-kapcsolat szükséges.';
  }).catch(() => {
    const status = document.getElementById('bleOfflineReady');
    if (status) status.textContent = 'Az offline felület még nem készült el. A következő megnyitáshoz internet szükséges.';
  });
});
</script>
'''
    page = page.replace("</body>", worker_registration + "</body>", 1)
    manifest = {
        "name": "AquaLight · terrárium", "short_name": "AquaLight", "lang": "hu",
        "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#0b1116", "theme_color": "#0b1116",
        "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(page, encoding="utf-8")
    (output / "manifest.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "icon.svg").write_text(ICON, encoding="utf-8")
    shutil.copyfile(ROOT / "ble-web/ble_transport.js", output / "ble_transport.js")
    public_files = ["index.html", "ble_transport.js", "manifest.webmanifest", "icon.svg"]
    digest = hashlib.sha256(b"".join((output / name).read_bytes() for name in public_files)).hexdigest()[:16]
    worker = ''''use strict';
const CACHE = 'aqualight-ble-__HASH__';
const ASSETS = ['./', './index.html', './ble_transport.js', './manifest.webmanifest', './icon.svg'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(names => Promise.all(names.filter(name => name.startsWith('aqualight-ble-') && name !== CACHE).map(name => caches.delete(name)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.search) return;
  const allowed = ASSETS.some(path => new URL(path, self.registration.scope).href === url.href);
  if (!allowed) return; // Never cache API data, arbitrary URLs or credentials.
  event.respondWith(caches.open(CACHE).then(async cache => (await cache.match(event.request)) || fetch(event.request)));
});
'''.replace("__HASH__", digest)
    (output / "sw.js").write_text(worker, encoding="utf-8")
    return {"directory": str(output), "cacheVersion": digest,
            "files": {name: (output / name).stat().st_size for name in public_files + ["sw.js"]}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "ble-web/dist")
    args = parser.parse_args()
    print(json.dumps(export(args.output), ensure_ascii=False, indent=2))
