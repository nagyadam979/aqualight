"""Two local AUTO lamps under distinct router and ESP-NOW failures.

Executable software specification, standard library only; virtual milliseconds,
not wall time, firmware, radio simulation, CPU scheduling, or a hardware proof.
Every output write belongs to a local control event. There is no network OFF.
This model checks link and reference handling. Its RAM-only dusk state is
used as a negative control for restart. The durable AUTO restart policy is
exercised separately in check_restart_light.py.

Public integration API: NetworkEvent, disconnection_events, ClockReference,
clock_usable_at. Expiry uses a half-open interval: exactly at expiry is invalid.
"""
from dataclasses import dataclass, field
import heapq
from itertools import permutations, product
from typing import Callable

DUSK_MS = 100
NEXT_DAY_MS = 200


@dataclass(frozen=True)
class NetworkEvent:
    at_ms: int
    link: str                  # wifi = terrarium/router; peer = local ESP-NOW
    connected: bool


def disconnection_events(kind: str, at_ms: int,
                         reconnect_ms: int | None = None) -> tuple[NetworkEvent, ...]:
    """Return independent link changes; never associates the strip to a router."""
    if kind not in ('wifi', 'peer', 'both'):
        raise ValueError(kind)
    if at_ms < 0 or (reconnect_ms is not None and reconnect_ms < at_ms):
        raise ValueError('invalid virtual outage interval')
    links = ('wifi', 'peer') if kind == 'both' else (kind,)
    events = [NetworkEvent(at_ms, link, False) for link in links]
    if reconnect_ms is not None:
        events += [NetworkEvent(reconnect_ms, link, True) for link in links]
    return tuple(events)


@dataclass(frozen=True)
class ClockReference:
    origin_ms: int | None = None
    expires_ms: int | None = None
    utc_offset_ms: int = 0

    def usable_at(self, now_ms: int) -> bool:
        return (self.origin_ms is not None and self.expires_ms is not None
                and self.origin_ms <= now_ms < self.expires_ms)


def clock_usable_at(reference: ClockReference, now_ms: int) -> bool:
    return reference.usable_at(now_ms)


@dataclass
class Lamp:
    name: str
    reference: ClockReference = field(default_factory=ClockReference)
    pwm: int = 0
    boot: int = 0
    closed_intervals: set[int] = field(default_factory=set)
    writes: list[tuple[int, int, str]] = field(default_factory=list)

    def control(self, now_ms: int) -> None:
        desired = 0
        if self.reference.usable_at(now_ms):
            utc = now_ms + self.reference.utc_offset_ms
            day, phase = divmod(utc, NEXT_DAY_MS)
            if phase >= DUSK_MS:
                self.closed_intervals.add(day)
            elif day not in self.closed_intervals:
                desired = 100
        self.pwm = desired
        self.writes.append((now_ms, desired, 'local_control'))

    def reboot(self, now_ms: int) -> None:
        self.boot += 1
        self.reference = ClockReference()
        self.closed_intervals.clear()
        self.pwm = 0
        self.writes.append((now_ms, 0, 'local_boot'))


@dataclass
class Request:
    token: tuple[int, int]
    deadline_ms: int
    link: str
    phase: str
    consumed: bool = False


class Engine:
    """Stable discrete event engine. Insertion order resolves equal timestamps."""
    def __init__(self):
        self.now_ms = 0
        self._serial = 0
        self._events = []
        self.trace = []
        self.after_event = None

    def schedule(self, at_ms: int, label: str, callback: Callable[[], None]):
        if at_ms < self.now_ms:
            raise ValueError('cannot schedule into the past')
        self._serial += 1
        heapq.heappush(self._events, (at_ms, self._serial, label, callback))

    def run(self):
        while self._events:
            self.now_ms, _, label, callback = heapq.heappop(self._events)
            callback()
            self.trace.append((self.now_ms, label))
            if self.after_event is not None:
                self.after_event()


