"""Exercise a failed OTA trial, an accepted slow clock and real flash admission."""

from itertools import product

from check_clock_bounds import Bound, Reply, Request, Tick, receive
from check_dusk_budget import PERIOD_MS
from check_flash_progress import Authorization, run_flash
from check_restart_light import Channel, Window
from check_rollback_policy import (
    Board, Installed, arm, armed_invariant, begin, image_set, reboot, select_trial,
    start_segment, verify,
)


def check():
    cases = 0
    for name, cut, flash_bound, retry in product(
            ('terrarium', 'strip'), ('writing', 'pending', 'trial'), (29, 97, 98, None),
            (0, 1300)):
        channels = 2 if name == 'terrarium' else 1
        old = Window(200_000, 1_000_000)
        _, bridge, release = image_set(name)
        original = arm(Board(name, (Installed(bridge, True), Installed(bridge, True))))
        original, lit = start_segment(original, old.start)
        assert lit
        original, lit = start_segment(original, None)
        assert not lit
        update = begin(original, release)
        if cut != 'writing':
            update = select_trial(verify(update))
        if cut == 'trial':
            update = reboot(update)
        restarted = reboot(update)
        if cut == 'pending':
            assert restarted.phase == 'trial'
            restarted = reboot(restarted)
        armed_invariant(restarted)
        assert restarted.running_image == original.running_image
        assert restarted.floor == old.start

        # Real receipt is at 1020 ms. The latest UTC estimate contains it,
        # even though the accepted nominal estimate still falls before dusk.
        receipt = 1020
        origin = Bound(1, 900_000, 900_000, 100_000)
        request = Request(Tick(2, 850_000), 1, 250_000)
        reference = receive(request, Reply(2, 1, origin), Tick(2, receipt * 1000),
                            0, 10_000_000, quantization=0)
        assert reference is not None

        def estimate(ms):
            bound = reference.at(Tick(2, ms * 1000), 0, 10_000_000, quantization=0)
            return bound.estimate()

        outputs, jobs = [], []
        for index in range(channels):
            upcoming = Window(1_500_000 + 100_000 * index, 2_600_000 + 100_000 * index)
            # The compatible rollback reads the two current durable values.
            channel = Channel((old, upcoming), slots=[restarted.floor, restarted.floor])
            channel.reset()
            late = estimate(receipt)
            assert late.nominal < old.end <= late.latest
            channel.cycle(late)
            assert channel.pwm == 0 and channel.request is None
            first_tick = next(ms for ms in range(1100, 3000, PERIOD_MS)
                              if estimate(ms).earliest >= upcoming.start)
            first_unsafe = next(ms for ms in range(first_tick, 3000)
                                if estimate(ms).latest >= upcoming.end)
            jobs.append(Authorization(index, first_tick, first_unsafe, flash_bound))
            outputs.append(channel)

        flash, schedule = run_flash(jobs, channels=channels, retry=retry,
                                    log_bound=89, log_period=17, horizon=5100)
        evaluations = [(start, 'evaluate') for _, start, _ in schedule.cycles
                       if start >= receipt]
        writes = [(end, label) for _, end, label in flash.operations
                  if label.startswith('authorize/')]
        opened = set()
        for now, event in sorted(evaluations + writes):
            if event == 'evaluate':
                time = estimate(now)
                for index, channel in enumerate(outputs):
                    channel.cycle(time)
                    if channel.pwm:
                        assert channel.active == channel.windows[1]
                        assert now >= flash.commits[jobs[index]]
                        opened.add(index)
            else:
                index = int(event.split('/')[1])
                channel = outputs[index]
                assert channel.request is not None
                channel.write_copy(channel.request)
        assert opened == (set(range(channels)) if flash_bound in (29, 97) else set())
        assert all(channel.pwm == 0 for channel in outputs)
        cases += 1
    return cases


if __name__ == '__main__':
    print(f'PASS: {check()} composed rollback, delayed clock and loaded flash scenarios')
