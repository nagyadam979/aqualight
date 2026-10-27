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
const elements = new Map(), all = [], calls = [], timers = new Map();
let activeElement = null, nextTimer = 1, heldPath = null, release = null;

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
const modeButtons = ['manual', 'auto', 'off'].map(mode => {
  const element = new Element('button'); element.dataset.mode = mode; return element;
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
const document = {
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
      if (route === '/api/manual') {
        state.mode = 'manual'; state.manualWarm = state.warm = Number(data.warm); state.manualCool = state.cool = Number(data.cool);
      }
      if (route === '/api/mode') { state.mode = data.mode; if (data.mode === 'off') state.warm = state.cool = 0; }
      if (route === '/api/program') {
        assert.deepEqual(Object.keys(data).sort(), ['coolPoints', 'warmPoints']);
        for (const channel of ['warm', 'cool']) state.schedule[channel] = data[channel + 'Points'].split(';').map(point => {
          const [minute, level] = point.split(',').map(Number); return {minute, level};
        });
      }
      if (route === heldPath) await new Promise(resolve => { release = resolve; });
      return {ok: true, json: async () => ({ok: true})};
    }
    return {ok: true, json: async () => JSON.parse(JSON.stringify(state))};
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

(async () => {
  run(script); await flush(); await flush();
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

  run("setSlider('warm',31);setSlider('cool',42);queueManual()");
  await run("changeMode('off')"); await run('sendManual()');
  assert.equal(calls.filter(call => call.route === '/api/manual').length, 0);
  heldPath = '/api/manual'; run("setSlider('warm',37);queueManual()");
  const manual = run('sendManual()'); await flush(); assert(release);
  const off = run("changeMode('off')"); release(); await manual; await off; heldPath = null;
  assert.equal(state.mode, 'off');
  const posts = calls.filter(call => call.options.method === 'POST');
  assert.equal(posts.at(-2).route, '/api/manual'); assert.equal(posts.at(-1).route, '/api/mode');

  const savedProgramBeforeModeSwitch = JSON.stringify(state.schedule);
  const programButton = modeButtons.find(button => button.dataset.mode === 'auto');
  const warmSlider = elements.get('warmSlider');
  state.mode = 'manual'; await run('refresh()');
  warmSlider.value = '99'; elements.get('coolSlider').value = '0';
  warmSlider.listeners.input(); // Start a real slider callback path, then cancel its pending write.
  await programButton.listeners.click();
  warmSlider.listeners.change(); // A delayed gesture commit must not recreate a manual command.
  await run('sendManual()'); await run('refresh()');
  assert.equal(state.mode, 'auto');
  assert.equal(run('manualQueued'), null);
  assert.equal(JSON.stringify(state.schedule), savedProgramBeforeModeSwitch);

  warmSlider.value = '98'; warmSlider.listeners.input();
  await run('sendManual()');
  assert.equal(state.mode, 'manual'); // A new intentional adjustment after auto still controls the lamp.
  heldPath = '/api/manual'; warmSlider.value = '88'; warmSlider.listeners.input();
  const pendingManual = run('sendManual()'); await flush();
  const automatic = programButton.listeners.click();
  release(); await pendingManual; await automatic; heldPath = null;
  await run('refresh()'); warmSlider.listeners.change();
  await run('sendManual()'); await run('refresh()');
  assert.equal(state.mode, 'auto');
  assert.equal(run('manualQueued'), null);
  const autoPosts = calls.filter(call => call.options.method === 'POST');
  assert.equal(autoPosts.at(-2).route, '/api/manual');
  assert.equal(new URLSearchParams(autoPosts.at(-1).options.body).get('mode'), 'auto');
  assert.equal(JSON.stringify(state.schedule), savedProgramBeforeModeSwitch);

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
  console.log('PASS UI: independent curves; drag/capture/coordinates; bounds/collision; mouse/touch dedup; point limits; keyboard focus; unsaved drafts; atomic API; manual/OFF/auto late-change and save/edit races; sensor/clock/storage status');
})().catch(error => { console.error(error); process.exitCode = 1; });
