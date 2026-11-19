"""One board's monotonic PWM scheduler and serial optional-work admission.

All times are integer local milliseconds; no firmware, wall clock or I/O.
A control cycle evaluates its channels at entry and writes them only after its
CPU cost. Optional jobs reserve real elapsed time. A blocking job owns the CPU;
an interruptible retry/network wait owns only the serial optional-work slot.
"""

from dataclasses import dataclass, field

PERIOD_MS = 100
CYCLE_MS = 2
_AUTO = object()


@dataclass(frozen=True)
class Work:
    ready_ms: int
    duration_ms: int
    kind: str = "flash"
    interruptible: bool = False
    bound_ms: int | None | object = _AUTO
    measurement_deadline_ms: int | None = None
    pending: bool = False

    @property
    def bound(self):
        return self.duration_ms if self.bound_ms is _AUTO else self.bound_ms


@dataclass
class Run:
    outputs: list[tuple[int, int, int]] = field(default_factory=list)
    cycles: list[tuple[int, int, int]] = field(default_factory=list)  # due, start, end
    reservations: list[tuple[int, int, int]] = field(default_factory=list)
    deferred: list[int] = field(default_factory=list)
    trace: list[tuple[int, str, int]] = field(default_factory=list)
    cutoffs: tuple[int, ...] = ()
    period_ms: int = PERIOD_MS
    cycle_ms: int = CYCLE_MS
    jitter_ms: int = 0

    @property
    def output_trace_times(self):
        return [at for at, _, _ in self.outputs]


def first_zero_times(run):
    """First committed zero per channel (None if outside the run horizon)."""
    return tuple(next((at for at, ch, duty in run.outputs if ch == channel and duty == 0), None)
                 for channel in range(len(run.cutoffs)))