class NetworkModel:
    def __init__(self, reference: ClockReference = ClockReference()):
        self.engine = Engine()
        self.lamps = {name: Lamp(name, reference) for name in ('terrarium', 'strip')}
        self.links = {'wifi': True, 'peer': True}
        self.router_associated = {'terrarium': True, 'strip': False}
        self.requests: dict[str, Request] = {}
        self.serial = 0
        self.accepted = []
        self.acked = 0

    @property
    def now_ms(self):
        return self.engine.now_ms

    def open_request(self, board: str, phase: str, deadline_ms: int):
        link = 'peer' if phase == 'peer_time' else 'wifi'
        if link == 'wifi' and board != 'terrarium':
            raise ValueError('only terrarium associates with the router')
        if not self.links[link] or deadline_ms <= self.now_ms:
            raise ValueError('request requires a connected link and future deadline')
        self.serial += 1
        token = (self.lamps[board].boot, self.serial)
        self.requests[board] = Request(token, deadline_ms, link, phase)
        return token

    def change_link(self, event: NetworkEvent):
        assert self.now_ms == event.at_ms
        self.links[event.link] = event.connected
        if event.link == 'wifi':
            self.router_associated['terrarium'] = event.connected
        assert not self.router_associated['strip']
        if not event.connected:
            for request in self.requests.values():
                if request.link == event.link:
                    request.consumed = True

    def timeout(self, board: str):
        request = self.requests.get(board)
        if request and self.now_ms >= request.deadline_ms:
            request.consumed = True

    def reply(self, board: str, token: tuple[int, int], reference: ClockReference):
        request = self.requests.get(board)
        if (request is None or request.consumed or request.token != token
                or token[0] != self.lamps[board].boot
                or self.now_ms >= request.deadline_ms or not self.links[request.link]):
            return False
        if request.phase != 'ack' and not reference.usable_at(self.now_ms):
            return False
        request.consumed = True
        if request.phase == 'ack':
            self.acked += 1
        else:
            self.lamps[board].reference = reference
        self.accepted.append((self.now_ms, board, request.phase, token))
        # Applying a reply does not touch PWM. The local cycle applies its state.
        return True

    def schedule_links(self, events):
        for event in events:
            self.engine.schedule(event.at_ms, f'{event.link}={event.connected}',
                                 lambda event=event: self.change_link(event))

    def control_both(self):
        for lamp in self.lamps.values():
            lamp.control(self.now_ms)



@dataclass(frozen=True)
class ReferenceSnapshot:
    at_ms: int
    reference: ClockReference
    boot: int
    wifi_connected: bool
    peer_connected: bool
    phase: str
    pending: bool


@dataclass
class NetworkTimeline:
    snapshots: dict[str, list[ReferenceSnapshot]]
    trace: list[tuple[int, str]]
    accepted: tuple
    acked: int

    def state_at(self, board: str, at_ms: int) -> ReferenceSnapshot:
        if at_ms < 0:
            raise ValueError('virtual time starts at zero')
        # Same-ms queries expose the state after every callback at that instant.
        return next(snapshot for snapshot in reversed(self.snapshots[board])
                    if snapshot.at_ms <= at_ms)

    def reference_at(self, board: str, at_ms: int) -> ClockReference:
        return self.state_at(board, at_ms).reference

    def usable_at(self, board: str, at_ms: int) -> bool:
        return self.reference_at(board, at_ms).usable_at(at_ms)


