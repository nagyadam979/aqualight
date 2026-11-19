"""Virtual sensor ownership/deadline model; no firmware or wall-clock I/O.

Integer milliseconds make the strict boundary explicit: timeout + positive cleanup
must end at most at boundary - 1. A timeout invalidates the attempt token before
cleanup; a callback at the timeout timestamp is already expired in either order.
"""

from dataclasses import dataclass, field
from heapq import heappop, heappush
from itertools import product

PERIOD_MS = 5000


@dataclass(frozen=True)
class Response:
    delay_ms: int | None
    ok: bool = True
    duplicates: tuple[int, ...] = ()  # additional delays from attempt start


@dataclass(frozen=True)
class SlotSpec:
    due: int
    first_response: Response = Response(None)
    retry_response: Response = Response(None)
    reinit_ok: bool = True


@dataclass
class SlotResult:
    due: int
    started: int | None = None
    finished: int | None = None
    outcome: str | None = None
    attempts: int = 0
    accounted: int = 0
    retry_admitted: bool = False
    ignored: list[tuple[int, tuple[int, int]]] = field(default_factory=list)


@dataclass
class Run:
    slots: list[SlotResult]
    reservations: list[tuple[int, int, int]]
    trace: list[tuple[int, str, int]]
    owner: int | None


def simulate_slots(specs, *, initial_timeout_ms=1000, retry_timeout_ms=1000,
                   reinit_ms=100, cleanup_ms=10, broken_admission=False,
                   reply_first=False):
    """Run independent due events with one shared sensor reservation.

    Response delays are relative to their attempt's start, after any reinit.
    broken_admission deliberately omits final cleanup from retry admission.
    It is a negative control, never the default policy.
    """
    specs = tuple(specs)
    if cleanup_ms <= 0 or min(initial_timeout_ms, retry_timeout_ms) <= 0 or reinit_ms < 0:
        raise ValueError("positive timeout/cleanup and nonnegative reinit required")
    if initial_timeout_ms + cleanup_ms >= PERIOD_MS:
        raise ValueError("initial timeout and cleanup must fit strictly inside slot")
    dues = [spec.due for spec in specs]
    if any(due % PERIOD_MS for due in dues) or len(set(dues)) != len(dues):
        raise ValueError("unique 5000 ms grid points required")
    for spec in specs:
        for response in (spec.first_response, spec.retry_response):
            delays = response.duplicates + (() if response.delay_ms is None else (response.delay_ms,))
            if any(delay < 0 for delay in delays):
                raise ValueError("response delays must be nonnegative")

    results = [SlotResult(spec.due) for spec in specs]
    events, trace, reservations = [], [], []
    owner = None
    active_token = None
    active_deadline = None
    reservation_start = None
    serial = 0

    def enqueue(at, priority, kind, index, payload=None):
        nonlocal serial
        serial += 1
        heappush(events, (at, priority, serial, kind, index, payload))

    def begin_attempt(at, index):
        nonlocal active_token, active_deadline
        result = results[index]
        result.attempts += 1
        token = (index, result.attempts)
        active_token = token
        timeout = initial_timeout_ms if result.attempts == 1 else retry_timeout_ms
        active_deadline = at + timeout
        response = specs[index].first_response if result.attempts == 1 else specs[index].retry_response
        trace.append((at, "attempt", index))
        enqueue(active_deadline, 0, "timeout", index, token)
        reply_priority = -1 if reply_first else 3
        if response.delay_ms is not None:
            enqueue(at + response.delay_ms, reply_priority, "response", index, (token, response.ok))
        for delay in response.duplicates:
            enqueue(at + delay, reply_priority, "response", index, (token, response.ok))

    def cleanup(at, index, ok):
        nonlocal active_token
        active_token = None  # invalidate before even a same-time callback
        trace.append((at, "cleanup_start", index))
        enqueue(at + cleanup_ms, 0, "released", index, ok)

    for index, spec in enumerate(specs):
        enqueue(spec.due, 2, "due", index)

    while events:
        at, _, _, kind, index, payload = heappop(events)
        result = results[index]
        if kind == "due":
            if owner is not None:
                # An actually blocked start, rather than a synthetic arithmetic check.
                trace.append((at, "blocked_due", index))
                continue
            owner, reservation_start = index, at
            result.started = at
            trace.append((at, "slot_start", index))
            begin_attempt(at, index)
        elif kind == "response":
            token, ok = payload
            if token != active_token or owner != index or at >= active_deadline:
                result.ignored.append((at, token))
            else:
                cleanup(at, index, ok)
        elif kind == "timeout":
            if payload == active_token and owner == index:
                cleanup(at, index, False)
        elif kind == "reinit_done":
            assert owner == index and active_token is None
            if specs[index].reinit_ok:
                begin_attempt(at, index)
            else:
                cleanup(at, index, False)
        elif kind == "released":
            assert owner == index and active_token is None
            reservations.append((reservation_start, at, index))
            owner, reservation_start = None, None
            trace.append((at, "release", index))
            remaining = reinit_ms + retry_timeout_ms + (0 if broken_admission else cleanup_ms)
            if not payload and not result.retry_admitted and at + remaining < result.due + PERIOD_MS:
                result.retry_admitted = True
                owner, reservation_start = index, at
                trace.append((at, "reinit", index))
                enqueue(at + reinit_ms, 0, "reinit_done", index)
            else:
                result.finished = at
                result.outcome = "valid" if payload else "error"
                result.accounted += 1
                trace.append((at, "account", index))
    return Run(results, reservations, trace, owner)


