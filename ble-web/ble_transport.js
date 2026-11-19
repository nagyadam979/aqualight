/* AquaLight BLE pilot, protocol 1. No credentials or device state are persisted.
 * Browser API: https://developer.chrome.com/docs/capabilities/bluetooth
 */
(function (root) {
  'use strict';
  const UUIDS = Object.freeze({
    service: 'a9f00001-7e2a-4d76-9c43-819d9b3b6a10',
    rx: 'a9f00002-7e2a-4d76-9c43-819d9b3b6a10',
    tx: 'a9f00003-7e2a-4d76-9c43-819d9b3b6a10',
    info: 'a9f00004-7e2a-4d76-9c43-819d9b3b6a10'
  });
  const HEADER = 10, REQUEST_LIMIT = 2048, RESPONSE_LIMIT = 6144;
  const POST_PATHS = new Set(['/api/mode', '/api/manual', '/api/program', '/api/time']);

  class BleTransport extends EventTarget {
    constructor(options = {}) {
      super();
      this.bluetooth = options.bluetooth || (root.navigator && root.navigator.bluetooth);
      this.secureContext = options.secureContext === undefined ? root.isSecureContext !== false : options.secureContext;
      this.now = options.now || (() => Date.now());
      this.setTimer = options.setTimer || ((callback, delay) => setTimeout(callback, delay));
      this.clearTimer = options.clearTimer || (timer => clearTimeout(timer));
      this.timeoutMs = options.timeoutMs || 20000;
      this.writePauseMs = options.writePauseMs === undefined ? 5 : options.writePauseMs;
      this.device = this.rx = this.tx = this.info = null;
      this.connected = false;
      this.connecting = false;
      this.generation = 0;
      this.nextId = 1;
      this.active = null;
      this.queue = [];
      this.onNotification = event => this.receive(event);
      this.onDisconnected = () => this.failSession(new Error('A Bluetooth-kapcsolat megszakadt. A parancsokat nem küldjük újra.'));
    }

    get supported() {
      return !!(this.secureContext && this.bluetooth && typeof this.bluetooth.requestDevice === 'function');
    }

    state(name, error) {
      const event = new Event('state');
      Object.defineProperty(event, 'detail', {value: {state: name, error: error ? error.message : '', info: this.info}});
      this.dispatchEvent(event);
    }

    clearCharacteristics() {
      if (this.tx) this.tx.removeEventListener('characteristicvaluechanged', this.onNotification);
      if (this.device) this.device.removeEventListener('gattserverdisconnected', this.onDisconnected);
      this.rx = this.tx = this.info = null;
    }

    failSession(error, state = 'disconnected') {
      ++this.generation;
      this.connected = this.connecting = false;
      const device = this.device;
      this.clearCharacteristics();
      this.device = null;
      const jobs = this.queue.splice(0);
      if (this.active) { jobs.unshift(this.active.job); this.active = null; }
      for (const job of jobs) { this.clearTimer(job.timer); job.reject(error); }
      if (device && device.gatt && device.gatt.connected) device.gatt.disconnect();
      this.state(state, error);
    }

    disconnect() {
      this.failSession(new Error('A Bluetooth-kapcsolat bontva. A parancsokat nem küldjük újra.'));
    }

    async connect() {
      if (!this.supported) throw new Error('Ehhez Android Chrome és HTTPS-en megnyitott oldal szükséges.');
      if (this.connecting) throw new Error('A Bluetooth-kapcsolódás már folyamatban van.');
      if (this.connected) return this.info;
      const generation = ++this.generation;
      this.connecting = true;
      this.state('connecting');
      let timer;
      const current = () => { if (this.generation !== generation) throw new Error('A kapcsolódás megszakadt.'); };
      try {
        // requestDevice is called before the first await, inside the button's
        // user gesture. The Android pairing UI owns PIN entry and bonding.
        const device = await this.bluetooth.requestDevice({filters: [{services: [UUIDS.service]}]});
        current();
        this.device = device;
        device.addEventListener('gattserverdisconnected', this.onDisconnected);
        const handshake = (async () => {
          const server = await device.gatt.connect(); current();
          const service = await server.getPrimaryService(UUIDS.service); current();
          const tx = await service.getCharacteristic(UUIDS.tx); current();
          const rx = await service.getCharacteristic(UUIDS.rx); current();
          const infoCharacteristic = await service.getCharacteristic(UUIDS.info); current();
          if (typeof rx.writeValueWithResponse !== 'function') throw new Error('A böngésző nem támogatja a visszaigazolt Bluetooth-írást.');
          this.tx = tx; this.rx = rx;
          tx.addEventListener('characteristicvaluechanged', this.onNotification);
          await tx.startNotifications(); current();
          // Protected INFO read triggers Android's authenticated pairing flow.
          const value = await infoCharacteristic.readValue(); current();
          const bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
          const info = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes));
          const maxFrame = info.maxFrame === undefined ? 20 : info.maxFrame;
          if (info.protocol !== 1 || !Number.isInteger(maxFrame) || maxFrame < 20 || maxFrame > 180 ||
              !Number.isInteger(info.requestLimit) || info.requestLimit < 1 || info.requestLimit > REQUEST_LIMIT ||
              !Number.isInteger(info.responseLimit) || info.responseLimit < 1 || info.responseLimit > RESPONSE_LIMIT ||
              ![1, 2].includes(info.channels) || typeof info.version !== 'string' || !info.version || info.version.length > 64)
            throw new Error('A vezérlő Bluetooth-protokollja nem támogatott.');
          this.info = Object.freeze({...info, maxFrame});
          this.nextId = 1;
          this.connected = true; this.connecting = false;
          this.state('connected');
          return this.info;
        })();
        const timeout = new Promise((resolve, reject) => {
          timer = this.setTimer(() => reject(new Error('A Bluetooth-párosítás időtúllépéssel megszakadt.')), 45000);
        });
        return await Promise.race([handshake, timeout]);
      } catch (error) {
        const message = new Error(error.name === 'NotFoundError' ? 'Nem választottál Bluetooth-vezérlőt.' :
          'A Bluetooth-kapcsolódás vagy párosítás nem sikerült. ' + (error.message || ''));
        if (this.generation === generation) this.failSession(message, 'error');
        throw message;
      } finally { if (timer !== undefined) this.clearTimer(timer); }
    }

    request(path, form) {
      if (!this.connected || !this.info || !this.rx) return Promise.reject(new Error('Előbb csatlakozz a Bluetooth-vezérlőhöz.'));
      const method = form === undefined ? 'GET' : 'POST';
      if (method === 'GET' ? path !== '/api/state' : !POST_PATHS.has(path))
        return Promise.reject(new Error('Ez a művelet a Bluetooth-próbában nem érhető el.'));
      const encodedForm = form === undefined ? '' : new URLSearchParams(form).toString();
      const bytes = new TextEncoder().encode(JSON.stringify({method, path, form: encodedForm}));
      if (bytes.length > this.info.requestLimit) return Promise.reject(new Error('A parancs túl hosszú a Bluetooth-kapcsolathoz.'));
      if (this.queue.length + (this.active ? 1 : 0) >= 4) return Promise.reject(new Error('A vezérlő még az előző műveleteket végzi.'));
      return new Promise((resolve, reject) => {
        const job = {bytes, resolve, reject, deadline: this.now() + this.timeoutMs};
        job.timer = this.setTimer(() => {
          const error = new Error('A vezérlő nem válaszolt időben. A végrehajtás még nem igazolt; nincs automatikus újraküldés.');
          if (this.active && this.active.job === job) this.failSession(error);
          else {
            const index = this.queue.indexOf(job);
            if (index >= 0) { this.queue.splice(index, 1); job.reject(error); }
          }
        }, this.timeoutMs);
        this.queue.push(job); this.pump();
      });
    }

    pump() {
      if (this.active || !this.connected || !this.queue.length) return;
      if (this.nextId > 65535) {
        this.failSession(new Error('A parancsazonosítók elfogytak. Csatlakozz újra a vezérlőhöz.'));
        return;
      }
      const job = this.queue.shift();
      if (this.now() >= job.deadline) { this.clearTimer(job.timer); job.reject(new Error('A parancs várakozás közben lejárt.')); this.pump(); return; }
      const active = {job, id: this.nextId++, received: 0, total: 0, buffer: null, sending: true, reply: null};
      this.active = active;
      const generation = this.generation;
      (async () => {
        try {
          const chunkSize = this.info.maxFrame - HEADER;
          for (let offset = 0; offset < job.bytes.length; offset += chunkSize) {
            if (this.active !== active || generation !== this.generation) return;
            const count = Math.min(chunkSize, job.bytes.length - offset);
            const frame = new Uint8Array(HEADER + count), view = new DataView(frame.buffer);
            view.setUint8(0, 1); view.setUint8(1, 1);
            view.setUint16(2, active.id, true); view.setUint16(4, offset, true);
            view.setUint16(6, job.bytes.length, true); view.setUint16(8, count, true);
            frame.set(job.bytes.subarray(offset, offset + count), HEADER);
            await this.rx.writeValueWithResponse(frame);
            if (this.writePauseMs && offset + count < job.bytes.length)
              await new Promise(resolve => this.setTimer(resolve, this.writePauseMs));
          }
          if (this.active !== active) return;
          active.sending = false;
          if (active.reply) this.finish(active);
        } catch (error) {
          if (this.active === active && generation === this.generation)
            this.failSession(new Error('A Bluetooth-küldés nem sikerült. A parancsot nem küldjük újra. ' + (error.message || '')));
        }
      })();
    }

    receive(event) {
      const active = this.active;
      if (!active || !this.connected || event.target !== this.tx) return;
      try {
        const value = event.target.value;
        if (!value || value.byteLength < HEADER) throw new Error('Hiányos Bluetooth-válaszkeret.');
        const view = new DataView(value.buffer, value.byteOffset, value.byteLength);
        // Old replies cannot complete or corrupt a newer request.
        if (view.getUint16(2, true) !== active.id) return;
        const offset = view.getUint16(4, true), total = view.getUint16(6, true), count = view.getUint16(8, true);
        if (view.getUint8(0) !== 1 || view.getUint8(1) !== 2 || !count || value.byteLength !== HEADER + count ||
            value.byteLength > this.info.maxFrame || !total || total > this.info.responseLimit ||
            offset !== active.received || offset + count > total || active.buffer && total !== active.total)
          throw new Error('A Bluetooth-válasz sorrendje vagy mérete érvénytelen.');
        if (!active.buffer) { active.total = total; active.buffer = new Uint8Array(total); }
        active.buffer.set(new Uint8Array(value.buffer, value.byteOffset + HEADER, count), offset);
        active.received += count;
        if (active.received === active.total) {
          const reply = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(active.buffer));
          if (!reply || !Number.isInteger(reply.status) || reply.status < 100 || reply.status > 599 ||
              !reply.body || typeof reply.body !== 'object' || Array.isArray(reply.body)) throw new Error('A vezérlő válasza nem olvasható.');
          active.reply = reply;
          if (!active.sending) this.finish(active);
        }
      } catch (error) { this.failSession(error); }
    }

    finish(active) {
      if (this.active !== active) return;
      this.clearTimer(active.job.timer); this.active = null;
      const reply = active.reply;
      if (reply.status < 200 || reply.status >= 300 || reply.body.ok === false) active.job.reject(new Error(reply.body.error || 'A művelet nem sikerült.'));
      else active.job.resolve(reply.body);
      this.pump();
    }
  }

  root.AquaBleTransport = new BleTransport();
  if (typeof module !== 'undefined' && module.exports) module.exports = {BleTransport, UUIDS};
})(typeof window !== 'undefined' ? window : globalThis);
