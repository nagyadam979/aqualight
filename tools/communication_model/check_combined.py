"""Cross the clock, output, and durable journal transitions using virtual events."""

from dataclasses import replace
from itertools import permutations, product

import check_clock as clock
import check_journal as journal
import check_light as light


def run(order, target, linked, initial_age):
    donor = "b" if target == "a" else "a"
    boards = clock.State(a=clock.Board(1, initial_age), b=clock.Board(1, initial_age),
                         link=linked, origin_age=initial_age)
    lamps = {name: light.State() for name in ("a", "b")}
    # ID 0 is on the server, but its reply is waiting to be delivered.
    log = journal.State(next_id=1, ring=(0,), batch=(0,), reply=(0,), server=frozenset({0}))
    dark = False
    for event in order:
        before = boards
        if event == "boot":
            boards = dict(clock.successors(boards))["reset_" + target]
            lamps[target] = light.step(lamps[target], "cold_boot")
            if target == "a" and log.reply:
                log = dict(journal.transitions(log))["restart / disconnect"]
        elif event == "peer":
            boards = dict(clock.successors(boards)).get(f"relay_{donor}_{target}", boards)
        elif event == "tick":
            boards = dict(clock.successors(boards))["tick"]
        elif event == "evening":
            dark = True
            lamps = {name: light.step(lamp, "evening") for name, lamp in lamps.items()}
        elif event == "close_record":
            log = dict(journal.transitions(log))["append 1"]
        elif event == "deliver_ack" and log.reply:
            log = dict(journal.transitions(log))["persist ACK (0,)"]

        # Each event is followed by a successful local control cycle. The
        # separate light model explores stalls, watchdog recovery and OTA.
        for name in lamps:
            clock_ok = getattr(boards, name).clock_usable
            if clock_ok != lamps[name].valid:
                lamps[name] = replace(lamps[name], valid=clock_ok)
            lamps[name] = light.step(lamps[name], "cycle")
            assert lamps[name].pwm == (100 if clock_ok and not dark else 0)
        if event == "boot":
            assert getattr(boards, donor) == getattr(before, donor)
            assert lamps[target].pwm == 0
        journal.invariant(log)

    # Independent end-of-trace oracle calculated from event positions. Without
    # NTP, the donor has aged one tick; the reset board needs a post-boot relay.
    fresh = initial_age + 1 < clock.LIMIT
    assert getattr(boards, donor).clock_usable == fresh
    assert getattr(boards, target).clock_usable == (fresh and linked and
                                                    order.index("peer") > order.index("boot"))
    assert lamps["a"].pwm == lamps["b"].pwm == 0
    assert log.ring == (0, 1) and log.server == {0}
    ack_survives = target == "b" or order.index("deliver_ack") < order.index("boot")
    assert log.acked == ({0} if ack_survives else set())


if __name__ == "__main__":
    events = ("boot", "peer", "tick", "evening", "close_record", "deliver_ack")
    count = 0
    for target, linked, age in product(("a", "b"), (False, True), (0, clock.LIMIT - 1)):
        for order in permutations(events):
            run(order, target, linked, age)
            count += 1
    print(f"PASS: {count} cross-model event orderings")