def run_network(*, outage_kind='both', outage_ms=99, reconnect_ms=None,
                phase='ntp', reply_ms=None, timeout_ms=100,
                initial_references=None, reply_reference=None,
                reply_token_current=True, reboot_board=None,
                reboot_ms=None) -> NetworkTimeline:
    """Run once; query actual per-board reference snapshots at any virtual ms.

    ntp/ack runs on terrarium, peer_time on strip. The reply's reference is
    canonical (origin and absolute expiry are not reset when it is relayed).
    Queries after the last event still age the last reference independently.
    """
    if phase not in ('ntp', 'ack', 'peer_time'):
        raise ValueError(phase)
    model = NetworkModel(ClockReference(0, 1000))
    if initial_references is not None:
        for board, reference in initial_references.items():
            model.lamps[board].reference = reference
    board = 'strip' if phase == 'peer_time' else 'terrarium'
    token = model.open_request(board, phase, timeout_ms)
    if not reply_token_current:
        token = (token[0], token[1] - 1)
    snapshots = {name: [] for name in model.lamps}
    def capture():
        for name, lamp in model.lamps.items():
            request = model.requests.get(name)
            snapshots[name].append(ReferenceSnapshot(
                model.now_ms, lamp.reference, lamp.boot,
                model.links['wifi'], model.links['peer'],
                request.phase if request else 'idle',
                bool(request and not request.consumed
                     and request.token[0] == lamp.boot
                     and model.now_ms < request.deadline_ms)))
    capture()
    model.engine.after_event = capture
    model.schedule_links(disconnection_events(outage_kind, outage_ms, reconnect_ms))
    if reboot_board is not None:
        if reboot_ms is None:
            raise ValueError('reboot_ms is required with reboot_board')
        model.engine.schedule(reboot_ms, 'reboot_' + reboot_board,
                              lambda: model.lamps[reboot_board].reboot(model.now_ms))
    if reply_ms is not None:
        reference = reply_reference if reply_reference is not None else ClockReference(0, 1000)
        def deliver():
            model.reply(board, token, reference)
        model.engine.schedule(reply_ms, 'reply', deliver)
        model.engine.schedule(reply_ms, 'duplicate_reply', deliver)
    model.engine.schedule(timeout_ms, 'timeout', lambda: model.timeout(board))
    model.engine.run()
    return NetworkTimeline(snapshots, model.engine.trace,
                           tuple(model.accepted), model.acked)


def check_timeline():
    local = ClockReference(0, 101)
    timeline = run_network(initial_references={'terrarium': local, 'strip': local},
                           reply_ms=100, reply_reference=ClockReference(100, 500))
    for board in ('terrarium', 'strip'):
        assert timeline.usable_at(board, 100)
        assert not timeline.usable_at(board, 101)
        assert timeline.reference_at(board, 100) == local
        assert not timeline.state_at(board, 100).wifi_connected
        assert not timeline.state_at(board, 100).peer_connected
    assert not timeline.accepted
    # Success really changes snapshots; reboot only clears the affected board.
    recovered = run_network(outage_ms=101, reply_ms=98,
                            initial_references={'terrarium': ClockReference(),
                                                'strip': ClockReference()},
                            reply_reference=ClockReference(98, 150),
                            reboot_board='terrarium', reboot_ms=102)
    assert not recovered.usable_at('terrarium', 97)
    assert recovered.usable_at('terrarium', 98)
    assert recovered.usable_at('terrarium', 101)
    assert not recovered.usable_at('terrarium', 102)
    assert not recovered.usable_at('strip', 98)
    assert len(recovered.accepted) == 1


def check_outages():
    cases = 0
    references = {
        'valid': ClockReference(0, 1000),
        'expires_before': ClockReference(0, 99),
        'expires_at': ClockReference(0, 100),
        'expires_after': ClockReference(0, 101),
        'unknown_at_boot': ClockReference(),
    }
    for kind, outage_ms, phase, (_, reference), order in product(
            ('wifi', 'peer', 'both'), (99, 100, 101), ('ntp', 'ack'),
            references.items(), permutations(('outage', 'control', 'reply', 'timeout'))):
        model = NetworkModel(reference)
        token = model.open_request('terrarium', phase, 100)
        model.engine.schedule(98, 'local_control', model.control_both)
        for event in order:
            if event == 'outage':
                model.schedule_links(disconnection_events(kind, outage_ms))
            elif event == 'control':
                model.engine.schedule(100, event, model.control_both)
            elif event == 'reply':
                # A response at the absolute deadline is invalid even before timeout.
                model.engine.schedule(100, event, lambda: model.reply(
                    'terrarium', token, ClockReference(100, 1000, -50)))
            else:
                model.engine.schedule(100, event, lambda: model.timeout('terrarium'))
        model.engine.schedule(102, 'local_control', model.control_both)
        model.engine.run()
        for lamp in model.lamps.values():
            expected_before = 100 if reference.usable_at(98) else 0
            assert [(at, value) for at, value, _ in lamp.writes] == [
                (98, expected_before), (100, 0), (102, 0)]
        assert all(lamp.reference == reference for lamp in model.lamps.values())
        assert not model.accepted and model.acked == 0
        assert all(source.startswith('local_') for lamp in model.lamps.values()
                   for _, _, source in lamp.writes)
        cases += 1
    return cases


