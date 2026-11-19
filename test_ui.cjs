#!/usr/bin/env node
'use strict';
// Runs the actual embedded JavaScript with a small DOM/fetch model, without npm.
// Does not prove browser hit-testing, CSS layout, Wi-Fi, PWM or flash behavior.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');
const header = fs.readFileSync(path.join(__dirname, 'src/web_ui.h'), 'utf8');
const script = header.match(/<script>([\s\S]*?)<\/script>/)[1];
// Exercise the actual hub script for both hosting boards, including IP/AP access.
const hub = header.slice(header.indexOf('const char HUB_HTML'));
const hubScript = hub.match(/<script>([\s\S]*?)<\/script>/)[1];
assert.equal((hub.match(/<iframe\b/g) || []).length, 2);
assert.match(hub, /role="tablist"/);
for (const suffix of ['Two', 'One']) {
  assert.match(hub, new RegExp('id="tab' + suffix + '"[^>]*role="tab"[^>]*aria-controls="panel' + suffix + '"'));
  assert.match(hub, new RegExp('id="panel' + suffix + '"[^>]*role="tabpanel"[^>]*aria-labelledby="tab' + suffix + '"'));
}
for (const hostChannels of ['1', '2']) {
  let focused = null;
  const nodes = new Map();
  for (const id of ['frameTwo', 'frameOne', 'directTwo', 'directOne', 'tabTwo', 'tabOne', 'panelTwo', 'panelOne']) {
    nodes.set(id, {attrs: {}, loads: 0, listeners: {},
      setAttribute(name, value) { this.attrs[name] = value; if (name === 'src') this.loads++; },
      getAttribute(name) { return this.attrs[name]; },
      addEventListener(name, callback) { this.listeners[name] = callback; },
      focus() { focused = id; }
    });
  }
  const sandbox = vm.createContext({document: {
    documentElement: {dataset: {channels: hostChannels}}, getElementById: id => nodes.get(id)
  }, fetch() { throw new Error('Switching tabs must not send controller API requests'); }});
  vm.runInContext(hubScript, sandbox);
  const localSuffix = hostChannels === '1' ? 'One' : 'Two', remoteSuffix = hostChannels === '1' ? 'Two' : 'One';
  const local = nodes.get('frame' + localSuffix), remote = nodes.get('frame' + remoteSuffix);
  function selected(suffix) {
    for (const current of ['Two', 'One']) {
      assert.equal(nodes.get('tab' + current).attrs['aria-selected'], String(current === suffix));
      assert.equal(nodes.get('tab' + current).tabIndex, current === suffix ? 0 : -1);
      assert.equal(nodes.get('panel' + current).hidden, current !== suffix);
    }
  }
  assert.equal(local.attrs.src, '/control'); // Relative even when hub opened by IP or AP.
  assert.equal(remote.loads, 0); selected(localSuffix);
  nodes.get('tab' + remoteSuffix).listeners.click(); selected(remoteSuffix);
  assert.equal(remote.attrs.src, hostChannels === '1' ? '/control?target=peer&channels=2' : '/control?target=peer&channels=1');
  assert.equal(nodes.get('directTwo').href, hostChannels === '1' ? remote.attrs.src : local.attrs.src);
  assert.equal(nodes.get('directOne').href, hostChannels === '1' ? local.attrs.src : remote.attrs.src);
  local.unsavedDraft = 'retained';
  // Even if the peer never loads, the local tab remains usable without a reload.
  nodes.get('tab' + localSuffix).listeners.click(); selected(localSuffix);
  nodes.get('tab' + remoteSuffix).listeners.click(); selected(remoteSuffix);
  assert.equal(local.unsavedDraft, 'retained'); assert.equal(local.loads, 1); assert.equal(remote.loads, 1);
  for (const [from, key, to] of [['Two','ArrowRight','One'], ['One','ArrowRight','Two'], ['Two','ArrowLeft','One'], ['One','ArrowLeft','Two'], ['One','Home','Two'], ['Two','End','One']]) {
    let prevented = false;
    nodes.get('tab' + from).listeners.keydown({key, preventDefault() { prevented = true; }});
    assert.equal(prevented, true); assert.equal(focused, 'tab' + to); selected(to);
  }
  nodes.get('tabOne').listeners.keydown({key: 'Tab', preventDefault() { throw new Error('Leave native Tab behavior intact'); }});
  assert.equal(local.loads, 1); assert.equal(remote.loads, 1);
}
const mainSource = fs.readFileSync(path.join(__dirname, 'src/main.cpp'), 'utf8');
assert.match(mainSource, /server\.on\("\/", HTTP_GET,[\s\S]*?HUB_HTML/);
assert.match(mainSource, /server\.on\("\/control", HTTP_GET,[\s\S]*?INDEX_HTML/);
assert.match(mainSource, /WiFi\.setHostname\(LED_HOSTNAME\)/);
assert.match(mainSource, /MDNS\.begin\(LED_HOSTNAME\)/);
assert.match(header, /#ifndef LED_HOSTNAME\s+#define LED_HOSTNAME "led-control"/);
assert.match(fs.readFileSync(path.join(__dirname, 'platformio.ini'), 'utf8'), /-DLED_HOSTNAME=\\"led-control-1ch\\"/);
assert.match(fs.readFileSync(path.join(__dirname, 'src/maintenance.cpp'), 'utf8'), /ArduinoOTA\.setHostname\(WiFi\.getHostname\(\)\)/);
console.log('PASS hub: local initial tab; both hosting variants; same-origin peer target; ARIA and keyboard tabs; offline peer isolation; drafts survive without reload or API writes; root/control routes and hostname integration');

const elements = new Map(), all = [], calls = [], timers = new Map();
let activeElement = null, nextTimer = 1, heldPath = null, release = null, holdNextGet = false, releaseGet = null, updateRequestError = '';

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.attrs = {}; this.listeners = {};
    this.dataset = {}; this.style = {setProperty() {}};
    this.textContent = ''; this.value = '0'; this.disabled = false;
    this.clientHeight = 300; this.className = ''; this.captured = new Set();
    this.handles = new Map();
    this.classList = {add() {}, remove() {}, toggle: (name, value) => { this.attrs['class-' + name] = value; }};
    all.push(this);
  }
  setAttribute(key, value) { this.attrs[key] = value; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  getBoundingClientRect() { return {left: 10, top: 20, width: 320, height: 300}; }
  focus() { activeElement = this; }
  scrollIntoView() {}
  closest() { return this.dataset.pointId ? this : null; }
  contains(element) { return element === this || [...this.handles.values()].includes(element); }
  querySelector(selector) {
    const match = selector.match(/data-point-id="(\d+)"/);
    return match ? this.handles.get(match[1]) || null : null;
  }
  setPointerCapture(id) { this.captured.add(id); }
  hasPointerCapture(id) { return this.captured.has(id); }
  releasePointerCapture(id) { this.captured.delete(id); }
  set innerHTML(value) {
    this._html = value;
    if (this.id !== 'graph') return;
    if (this.contains(activeElement)) activeElement = null;
    this.handles.clear();
    for (const match of value.matchAll(/class="point-handle" data-point-id="(\d+)"/g)) {
      const handle = new Element('g'); handle.dataset.pointId = match[1];
      this.handles.set(match[1], handle);
    }
  }
  get innerHTML() { return this._html; }
}
for (const match of header.matchAll(/id="([^"]+)"/g)) {
  const element = new Element(); element.id = match[1]; elements.set(match[1], element);
}
const modeButtons = [...header.matchAll(/<button\b([^>]*)>/g)].flatMap(match => {
  const attributes = match[1], classes = attributes.match(/class="([^"]*)"/);
  if (!classes || !classes[1].split(/\s+/).includes('modebtn')) return [];
  const id = attributes.match(/id="([^"]*)"/), mode = attributes.match(/data-mode="([^"]*)"/);
  const element = id ? elements.get(id[1]) : new Element('button');
  element.dataset.mode = mode[1]; return [element];
});
const curveButtons = ['warm', 'cool'].map(curve => {
  const element = new Element('button'); element.dataset.curve = curve; return element;
});
const stepButtons = ['warm', 'cool'].flatMap(channel => [-1, 1].map(step => {
  const element = new Element('button'); element.dataset = {channel, step: String(step)}; return element;
}));
const state = {
  mode: 'auto', warm: 50, cool: 40, manualWarm: 30, manualCool: 20,
  pwmOk: true, sensorOk: true, temperature: 25.6, humidity: 34.3, sensorAgeSec: 2,
  clockValid: true, epoch: 1793100000, localTime: '2026-10-27 13:00:00', minuteOfDay: 780, timeSource: 'ntp',
  schedule: {
    warm: [{minute: 480, level: 0}, {minute: 540, level: 50}, {minute: 1020, level: 50}, {minute: 1080, level: 0}],
    cool: [{minute: 450, level: 0}, {minute: 570, level: 40}, {minute: 1110, level: 0}]
  },
  network: {connected: true, ssid: '<img src=x onerror=alert(1)>', ip: '192.168.1.2', apActive: true, apSsid: 'AquaLight-test', apIp: '192.168.4.1'},
  storageOk: true, savePending: false
};
const singleChannel = process.env.UI_CHANNELS === '1';
const initTexts = {};
const document = {
  documentElement: {dataset: {channels: singleChannel ? '1' : '2'}},
  querySelector: selector => initTexts[selector] || (initTexts[selector] = new Element()),
  get activeElement() { return activeElement; },
  getElementById: id => elements.get(id) || all.find(element => element.id === id),
  createElement: tag => new Element(tag), hidden: false, addEventListener() {},
  querySelectorAll: selector => selector === '.modebtn' ? modeButtons : selector === '.curvebtn' ? curveButtons :
    selector === '.stepbtn' ? stepButtons : selector === '.stepbtn,.slider' ? [...stepButtons, elements.get('warmSlider'), elements.get('coolSlider')] : []
};
const context = vm.createContext({
  console, document, window: {addEventListener() {}}, ResizeObserver: class {observe() {}},
  AbortController, TextEncoder, URLSearchParams, Intl, Date, Number, Math, Set, Promise,
  setTimeout: (callback, ms) => { const id = nextTimer++; timers.set(id, {callback, ms}); return id; },
  clearTimeout: id => timers.delete(id),
  fetch: async (route, options = {}) => {
    calls.push({route, options});
    if (options.method === 'POST') {
      assert.equal(options.headers['X-Aqua-Request'], '1');
      const data = Object.fromEntries(new URLSearchParams(options.body));
      if (route === '/api/update/check') {
        assert.deepEqual(Object.keys(data), []);
        if (updateRequestError) return {ok: false, status: 503, json: async () => ({ok: false, error: updateRequestError})};
      }
      if (route === '/api/manual') {
        if (state.mode !== 'manual') return {ok: false, status: 409, json: async () => ({ok: false, error: 'Előbb kapcsold be a kézi módot.'})};
        state.manualWarm = state.warm = Number(data.warm); state.manualCool = state.cool = Number(data.cool);
      }
      if (route === '/api/mode') {
        state.mode = data.mode;
        if (data.mode === 'off') state.warm = state.cool = 0;
        else if (data.mode === 'manual') { state.warm = state.manualWarm; state.cool = state.manualCool; }
        else { state.warm = 50; state.cool = 40; }
      }
      if (route === '/api/program') {
        assert.deepEqual(Object.keys(data).sort(), ['coolPoints', 'warmPoints']);
        for (const channel of ['warm', 'cool']) state.schedule[channel] = data[channel + 'Points'].split(';').map(point => {
          const [minute, level] = point.split(',').map(Number); return {minute, level};
        });
      }
      if (route === heldPath) await new Promise(resolve => { release = resolve; });
      return {ok: true, json: async () => ({ok: true})};
    }
    const snapshot = JSON.parse(JSON.stringify(state));
    if (holdNextGet) { holdNextGet = false; await new Promise(resolve => { releaseGet = resolve; }); }
    return {ok: true, json: async () => snapshot};
  }
});
const run = code => vm.runInContext(code, context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const snapshot = curve => run('JSON.stringify(draft.' + curve + ')');
function eventAt(minute, level, target = elements.get('graph'), extra = {}) {
  const g = run('geometry'), bounds = elements.get('graph').getBoundingClientRect();
  return {clientX: bounds.left + (g.left + minute / 1440 * g.plotWidth) * bounds.width / g.width,
    clientY: bounds.top + (g.top + (100 - level) / 100 * g.plotHeight) * bounds.height / g.height,
    target, pointerId: 1, pointerType: 'mouse', button: 0, preventDefault() {}, ...extra};
}
function call(name, argument) { context.testEvent = argument; return run(name + '(testEvent)'); }

async function touchAndUndoTest() {
  const original = run('JSON.stringify(draft)'), originalWarm = snapshot('warm');
  const id = run('draft.warm[0].id'), graph = elements.get('graph');
  const pointHandle = () => graph.handles.get(id);
  let prevented = false;
  const touch = {pointerType: 'touch', preventDefault() { prevented = true; }};
  assert.match(header, /touch-action:pan-y pinch-zoom/);
  assert.match(header, /\.chart\.moving,\.chart\.moving svg\{touch-action:none/);
  assert.equal(run('graphMoveEnabled'), false);
  call('graphPointerDown', eventAt(480, 0, pointHandle(), touch));
  call('graphPointerMove', eventAt(600, 70, undefined, touch));
  call('graphPointerCancel', eventAt(600, 70, undefined, touch));
  assert.equal(snapshot('warm'), originalWarm); // A vertical scroll starting on a handle cannot edit.
  assert.equal(prevented, false); assert.equal(graph.hasPointerCapture(1), false);
  assert.equal(run('undoHistory.length'), 0);
  assert.equal(elements.get('undoProgram').disabled, true);
  elements.get('graphMove').listeners.click();
  assert.equal(run('graphMoveEnabled'), true);
  assert.equal(elements.get('graphMove').attrs['aria-pressed'], 'true');
  call('graphPointerDown', eventAt(480, 0, pointHandle(), touch));
  call('graphPointerMove', eventAt(600, 70, undefined, touch));
  call('graphPointerMove', eventAt(630, 80, undefined, touch));
  const midDrag = run('JSON.stringify(draft)'), callsBeforeBlockedSave = calls.length;
  assert.equal(elements.get('saveProgram').disabled, true);
  assert.equal(elements.get('preset').disabled, true);
  assert.equal(elements.get('addPoint').disabled, true);
  assert.equal(run("insertPoint('warm',720,10)"), false);
  assert.equal(run('removePoint(selected.warm)'), false);
  elements.get('preset').listeners.click();
  run("activateCurve('cool')");
  call('graphKeyDown', eventAt(630, 80, pointHandle(), {key: 'ArrowUp'}));
  await run('saveProgram()');
  assert.equal(run('JSON.stringify(draft)'), midDrag); // Second-finger actions cannot corrupt a drag transaction.
  assert.equal(run('activeCurve'), 'warm');
  assert.equal(calls.length, callsBeforeBlockedSave);
  assert.equal(run('undoHistory.length'), 1); // One complete gesture is one undo.
  assert.equal(elements.get('undoProgram').disabled, true); // No undo while a pointer owns the edit.
  call('graphPointerUp', eventAt(630, 80, undefined, touch));
  assert.notEqual(snapshot('warm'), originalWarm);
  const callsBeforeUndo = calls.length;
  elements.get('undoProgram').listeners.click();
  assert.equal(calls.length, callsBeforeUndo);
  assert.equal(snapshot('warm'), originalWarm);
  assert.equal(run('dirty'), true); // Undo remains an explicit unsaved preview.
  const revision = run('draftRevision');
  call('graphPointerDown', eventAt(480, 0, pointHandle(), touch));
  call('graphPointerMove', eventAt(600, 70, undefined, touch));
  graph.listeners.lostpointercapture(eventAt(600, 70, undefined, touch));
  assert.equal(snapshot('warm'), originalWarm); // Cancelled gestures roll back instead of committing.
  assert.equal(run('undoHistory.length'), 0);
  assert.ok(run('draftRevision') > revision);
  assert.equal(run('drag'), null);
  elements.get('graphMove').listeners.click();
  assert.equal(elements.get('graphMove').attrs['aria-pressed'], 'false');

  run("insertPoint('warm',600,30)");
  const withInserted = snapshot('warm');
  run('removePoint(selected.warm)');
  assert.equal(snapshot('warm'), originalWarm);
  run('undoProgram()'); assert.equal(snapshot('warm'), withInserted);
  run('undoProgram()'); assert.equal(snapshot('warm'), originalWarm);

  const countBeforeTaps = run('draft.warm.length');
  run('suppressDoubleUntil=0');
  for (let tap = 0; tap < 2; tap++) {
    call('graphPointerDown', eventAt(660, 35, undefined, touch));
    call('graphPointerUp', eventAt(660, 35, undefined, touch));
    graph.listeners.lostpointercapture(eventAt(660, 35, undefined, touch));
  }
  assert.equal(run('draft.warm.length'), countBeforeTaps + 1); // Normal implicit capture release preserves double-tap detection.
  run('undoProgram()'); assert.equal(snapshot('warm'), originalWarm);
  call('graphKeyDown', eventAt(480, 0, pointHandle(), {key: 'ArrowUp'}));
  assert.equal(run('draft.warm[0].level'), '1');
  run('undoProgram()'); assert.equal(snapshot('warm'), originalWarm);

  const input = elements.get('points').children[0].children[1].children[0];
  input.listeners.focus(); input.value = '2'; input.listeners.input();
  input.value = '25'; input.listeners.input();
  assert.equal(run('undoHistory.length'), 1); // Typing a number is one edit, not one per digit.
  input.listeners.focus(); input.value = '26'; input.listeners.input();
  assert.equal(run('undoHistory.length'), 2); // Returning to a field starts a new edit.
  run('undoProgram()'); assert.equal(run('draft.warm[0].level'), '25');
  run('undoProgram()'); assert.equal(snapshot('warm'), originalWarm);
  elements.get('preset').listeners.click();
  run('undoProgram()'); assert.equal(run('JSON.stringify(draft)'), original);
  if (!singleChannel) {
    run("activateCurve('cool');insertPoint('cool',660,25)");
    run("activateCurve('warm');undoProgram()");
    assert.equal(run('activeCurve'), 'cool');
    assert.equal(run('JSON.stringify(draft)'), original);
    run("activateCurve('warm')");
  }
  // A pending save must never overwrite an undo made while its HTTP response is in flight.
  run("insertPoint('warm',600,30)");
  heldPath = '/api/program';
  const pendingSave = run('saveProgram()'); await flush();
  run('undoProgram()'); release(); await pendingSave; heldPath = null;
  assert.equal(snapshot('warm'), originalWarm); assert.equal(run('dirty'), true);
  // Restore the fixture without writing another program, leaving other tests independent.
  state.schedule.warm = [{minute:480,level:0},{minute:540,level:50},{minute:1020,level:50},{minute:1080,level:0}];
  state.schedule.cool = [{minute:450,level:0},{minute:570,level:40},{minute:1110,level:0}];
  run('dirty=false;savedSignature=""'); await run('refresh()');
  calls.length = 0;
  console.log('PASS editor: scroll-safe touch lock; explicit movement mode; cancelled drag rollback; multitouch guards; grouped drag/input undo; insert/delete/preset/channel undo; undo during save preserves draft');
}

async function diagnosticsTest() {
  const draftBeforeDiagnostics = run('JSON.stringify(draft)');
  // Diagnostics arrive in the existing state response: no new polling or writes.
  assert.equal(elements.get('diagnosticData').hidden, true);
  assert.match(elements.get('diagnosticNotice').textContent, /újabb diagnosztikai firmware/);
  state.diagnostics = {
    resetReason: 'power_on', freeHeap: 100000, minFreeHeap: 90000, largestFreeBlock: 65536,
    loop: {lastMs: 1, maxMs: 900, slowCount: 2}, http: {lastMs: 0, maxMs: 780, slowCount: 1},
    control: {ticks: 320, lastAgeMs: 6, maxGapMs: 21},
    sensor: {ready: false, lastStatus: 2, lastStatusText: 'ack_error', initAttempts: 5, initFailures: 2,
      readAttempts: 8, readFailures: 3, consecutiveFailures: 2, lastReadMs: 51, maxReadMs: 100,
      lastInitMs: 150, maxInitMs: 210, lastErrorStatus: 2, lastErrorText: 'ack_error',
      lastErrorPhase: 'read', lastErrorAgeSec: 6, sdaHigh: false, sclHigh: true,
      firstErrorStatus: 4, firstErrorText: 'crc_error', firstErrorPhase: 'read', firstErrorAgeSec: 40,
      firstErrorSdaHigh: true, firstErrorSclHigh: true, lastErrorSdaHigh: false, lastErrorSclHigh: true,
      lastInitProbeCode: 2}
  };
  const writesBeforeDiagnostics = calls.filter(call => call.options.method === 'POST').length;
  await run('refresh()');
  assert.equal(elements.get('diagnosticData').hidden, false);
  assert.match(elements.get('diagnosticSensor').textContent, /Inicializálásra vár.*ACK.*kód: 2/);
  assert.match(elements.get('diagnosticLastError').textContent, /ACK.*mérés.*6 mp ezelőtt/);
  assert.match(elements.get('diagnosticFirstError').textContent, /CRC.*kód: 4.*mérés.*40 mp ezelőtt/);
  assert.match(elements.get('diagnosticFirstError').textContent, /hiba után SDA: magas \/ SCL: magas/);
  assert.match(elements.get('diagnosticLastError').textContent, /hiba után SDA: alacsony \/ SCL: magas/);
  assert.match(elements.get('diagnosticReads').textContent, /8 kísérlet.*3 hiba.*2 egymást követő/);
  assert.match(elements.get('diagnosticInits').textContent, /5 kísérlet.*2 hiba/);
  assert.match(elements.get('diagnosticBus').textContent, /SDA: alacsony.*SCL: magas/);
  assert.match(elements.get('diagnosticProbe').textContent, /NACK.*nyers kód: 2/);
  assert.match(elements.get('diagnosticReadTiming').textContent, /51 ms.*100 ms/);
  assert.match(elements.get('diagnosticInitTiming').textContent, /150 ms.*210 ms/);
  assert.match(elements.get('diagnosticControl').textContent, /320 frissítés.*6 ms ezelőtt.*21 ms/);
  assert.match(elements.get('diagnosticLoop').textContent, /900 ms.*500 ms felett: 2/);
  assert.match(elements.get('diagnosticHttp').textContent, /780 ms.*500 ms felett: 1/);
  assert.match(elements.get('diagnosticHeap').textContent, /100000 B.*90000 B.*65536 B/);
  assert.equal(elements.get('diagnosticReset').textContent, 'power_on');
  state.diagnostics.control.ticks = 324; await run('refresh()');
  assert.match(elements.get('diagnosticControl').textContent, /324 frissítés/);
  delete state.diagnostics.control; await run('refresh()');
  assert.equal(elements.get('diagnosticControl').textContent, 'Állapot nem közölt');
  state.diagnostics.sensor.lastStatus = 253; state.diagnostics.sensor.lastErrorStatus = 253;
  state.diagnostics.sensor.lastErrorPhase = 'init'; state.diagnostics.sensor.lastInitProbeCode = 0;
  await run('refresh()');
  assert.match(elements.get('diagnosticSensor').textContent, /Inicializálási hiba.*kód: 253/);
  assert.match(elements.get('diagnosticLastError').textContent, /Inicializálási hiba.*inicializálás/);
  assert.match(elements.get('diagnosticProbe').textContent, /Címválasz \(ACK\).*nyers kód: 0/);
  state.diagnostics.sensor.lastStatus = 254; state.diagnostics.sensor.lastInitProbeCode = 5;
  await run('refresh()');
  assert.match(elements.get('diagnosticSensor').textContent, /Érvénytelen mérési érték.*kód: 254/);
  assert.match(elements.get('diagnosticProbe').textContent, /Időtúllépés.*nyers kód: 5/);
  state.diagnostics.sensor.lastInitProbeCode = null; await run('refresh()');
  assert.equal(elements.get('diagnosticProbe').textContent, 'Még nem történt próba');
  delete state.diagnostics.sensor.lastInitProbeCode; await run('refresh()');
  assert.equal(elements.get('diagnosticProbe').textContent, 'Állapot nem közölt');
  state.diagnostics.sensor.lastStatus = 4; state.diagnostics.sensor.lastErrorStatus = 4;
  state.diagnostics.sensor.lastErrorPhase = 'init'; await run('refresh()');
  assert.match(elements.get('diagnosticSensor').textContent, /CRC.*kód: 4/);
  assert.match(elements.get('diagnosticLastError').textContent, /CRC.*inicializálás/);
  // A recovered sensor must not erase the firmware's retained failure history.
  state.diagnostics.sensor.ready = true; state.diagnostics.sensor.lastStatus = 0;
  state.diagnostics.sensor.consecutiveFailures = 0; await run('refresh()');
  assert.match(elements.get('diagnosticSensor').textContent, /Inicializálva.*Rendben.*kód: 0/);
  assert.match(elements.get('diagnosticLastError').textContent, /CRC/);
  assert.match(elements.get('diagnosticFirstError').textContent, /CRC.*mérés.*40 mp ezelőtt/);
  state.diagnostics.sensor.lastErrorStatus = null; await run('refresh()');
  assert.equal(elements.get('diagnosticLastError').textContent, 'Még nem történt hiba');
  state.diagnostics.sensor.firstErrorStatus = null; await run('refresh()');
  assert.equal(elements.get('diagnosticFirstError').textContent, 'Még nem történt hiba');
  delete state.diagnostics.sensor.firstErrorStatus; await run('refresh()');
  assert.equal(elements.get('diagnosticFirstError').textContent, 'Állapot nem közölt');
  // Unknown future codes and error text stay plain text, never HTML.
  state.diagnostics.sensor.lastStatus = 250;
  state.diagnostics.sensor.lastStatusText = '<img src=x onerror=alert(1)>';
  state.diagnostics.resetReason = '<b>unknown</b>'; await run('refresh()');
  assert.match(elements.get('diagnosticSensor').textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(elements.get('diagnosticSensor').innerHTML, undefined);
  assert.equal(elements.get('diagnosticReset').textContent, '<b>unknown</b>');
  assert.equal(elements.get('diagnosticReset').innerHTML, undefined);
  state.diagnostics = {}; await run('refresh()'); // Partial responses degrade without throwing.
  assert.match(elements.get('diagnosticBus').textContent, /SDA: —.*SCL: —/);
  delete state.diagnostics; await run('refresh()');
  assert.equal(elements.get('diagnosticData').hidden, true);
  assert.match(elements.get('diagnosticNotice').textContent, /újabb diagnosztikai firmware/);
  assert.equal(calls.filter(call => call.options.method === 'POST').length, writesBeforeDiagnostics);
  assert.equal(run('JSON.stringify(draft)'), draftBeforeDiagnostics);
  console.log('PASS diagnostics: optional/partial data; ACK/CRC and retained error history; counters, timing, bus lines, memory and reset; safe text; no control writes or draft changes');
}

async function singleChannelTest() {
  run(script); await flush(); await flush();
  assert.equal(run('single'), true);
  await touchAndUndoTest();
  assert.equal(elements.get('points').children.length, 4);
  // Only one curve is drawn and the cool outputs/curve are never exposed.
  assert.equal((elements.get('graph').innerHTML.match(/fill="url\(#/g) || []).length, 1);
  assert.equal(elements.get('selectedChannel').textContent, 'Világítás · 1 csatorna');
  // Saving sends the single curve for both fields so the unchanged API contract holds.
  run("insertPoint('warm', 600, 20)"); await flush();
  await run('saveProgram()'); await flush();
  const program = calls.filter(c => c.route === '/api/program').pop();
  const data = Object.fromEntries(new URLSearchParams(program.options.body));
  assert.equal(data.warmPoints, data.coolPoints);
  assert.ok(data.warmPoints.includes('600,20'));
  // Manual brightness: the hidden second channel is always 0.
  state.mode = 'manual'; await run('refresh()');
  elements.get('warmSlider').value = '37'; run('queueManual()');
  await run('sendManual()'); await flush();
  const manual = calls.filter(c => c.route === '/api/manual').pop();
  assert.deepEqual(Object.fromEntries(new URLSearchParams(manual.options.body)), {warm: '37', cool: '0'});
  await diagnosticsTest();
  console.log('PASS UI single channel: one curve, shared program encoding, manual cool fixed at 0');
}


function meshView(hostChannels, search, role, bleAdapter = null) {
  const nodes = new Map(), created = [], viewCalls = [], callbacks = new Map();
  const make = (tag = 'div') => { const node = new Element(tag); created.push(node); return node; };
  for (const match of header.matchAll(/id="([^"]+)"/g)) { const node = make(); node.id = match[1]; nodes.set(node.id, node); }
  const modes = [...header.matchAll(/<button\b([^>]*)>/g)].flatMap(match => {
    const attributes = match[1];
    if (!/class="[^"]*\bmodebtn\b/.test(attributes)) return [];
    const id = attributes.match(/id="([^"]*)"/), mode = attributes.match(/data-mode="([^"]*)"/);
    const node = id ? nodes.get(id[1]) : make('button'); node.dataset.mode = mode[1]; return [node];
  });
  const curves = ['warm', 'cool'].map(curve => { const node = make('button'); node.dataset.curve = curve; return node; });
  const steps = ['warm', 'cool'].flatMap(channel => [-1, 1].map(step => { const node = make('button'); node.dataset = {channel, step: String(step)}; return node; }));
  const selectors = new Map();
  const viewDocument = {
    documentElement: {dataset: {channels: String(hostChannels)}}, hidden: false, addEventListener() {},
    get activeElement() { return null; },
    getElementById: id => nodes.get(id) || created.find(node => node.id === id),
    createElement: make,
    querySelector: selector => { if (!selectors.has(selector)) selectors.set(selector, make()); return selectors.get(selector); },
    querySelectorAll: selector => selector === '.modebtn' ? modes : selector === '.curvebtn' ? curves : selector === '.stepbtn' ? steps : selector === '.stepbtn,.slider' ? [...steps, nodes.get('warmSlider'), nodes.get('coolSlider')] : []
  };
  const data = {
    mode: 'auto', warm: 50, cool: 40, manualWarm: 30, manualCool: 20,
    pwmOk: true, sensorOk: true, temperature: 25.6, humidity: 34.3, sensorAgeSec: 2,
    clockValid: true, epoch: 1793100000, localTime: '2026-10-27 13:00:00', minuteOfDay: 780, timeSource: 'ntp',
    schedule: {warm: [{minute:480,level:0},{minute:540,level:50},{minute:1020,level:50},{minute:1080,level:0}], cool: [{minute:450,level:0},{minute:570,level:40},{minute:1110,level:0}]},
    storageOk: true, savePending: false,
    network: {connected: role === 'master', ssid: 'Home', ip: '192.168.1.106', apActive: false},
    mesh: {role, peerOnline: true, peerAgeSec: 1, channel: 4, masterNetworkConnected: true, ready: true, rxDropped: 0},
    transport: {remote: search.includes('target=peer'), online: true, ageSec: 0},
    update: {configured: true, state: 'waiting', version: '1.3.0', progress: 0, last_check: 0},
    maintenance: {version: '1.3.0', localOtaReady: false, telnetReady: false, localOtaBusy: false}
  };
  let now = 1793100000000, timerCounter = 0, nextCommand = 40;
  const model = {data, ackHold: false, ackOutcome: 'applied', ackPolls: 0, releaseAck: null};
  const pending = new Map();
  class ViewDate extends Date { static now() { return now; } }
  const apply = (route, body) => {
    if (route.endsWith('/mode')) data.mode = body.mode;
    if (route.endsWith('/manual')) { data.manualWarm = data.warm = Number(body.warm); data.manualCool = data.cool = Number(body.cool); }
    if (route.endsWith('/program')) for (const channel of ['warm', 'cool']) data.schedule[channel] = body[channel + 'Points'].split(';').map(point => {
      const [minute, level] = point.split(',').map(Number); return {minute, level};
    });
    if (route.endsWith('/maintenance/ap')) data.network.apActive = true;
  };
  const response = body => ({ok: true, status: 200, json: async () => body});
  const sandbox = vm.createContext({
    console, document: viewDocument, window: {location: {search}, AQUA_BLE_MODE: !!bleAdapter, AquaBleTransport: bleAdapter, addEventListener() {}}, ResizeObserver: class {observe() {}},
    AbortController, TextEncoder, URLSearchParams, Intl, Date: ViewDate, Number, Math, Set, Promise,
    setTimeout: (callback, ms) => {
      const id = ++timerCounter;
      if (ms === 150) queueMicrotask(() => { now += ms; callback(); });
      else callbacks.set(id, {callback, ms});
      return id;
    },
    clearTimeout: id => callbacks.delete(id),
    fetch: async (route, options = {}) => {
      viewCalls.push({route, options});
      assert.ok(route.startsWith('/'), 'Every request must stay on the hosting origin');
      if (options.method === 'POST') {
        const body = Object.fromEntries(new URLSearchParams(options.body));
        assert.equal(options.headers['X-Aqua-Request'], '1');
        const queued = route.startsWith('/api/peer/') || route === '/api/wifi' && role === 'slave' && !search.includes('target=peer');
        if (!queued) { apply(route, body); return response({ok: true}); }
        const commandId = ++nextCommand; pending.set(commandId, {route, body});
        return {ok: true, status: 202, json: async () => ({ok: true, pending: true, commandId})};
      }
      if (route.startsWith('/api/peer/command?')) {
        model.ackPolls++;
        const commandId = Number(new URLSearchParams(route.split('?')[1]).get('id'));
        const finish = () => {
          if (model.ackOutcome === 'error') return response({ok: false, commandId, status: 'error', error: 'A társvezérlő elutasította a parancsot.'});
          if (model.ackOutcome === 'mismatch') return response({ok: true, commandId: commandId + 1, status: 'applied'});
          const command = pending.get(commandId); assert.ok(command);
          apply(command.route, command.body); pending.delete(commandId);
          return response({ok: true, commandId, status: 'applied', httpStatus: 200});
        };
        if (model.ackOutcome === 'timeout' || model.ackPolls === 1) return response({ok: true, commandId, status: 'pending'});
        if (model.ackHold) return new Promise(resolve => { model.releaseAck = () => resolve(finish()); });
        return finish();
      }
      assert.ok(route === '/api/state' || route === '/api/peer/state');
      return response(JSON.parse(JSON.stringify(data)));
    }
  });
  const execute = code => vm.runInContext(code, sandbox);
  execute(script);
  return {nodes, modes, calls: viewCalls, run: execute, model, document: viewDocument};
}


async function bleModeUiTest() {
  class Adapter extends EventTarget {
    constructor() { super(); this.supported = true; this.connected = false; this.calls = []; this.connects = 0; this.channels = 2; }
    event(state) { const event = new Event('state'); Object.defineProperty(event, 'detail', {value: {state, error: state === 'disconnected' ? 'A Bluetooth-kapcsolat megszakadt.' : ''}}); this.dispatchEvent(event); }
    async connect() { ++this.connects; this.connected = true; this.event('connected'); return {channels: this.channels, version: '1.4.0-ble'}; }
    disconnect() { this.connected = false; this.event('disconnected'); }
    async request(route, body) {
      if (!this.connected) throw new Error('Nincs Bluetooth-kapcsolat');
      this.calls.push({route, body});
      if (route === '/api/state') {
        const snapshot = JSON.parse(JSON.stringify(this.data));
        if (this.holdNextState) { this.holdNextState = false; await new Promise(resolve => { this.releaseState = resolve; }); }
        return snapshot;
      }
      if (route === '/api/time') {
        if (this.holdTime) await new Promise(resolve => { this.releaseTime = resolve; });
        this.data.timeSource = 'ble'; this.data.clockValid = true;
      }
      if (route === '/api/mode') this.data.mode = body.mode;
      if (route === '/api/program') for (const channel of ['warm', 'cool']) this.data.schedule[channel] = body[channel + 'Points'].split(';').map(point => {
        const [minute, level] = point.split(',').map(Number); return {minute, level};
      });
      return {ok: true};
    }
  }
  const adapter = new Adapter(), view = meshView(2, '?target=peer&channels=1', 'standalone', adapter);
  adapter.data = view.model.data;
  await flush(); await flush();
  assert.equal(view.run('bleMode'), true); assert.equal(view.run('peerTarget'), false); assert.equal(view.run('single'), false);
  assert.equal(adapter.connects, 0); assert.equal(view.run('online'), false);
  assert.equal(view.nodes.get('manualEnable').disabled, true); assert.equal(view.nodes.get('saveProgram').disabled, true);
  assert.equal(view.nodes.get('bleConnectionPanel').hidden, false);
  assert.equal(view.nodes.get('sensorHistory').hidden, true);
  for (const id of ['networkSettings', 'firmwareUpdateSection', 'localAccessSection', 'meshSection']) assert.equal(view.nodes.get(id).hidden, true);
  assert.equal(view.nodes.get('temperature').textContent, '—');
  await view.run('connectBle()');
  assert.equal(adapter.connects, 1); assert.equal(view.run('online'), true);
  assert.equal(view.nodes.get('bleConnect').disabled, true); assert.equal(view.nodes.get('bleDisconnect').disabled, false);
  assert.match(view.nodes.get('bleStatus').textContent, /órát a telefon idejére állítottuk/);
  assert.match(view.nodes.get('timeInfo').textContent, /Bluetoothon, a telefon idejéről/);
  adapter.data.clockValid = false; await view.run('refresh()');
  assert.match(view.nodes.get('timeInfo').textContent, /Csatlakozz Bluetoothon/);
  assert.doesNotMatch(view.nodes.get('timeInfo').textContent, /Internetes kapcsolat/);
  adapter.data.clockValid = true; await view.run('refresh()');
  assert.deepEqual(adapter.calls.slice(0, 3).map(call => call.route), ['/api/state', '/api/time', '/api/state']);
  assert.equal(view.calls.length, 0, 'BLE mode must never call HTTP fetch');
  await view.run("changeMode('manual')"); assert.equal(view.run('latest.mode'), 'manual');
  view.run("insertPoint('warm',600,12)"); await view.run('saveProgram()');
  assert.ok(adapter.calls.some(call => call.route === '/api/program' && call.body.warmPoints.includes('600,12')));
  view.run("draft.warm[0].level='29';markDirty()");
  const draft = view.run('JSON.stringify(draft)');
  adapter.holdNextState = true; const stale = view.run('refresh()'); await flush();
  adapter.disconnect();
  assert.equal(view.run('online'), false); assert.equal(view.nodes.get('temperature').textContent, '—');
  assert.equal(view.nodes.get('actualWarm').textContent, '—'); assert.equal(view.nodes.get('warmSlider').disabled, true);
  adapter.releaseState(); await stale;
  assert.equal(view.run('online'), false, 'A stale state reply must not reconnect the UI');
  assert.equal(view.run('JSON.stringify(draft)'), draft);
  await view.run('connectBle()');
  assert.equal(view.run('online'), true); assert.equal(view.run('JSON.stringify(draft)'), draft);

  // A write queued on the previous connection is never replayed on reconnect.
  adapter.holdTime = true;
  const held = view.run("mutate('/api/time',{epoch:'1793100000'})"); await flush();
  const queued = view.run("mutate('/api/mode',{mode:'off'})"); const rejected = assert.rejects(queued, /korábbi parancsot nem küldjük újra/);
  adapter.disconnect(); const reconnect = view.run('connectBle()'); await flush();
  adapter.holdTime = false; adapter.releaseTime(); await held; await rejected; await reconnect;
  assert.equal(adapter.calls.filter(call => call.route === '/api/mode' && call.body.mode === 'off').length, 0);
  assert.equal(view.calls.length, 0);

  const unsupported = new Adapter(); unsupported.supported = false;
  const unavailable = meshView(2, '', 'standalone', unsupported); unsupported.data = unavailable.model.data;
  await flush(); await flush(); assert.equal(unavailable.nodes.get('bleConnect').disabled, true);
  assert.match(unavailable.nodes.get('bleStatus').textContent, /Android Chrome/);
  const wrong = new Adapter(); wrong.channels = 1;
  const wrongBoard = meshView(2, '', 'standalone', wrong); wrong.data = wrongBoard.model.data;
  await flush(); await wrongBoard.run('connectBle()');
  assert.equal(wrong.connected, false); assert.equal(wrongBoard.run('online'), false);
  assert.match(wrongBoard.nodes.get('bleStatus').textContent, /kétcsatornás terráriumhoz/);
  assert.equal(wrong.calls.length, 0);
  console.log('PASS BLE UI mode: explicit connection, no HTTP/fake data, browser clock, existing program editor, disconnect controls/drafts, stale epochs and no queued mutation replay');
}

async function meshUiTest() {
  const peer = meshView(2, '?target=peer&channels=1', 'slave'); await flush(); await flush();
  assert.equal(peer.run('single'), true); assert.equal(peer.document.documentElement.dataset.channels, '1');
  assert.equal(peer.nodes.get('deviceName').textContent, 'led-szallag');
  assert.equal(peer.calls[0].route, '/api/peer/state');
  assert.equal(peer.nodes.get('checkUpdate').disabled, true);
  assert.equal(peer.nodes.get('maintenanceApAction').hidden, false);
  assert.match(peer.nodes.get('updateStatus').textContent, /Helyi frissítés szükséges/);
  assert.match(peer.nodes.get('meshRole').textContent, /Társvezérlő/);
  assert.equal(peer.nodes.get('localOtaAddress').textContent, 'led-control-1ch.local:3232');
  assert.equal(peer.nodes.get('telnetAddress').textContent, 'led-control-1ch.local:23');
  assert.match(peer.nodes.get('wifiHelp').textContent, /terrárium fővezérlőjének/);
  peer.model.data.timeSource = 'mesh'; await peer.run('refresh()');
  assert.match(peer.nodes.get('timeInfo').textContent, /másik vezérlőtől kapott időből jár/);
  assert.doesNotMatch(peer.nodes.get('timeInfo').textContent, /erről az eszközről/);

  peer.model.ackHold = true;
  const change = peer.run("changeMode('manual')"); await flush(); await flush();
  assert.equal(peer.model.data.mode, 'auto'); assert.equal(peer.run('modePending'), 1);
  assert.equal(peer.nodes.get('warmSlider').disabled, true);
  assert.equal(peer.calls.find(call => call.options.method === 'POST').route, '/api/peer/mode');
  assert.ok(peer.model.releaseAck); peer.model.releaseAck(); await change;
  assert.equal(peer.model.data.mode, 'manual'); assert.equal(peer.run('latest.mode'), 'manual');
  assert.equal(peer.nodes.get('warmSlider').disabled, false); assert.ok(peer.model.ackPolls >= 2);

  // Acknowledgement completion, rather than HTTP 202, controls successful save.
  peer.run("insertPoint('warm',600,12)"); peer.model.releaseAck = null;
  const save = peer.run('saveProgram()'); await flush(); await flush();
  assert.equal(peer.run('dirty'), true); assert.equal(peer.run('saving'), true);
  assert.doesNotMatch(peer.nodes.get('toast').textContent, /elmentve/);
  peer.run("draft.warm[0].level='29';markDirty()");
  peer.model.releaseAck(); await save;
  assert.equal(peer.run('dirty'), true); assert.equal(peer.run('draft.warm[0].level'), '29');
  const draftBefore = peer.run('JSON.stringify(draft)');
  peer.model.data.transport.ageSec = 10; peer.model.data.sensorAgeSec = 12; await peer.run('refresh()');
  assert.equal(peer.nodes.get('temperature').textContent, '25,6'); // Cache age was already included by the server.
  peer.model.data.transport.ageSec = 16; await peer.run('refresh()');
  assert.equal(peer.nodes.get('temperature').textContent, '—'); assert.equal(peer.nodes.get('actualWarm').textContent, '—');
  assert.equal(peer.nodes.get('staleNotice').hidden, false);
  peer.model.data.transport.online = false; peer.model.data.transport.ageSec = 31; await peer.run('refresh()');
  assert.equal(peer.nodes.get('offlineNotice').hidden, false); assert.equal(peer.nodes.get('saveProgram').disabled, true);
  assert.equal(peer.nodes.get('warmSlider').disabled, true);
  const writes = peer.calls.filter(call => call.options.method === 'POST').length;
  await assert.rejects(peer.run("request('/api/mode',{mode:'off'})"), /nem elérhető/);
  assert.equal(peer.calls.filter(call => call.options.method === 'POST').length, writes);
  assert.equal(peer.run('JSON.stringify(draft)'), draftBefore);

  for (const outcome of ['error', 'timeout', 'mismatch']) {
    const failed = meshView(2, '?target=peer&channels=1', 'slave'); await flush(); await flush();
    failed.run("insertPoint('warm',600,12)"); failed.model.ackOutcome = outcome;
    await failed.run('saveProgram()');
    assert.equal(failed.run('dirty'), true); assert.equal(failed.run('saving'), false);
    assert.doesNotMatch(failed.nodes.get('toast').textContent, /elmentve/);
    assert.match(failed.nodes.get('toast').className, /error/);
    assert.match(failed.nodes.get('toast').textContent, outcome === 'timeout' ? /nem igazolta vissza időben/ : outcome === 'mismatch' ? /másik parancs/ : /elutasította/);
  }

  const master = meshView(1, '?target=peer&channels=2', 'master'); await flush(); await flush();
  assert.equal(master.nodes.get('sensorHistory').hidden, true);
  assert.equal(master.nodes.get('localOtaAddress').textContent, 'led-control.local:3232');
  master.model.data.timeSource = 'mesh'; await master.run('refresh()');
  assert.match(master.nodes.get('timeInfo').textContent, /másik vezérlőtől kapott időből jár/);
  assert.doesNotMatch(master.nodes.get('timeInfo').textContent, /fővezérlője szinkronizálja|erről az eszközről/);
  assert.match(master.nodes.get('wifiHelp').textContent, /saját hotspotot nem indít.*hotspotot a led-szallag biztosítja/);
  assert.equal(master.run('single'), false); assert.equal(master.document.documentElement.dataset.channels, '2');
  assert.equal(master.nodes.get('checkUpdate').disabled, false); assert.equal(master.nodes.get('maintenanceApAction').hidden, true);
  await master.run("changeMode('manual')"); assert.equal(master.calls.find(call => call.options.method === 'POST').route, '/api/peer/mode');
  // Local operation does not depend on the other panel's online state.
  const local = meshView(2, '?channels=1', 'master'); await flush(); await flush();
  assert.equal(local.run('single'), false); assert.equal(local.calls[0].route, '/api/state');
  assert.equal(local.nodes.get('sensorHistory').hidden, false);
  local.model.data.mesh.peerOnline = false; await local.run('refresh()');
  await local.run("changeMode('manual')"); assert.equal(local.model.data.mode, 'manual');
  assert.equal(local.nodes.get('maintenanceApAction').hidden, true);
  delete local.model.data.mesh; delete local.model.data.transport; await local.run('refresh()');
  assert.equal(local.nodes.get('meshData').hidden, true); assert.equal(local.run('online'), true); // Older state schema remains valid.

  const hotspot = meshView(2, '?target=peer&channels=1', 'slave'); await flush(); await flush();
  hotspot.model.ackHold = true;
  const activate = hotspot.run('startMaintenanceAp()'); await flush(); await flush();
  assert.equal(hotspot.model.data.network.apActive, false);
  assert.doesNotMatch(hotspot.nodes.get('toast').textContent, /visszaigazolta/);
  hotspot.model.releaseAck(); await activate;
  assert.equal(hotspot.model.data.network.apActive, true);
  assert.equal(hotspot.calls.find(call => call.options.method === 'POST').route, '/api/peer/maintenance/ap');
  assert.match(hotspot.nodes.get('toast').textContent, /visszaigazolta/);

  for (const search of ['', '?target=peer&channels=1']) {
    const wifi = meshView(search ? 2 : 1, search, 'slave'); await flush(); await flush();
    wifi.nodes.get('wifiSsid').value = 'Home'; wifi.nodes.get('wifiPassword').value = 'test-password';
    await wifi.nodes.get('wifiForm').listeners.submit({preventDefault() {}});
    assert.equal(wifi.calls.find(call => call.options.method === 'POST').route, '/api/wifi');
    if (!search) assert.ok(wifi.calls.some(call => call.route.startsWith('/api/peer/command?id=')));
    assert.match(wifi.nodes.get('toast').textContent, /fővezérlő/);
  }
  const invalid = meshView(2, '?target=peer&channels=03', 'slave'); await flush(); await flush();
  assert.equal(invalid.calls.length, 0); assert.equal(invalid.run('online'), false);
  const wrongChannels = meshView(2, '?target=peer&channels=1', 'master'); await flush(); await flush();
  wrongChannels.model.data.channels = 2; await wrongChannels.run('refresh()');
  assert.equal(wrongChannels.run('online'), false);
  await assert.rejects(wrongChannels.run("request('/api/mode',{mode:'off'})"), /nem elérhető/);
  console.log('PASS mesh UI: same-origin peer routing, validated channels, applied ACK before success, command errors/timeouts, retained drafts, stale/offline data, master Wi-Fi and slave hotspot');
}

(async () => {
  if (singleChannel) { await singleChannelTest(); await meshUiTest(); await bleModeUiTest(); return; }
  run(script); await flush(); await flush();
  await touchAndUndoTest();
  assert.equal(elements.get('temperature').textContent, '25,6');
  assert.equal(elements.get('points').children.length, 4);
  assert.equal(elements.get('wifiStatus').textContent, state.network.ssid);
  assert.equal(elements.get('wifiStatus').innerHTML, undefined); // SSID is never interpreted as HTML.
  assert.equal(run('interpolate([{minute:120,level:20},{minute:1320,level:80}],0)'), 50);

  const coolBefore = snapshot('cool'), warmId = run('draft.warm[0].id');
  const handle = elements.get('graph').handles.get(warmId);
  call('graphPointerDown', eventAt(480, 0, handle));
  assert(elements.get('graph').hasPointerCapture(1));
  call('graphPointerMove', eventAt(600, 70));
  call('graphPointerUp', eventAt(600, 70));
  assert(!elements.get('graph').hasPointerCapture(1));
  assert.equal(run('draft.warm.find(p=>p.id==="' + warmId + '").time'), '10:00');
  assert.equal(run('draft.warm.find(p=>p.id==="' + warmId + '").level'), '70');
  assert.equal(snapshot('cool'), coolBefore);
  assert.equal(calls.filter(call => call.options.method === 'POST').length, 0); // Editing is only a preview.
  const warmCount = run('draft.warm.length');
  call('graphDoubleClick', eventAt(600, 70)); // A synthesized dblclick may be retargeted to SVG after redraw.
  assert.equal(run('draft.warm.length'), warmCount);

  run('movePoint(draft.warm.find(p=>p.id==="' + warmId + '"),540,150)');
  assert.equal(run('draft.warm.find(p=>p.id==="' + warmId + '").time'), '09:01');
  assert.equal(run('draft.warm.find(p=>p.id==="' + warmId + '").level'), '100');
  run('movePoint(draft.warm.find(p=>p.id==="' + warmId + '"),-50,-30)');
  assert.equal(run('draft.warm.find(p=>p.id==="' + warmId + '").time'), '00:00');
  run('movePoint(draft.warm.find(p=>p.id==="' + warmId + '"),1600,140)');
  assert.equal(run('draft.warm.find(p=>p.id==="' + warmId + '").time'), '23:59');
  assert.equal(snapshot('cool'), coolBefore);

  elements.get('graph').handles.get(warmId).focus();
  await run('refresh()');
  assert.equal(activeElement.dataset.pointId, warmId); // Poll redraw preserves keyboard focus.
  run('drawGraph()'); assert.equal(activeElement.dataset.pointId, warmId); // Resize redraw does too.
  activeElement = elements.get('wifiSsid'); run('drawGraph()');
  assert.equal(activeElement, elements.get('wifiSsid')); // Never steal another control's focus.

  run("activateCurve('cool'); suppressDoubleUntil=0");
  const warmBefore = snapshot('warm'), initialCoolCount = run('draft.cool.length');
  const coolId = run('draft.cool[0].id');
  call('graphPointerDown', eventAt(450, 0, elements.get('graph').handles.get(coolId)));
  call('graphPointerMove', eventAt(620, 62));
  call('graphPointerUp', eventAt(620, 62));
  assert.equal(run('draft.cool.find(p=>p.id==="' + coolId + '").time'), '10:20');
  assert.equal(snapshot('warm'), warmBefore);
  run('suppressDoubleUntil=0');
  call('graphDoubleClick', eventAt(720, 40));
  assert.equal(run('draft.cool.length'), initialCoolCount + 1);
  for (let tap = 0; tap < 2; tap++) {
    call('graphPointerDown', eventAt(660, 65, undefined, {pointerType: 'touch'}));
    call('graphPointerUp', eventAt(660, 65, undefined, {pointerType: 'touch'}));
  }
  assert.equal(run('draft.cool.length'), initialCoolCount + 2);
  call('graphDoubleClick', eventAt(660, 65));
  assert.equal(run('draft.cool.length'), initialCoolCount + 2); // Touch + synthesized dblclick inserts exactly once.
  assert.equal(snapshot('warm'), warmBefore);
  while (run('draft.cool.length') < 16) run("insertPoint('cool',60+draft.cool.length,30)");
  assert.equal(run("insertPoint('cool',300,20)"), false);
  while (run('draft.cool.length') > 2) run('removePoint(draft.cool[0].id)');
  assert.equal(run('removePoint(draft.cool[0].id)'), false);
  assert.equal(run('draft.cool.length'), 2);
  const beforePoll = run('JSON.stringify(draft)'); await run('refresh()');
  assert.equal(run('JSON.stringify(draft)'), beforePoll);
  assert.equal(run('dirty'), true);
  const validTime = run('draft.warm[1].time');
  run('draft.warm[1].time=draft.warm[0].time');
  await run('saveProgram()');
  assert.equal(calls.filter(call => call.route === '/api/program').length, 0);
  context.validTime = validTime; run('draft.warm[1].time=validTime');
  run('parseDraft()');

  const manualEnable = elements.get('manualEnable'), manualExit = elements.get('manualExit');
  const programButton = modeButtons.find(button => button.dataset.mode === 'auto');
  const warmSlider = elements.get('warmSlider');
  const savedProgramBeforeModeSwitch = JSON.stringify(state.schedule), unsavedDraftBeforeModeSwitch = run('JSON.stringify(draft)');
  assert.equal(warmSlider.disabled, true);
  await manualEnable.listeners.click();
  assert.equal(state.mode, 'manual'); assert.equal(warmSlider.disabled, false);
  warmSlider.value = '37'; elements.get('coolSlider').value = '19'; warmSlider.listeners.input();
  await run('sendManual()');
  assert.equal(state.manualWarm, 37); assert.equal(state.manualCool, 19);
  await manualExit.listeners.click();
  assert.equal(state.mode, 'auto'); assert.equal(warmSlider.disabled, true);
  assert.equal(elements.get('coolSlider').disabled, true);
  assert.equal(String(warmSlider.value), '50'); assert.equal(String(elements.get('coolSlider').value), '40');
  assert.equal(String(elements.get('actualWarm').textContent), '50');
  assert.equal(String(elements.get('actualCool').textContent), '40');
  assert.equal(elements.get('modeBadge').textContent, 'Automata program');
  assert.equal(state.manualWarm, 37); assert.equal(state.manualCool, 19);

  for (const mode of ['auto', 'off']) {
    await modeButtons.find(button => button.dataset.mode === mode).listeners.click();
    const manualCount = calls.filter(call => call.route === '/api/manual').length;
    warmSlider.value = '99'; warmSlider.listeners.input(); warmSlider.listeners.change();
    await run('sendManual()');
    assert.equal(state.mode, mode);
    assert.equal(calls.filter(call => call.route === '/api/manual').length, manualCount);
    const beforeRejectedWrite = JSON.stringify(state);
    await assert.rejects(run("request('/api/manual',{warm:'99',cool:'0'})"), /Előbb kapcsold be/);
    assert.equal(JSON.stringify(state), beforeRejectedWrite); // Another client's late write cannot revive manual mode.
  }

  await manualEnable.listeners.click();
  warmSlider.value = '98'; warmSlider.listeners.input(); await run('sendManual()');
  assert.equal(state.mode, 'manual'); assert.equal(state.manualWarm, 98);
  warmSlider.value = '99'; warmSlider.listeners.input(); // A queued gesture is cancelled by explicit exit.
  await manualExit.listeners.click(); warmSlider.listeners.change(); await run('sendManual()');
  assert.equal(state.mode, 'auto'); assert.equal(state.manualWarm, 98);

  await manualEnable.listeners.click();
  heldPath = '/api/manual'; warmSlider.value = '88'; warmSlider.listeners.input();
  const pendingManual = run('sendManual()'); await flush();
  const automatic = programButton.listeners.click();
  assert.equal(warmSlider.disabled, true); warmSlider.listeners.input();
  release(); await pendingManual; await automatic; heldPath = null;
  await run('refresh()'); warmSlider.listeners.change(); await run('sendManual()');
  assert.equal(state.mode, 'auto'); assert.equal(run('manualQueued'), null);
  const autoPosts = calls.filter(call => call.options.method === 'POST');
  assert.equal(autoPosts.at(-2).route, '/api/manual');
  assert.equal(new URLSearchParams(autoPosts.at(-1).options.body).get('mode'), 'auto');

  await manualEnable.listeners.click();
  heldPath = '/api/time'; const blockingWrite = run("mutate('/api/time',{epoch:'1793100000'})"); await flush();
  const manualCountBeforeCancel = calls.filter(call => call.route === '/api/manual').length;
  warmSlider.value = '77'; warmSlider.listeners.input(); const queuedWrite = run('sendManual()'); await flush();
  const exitAfterQueue = manualExit.listeners.click();
  release(); await blockingWrite; await queuedWrite; await exitAfterQueue; heldPath = null;
  assert.equal(calls.filter(call => call.route === '/api/manual').length, manualCountBeforeCancel);
  assert.equal(state.mode, 'auto');

  await manualEnable.listeners.click();
  holdNextGet = true; const oldPoll = run('refresh()'); await flush();
  const exitDuringPoll = manualExit.listeners.click(); await flush();
  assert.equal(warmSlider.disabled, true); // A mode change never re-enables controls from an old GET.
  releaseGet(); await oldPoll; await exitDuringPoll;
  assert.equal(state.mode, 'auto'); assert.equal(run('latest.mode'), 'auto');
  assert.equal(elements.get('modeBadge').textContent, 'Automata program');
  assert.equal(warmSlider.disabled, true);
  assert.equal(JSON.stringify(state.schedule), savedProgramBeforeModeSwitch);
  assert.equal(run('JSON.stringify(draft)'), unsavedDraftBeforeModeSwitch);

  heldPath = '/api/program'; const save = run('saveProgram()'); await flush();
  run("draft.warm[0].level='29';markDirty()"); release(); await save; heldPath = null;
  assert.equal(run('draft.warm[0].level'), '29'); assert.equal(run('dirty'), true);
  state.sensorOk = false; state.temperature = null; state.humidity = null; await run('refresh()');
  assert.equal(elements.get('temperature').textContent, '—');
  state.clockValid = false; await run('refresh()');
  assert(!elements.get('graph').innerHTML.includes('<rect '));
  state.mode = 'manual'; state.savePending = true; run('manualGuardUntil=0'); await run('refresh()');
  assert.equal(elements.get('manualStatus').textContent, 'Beállítások mentése…');
  state.storageOk = false; await run('refresh()');
  assert.equal(elements.get('storageNotice').hidden, false);
  const draftBeforeUpdate = run('JSON.stringify(draft)'), modeBeforeUpdate = state.mode;
  const manualBeforeUpdate = [state.manualWarm, state.manualCool];
  const checkButton = elements.get('checkUpdate');
  assert.equal(checkButton.disabled, true); // Update data is optional, never assumed configured.
  assert.equal(elements.get('firmwareVersion').textContent, '—');
  state.maintenance = {version: '1.1.0', localOtaReady: true, telnetReady: true, localOtaBusy: false};
  state.update = {configured: true, state: 'waiting', version: '1.1.0', release_id: '', progress: 0, last_check: 0, error: ''};
  await run('refresh()');
  assert.equal(checkButton.disabled, false);
  assert.equal(elements.get('firmwareVersion').textContent, '1.1.0');
  assert.equal(elements.get('availableVersion').hidden, true);
  assert.equal(elements.get('localOtaStatus').textContent, 'Elérhető');
  assert.equal(elements.get('telnetStatus').textContent, 'Elérhető');
  heldPath = '/api/update/check';
  const check = checkButton.listeners.click(); await flush();
  assert.equal(checkButton.disabled, true); assert.equal(run('updateCheckPending'), true);
  const checksBeforeDoubleClick = calls.filter(call => call.route === '/api/update/check').length;
  await checkButton.listeners.click();
  assert.equal(calls.filter(call => call.route === '/api/update/check').length, checksBeforeDoubleClick);
  release(); await check; heldPath = null;
  assert.equal(run('updateCheckPending'), false);
  const updatePost = calls.find(call => call.route === '/api/update/check');
  assert.equal(updatePost.options.body, '');
  assert.match(elements.get('toast').textContent, /sorba állította/); // HTTP202 is queued, not installed.
  assert.equal(elements.get('updateStatus').textContent, 'Várakozás a következő ellenőrzésre.');
  for (const busyState of ['checking', 'downloading', 'ready', 'rebooting', 'pending_validation']) {
    state.update.state = busyState; await run('refresh()');
    assert.equal(checkButton.disabled, true);
  }
  state.update.state = 'downloading'; state.update.progress = 27.5; await run('refresh()');
  assert.equal(elements.get('updateProgressRow').hidden, false);
  assert.equal(elements.get('updateProgress').value, 28);
  assert.equal(elements.get('updatePercent').textContent, '28%');
  state.update.state = 'error'; state.update.error = '<img src=x onerror=alert(1)>';
  state.update.available_version = '<b>1.2.0</b>'; state.update.version = '<script>bad()</script>';
  state.update.last_check = Date.UTC(2026, 9, 27, 12, 0) / 1000; await run('refresh()');
  assert.equal(checkButton.disabled, false);
  assert.equal(elements.get('updateDetail').textContent, state.update.error);
  assert.equal(elements.get('updateDetail').innerHTML, undefined);
  assert.equal(elements.get('firmwareVersion').textContent, state.update.version);
  assert.equal(elements.get('firmwareVersion').innerHTML, undefined);
  assert.equal(elements.get('availableVersion').textContent, state.update.available_version);
  assert.match(elements.get('lastUpdateCheck').textContent, /13:00/);
  state.update.state = '<svg onload=bad()>'; await run('refresh()');
  assert.equal(elements.get('updateStatus').textContent, 'A frissítési állapot nem ismert.');
  state.update.state = 'constructor'; await run('refresh()');
  assert.equal(elements.get('updateStatus').textContent, 'A frissítési állapot nem ismert.');
  state.update.state = 'waiting'; state.update.error = ''; state.update.configured = false; await run('refresh()');
  assert.equal(checkButton.disabled, true);
  state.update.configured = true; state.maintenance.localOtaBusy = true; await run('refresh()');
  assert.equal(checkButton.disabled, true);
  state.maintenance.localOtaBusy = false; await run('refresh()');
  updateRequestError = 'Az eszköz most nem tud csatlakozni.'; await checkButton.listeners.click();
  assert.equal(run('updateCheckPending'), false); assert.equal(checkButton.disabled, false);
  assert.equal(elements.get('updateStatus').textContent, 'Az ellenőrzés indítása nem sikerült.');
  assert.equal(elements.get('updateDetail').textContent, updateRequestError);
  updateRequestError = '';
  state.update.state = 'rebooting'; await run('refresh()'); run('connection(false)');
  assert.equal(checkButton.disabled, true);
  state.update.state = 'healthy'; state.update.version = '1.2.0'; await run('refresh()');
  assert.equal(elements.get('firmwareVersion').textContent, '1.2.0');
  delete state.update; await run('refresh()');
  assert.equal(checkButton.disabled, true);
  assert.equal(elements.get('firmwareVersion').textContent, '1.1.0'); // Local-maintenance fallback.
  assert.equal(run('JSON.stringify(draft)'), draftBeforeUpdate);
  assert.equal(state.mode, modeBeforeUpdate);
  assert.deepEqual([state.manualWarm, state.manualCool], manualBeforeUpdate);
  await diagnosticsTest();
  await meshUiTest();
  await bleModeUiTest();
  console.log('PASS UI: independent curves; drag/capture/coordinates; bounds/collision; mouse/touch dedup; point limits; keyboard focus; unsaved drafts; atomic API; explicit manual ON/OFF; guarded slider writes; stale polls and save/edit races; sensor/clock/storage status; OTA optional/pending/error/progress/reconnect state');
})().catch(error => { console.error(error); process.exitCode = 1; });
