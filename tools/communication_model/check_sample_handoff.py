"""Virtual sampling/queue handoff model; no real clock, flash, or network I/O.

The queue owns pending records until the independent writer commits them. Flash
errors and writer restarts retain the queued record; power-loss recovery of RAM
is outside this model. Uploading observes the durable log without freeing RAM.
"""

from dataclasses import dataclass
from itertools import product


PERIOD = 5
WINDOW = 600
SLOTS = WINDOW // PERIOD
WINDOWS = 3
END = WINDOWS * WINDOW


@dataclass(frozen=True)
class Record:
    identifier: int
    slots: tuple
    valid: int = 0
    error: int = 0


@dataclass(frozen=True)
class Scenario:
    writer: str
    uploader: str
    release: str
    writer_first: bool
    capacity: int = 1


@dataclass
class Result:
    scenario: Scenario
    starts: list
    records: dict
    status: dict
    queue: list
    durable: list
    uploaded: set
    dropped_slots: int
    enqueue_attempts: list
    writer_states: set

    @property
    def dropped_ids(self):
        return {key for key, state in self.status.items() if state == "dropped"}

    @property
    def pending_ids(self):
        return {key for key, state in self.status.items() if state == "pending"}

    @property
    def durable_ids(self):
        return {key for key, state in self.status.items() if state == "durable"}


def scenarios():
    """Small public scenario iterator, also usable in a combined state model."""
    for writer, uploader, release, first, capacity in product(
        ("free", "hung", "flash_error", "restarting"),
        ("blocked", "successful"),
        ("never", "before", "boundary"),
        (False, True),
        (1, 2),
    ):
        yield Scenario(writer, uploader, release, first, capacity)


def simulate(scenario, blocking_handoff=False, sample=None):
    """Run three windows; a broken blocking producer misses virtual starts.

    The optional sample(actual_ms) callback returns success (True) or failure
    (False). Each closed record keeps separate valid/error counts.

    'before' recovers one second before the first close, 'boundary' exactly
    at it. 'never' holds the selected writer mode throughout. A free writer
    is always available, independently of the requested recovery instant.
    """
    if sample is None:
        sample = lambda actual_ms: True
    seeds = [Record(-index - 1, ()) for index in range(scenario.capacity)]
    result = Result(scenario, [], {r.identifier: r for r in seeds},
                    {r.identifier: "pending" for r in seeds},
                    [r.identifier for r in seeds], [], set(), 0, [], set())
    active_slots = []
    active_valid = active_error = 0
    blocked_record = None

    def try_enqueue(record, now):
        result.enqueue_attempts.append((record.identifier, now))
        if len(result.queue) >= scenario.capacity:
            return False
        result.queue.append(record.identifier)
        result.status[record.identifier] = "pending"
        return True

    def writer_step(now):
        mode = scenario.writer
        recovery = {"never": END + 1, "before": WINDOW - 1,
                    "boundary": WINDOW}[scenario.release]
        if now >= recovery:
            mode = "free"
        result.writer_states.add(mode)
        if mode == "free" and result.queue:
            identifier = result.queue.pop(0)
            result.status[identifier] = "durable"
            result.durable.append(identifier)
        # All unsuccessful modes leave ownership in the queue. In particular,
        # a failed flash operation must not publish a durable record.

    def producer_step(now):
        nonlocal active_slots, active_valid, active_error, blocked_record
        if blocked_record is not None:
            # Deliberately broken variant: waiting for queue space suspends the
            # producer, including sampling. Finite virtual execution still ends
            # if the writer never returns.
            if not try_enqueue(blocked_record, now):
                return
            blocked_record = None
        if now and now % WINDOW == 0:
            identifier = now // WINDOW - 1
            record = Record(identifier, tuple(active_slots), active_valid, active_error)
            result.records[identifier] = record
            active_slots = []
            active_valid = active_error = 0
            if not try_enqueue(record, now):
                if blocking_handoff:
                    blocked_record = record
                    result.status[identifier] = "waiting"
                    return
                result.status[identifier] = "dropped"
                result.dropped_slots += len(record.slots)
        if now < END and now % PERIOD == 0:
            result.starts.append((now, now))  # (scheduled, actual virtual start)
            active_slots.append(now)
            if sample(now * 1000):
                active_valid += 1
            else:
                active_error += 1

    for now in range(END + 1):
        if scenario.writer_first:
            writer_step(now)
            producer_step(now)
        else:
            producer_step(now)
            writer_step(now)
        if scenario.uploader == "successful":
            result.uploaded.update(result.durable)
        assert result.uploaded <= result.durable_ids
        assert len(result.queue) <= scenario.capacity
        assert set(result.queue) == result.pending_ids
        assert not (result.pending_ids & result.durable_ids)
        assert not (result.dropped_ids & result.durable_ids)
    return result


