#!/usr/bin/env node
'use strict';
// Exercises the actual browser BLE transport; no radio, device or PIN needed.
const assert = require('assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const {execFileSync} = require('child_process');
const {BleTransport, UUIDS} = require('./ble-web/ble_transport.js');
const flush = () => new Promise(resolve => setImmediate(resolve));

class Clock {
  constructor() { this.time = 0; this.next = 0; this.timers = new Map(); this.delays = []; }
  set(callback, delay) {
    const id = ++this.next; this.delays.push(delay);
    if (delay === 5) queueMicrotask(() => { this.time += 5; callback(); });
    else this.timers.set(id, {callback, at: this.time + delay});
    return id;
  }
  clear(id) { this.timers.delete(id); }
  async advance(ms) {
    this.time += ms;
    for (const [id, timer] of [...this.timers]) if (timer.at <= this.time) { this.timers.delete(id); timer.callback(); }
    await flush();
  }
}

function frame(id, offset, total, bytes) {
  const array = new Uint8Array(10 + bytes.length), view = new DataView(array.buffer);
  view.setUint8(0, 1); view.setUint8(1, 2); view.setUint16(2, id, true);
  view.setUint16(4, offset, true); view.setUint16(6, total, true); view.setUint16(8, bytes.length, true);
  array.set(bytes, 10); return array;
}
function withOffset(bytes) {
  const buffer = new Uint8Array(bytes.length + 6); buffer.set(bytes, 3);
  return new DataView(buffer.buffer, 3, bytes.length);
}

class Characteristic extends EventTarget {
  constructor(owner, kind) { super(); this.owner = owner; this.kind = kind; this.value = null; }
  async startNotifications() { this.owner.order.push('notifications'); return this; }
  async readValue() {
    this.owner.order.push('info');
    if (this.owner.authenticationError) throw new Error('Authentication failed');
    return withOffset(new TextEncoder().encode(JSON.stringify(this.owner.info)));
  }
  async writeValueWithResponse(value) {
    const owner = this.owner;
    assert.equal(this.kind, 'rx');
    assert.equal(++owner.writesInFlight, 1, 'GATT writes cannot overlap');
    await Promise.resolve();
    const bytes = new Uint8Array(value), view = new DataView(bytes.buffer);
    owner.frames.push(new Uint8Array(bytes));
    assert.ok(bytes.length <= (owner.info.maxFrame || 20));
    assert.equal(view.getUint8(0), 1); assert.equal(view.getUint8(1), 1);
    const id = view.getUint16(2, true), offset = view.getUint16(4, true), total = view.getUint16(6, true), count = view.getUint16(8, true);
    assert.equal(bytes.length, count + 10); assert.ok(id > 0);
    if (!offset) { assert.ok(!owner.receiving); owner.receiving = {id, bytes: new Uint8Array(total), received: 0}; }
    assert.equal(owner.receiving.id, id); assert.equal(owner.receiving.received, offset);
    owner.receiving.bytes.set(bytes.subarray(10), offset); owner.receiving.received += count;
    if (owner.receiving.received === total) {
      const request = JSON.parse(new TextDecoder().decode(owner.receiving.bytes));
      owner.requests.push({id, request}); owner.receiving = null;
      if (owner.autoReply) owner.reply(id, owner.replyBody);
    }
    owner.writesInFlight--;
  }
  notify(bytes) { this.value = withOffset(bytes); this.dispatchEvent(new Event('characteristicvaluechanged')); }
}

class Device extends EventTarget {
  constructor(owner) {
    super(); this.owner = owner;
    this.gatt = {
      connected: false,
      connect: async () => {
        owner.connections++; this.gatt.connected = true;
        owner.currentTx = new Characteristic(owner, 'tx'); owner.currentRx = new Characteristic(owner, 'rx');
        const info = new Characteristic(owner, 'info');
        return {getPrimaryService: async uuid => {
          assert.equal(uuid, UUIDS.service);
          return {getCharacteristic: async characteristic => {
            owner.discoveries.push(characteristic);
            return {[UUIDS.tx]: owner.currentTx, [UUIDS.rx]: owner.currentRx, [UUIDS.info]: info}[characteristic];
          }};
        }};
      },
      disconnect: () => { this.gatt.connected = false; this.dispatchEvent(new Event('gattserverdisconnected')); }
    };
  }
}

function fixture(info = {}) {
  const owner = {
    info: {protocol: 1, maxFrame: 180, requestLimit: 2048, responseLimit: 6144, channels: 2, version: '1.4.0-ble', ...info},
    order: [], discoveries: [], choices: [], frames: [], requests: [], receiving: null, writesInFlight: 0, connections: 0,
    autoReply: true, authenticationError: false,
    replyBody: {status: 200, body: {ok: true, label: 'Pára: őűá · terrárium'}}
  };
  owner.device = new Device(owner);
  owner.reply = (id, envelope = owner.replyBody, tx = owner.currentTx) => {
    const bytes = new TextEncoder().encode(JSON.stringify(envelope)), size = (owner.info.maxFrame || 20) - 10;
    for (let offset = 0; offset < bytes.length; offset += size) tx.notify(frame(id, offset, bytes.length, bytes.subarray(offset, offset + size)));
  };
  const clock = new Clock();
  const bluetooth = {requestDevice: async options => { owner.choices.push(options); return owner.device; }};
  const transport = new BleTransport({bluetooth, secureContext: true, now: () => clock.time,
    setTimer: (callback, delay) => clock.set(callback, delay), clearTimer: id => clock.clear(id)});
  return {owner, clock, transport};
}

async function protocolTests() {
  for (const maxFrame of [20, 180, undefined]) {
    const {owner, transport} = fixture({maxFrame});
    await transport.connect();
    assert.deepEqual(owner.choices[0], {filters: [{services: [UUIDS.service]}]});
    assert.deepEqual(owner.order, ['notifications', 'info']);
    assert.equal(transport.info.maxFrame, maxFrame || 20);
    const program = '0,0;60,1;120,5;240,8;360,15;480,30;600,50;720,65;840,70;960,60;1080,45;1200,30;1300,20;1350,10;1400,1;1439,0';
    const reply = await transport.request('/api/program', {warmPoints: program, coolPoints: program});
    assert.equal(reply.label, 'Pára: őűá · terrárium');
    assert.equal(owner.requests[0].request.method, 'POST');
    assert.equal(owner.requests[0].request.path, '/api/program');
    assert.deepEqual(Object.fromEntries(new URLSearchParams(owner.requests[0].request.form)), {warmPoints: program, coolPoints: program});
    assert.ok(owner.frames.length > 1); assert.ok(owner.frames.every(bytes => bytes.length <= (maxFrame || 20)));
    const requests = [transport.request('/api/state'), transport.request('/api/time', {epoch: '1793100000'})];
    await Promise.all(requests);
    assert.deepEqual(owner.requests.map(request => request.id), [1, 2, 3]);
    assert.equal(owner.writesInFlight, 0);
    const count = owner.frames.length;
    await assert.rejects(transport.request('/api/wifi', {password: 'never-sent'}), /nem érhető el/);
    await assert.rejects(transport.request('/api/program', {warmPoints: 'x'.repeat(3000)}), /túl hosszú/);
    assert.equal(owner.frames.length, count);
    transport.disconnect();
  }

  const auth = fixture(); auth.owner.authenticationError = true;
  await assert.rejects(auth.transport.connect(), /párosítás nem sikerült/);
  assert.equal(auth.transport.connected, false); assert.equal(auth.owner.frames.length, 0);
  assert.equal(auth.owner.device.gatt.connected, false);
  for (const info of [{protocol: 2}, {maxFrame: 181}, {requestLimit: 65535}, {responseLimit: 65535}]) {
    const invalid = fixture(info); await assert.rejects(invalid.transport.connect(), /nem támogatott/);
    assert.equal(invalid.transport.connected, false);
  }

  const stale = fixture(); await stale.transport.connect(); stale.owner.autoReply = false;
  const first = stale.transport.request('/api/state'); await flush();
  stale.owner.reply(1); await first;
  let completed = false;
  const second = stale.transport.request('/api/state').then(reply => { completed = true; return reply; }); await flush();
  stale.owner.reply(1, {status: 200, body: {ok: true, stale: true}}); await flush();
  assert.equal(completed, false);
  stale.owner.reply(2, {status: 200, body: {ok: true, fresh: true}});
  assert.equal((await second).fresh, true);
  const oldTx = stale.owner.currentTx;
  stale.transport.disconnect(); await stale.transport.connect();
  assert.equal(stale.owner.connections, 2); assert.equal(stale.owner.discoveries.length, 6);
  const afterReconnect = stale.transport.request('/api/state'); await flush();
  const oldBytes = new TextEncoder().encode(JSON.stringify({status: 200, body: {ok: true, stale: true}}));
  oldTx.value = withOffset(frame(1, 0, oldBytes.length, oldBytes));
  stale.transport.receive({target: oldTx}); // Already queued event from the old characteristic.
  stale.owner.reply(1, {status: 200, body: {ok: true, fresh: true}});
  assert.equal((await afterReconnect).fresh, true);

  const timeout = fixture(); await timeout.transport.connect(); timeout.owner.autoReply = false;
  const mutation = timeout.transport.request('/api/mode', {mode: 'off'});
  const queued = timeout.transport.request('/api/state');
  const settled = Promise.allSettled([mutation, queued]);
  await flush(); assert.equal(timeout.owner.requests.length, 1);
  assert.ok(timeout.clock.delays.includes(20000));
  await timeout.clock.advance(20001);
  assert.ok((await settled).every(result => result.status === 'rejected'));
  assert.equal(timeout.transport.connected, false);
  await timeout.transport.connect();
  assert.equal(timeout.owner.requests.length, 1, 'Reconnect must never replay a mutation');

  const disconnected = fixture(); await disconnected.transport.connect(); disconnected.owner.autoReply = false;
  const inFlight = disconnected.transport.request('/api/manual', {warm: '23', cool: '18'});
  const inQueue = disconnected.transport.request('/api/state');
  const disconnectResults = Promise.allSettled([inFlight, inQueue]);
  await flush(); disconnected.owner.device.gatt.disconnect();
  assert.ok((await disconnectResults).every(result => result.status === 'rejected'));
  assert.equal(disconnected.owner.requests.length, 1);

  const bounded = fixture(); await bounded.transport.connect(); bounded.owner.autoReply = false;
  const accepted = Array.from({length: 4}, () => bounded.transport.request('/api/state'));
  const boundedSettled = Promise.allSettled(accepted);
  await assert.rejects(bounded.transport.request('/api/state'), /előző műveleteket/);
  bounded.transport.disconnect(); assert.equal((await boundedSettled).length, 4);

  for (const malformed of ['offset', 'length', 'limit']) {
    const invalid = fixture(); await invalid.transport.connect(); invalid.owner.autoReply = false;
    const request = invalid.transport.request('/api/state'); const rejection = assert.rejects(request, /válasz/); await flush();
    const bytes = frame(1, malformed === 'offset' ? 1 : 0, malformed === 'limit' ? 6145 : 3, new Uint8Array([1, 2, 3]));
    if (malformed === 'length') new DataView(bytes.buffer).setUint16(8, 4, true);
    invalid.owner.currentTx.notify(bytes); await rejection; assert.equal(invalid.transport.connected, false);
  }

  const failure = fixture(); await failure.transport.connect();
  failure.owner.replyBody = {status: 409, body: {ok: false, error: 'A kézi mód nincs bekapcsolva.'}};
  await assert.rejects(failure.transport.request('/api/manual', {warm: '2', cool: '3'}), /kézi mód/);
  assert.equal(failure.transport.connected, true);

  const exhausted = fixture(); await exhausted.transport.connect(); exhausted.transport.nextId = 65535;
  await exhausted.transport.request('/api/state');
  await assert.rejects(exhausted.transport.request('/api/state'), /azonosítók elfogytak/);
  assert.equal(exhausted.owner.requests.length, 1);
  console.log('PASS BLE transport: authenticated INFO, 20/180B fragmentation, UTF-8, serialized GATT writes/IDs, reconnect, stale frames, limits, timeout/disconnect without mutation replay');
}

async function exportTests() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'aqualight-ble-web-'));
  try {
    execFileSync('python3', ['tools/export_ble_ui.py', '--output', directory], {cwd: __dirname});
    const page = fs.readFileSync(path.join(directory, 'index.html'), 'utf8');
    assert.match(page, /window\.AQUA_BLE_MODE = true/);
    assert.match(page, /id="graph"/); assert.match(page, /id="saveProgram"/); assert.match(page, /id="bleConnect"/);
    assert.match(page, /name="viewport"/); assert.match(page, /manifest\.webmanifest/);
    assert.doesNotMatch(page, /(?:passkey|pin)\s*[:=]\s*['"]?\d{6}/i);
    const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'manifest.webmanifest'), 'utf8'));
    assert.equal(manifest.start_url, './'); assert.equal(manifest.display, 'standalone');
    const worker = fs.readFileSync(path.join(directory, 'sw.js'), 'utf8'), handlers = {}, added = [], matched = [];
    const self = {location: {origin: 'https://aqualight.test'}, registration: {scope: 'https://aqualight.test/pilot/'},
      addEventListener: (name, callback) => handlers[name] = callback, skipWaiting: async () => {}, clients: {claim: async () => {}}};
    const cache = {addAll: async paths => added.push(...paths), match: async request => { matched.push(request.url); return {cached: true}; }};
    vm.runInNewContext(worker, {self, URL, caches: {open: async () => cache, keys: async () => [], delete: async () => {}}, fetch: async () => ({network: true})});
    let pending;
    handlers.install({waitUntil: promise => pending = promise}); await pending;
    assert.ok(added.includes('./index.html') && added.includes('./ble_transport.js'));
    assert.ok(added.every(asset => !asset.includes('/api/') && !asset.includes('state')));
    for (const [url, method, allowed] of [
      ['https://aqualight.test/pilot/index.html', 'GET', true],
      ['https://aqualight.test/api/state', 'GET', false],
      ['https://aqualight.test/pilot/index.html?pin=123456', 'GET', false],
      ['https://other.test/pilot/index.html', 'GET', false],
      ['https://aqualight.test/pilot/index.html', 'POST', false]
    ]) {
      let intercepted = false;
      handlers.fetch({request: {url, method}, respondWith: promise => { intercepted = true; pending = promise; }});
      assert.equal(intercepted, allowed); if (intercepted) await pending;
    }
    assert.deepEqual(matched, ['https://aqualight.test/pilot/index.html']);
    console.log('PASS BLE static export: existing Hungarian editor, PWA assets, offline UI allowlist; no hardware-state/PIN cache');
  } finally { fs.rmSync(directory, {recursive: true, force: true}); }
}

(async () => { await protocolTests(); await exportTests(); })().catch(error => { console.error(error); process.exitCode = 1; });