def simulate_slot(due, *, first_response=Response(None), retry_response=Response(None), **budget):
    """Return one SlotResult; use simulate_slots for shared ownership assertions."""
    return simulate_slots((SlotSpec(due, first_response, retry_response),), **budget).slots[0]


def assert_invariants(run):
    assert run.owner is None, "old sensor reservation survived"
    for slot in run.slots:
        assert slot.started == slot.due, ("sampling slipped/blocked", slot)
        assert slot.accounted == 1, ("outcome counted more/less than once", slot)
        assert slot.finished < slot.due + PERIOD_MS, ("strict boundary violated", slot)
        assert slot.attempts in (1, 2)
    for start, end, index in run.reservations:
        assert start < end < run.slots[index].due + PERIOD_MS
    ordered = sorted(run.reservations)
    assert all(left[1] <= right[0] for left, right in zip(ordered, ordered[1:]))


def check():
    cases = 0
    # At first timeout=1000 and cleanup=10, available budget is 3990.
    # reinit=100, final cleanup=10 => retry=3879 fits, 3880 hits exactly,
    # 3881 exceeds. Admission must reject both latter cases.
    for retry_timeout, first_kind, retry_kind, first_ok, retry_ok, reply_first in product(
            (3879, 3880, 3881), ("before", "at", "after", "missing"),
            ("before", "at", "after", "missing"), (False, True), (False, True), (False, True)):
        def response(kind, timeout, ok):
            delay = {"before": timeout - 1, "at": timeout,
                     "after": timeout + 1, "missing": None}[kind]
            # Exercise duplicate and cross-slot callbacks, including those from
            # an old attempt while a new slot owns the same physical sensor.
            duplicates = () if delay is None else (delay, timeout + 11, timeout + 111, timeout + 4001, timeout + 5001)
            return Response(delay, ok, duplicates)

        first = response(first_kind, 1000, first_ok)
        retry = response(retry_kind, retry_timeout, retry_ok)
        run = simulate_slots((SlotSpec(0, first, retry), SlotSpec(5000, Response(20))),
                             retry_timeout_ms=retry_timeout, reply_first=reply_first)
        assert_invariants(run)
        slot, recovered = run.slots
        first_success = first_kind == "before" and first_ok
        first_finish = 999 if first_kind == "before" else 1000
        should_retry = (not first_success and
                        first_finish + 10 + 100 + retry_timeout + 10 < PERIOD_MS)
        assert slot.retry_admitted == should_retry
        expected_valid = first_success or (should_retry and retry_kind == "before" and retry_ok)
        assert slot.outcome == ("valid" if expected_valid else "error")
        assert recovered.outcome == "valid" and recovered.attempts == 1
        if first_kind != "missing":
            assert slot.ignored, "duplicates/late response must be discarded"
        cases += 1

    for old_delay in (4999, 5000, 5001):
        for reply_first in (True, False):
            run = simulate_slots((SlotSpec(0, Response(old_delay), reinit_ok=False),
                                  SlotSpec(5000, Response(20))), reply_first=reply_first)
            assert_invariants(run)
            failed, next_slot = run.slots
            assert failed.outcome == "error" and failed.attempts == 1 and failed.retry_admitted
            assert (old_delay, (0, 1)) in failed.ignored
            assert next_slot.outcome == "valid"
            cases += 1

    # Different absolute grid points must produce the same relative behaviour.
    for due in (0, 5000, 500000):
        slot = simulate_slot(due, first_response=Response(None), retry_response=Response(25))
        assert slot.started == due and slot.finished - due == 1145
        assert slot.outcome == "valid" and slot.accounted == 1
        cases += 1

    # Omitting cleanup admits a retry whose release is exactly on the boundary.
    mutant = simulate_slots((SlotSpec(0), SlotSpec(5000, Response(20))),
                            retry_timeout_ms=3880, broken_admission=True)
    assert mutant.slots[0].finished == PERIOD_MS
    try:
        assert_invariants(mutant)
    except AssertionError:
        pass
    else:
        raise AssertionError("negative control was not detected")
    # A slightly bigger retry is admitted by the same mutant and actually keeps
    # ownership beyond due=5000, so the next independent due event is blocked.
    blocked = simulate_slots((SlotSpec(0), SlotSpec(5000, Response(20))),
                             retry_timeout_ms=3881, broken_admission=True)
    assert blocked.slots[0].finished == 5001
    assert blocked.slots[1].started is None
    assert (5000, "blocked_due", 1) in blocked.trace
    return cases, mutant.slots[0].finished, blocked.slots[0].finished


if __name__ == "__main__":
    cases, exact, over = check()
    print(f"PASS: {cases} sensor budget/reply/recovery scenarios; 5000 ms grid")
    print(f"Negative controls detected: release at {exact} ms; blocked next slot until {over} ms")
