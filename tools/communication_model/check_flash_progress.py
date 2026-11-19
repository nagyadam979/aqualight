"""Virtual flash admission and progress under continuous local control.

Each board has one flash writer. A bounded blocking step must fit in a
contiguous idle interval, including verification, and leave its right-hand
boundary free. The model uses pre-erased slots and separately schedules
reclamation. Its millisecond values are test data, not flash timing claims.
"""

from dataclasses import dataclass, field
from itertools import product

from check_dusk_budget import PERIOD_MS, Work, first_zero_times, simulate


SAMPLE_PERIOD = 5000


@dataclass(frozen=True)
class Authorization:
    channel: int
    ready: int
    end: int
    copy_bound: int | None


@dataclass
class FlashResult:
    operations: list[tuple[int, int, str]] = field(default_factory=list)
    commits: dict[Authorization, int] = field(default_factory=dict)
    log_finished: int = 0
    log_missed: int = 0


def available_intervals(until, phase, cycles, measurements):
    ends = {due: end for due, _, end in cycles}
    busy = sorted([(due, ends.get(due, until + 1))
                   for due in range(phase, until + 1, PERIOD_MS)] + list(measurements))
    now = 0
    for start, end in busy + [(until + 1, until + 1)]:
        if start > now:
            yield now, start
        now = max(now, end)


