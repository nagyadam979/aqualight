"""Explore shared deadlines with a virtual clock. No network or device I/O."""

from dataclasses import dataclass
from itertools import permutations, product


@dataclass
class Window:
    phase: str
    deadline: int
    attempt: int
    clock_known: bool
    record_acked: bool = False

    def dispatch(self, event, now, attempt, ignore_deadline=False):
        if now >= self.deadline and not ignore_deadline:
            self.phase = "closed"
        if self.phase == "closed" or attempt != self.attempt:
            return
        if event == "timeout" and now >= self.deadline:
            self.phase = "closed"
        if event == "reply":
            if self.phase == "ntp":
                self.clock_known = True
                self.phase = "closed"
            elif self.phase == "ack":
                self.record_acked = True
                self.phase = "closed"


def check_simultaneous_events(ignore_deadline=False):
    # At the common boundary an evening control cycle and one sensor slot are
    # due. The NTP case represents an empty backlog. The ACK case represents
    # one outstanding batch. A reply can belong to this attempt or an old one.
    cases = 0
    for phase, now, current_reply, sensor_ok in product(
        ("ntp", "ack"), (59, 60, 61), (True, False), (True, False)
    ):
        for order in permutations(("reply", "timeout", "control", "sample")):
            window = Window(phase, deadline=60, attempt=2, clock_known=phase == "ack")
            pwm, samples, failed_samples = 100, 0, 0
            for event in order:
                if event == "control":
                    # Both an unidentified time and the scheduled night request 0%.
                    pwm = 0
                elif event == "sample":
                    samples += int(sensor_ok)
                    failed_samples += int(not sensor_ok)
                else:
                    token = 1 if event == "reply" and not current_reply else 2
                    window.dispatch(event, now, token, ignore_deadline)
            timely = now < 60 and current_reply
            outcome = (pwm, samples, failed_samples, window.clock_known, window.record_acked)
            expected = (0, int(sensor_ok), int(not sensor_ok), phase == "ack" or timely,
                        phase == "ack" and timely)
            cases += 1
            if outcome != expected or (now >= 60 and window.phase != "closed"):
                return cases, (phase, now, current_reply, sensor_ok, order, outcome, expected)
    return cases, None


def check_background_stalls():
    # Explore each outage for an entire window at 1 s virtual resolution.
    # A nonblocking background step leaves each interval free for local work.
    phases = ("association", "dhcp", "dns", "ntp", "tls", "send", "ack")
    cases = 0
    for phase, evening, sensor_fails_at_evening in product(phases, (5, 10, 55, 60),
                                                         (True, False)):
        deadline = 10 if phase in ("association", "dhcp") else 60
        window = Window(phase, deadline, attempt=1, clock_known=True)
        valid_slots, failed_slots, changes = [], [], []
        previous = True
        for now in range(66):
            window.dispatch("poll", now, 1)
            light = now < evening
            if light != previous:
                changes.append(now)
            previous = light
            if now % 5 == 0:
                failed = now == evening and sensor_fails_at_evening
                (failed_slots if failed else valid_slots).append(now)
            if now >= deadline:
                assert window.phase == "closed"
        assert changes == [evening]
        assert sorted(valid_slots + failed_slots) == list(range(0, 66, 5))
        assert failed_slots == ([evening] if sensor_fails_at_evening else [])
        cases += 1
    return cases


if __name__ == "__main__":
    passed, failure = check_simultaneous_events()
    assert failure is None, failure
    _, witness = check_simultaneous_events(ignore_deadline=True)
    assert witness is not None
    print(f"PASS: {passed} boundary orderings and {check_background_stalls()} phase stalls")
    print("Broken callback-order-only timeout:", witness)