def validate(result):
    """Return a compact witness, or None; suitable for crossing other branches."""
    expected_starts = [(now, now) for now in range(0, END, PERIOD)]
    if result.starts != expected_starts:
        actual = dict(result.starts)
        first = next(now for now, _ in expected_starts if actual.get(now) != now)
        return ("sampling stalled", result.scenario, first, actual.get(first))
    for identifier in range(WINDOWS):
        expected = tuple(range(identifier * WINDOW, (identifier + 1) * WINDOW, PERIOD))
        record = result.records.get(identifier)
        if (record is None or record.slots != expected
                or record.valid + record.error != SLOTS):
            return ("incomplete window", result.scenario, identifier, record)
    expected_attempts = [(identifier, (identifier + 1) * WINDOW)
                         for identifier in range(WINDOWS)]
    if result.enqueue_attempts != expected_attempts:
        return ("handoff waited or retried", result.scenario, result.enqueue_attempts[:8])
    if set(result.status) != set(result.records):
        return ("unaccounted record", result.scenario)
    if set(result.status.values()) - {"pending", "dropped", "durable"}:
        return ("invalid ownership", result.scenario)
    if result.dropped_slots != SLOTS * len(result.dropped_ids):
        return ("incorrect dropped slot count", result.scenario)
    expected_upload = result.durable_ids if result.scenario.uploader == "successful" else set()
    if result.uploaded != expected_upload:
        return ("uploader observed non-durable record", result.scenario)
    return None


def check(blocking_handoff=False, cases=None):
    """Return (checked case count, first failure); accepts a scenario iterable."""
    count = 0
    for scenario in scenarios() if cases is None else cases:
        count += 1
        failure = validate(simulate(scenario, blocking_handoff))
        if failure is not None:
            return count, failure
    return count, None


def check_boundary_outcomes():
    """Verify the specific queue-space races, beyond generic accounting."""
    for first in (False, True):
        before = simulate(Scenario("hung", "blocked", "before", first))
        boundary = simulate(Scenario("hung", "blocked", "boundary", first))
        full = simulate(Scenario("hung", "successful", "never", first))
        assert before.status[0] == "durable"
        assert (boundary.status[0] == "dropped") == (not first)
        assert full.dropped_ids == set(range(WINDOWS))
        assert full.dropped_slots == WINDOWS * SLOTS
        assert full.pending_ids == {-1}
        assert full.uploaded == set()


def check_with_sensor():
    import check_sensor_budget as sensor

    # Share one bus across all 360 slots. At the end of each window the
    # writer receives the actual accounting from these attempts, including
    # an error just before rollover and a recovered sensor just after it.
    dues = range(0, END * 1000, sensor.PERIOD_MS)
    scripts = []
    for index, due in enumerate(dues):
        pattern = index % 4
        first = sensor.Response(20) if pattern == 0 else sensor.Response(None)
        retry = sensor.Response(25) if pattern == 1 else sensor.Response(None)
        scripts.append(sensor.SlotSpec(due, first, retry, reinit_ok=pattern != 2))
    count = 0
    for retry_timeout in (3879, 3880, 3881):
        run = sensor.simulate_slots(scripts, retry_timeout_ms=retry_timeout)
        sensor.assert_invariants(run)
        by_start = {s.started: s.outcome == "valid" for s in run.slots}
        # The +1 ms case fits; equality and overrun both disable the retry.
        valid = SLOTS // 2 if retry_timeout == 3879 else SLOTS // 4
        for scenario in scenarios():
            result = simulate(scenario, sample=by_start.__getitem__)
            assert validate(result) is None
            assert [start * 1000 for _, start in result.starts] == list(by_start)
            for index in range(WINDOWS):
                record = result.records[index]
                assert (record.valid, record.error) == (valid, SLOTS - valid)
            count += 1
    return count


def check_nonpreemptible_work():
    # If a flash operation could suspend the sampler, admit it only after
    # release of the sensor and its retry, with a known worst-case duration.
    # The flash writer and uploader share one serialized admission decision.
    due, released = 5000, 4900
    durations = (None, 1, 49, 50, 99, 100, 101)
    cases = 0
    for request, first, second in product((4899, 4900, 4999), durations, durations):
        available = request
        admitted = []
        for duration in (first, second):
            if available >= released and duration is not None and available + duration < due:
                admitted.append((available, available + duration))
                available += duration
        assert all(released <= start < end < due for start, end in admitted)
        assert max(due, available) == due  # actual virtual start of next slot
        if request == released and (first, second) == (50, 50):
            assert admitted == [(4900, 4950)]  # two individually short jobs do not both fit
        if request == released and (first, second) == (49, 50):
            assert admitted == [(4900, 4949), (4949, 4999)]
        cases += 1
    return cases


if __name__ == "__main__":
    count, failure = check()
    assert failure is None, failure
    check_boundary_outcomes()
    _, witness = check(blocking_handoff=True)
    assert witness is not None
    print(f"PASS: {count} sample/queue/writer/uploader cases, "
          f"{WINDOWS} windows of {SLOTS} slots each")
    print("Broken blocking handoff:", witness)
    print(f"PASS: {check_with_sensor()} combined three-window scenarios")
    print(f"PASS: {check_nonpreemptible_work()} shared execution budget cases")
