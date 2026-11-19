"""Check clock-message eligibility and a backward jump over a saved dusk."""

from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class Reply:
    session: int
    request: int
    origin: int
    age: int


def effective_age(reply, session, request, rtt, ttl, timeout):
    if (reply.session, reply.request) != (session, request) or not reply.origin:
        return None
    # Full round trip is a conservative upper bound on age added in transit.
    age = reply.age + rtt
    return age if rtt < timeout and age < ttl else None


def check_replies():
    # Enumeration includes equality on both boundaries. Numbers are model units.
    count = 0
    for session, request, origin, age, rtt in product(
        (6, 7), (8, 9), (0, 1), (0, 9, 10, 11), (0, 1, 2, 3)
    ):
        reply = Reply(session, request, origin, age)
        accepted = effective_age(reply, session=7, request=9, rtt=rtt, ttl=10, timeout=2)
        reject = session == 6 or request == 8 or origin == 0 or rtt >= 2 or age + rtt >= 10
        assert (accepted is None) == reject
        if not reject:
            assert age <= accepted < 10
        count += 1
    return count


def check_evening_reentry():
    # One local daily light interval. Its key is the UTC instant of its
    # scheduled start. Only AUTO uses this rule; manual is an explicit mode.
    from datetime import datetime, time, timedelta, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Budapest")
    tested = 0
    for date in ((2026, 3, 28), (2026, 10, 24), (2026, 11, 19)):
        day = datetime(*date, tzinfo=tz)
        times = [(0, 19), (0, 20), (0, 19), (0, 21), (1, 7), (1, 8)]
        closed_through = None
        outputs = []
        for day_offset, hour in times:
            local = day + timedelta(days=day_offset, hours=hour)
            start = datetime.combine(local.date(), time(8), tzinfo=tz).astimezone(timezone.utc)
            if local.hour >= 20:
                closed_through = max(closed_through, start) if closed_through else start
            outputs.append(8 <= local.hour < 20 and
                           (closed_through is None or start > closed_through))
        assert outputs == [True, False, False, False, False, True]
        tested += 1
    return tested


def check_disagreement():
    # Candidates have already passed request/session/age checks. Their values
    # are UTC estimates projected to the same moment and grouped by tolerance.
    # Start with a valid running estimate (0) and one conflicting NTP reply (1).
    # While quarantined a peer cannot cast an independent NTP vote.
    events = tuple(product(("ntp_a", "ntp_b", "peer"), (0, 1)))
    cases = 0
    for sequence in product(events, repeat=3):
        votes = {"ntp_a": 1}
        chosen = None
        for source, estimate in sequence:
            if source.startswith("ntp"):
                votes[source] = estimate
            if len(votes) == 2 and votes["ntp_a"] == votes["ntp_b"]:
                chosen = votes["ntp_a"]
                break
        # Replay independently, inspect each prefix for two agreeing direct
        # responders, counting each source's most recent reply only.
        history = (("ntp_a", 1),) + sequence
        expected = None
        for end in range(1, len(history) + 1):
            latest = {k: v for k, v in history[:end] if k != "peer"}
            if len(latest) == 2 and len(set(latest.values())) == 1:
                expected = next(iter(latest.values()))
                break
        assert chosen == expected
        cases += 1
    return cases


if __name__ == "__main__":
    print(f"PASS: {check_replies()} time replies, {check_disagreement()} conflict sequences,"
          f" {check_evening_reentry()} dusk rollbacks")