def can_light(mark, remaining, now, gaps, control_cycles):
    """Test the rest of this two-copy commit against worst-case free gaps."""
    for start, stop in gaps:
        start = max(start, now)
        bound = mark.copy_bound
        fitting = max(0, (min(stop, mark.end) - start - 1) // bound)
        if fitting >= remaining:
            commit = start + remaining * bound
            return any(commit <= cycle_start and cycle_end < mark.end
                       for _, cycle_start, cycle_end in control_cycles)
        remaining -= fitting
    return False


def run_flash(authorizations, *, phase=0, retry=0, sensor_phase=0,
              reclaim_bound=29, log_bound=None, log_period=30, queue_size=4,
              horizon=6000, channels=2, broken_log_first=False):
    """Recalculate a shared allowance after each verified flash operation.

    Two spare slots per channel start erased. Both copies of an authorization
    must commit before the old pair can be recycled. An interrupted/abandoned
    one-copy attempt conservatively leaves that slot occupied here.
    """
    ready_order = sorted(authorizations, key=lambda mark: (mark.end, mark.ready, mark.channel))
    cutoffs = tuple(max((mark.end for mark in ready_order if mark.channel == channel),
                        default=4999) for channel in range(channels))
    measurements = tuple((start, start + (retry or 1))
                         for start in range(sensor_phase, horizon + 1, SAMPLE_PERIOD))
    settings = dict(local_cutoff_ms=cutoffs, board_phase_ms=phase, channels=channels,
                    until_ms=horizon, sensor_busy=measurements,
                    sensor_phase_ms=sensor_phase, sensor_start_cost_ms=1)
    baseline = simulate(**settings)
    retired = [0] * channels
    erased = [2] * channels
    written = dict.fromkeys(ready_order, 0)
    result = FlashResult()
    queued = 0
    next_log = 0
    gaps = tuple(available_intervals(horizon, phase, baseline.cycles, measurements))

    for gap_index, (start, stop) in enumerate(gaps):
        now = start
        while now < stop:
            if log_bound is not None and next_log <= now:
                arrivals = (now - next_log) // log_period + 1
                next_log += arrivals * log_period
                result.log_missed += max(0, queued + arrivals - queue_size)
                queued = min(queue_size, queued + arrivals)
            duties = [entry for entry in ready_order if entry.ready <= now < entry.end
                      and written[entry] < 2]
            pending = [entry.ready for entry in ready_order if now < entry.ready < stop]
            # A log step can start only if it cannot consume a planned light
            # authorization's first opportunity in this same interval.
            lower_limit = min([stop] + pending)
            label = ""
            channel = None
            operation_end = None
            target = next((entry for entry in duties if entry.copy_bound is not None
                           and erased[entry.channel] >= 2 - written[entry]
                           and now + entry.copy_bound < min(stop, entry.end)
                           and can_light(entry, 2 - written[entry], now,
                                         gaps[gap_index:], baseline.cycles)), None)
            spare = next((index for index in range(channels) if retired[index]
                          and reclaim_bound is not None
                          and now + reclaim_bound < lower_limit), None)
            log_fits = queued and log_bound is not None and now + log_bound < (
                stop if broken_log_first else lower_limit)
            if log_fits and (broken_log_first or (target is None and spare is None)):
                queued -= 1
                result.log_finished += 1
                label = "log"
                operation_end = now + log_bound
            elif target is not None:
                channel = target.channel
                erased[channel] -= 1
                written[target] += 1
                label = f"authorize/{channel}/{written[target]}"
                operation_end = now + target.copy_bound
                if written[target] == 2:
                    result.commits[target] = operation_end
                    retired[channel] += 2
            elif spare is not None:
                channel = spare
                retired[channel] -= 1
                erased[channel] += 1
                label = f"reclaim/{channel}"
                operation_end = now + reclaim_bound
            if operation_end is not None:
                result.operations.append((now, operation_end, label))
                now = operation_end
            else:
                if log_bound is not None and next_log < stop:
                    pending.append(next_log)
                now = min(pending, default=stop)

    # Replay the admitted work as actual CPU stalls without the PWM admission
    # check. Comparing dispatch to a no-flash run detects a missed release.
    retest = simulate(**settings, broken_sensor_only_guard=True,
                      workload=tuple(Work(start, end - start, label,
                                          measurement_deadline_ms=horizon + 2)
                                     for start, end, label in result.operations),
                      pwm_at=lambda at, channel, cutoff: (
                          100 if any(mark.channel == channel and mark.ready <= at < mark.end
                                     and committed <= at
                                     for mark, committed in result.commits.items()) else 0))
    assert retest.reservations == [(start, end, index)
        for index, (start, end, _) in enumerate(result.operations)]
    assert baseline.cycles == retest.cycles
    expected_samples = [start for start, _ in measurements]
    assert [at for at, kind, _ in retest.trace if kind == "sensor_start"] == expected_samples
    for mark, committed in result.commits.items():
        assert mark.ready < committed < mark.end
        assert sum(1 for start, end, label in result.operations
                   if mark.ready <= start < end <= committed
                   and label.startswith(f"authorize/{mark.channel}/")) == 2
    for at, channel, duty in retest.outputs:
        evaluated = next(start for _, start, finish in retest.cycles if finish == at)
        assert duty == (100 if any(mark.channel == channel and mark.ready <= evaluated < mark.end
                                   and committed <= evaluated
                                   for mark, committed in result.commits.items()) else 0)
    return result, retest


def check_progress():
    cases = 0
    for phase, retry, sensor_phase, size, channel_count in product(
            (0, 1, 49, 99), (0, 500, 1300), (0, 37, 4860), (1, 35, 80, 97), (1, 2)):
        marks = tuple(Authorization(channel, ready + channel, ready + 800 + channel, size)
                      for ready in (1500, 3850) for channel in range(channel_count))
        clean, _ = run_flash(marks, channels=channel_count, phase=phase, retry=retry,
                             sensor_phase=sensor_phase)
        backlogged, trace = run_flash(marks, channels=channel_count, phase=phase, retry=retry,
                                        sensor_phase=sensor_phase, log_bound=89, log_period=17)
        assert clean.commits == backlogged.commits
        assert len(backlogged.commits) == len(marks)
        assert backlogged.log_finished and backlogged.log_missed
        for mark in marks:
            assert any(at >= mark.ready and channel == mark.channel and duty
                       for at, channel, duty in trace.outputs if at < mark.end)
        cases += 1
    return cases


def check_unavailable():
    cases = 0
    for size, retry in product((None, 98, 200), (0, 1300)):
        marks = (Authorization(0, 3, 555, size), Authorization(1, 3, 555, 35))
        result, trace = run_flash(marks, retry=retry, log_bound=30)
        assert marks[0] not in result.commits
        assert not any(ch == 0 and duty for _, ch, duty in trace.outputs)
        assert (marks[1] in result.commits) == (retry == 0)
        assert result.log_finished
        cases += 1

    # Two free slots are viable once, but they give no recurring guarantee
    # unless reclaim also fits. No current/retained copy is erased to force it.
    marks = tuple(Authorization(0, ready, ready + 850, 42) for ready in (30, 1600, 3100))
    for bound in (45, 98, None):
        result, _ = run_flash(marks, reclaim_bound=bound, channels=1, log_bound=59)
        assert len(result.commits) == (len(marks) if bound == 45 else 1)
        cases += 1

    # Each copy fits, but neither channel can fit its entire commit in the
    # surviving intervals of this short light segment.
    marks = tuple(Authorization(ch, 4899, 5320, 97) for ch in (0, 1))
    blocked, trace = run_flash(marks, retry=350, log_bound=30)
    assert not blocked.commits
    assert all(duty == 0 for at, _, duty in trace.outputs if at >= 5320)
    cases += 1

    # An infeasible earlier deadline cannot spend a slot the feasible channel
    # needs. Its first copy would fit, but its second could never finish.
    marks = (Authorization(0, 2, 145, 97), Authorization(1, 3, 150, 45))
    result, trace = run_flash(marks, sensor_phase=250, horizon=300)
    assert marks[0] not in result.commits and result.commits[marks[1]] == 93
    assert (102, 1, 100) in trace.outputs
    assert not any(label.startswith("authorize/0/") for _, _, label in result.operations)
    return cases + 1


def check_mutants():
    marks = tuple(Authorization(ch, 3, 755, 75) for ch in (0, 1))
    safe, _ = run_flash(marks, log_bound=82, log_period=10, horizon=1000)
    starved, _ = run_flash(marks, log_bound=82, log_period=10, horizon=1000,
                           broken_log_first=True)
    assert len(safe.commits) == 2 and not starved.commits
    assert starved.log_finished > 0
    late = simulate(local_cutoff_ms=99, workload=(Work(3, 250),),
                    until_ms=500, broken_sensor_only_guard=True)
    assert first_zero_times(late) == (255, 255)
    return starved.log_finished


if __name__ == "__main__":
    print(f"PASS: {check_progress()} flash progress cases under saturated logging")
    print(f"PASS: {check_unavailable()} unavailable/reclamation/deadline cases")
    print(f"Mutants: log-first starved both channels ({check_mutants()} log writes); "
          "sensor-only guard delayed both zero writes")