def simulate(*, local_cutoff_ms=250, board_phase_ms=0, workload=(),
             period_ms=PERIOD_MS, cycle_ms=CYCLE_MS, channels=2,
             until_ms=None, measurement_period_ms=5000,
             broken_sensor_only_guard=False, sensor_start_cost_ms=0,
             sensor_phase_ms=0, sensor_busy=(), valid_at=None, pwm_at=None):
    """Return traces in the board's own valid monotonic time domain.

    A scalar cutoff applies to every channel; a sequence supplies each channel's
    cutoff. The simple PWM curve is positive before cutoff, zero thereafter.
    CPU-blocking optional work must finish STRICTLY before both the next control
    tick and the next measurement. An interruptible wait may cross control ticks.
    Unknown/pending work remains deferred, without
    preventing later independent jobs from admission. The broken policy is an
    executable negative control that checks only the measurement deadline.
    sensor_start_cost_ms=1 reserves highest-priority CPU once per measurement
    period, including when a PWM computation is already in progress. sensor_busy
    lists (start, end) measurement/retry ownership intervals. Blocking optional
    work cannot overlap them; interruptible waits leave PWM dispatch available.
    valid_at(now) gates the evaluated output; pwm_at(now, channel, cutoff) may
    supply a custom duty value. Both run only at real control-cycle entry.
    """
    if period_ms <= 0 or not 0 < cycle_ms < period_ms or channels <= 0:
        raise ValueError("positive period/channels and 0 < cycle < period required")
    if not 0 <= board_phase_ms < period_ms or measurement_period_ms <= 0:
        raise ValueError("phase must lie in one period; measurement period positive")
    cutoffs = ((local_cutoff_ms,) * channels if isinstance(local_cutoff_ms, int)
               else tuple(local_cutoff_ms))
    if len(cutoffs) != channels or any(not isinstance(t, int) or t < 0 for t in cutoffs):
        raise ValueError("one nonnegative integer cutoff per channel required")
    if sensor_start_cost_ms not in (0, 1) or not 0 <= sensor_phase_ms < measurement_period_ms:
        raise ValueError("sensor start cost must be 0 or 1 ms and phase within period")
    sensor_busy = tuple(sensor_busy)
    if any(start < 0 or end <= start for start, end in sensor_busy):
        raise ValueError("sensor busy intervals must have positive length")
    jobs = tuple(workload)
    for job in jobs:
        if job.ready_ms < 0 or job.duration_ms <= 0:
            raise ValueError("nonnegative release and positive duration required")
        if job.bound is not None and (not isinstance(job.bound, int) or job.bound < job.duration_ms):
            raise ValueError("known bound must cover actual elapsed duration")
    if until_ms is None:
        until_ms = max((*cutoffs, *(j.ready_ms for j in jobs)), default=0) + 3 * period_ms + cycle_ms
    if until_ms < 0:
        raise ValueError("nonnegative horizon required")
    run = Run(cutoffs=cutoffs, period_ms=period_ms, cycle_ms=cycle_ms,
              jitter_ms=sensor_start_cost_ms)
    next_sensor = sensor_phase_ms
    sensor_cpu_end = 0
    next_tick = board_phase_ms
    active = None
    active_end = None
    completed = set()
    cycle_end = None
    cycle_due = cycle_start = None
    values = None
    # A one-ms event clock is intentional: blocking reservations actually stop
    # dispatch, rather than merely asserting a duration inequality on paper.
    for now in range(until_ms + 1):
        if active is not None and now == active_end:
            run.trace.append((now, "work_end", active))
            completed.add(active)
            active = active_end = None
        if cycle_end is not None and now == cycle_end:
            run.cycles.append((cycle_due, cycle_start, now))
            for channel, value in enumerate(values):
                run.outputs.append((now, channel, value))
            run.trace.append((now, "write_outputs", -1))
            cycle_end = None
        if now < sensor_cpu_end or (active is not None and not jobs[active].interruptible):
            continue
        if sensor_start_cost_ms and now >= next_sensor:
            next_sensor += ((now - next_sensor) // measurement_period_ms + 1) * measurement_period_ms
            sensor_cpu_end = now + sensor_start_cost_ms
            run.trace.append((now, "sensor_start", -1))
            if cycle_end is not None:
                cycle_end += sensor_start_cost_ms
            continue
        if cycle_end is not None:
            continue
        if now >= next_tick:
            cycle_due, cycle_start = next_tick, now
            # Coalesce missed ticks; a late cycle is still a genuine evaluation.
            next_tick += ((now - next_tick) // period_ms + 1) * period_ms
            valid = valid_at is None or valid_at(now)
            values = tuple((0 if not valid else
                            pwm_at(now, channel, cutoff) if pwm_at is not None else
                            0 if now >= cutoff else min(255, max(1, cutoff - now)))
                           for channel, cutoff in enumerate(cutoffs))
            cycle_end = now + cycle_ms
            run.trace.append((now, "evaluate_pwm", -1))
            continue
        if active is not None:
            continue
        # All optional kinds use this same serial admission point. Reevaluate
        # the remaining budget after each job, never from a stale batch snapshot.
        for index, job in enumerate(jobs):
            if index in completed or job.ready_ms > now:
                continue
            deadline = (job.measurement_deadline_ms if job.measurement_deadline_ms is not None
                        else (now // measurement_period_ms + 1) * measurement_period_ms)
            bound = job.bound
            if job.pending or bound is None:
                continue
            boundary = deadline
            if not job.interruptible and not broken_sensor_only_guard:
                boundary = min(deadline, next_tick, next_sensor if sensor_start_cost_ms else deadline)
            if not job.interruptible and any(
                    now < end and now + bound > start for start, end in sensor_busy):
                continue
            if now + bound >= boundary:
                continue
            active, active_end = index, now + job.duration_ms
            run.reservations.append((now, active_end, index))
            run.trace.append((now, "work_start", index))
            break
    run.deferred = [i for i in range(len(jobs)) if i not in completed]
    return run


def assert_invariants(run):
    for due, start, end in run.cycles:
        assert due <= start <= due + run.jitter_ms, ("PWM tick delayed by optional work", due, start)
        assert run.cycle_ms <= end - start <= run.cycle_ms + run.jitter_ms
    for cutoff, zero in zip(run.cutoffs, first_zero_times(run)):
        assert zero is not None and cutoff <= zero <= cutoff + run.period_ms + run.jitter_ms + run.cycle_ms, (
            "first zero outside local P+C bound", cutoff, zero)
    assert all(left[1] <= right[0] for left, right in zip(run.reservations, run.reservations[1:])), (
        "optional work overlapped")
    # Every write comes from a completed evaluation cycle, including nonzero
    # values. This prevents satisfying the bound with a synthetic zero callback.
    assert {at for at, _, _ in run.outputs} == {end for _, _, end in run.cycles}
    assert len(run.outputs) == len(run.cycles) * len(run.cutoffs)


def check():
    cases = 0
    for phase in range(PERIOD_MS):
        for offset in (-1, 0, 1):
            cutoff = 3 * PERIOD_MS + phase + offset
            work = tuple(Work(at, duration, kind, interruptible)
                         for at in range(0, 550, 7)
                         for duration, kind, interruptible in
                         ((9, "sensor_retry", True), (11, "network_wait", True),
                          (13, "flash", False), (5, "driver", False)))
            run = simulate(local_cutoff_ms=(cutoff, cutoff + 17), board_phase_ms=phase,
                           workload=work, until_ms=800)
            assert_invariants(run)
            assert any(value > 0 for _, _, value in run.outputs)
            assert run.reservations
            cases += 1

    # Admission equality and dependency/unknown decisions are observable: the
    # first cycle finishes at 2; next tick=100, so 97 fits and 98 does not.
    for duration, accepted in ((97, True), (98, False), (99, False)):
        run = simulate(workload=(Work(2, duration),), until_ms=101)
        assert bool(run.reservations) == accepted
        cases += 1
    for deadline, accepted in ((100, True), (99, False), (98, False)):
        run = simulate(workload=(Work(2, 97, measurement_deadline_ms=deadline),), until_ms=101)
        assert bool(run.reservations) == accepted
        cases += 1
    deferred = simulate(workload=(Work(2, 20, bound_ms=None), Work(2, 20, pending=True),
                                  Work(2, 5)), until_ms=500)
    assert_invariants(deferred)
    assert deferred.deferred == [0, 1]
    assert [i for _, _, i in deferred.reservations] == [2]
    cases += 1

    # Sequential jobs individually fit the initial budget, but their sum does
    # not. The shared admission point moves the second start past tick 100.
    serial = simulate(workload=(Work(2, 60), Work(2, 60)), until_ms=500)
    assert_invariants(serial)
    assert serial.reservations == [(2, 62, 0), (102, 162, 1)]
    cases += 1

    # An interruptible wait leaves the CPU available even under the broken
    # guard; a real blocking flash reservation delays dispatch and first zero.
    for interruptible in (False, True):
        bad = simulate(local_cutoff_ms=99, workload=(Work(3, 250, interruptible=interruptible),),
                       broken_sensor_only_guard=True, until_ms=500)
        if interruptible:
            assert_invariants(bad)
        else:
            assert first_zero_times(bad) == (255, 255)
            try:
                assert_invariants(bad)
            except AssertionError:
                pass
            else:
                raise AssertionError("sensor-only negative control escaped detection")
        cases += 1
    for phase in range(PERIOD_MS):
        jittered = simulate(local_cutoff_ms=4999, board_phase_ms=phase,
                            sensor_start_cost_ms=1, until_ms=5300,
                            workload=(Work(4970, 40), Work(4971, 20)))
        assert_invariants(jittered)
        assert any(kind == "sensor_start" and at == 5000
                   for at, kind, _ in jittered.trace)
        cases += 1
    busy = simulate(local_cutoff_ms=99, sensor_busy=((2, 180),),
                    workload=(Work(2, 10), Work(2, 5, interruptible=True)), until_ms=500)
    assert_invariants(busy)
    assert busy.reservations == [(2, 7, 1), (180, 190, 0)]
    invalid = simulate(local_cutoff_ms=999, valid_at=lambda now: now < 99, until_ms=210)
    assert first_zero_times(invalid) == (102, 102)
    assert any(value > 0 for _, _, value in invalid.outputs)
    cases += 2
    waiting = simulate(local_cutoff_ms=99, workload=(Work(3, 250, "io_wait", True),))
    assert waiting.reservations == [(3, 253, 0)]
    assert first_zero_times(waiting) == (102, 102)
    assert_invariants(waiting)
    cases += 1
    return cases


if __name__ == "__main__":
    print(f"PASS: {check()} local dusk/CPU/admission scenarios; P=100 ms, C=2 ms")
    print("Negative control detected: sensor-only guard delays first zero to 255 ms")
