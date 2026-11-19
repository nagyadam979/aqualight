"""Integer UTC interval propagation through local clocks and ESP-NOW replies.

Units are microseconds. This specification assumes bounded oscillator rate
and timestamp quantization, an independently bounded external UTC sample, and
a sender that snapshots the current projection between request and response.
It does not assume equal outbound and inbound delays.
"""

from dataclasses import dataclass
from itertools import product

from check_restart_light import Channel, Estimate, Window, visit


PPM = 1_000_000
MAX_US = (1 << 63) - 1


@dataclass(frozen=True)
class Tick:
    boot: int
    us: int


@dataclass(frozen=True)
class Bound:
    origin: int
    earliest: int
    latest: int
    age_upper: int

    def advance(self, elapsed, ttl):
        lo, hi = elapsed
        lower, upper, age = self.earliest + lo, self.latest + hi, self.age_upper + hi
        if (self.origin <= 0 or not 0 <= self.earliest <= self.latest <= MAX_US
                or not 0 <= self.age_upper or not 0 <= lo <= hi
                or not 0 <= age < ttl <= MAX_US or upper > MAX_US):
            return None
        return Bound(self.origin, lower, upper, age)

    def estimate(self):
        return Estimate(self.earliest + (self.latest - self.earliest) // 2,
                        self.earliest, self.latest)


@dataclass(frozen=True)
class Reference:
    anchor: Tick
    bound: Bound

    def at(self, tick, drift_ppm, ttl, quantization=1):
        elapsed = elapsed_bounds(self.anchor, tick, drift_ppm, quantization)
        return self.bound.advance(elapsed, ttl) if elapsed is not None else None


@dataclass(frozen=True)
class Request:
    started: Tick
    sequence: int
    timeout: int


@dataclass(frozen=True)
class Reply:
    boot: int
    sequence: int
    snapshot: Bound


def elapsed_bounds(start, end, drift_ppm, quantization=1):
    """Translate a local counter delta into a true elapsed-time interval."""
    if (drift_ppm is None or not 0 <= drift_ppm < PPM or quantization < 0
            or start.boot != end.boot or not 0 <= start.us <= end.us <= MAX_US):
        return None
    ticks = end.us - start.us
    lower = max(0, ticks - quantization) * PPM // (PPM + drift_ppm)
    upper, remainder = divmod((ticks + quantization) * PPM, PPM - drift_ppm)
    upper += bool(remainder)
    return (lower, upper) if upper <= MAX_US else None


def receive(request, reply, now, drift_ppm, ttl, quantization=1):
    """Validate the current pending token, then anchor at this receipt.

    The caller consumes the successful request once. The sender already
    projected its reference to the instant it produced reply.snapshot.
    """
    if (reply.boot != request.started.boot or reply.sequence != request.sequence
            or now.us - request.started.us >= request.timeout):
        return None
    elapsed = elapsed_bounds(request.started, now, drift_ppm, quantization)
    if elapsed is None:
        return None
    snapshot = reply.snapshot.advance((0, elapsed[1]), ttl)
    return Reference(now, snapshot) if snapshot is not None else None


def direct_sample(origin, transmit_utc, source_error):
    """Initial UTC envelope at the matched external reply's transmit instant."""
    if source_error is None or source_error < 0:
        return None
    return Bound(origin, transmit_utc - source_error, transmit_utc + source_error, 0)