def check_callbacks():
    cases = 0
    for board, phase, reply_ms, cause in product(
            ('terrarium', 'strip'), ('ntp', 'ack', 'peer_time'),
            (99, 100, 101), ('success', 'disconnect', 'expired', 'old_window', 'reboot')):
        if board == 'strip' and phase != 'peer_time':
            continue
        model = NetworkModel()
        token = model.open_request(board, phase, 100)
        link = 'peer' if phase == 'peer_time' else 'wifi'
        if cause == 'disconnect':
            model.schedule_links(disconnection_events(link, 98, 99))
        elif cause == 'old_window':
            model.open_request(board, phase, 100)
        elif cause == 'reboot':
            model.engine.schedule(98, 'reboot', lambda: model.lamps[board].reboot(98))
        reference = ClockReference(0, reply_ms if cause == 'expired' else 1000)
        results = []
        def reply():
            before = model.lamps[board].pwm
            results.append(model.reply(board, token, reference))
            assert model.lamps[board].pwm == before
        model.engine.schedule(reply_ms, 'reply', reply)
        model.engine.schedule(reply_ms, 'duplicate_reply', reply)
        model.engine.schedule(101, 'timeout', lambda: model.timeout(board))
        model.engine.schedule(102, 'control', model.control_both)
        model.engine.run()
        expected = reply_ms < 100 and cause == 'success'
        if phase == 'ack' and cause == 'expired':
            expected = reply_ms < 100  # ACK does not establish a time reference.
        assert results == [expected, False], (board, phase, reply_ms, cause, results)
        assert len(model.accepted) == int(expected)
        assert model.acked == int(expected and phase == 'ack')
        assert model.lamps[board].pwm == 0
        cases += 1
    return cases


def ram_only_restart_mutant_witness():
    model = NetworkModel(ClockReference(0, 1000))
    model.engine.schedule(99, 'daylight', model.control_both)
    model.engine.schedule(100, 'dusk', model.control_both)
    model.engine.schedule(101, 'reboot_strip', lambda: model.lamps['strip'].reboot(101))
    model.schedule_links(disconnection_events('both', 102, 103))
    def recover():
        for board, phase in (('terrarium', 'ntp'), ('strip', 'peer_time')):
            token = model.open_request(board, phase, 110)
            assert model.reply(board, token, ClockReference(104, 1000, -50))
        model.control_both()
        assert model.lamps['terrarium'].pwm == 0  # RAM remembers closed interval.
        assert model.lamps['strip'].pwm == 100    # Reboot has no persisted night latch.
    model.engine.schedule(104, 'fresh_recovery', recover)
    model.engine.schedule(150, 'corrected_dusk', model.control_both)
    model.engine.schedule(250, 'next_light_interval', model.control_both)
    model.engine.run()
    assert all(lamp.pwm == 100 for lamp in model.lamps.values())
    return model.engine.trace


def packet_wait_mutant_witness():
    """Short negative witness: waiting for a packet leaves lit PWM after dusk."""
    model = NetworkModel(ClockReference(0, 1000))
    model.engine.schedule(99, 'daylight', model.control_both)
    model.schedule_links(disconnection_events('both', 100))
    def packet_driven_dusk():
        model.lamps['terrarium'].control(model.now_ms)
        if model.links['peer']:
            model.lamps['strip'].control(model.now_ms)
    model.engine.schedule(100, 'strip waits for remote OFF', packet_driven_dusk)
    model.engine.run()
    assert model.lamps['terrarium'].pwm == 0
    assert model.lamps['strip'].pwm == 100
    return model.engine.trace


def main():
    check_timeline()
    outages = check_outages()
    callbacks = check_callbacks()
    restart_mutant = ram_only_restart_mutant_witness()
    witness = packet_wait_mutant_witness()
    print(f'PASS: {outages} outage/order cases; {callbacks} callback cases')
    print('RAM-ONLY RESTART MUTANT: delayed clock relights the restarted strip')
    print('PACKET-WAIT MUTANT:', witness)
    return {'outage_cases': outages, 'callback_cases': callbacks,
            'recovery_trace': restart_mutant, 'mutant_witness': witness}


if __name__ == '__main__':
    main()
