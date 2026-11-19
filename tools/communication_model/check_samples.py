"""One conversion is outstanding across a ten-minute window boundary."""

from dataclasses import dataclass
from itertools import permutations, product


@dataclass(frozen=True)
class Slot:
    boot: int
    due: int

    @property
    def window(self):
        return self.boot, self.due // 600


def check(use_completion_time=False):
    count = 0
    for wall_step, restart, read_ok in product((-3600, 0, 3600), (False, True), (False, True)):
        for order in permutations(("clock_update", "boundary", "result", "restart_or_noop")):
            # Slot 595 belongs to the closing bucket. A notification at or
            # after 600 is stale. A fresh boot also invalidates the old token.
            token = Slot(1, 595)
            boot, monotonic, utc_offset = 1, 599, 0
            outcomes = {token: "pending"}
            buckets = {}
            for event in order:
                if event == "boundary":
                    monotonic = 600 if boot == 1 else 5
                    if outcomes[token] == "pending":
                        outcomes[token] = "timeout"
                    outcomes[Slot(boot, monotonic)] = "pending"
                elif event == "clock_update":
                    utc_offset += wall_step
                elif event == "restart_or_noop" and restart:
                    for slot in list(outcomes):
                        if outcomes[slot] == "pending":
                            outcomes[slot] = "interrupted"
                    boot, monotonic = 2, 0
                    outcomes[Slot(boot, monotonic)] = "pending"
                elif (event == "result" and token.boot == boot and
                      monotonic < token.due + 5 and outcomes[token] == "pending"):
                    outcomes[token] = "valid" if read_ok else "error"
                    key = token.window
                    if use_completion_time:
                        key = (boot, (monotonic + utc_offset) // 600)
                    buckets.setdefault(key, []).append(token)

            terminal = {"result", "boundary"}
            if restart:
                terminal.add("restart_or_noop")
            first = next(event for event in order if event in terminal)
            completed = first == "result"
            expected = "timeout" if first == "boundary" else "interrupted"
            if completed:
                expected = "valid" if read_ok else "error"
            expected_buckets = {(1, 0): [token]} if completed else {}
            count += 1
            if outcomes[token] != expected or buckets != expected_buckets:
                return count, (order, wall_step, restart, read_ok, outcomes[token], buckets)
    return count, None


if __name__ == "__main__":
    cases, failure = check()
    assert failure is None, failure
    _, bad = check(use_completion_time=True)
    assert bad is not None
    print(f"PASS: {cases} sample/window/restart orderings")
    print("Broken completion-time bucketing:", bad)
