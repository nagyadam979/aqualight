"""Executable AUTO policy for reset with a slow peer clock.

Schedule intervals and time estimates use the same integer UTC axis. The
estimates can contain a slow nominal time while bounding the true instant.
Some probes also evaluate deliberate clock rollbacks to test the stored limit.
Storage slots represent validated complete values; None is an unreadable slot.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import product
from zoneinfo import ZoneInfo

from check_dusk_network import ClockReference, NetworkModel


@dataclass(frozen=True)
class Window:
    start: int
    end: int


@dataclass(frozen=True)
class Estimate:
    nominal: int
    earliest: int
    latest: int


@dataclass
class Channel:
    windows: tuple[Window, ...]
    slots: list[int | None] = field(default_factory=lambda: [-1, -1])
    boot: int = 0
    request: tuple[int, int, Window] | None = None
    serial: int = 0
    active: Window | None = None
    pwm: int = 0
    use_uncertainty: bool = True

    def reset(self):
        self.boot += 1
        self.request = self.active = None
        self.pwm = 0

    def persisted_floor(self):
        return max((value for value in self.slots if value is not None), default=None)

    def cycle(self, time):
        was_lit = self.pwm > 0
        self.pwm = 0
        # Unknown time is dark. Keep an existing live grant until a valid time
        # actually places us outside it; any new activation checks the bounds.
        if time is None:
            self.request = None
            return
        window = next((item for item in self.windows
                       if item.start <= time.nominal < item.end), None)
        certain = window is not None and (
            not self.use_uncertainty or window.start <= time.earliest <= time.latest < window.end)
        floor = self.persisted_floor()
        if (window is not None and self.active == window and floor == window.start
                and (was_lit or certain)):
            self.pwm = 100
            return
        self.active = None
        if not certain or floor is None:
            self.request = None
            return
        if self.request is not None and self.request[2] == window:
            if self.slots == [window.start, window.start]:
                self.active, self.request = window, None
                self.pwm = 100
            return
        self.request = None
        if window.start > floor:
            self.serial += 1
            self.request = (self.boot, self.serial, window)

    def write_copy(self, request, *, cut=False):
        if request is None or request != self.request:
            return
        start = request[2].start
        floor = self.persisted_floor()
        if floor is None or floor > start or self.slots == [start, start]:
            return
        # Leave the newest valid slot untouched until the other copy is valid.
        slot = min(range(2), key=lambda index: -2 if self.slots[index] is None
                   else self.slots[index])
        self.slots[slot] = None if cut else start


def visit(channel, time, writes=2):
    channel.cycle(time)
    for _ in range(writes):
        channel.write_copy(channel.request)
    channel.cycle(time)
    return channel.pwm


def at(now, lag=0, margin=0):
    return Estimate(now - lag, now - lag - margin, now + margin)


def channel_plan(offset):
    return tuple(Window(start + offset, end + offset)
                 for start, end in ((10, 30), (40, 60), (70, 90), (110, 130)))


def check_board_resets():
    cases = 0
    for reset, anchor, shift, lag, margin, prior in product(
            ((0, 1), (2,), (0, 1, 2)), range(3), (-1, 0, 1),
            (0, 1, 3, 8), (0, 1), range(3)):
        channels = [Channel(channel_plan(offset)) for offset in (0, 2, -2)]
        for channel in channels:
            for window in channel.windows[:prior + 1]:
                assert visit(channel, at(window.start + 2)) == 100
                if window != channel.windows[prior]:
                    assert visit(channel, at(window.end)) == 0
        now = channels[anchor].windows[prior].end + shift
        for index, channel in enumerate(channels):
            window = channel.windows[prior]
            before = visit(channel, at(now))
            if index in reset:
                channel.reset()
                assert channel.pwm == 0
                assert visit(channel, at(now, lag, margin)) == 0
                assert visit(channel, at(window.start + 2, lag, margin)) == 0
            else:
                assert channel.pwm == before == (100 if now < window.end else 0)
        for index, channel in enumerate(channels):
            following = channel.windows[prior + 1]
            assert visit(channel, at(following.start + lag + margin, lag, margin)) == 100
            assert visit(channel, at(following.end + lag, lag, margin)) == 0
            if index in reset:
                channel.reset()
            assert visit(channel, at(following.end - 1)) == 0
        cases += 1
    return cases


def check_storage_and_skips():
    cases = 0
    first, skipped, following = channel_plan(0)[:3]
    for writes, torn, loss in product(range(3), (False, True), (None, 0, 1)):
        channel = Channel(channel_plan(0))
        assert visit(channel, at(first.start + 1)) == 100
        channel.cycle(at(first.end))
        channel.cycle(at(following.start + 1))
        stale = channel.request
        for _ in range(writes):
            channel.write_copy(stale)
        if torn:
            channel.write_copy(stale, cut=True)
        channel.reset()
        if loss is not None:
            channel.slots[loss] = None
        recovered = list(channel.slots)
        channel.write_copy(stale)
        assert channel.slots == recovered
        assert visit(channel, at(first.end - 1)) == 0
        # A skipped interval lies after the stored first interval. A lagged
        # clock selects it, but the estimate still contains its dark boundary.
        assert visit(channel, at(skipped.end, lag=2)) == 0
        if all(value is None for value in recovered):
            assert visit(channel, at(following.start + 5)) == 0
        elif following.start in recovered:
            assert visit(channel, at(following.start + 5)) == 0
        else:
            assert visit(channel, at(following.start + 5)) == 100
        cases += 1

    for copies in (0, 1, 2):
        channel = Channel(channel_plan(0))
        assert visit(channel, at(first.start + 1), writes=copies) == (100 if copies == 2 else 0)
        request = channel.request
        assert visit(channel, at(first.end)) == 0
        channel.write_copy(request)
        assert channel.pwm == 0
        cases += 1

    running = Channel(channel_plan(0))
    assert visit(running, at(first.start + 1)) == 100
    assert visit(running, None) == 0
    assert visit(running, at(first.start + 3)) == 100
    assert visit(running, at(first.end - 1, margin=1)) == 100
    assert visit(running, None) == 0
    assert visit(running, at(first.end, lag=1)) == 0
    assert visit(running, at(first.end - 2)) == 0
    cases += 1

    mutant = Channel(channel_plan(0), use_uncertainty=False)
    assert visit(mutant, at(first.start + 1)) == 100
    assert visit(mutant, at(skipped.end)) == 0
    mutant.reset()
    assert visit(mutant, at(skipped.end, lag=2)) == 100
    ram_only = Channel(channel_plan(0))
    assert visit(ram_only, at(first.start + 1)) == 100
    assert visit(ram_only, at(first.end)) == 0
    ram_only.reset()
    ram_only.slots = [-1, -1]  # Old policy forgot previously enabled segments.
    assert visit(ram_only, at(first.end - 1)) == 100
    return cases


def utc_second(local, zone):
    # Use the earlier UTC instant of a repeated boundary. If a civil minute
    # is missing in spring, advance to the first existing wall-clock minute.
    while True:
        candidates = [local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                      for fold in (0, 1)]
        valid = [value for value in candidates
                 if value.astimezone(zone).replace(tzinfo=None) == local]
        if valid:
            return int(min(valid).timestamp())
        local += timedelta(minutes=1)


def check_calendar():
    zone = ZoneInfo('Europe/Budapest')
    start = datetime(2026, 10, 24, 23)
    end = datetime(2026, 10, 25, 2, 30)
    overnight = Window(utc_second(start, zone), utc_second(end, zone))
    later = Window(utc_second(datetime(2026, 10, 25, 4), zone),
                   utc_second(datetime(2026, 10, 25, 5), zone))
    channel = Channel((overnight, later))
    assert visit(channel, at(overnight.start + 1)) == 100
    midnight = utc_second(datetime(2026, 10, 25), zone)
    assert visit(channel, at(midnight)) == 100 and channel.slots == [overnight.start] * 2
    assert visit(channel, at(overnight.end)) == 0
    channel.reset()
    assert visit(channel, at(overnight.end, lag=2)) == 0
    repeated_0205 = int(datetime(2026, 10, 25, 2, 5, tzinfo=zone, fold=1).timestamp())
    assert overnight.end < repeated_0205 < later.start
    assert visit(channel, at(repeated_0205)) == 0
    assert visit(channel, at(later.start + 1)) == 100
    gap_start = datetime(2026, 3, 29, 2, 5)
    gap_end = datetime(2026, 3, 29, 2, 45)
    assert utc_second(gap_start, zone) == utc_second(gap_end, zone)
    assert utc_second(gap_end, zone) == utc_second(datetime(2026, 3, 29, 3), zone)
    return 2


def check_accepted_peer_reply():
    network = NetworkModel(ClockReference(0, 1000))
    channel = Channel((Window(10, 100), Window(150, 190)))
    assert visit(channel, at(99)) == 100
    assert visit(channel, at(100)) == 0

    def restart():
        network.lamps['strip'].reboot(network.now_ms)
        channel.reset()
        assert visit(channel, None) == 0

    def delayed_reply():
        token = network.open_request('strip', 'peer_time', 110)
        assert network.reply('strip', token, ClockReference(102, 1000, -4))
        ref = network.lamps['strip'].reference
        assert ref.usable_at(network.now_ms)
        nominal = network.now_ms + ref.utc_offset_ms
        assert nominal == 98 and channel.windows[0].start <= nominal < channel.windows[0].end
        assert visit(channel, Estimate(nominal, nominal, network.now_ms)) == 0

    def next_interval():
        ref = network.lamps['strip'].reference
        now = network.now_ms
        assert ref.usable_at(now)
        assert visit(channel, at(now, lag=-ref.utc_offset_ms)) == 100

    network.engine.schedule(101, 'restart_strip', restart)
    network.engine.schedule(102, 'accepted_peer_time', delayed_reply)
    network.engine.schedule(154, 'next_interval', next_interval)
    network.engine.run()


if __name__ == '__main__':
    check_accepted_peer_reply()
    print('PASS: ESP-NOW model accepts delayed reply after reboot; no relight')
    print(f'PASS: {check_board_resets()} two-board reset/clock combinations')
    print(f'PASS: {check_storage_and_skips()} commit/cut/skip cases; two mutants reproduced')
    print(f'PASS: {check_calendar()} calendar traces (midnight, autumn fold, spring gap)')