def device_tick(boot, actual, rate=0, phase=0):
    """Idealized independent quantized counter for the oracle."""
    return Tick(boot, (actual * (PPM + rate) + phase) // PPM)


def check_elapsed():
    cases = 0
    for drift, duration, fraction, direction in product(
            (0, 1, 60, 100_000), (0, 1, 2, 9, 99, 1000, 1_000_001),
            (0, 1, PPM // 2, PPM - 1), (-1, 0, 1)):
        before = device_tick(7, 123, direction * drift, fraction)
        after = device_tick(7, 123 + duration, direction * drift, fraction)
        lo, hi = elapsed_bounds(before, after, drift)
        assert lo <= duration <= hi
        cases += 1
    return cases


def check_relays():
    cases = 0
    drift, ttl, initial_utc = 60, 1_000_000, 1_000_000_000
    for outbound, inbound, process, rate_a, rate_b, hops in product(
            (0, 1, 40, 3000), (0, 1, 50, 1600), (0, 10, 60),
            (-drift, 0, drift), (-drift, 0, drift), (1, 2, 4)):
        rates = (rate_a, rate_b)
        phases = (1, PPM - 3)
        prior = Reference(Tick(1, 0), Bound(81, initial_utc - 3, initial_utc + 2, 0))
        actual, source = 120, 0
        for hop in range(hops):
            target = 1 - source
            departure = device_tick(target + 1, actual, rates[target], phases[target])
            sample_at = actual + outbound + process
            sample_tick = device_tick(source + 1, sample_at, rates[source], phases[source])
            snapshot = prior.at(sample_tick, drift, ttl)
            assert snapshot is not None
            assert snapshot.earliest <= initial_utc + sample_at <= snapshot.latest
            assert snapshot.age_upper >= sample_at
            actual = sample_at + inbound
            arrival = device_tick(target + 1, actual, rates[target], phases[target])
            req = Request(departure, hop, ttl)
            reply = Reply(target + 1, hop, snapshot)
            updated = receive(req, reply, arrival, drift, ttl)
            assert updated is not None
            assert updated.bound.origin == prior.bound.origin == 81
            assert updated.bound.age_upper >= max(actual, snapshot.age_upper, prior.bound.age_upper)
            assert updated.bound.earliest <= initial_utc + actual <= updated.bound.latest
            prior, source = updated, target
            actual += 17
        cases += 1
    return cases


def check_direct_source():
    cases = 0
    for outbound, process, inbound, error, rate, offset in product(
            (1, 100, 300), (0, 37), (1, 200, 700), (0, 30),
            (-60, 0, 60), (-1, 0, 1)):
        source_sent = 1000 + outbound + process
        actual_arrival = source_sent + inbound
        request = Request(device_tick(7, 1000, rate, 11), 12, 2000)
        reply = Reply(7, 12, direct_sample(3, source_sent + offset * error, error))
        bound = receive(request, reply, device_tick(7, actual_arrival, rate, 11), 60, 2000)
        assert bound is not None and bound.bound.earliest <= actual_arrival <= bound.bound.latest
        assert bound.bound.age_upper >= actual_arrival - source_sent
        cases += 1
    assert direct_sample(3, 1234, None) is None
    return cases + 1


def check_rejection():
    cases = 0
    for boot, sequence, age, arrival in product((7, 8), (13, 14), (0, 8, 9, 10, 11),
                                               (99, 100, 101, 102, 103, 104)):
        req = Request(Tick(7, 100), 13, 4)
        reply = Reply(boot, sequence, Bound(1, 1000, 1000, age))
        accepted = receive(req, reply, Tick(7, arrival), 0, 10, quantization=0)
        expected = boot == 7 and sequence == 13 and 100 <= arrival < 104 and age + arrival - 100 < 10
        assert (accepted is not None) == expected
        cases += 1

    reference = Reference(Tick(7, 0), Bound(1, 1000, 1000, 8))
    assert reference.at(Tick(7, 1), 0, 10, quantization=0).age_upper == 9
    assert reference.at(Tick(7, 2), 0, 10, quantization=0) is None
    assert reference.at(Tick(8, 2), 0, 10) is None
    assert reference.at(Tick(7, 2), None, 10) is None
    assert elapsed_bounds(Tick(7, 0), Tick(7, MAX_US), 1000) is None
    assert elapsed_bounds(Tick(7, MAX_US), Tick(7, 0), 0) is None
    assert elapsed_bounds(Tick(7, 0), Tick(7, 1), PPM) is None
    assert receive(Request(Tick(7, 100), 13, 4),
                   Reply(7, 13, Bound(1, MAX_US, MAX_US, 0)), Tick(7, 101), 0, 10) is None
    assert Bound(0, 100, 200, 0).advance((0, 0), 10) is None
    assert Bound(1, 101, 100, 0).advance((0, 0), 10) is None
    assert Bound(1, 100, 200, -1).advance((0, 0), 10) is None
    return cases + 11


def check_lighting():
    cases = 0
    first, skipped, following = Window(10, 30), Window(40, 100), Window(120, 160)
    for known, actual, outbound, inbound, error in product(
            (False, True), (38, 39, 40, 41, 98, 99, 100, 101, 102, 120, 121, 124),
            (0, 1, 3), (0, 1, 3), (0, 1, 2)):
        departure, sample = actual - outbound - inbound, actual - inbound
        req = Request(Tick(7, departure), 13, 10)
        reply = Reply(7, 13, Bound(1, sample - error, sample + error, 1))
        ref = receive(req, reply, Tick(7, actual), 0 if known else None, 20, quantization=0)
        channel = Channel((first, skipped, following))
        assert visit(channel, Estimate(20, 20, 20)) == 100
        assert visit(channel, Estimate(30, 30, 30)) == 0
        channel.reset()
        interval = ref.bound if ref is not None else None
        if interval is not None:
            assert interval.earliest <= actual <= interval.latest
        expected = interval is not None and any(
            window.start <= interval.earliest and interval.latest < window.end
            for window in (skipped, following))
        assert visit(channel, interval.estimate() if interval is not None else None) == 100 * expected
        if channel.pwm:
            assert any(window.start <= actual < window.end for window in (skipped, following))
        cases += 1

    # Skipped interval already ended. Full RTT covers the actual instant;
    # halved RTT incorrectly permits a previously unlit interval to start.
    req = Request(Tick(7, 80), 13, 30)
    snap = Bound(1, 81, 81, 0)
    ref = receive(req, Reply(7, 13, snap), Tick(7, 104), 0, 30, quantization=0)
    assert ref is not None and ref.bound.latest == 105
    channel = Channel((skipped,))
    assert visit(channel, ref.bound.estimate()) == 0
    half_rtt = snap.advance((0, 12), 30)
    assert visit(channel, half_rtt.estimate()) == 100
    return cases


def check_age_cannot_restart():
    ttl = 50
    a = Reference(Tick(7, 0), Bound(1, 1000, 1000, 0))
    snap = a.at(Tick(7, 40), 0, ttl, quantization=0)
    request = Request(Tick(8, 100), 31, 20)
    b = receive(request, Reply(8, 31, snap), Tick(8, 109), 0, ttl, quantization=0)
    assert b.bound.age_upper == 49 and b.bound.origin == a.bound.origin
    echo = b.at(Tick(8, 109), 0, ttl, quantization=0)
    request = Request(Tick(7, 49), 32, 20)
    assert receive(request, Reply(7, 32, echo), Tick(7, 50), 0, ttl, quantization=0) is None
    falsely_fresh = Bound(echo.origin, echo.earliest, echo.latest, 0)
    assert receive(request, Reply(7, 32, falsely_fresh), Tick(7, 50), 0, ttl, quantization=0)


if __name__ == '__main__':
    print(f'PASS: {check_elapsed()} quantized oscillator-delta cases')
    print(f'PASS: {check_direct_source()} direct external reference cases')
    print(f'PASS: {check_relays()} bound propagation cases, including four-hop relays')
    print(f'PASS: {check_rejection()} token, expiry, counter and input boundary cases')
    print(f'PASS: {check_lighting()} composed clock/PWM cases; half-RTT mutant relights skipped interval')
    check_age_cannot_restart()
    print('PASS: echoed original expires at TTL; resetting age falsely accepts the same echo')
