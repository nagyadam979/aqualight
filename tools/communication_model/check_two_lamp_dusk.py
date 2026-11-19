"""Cross two local PWM schedulers with sensor reservations and network events.

The clock uses virtual milliseconds around one evening. Each channel has a
positive last daylight value and a dark segment lasting beyond the test.
"""

from itertools import product

from check_dusk_budget import CYCLE_MS, PERIOD_MS, Work, simulate
from check_dusk_network import ClockReference, run_network
from check_sensor_budget import Response, SlotSpec, simulate_slots


DUSK = 1200
END = 1550
BOARDS = ('terrarium', 'strip')


def sensor_intervals(retry):
    spec = SlotSpec(0, Response(None), Response(220)) if retry else SlotSpec(0, Response(20))
    result = simulate_slots([spec])
    assert result.slots[0].attempts == (2 if retry else 1)
    intervals = [(start, end) for start, end, _ in result.reservations]
    assert any(start <= DUSK < end for start, end in intervals) == retry
    return intervals


def check_board(board, phase, cutoffs, busy, jobs, network):
    args = dict(board_phase_ms=phase, local_cutoff_ms=cutoffs, channels=len(cutoffs),
                until_ms=END, sensor_start_cost_ms=1, sensor_busy=busy,
                valid_at=lambda at: network.usable_at(board, at))
    run = simulate(**args, workload=jobs)
    idle = simulate(**args)
    assert run.outputs == idle.outputs, (board, phase, cutoffs, jobs)
    for ch, cutoff in enumerate(cutoffs):
        writes = [(at, duty) for at, channel, duty in run.outputs if channel == ch]
        assert any(duty > 0 for at, duty in writes if at < cutoff)
        first_zero = next(at for at, duty in writes if duty == 0)
        assert first_zero <= cutoff + PERIOD_MS + 1 + CYCLE_MS
        assert all(duty == 0 for at, duty in writes if at >= first_zero)
        for (_, started, ended), (at, duty) in zip(run.cycles, writes):
            assert at == ended
            assert (duty > 0) == (started < cutoff and network.usable_at(board, started))
    return run


def check():
    cases = 0
    saw_flash_across_dusk = saw_retry_with_writer_waiting = saw_pending_after_dusk = False
    intervals = {retry: sensor_intervals(retry) for retry in (False, True)}
    fresh = ClockReference(0, 10_000)
    for link, offset, recovery, request, phases, retries, log in product(
            ('wifi', 'peer', 'both'), (-1, 0, 1), (None, DUSK + 30),
            ('ack', 'ntp', 'peer_time'), ((0, 37), (99, 0), (37, 99)),
            product((False, True), repeat=2), (False, True)):
        requester = 'strip' if request == 'peer_time' else 'terrarium'
        network = run_network(outage_kind=link, outage_ms=DUSK + offset,
                              reconnect_ms=recovery, phase=request,
                              timeout_ms=DUSK + 90, reply_ms=DUSK + 45,
                              initial_references={board: fresh for board in BOARDS},
                              reply_reference=ClockReference(DUSK + 45, 10_045))
        affected = link == 'both' or link == ('peer' if requester == 'strip' else 'wifi')
        assert bool(network.accepted) == (not affected)
        assert network.acked == int(request == 'ack' and not affected)
        # The request is already in flight when dusk approaches. Its network
        # wait consumes no control CPU. Model short driver sections at events,
        # on the board(s) that handle that link, even when a reply is rejected.
        assert network.state_at(requester, DUSK - 2).pending
        saw_pending_after_dusk |= network.state_at(requester, DUSK + 2).pending
        for board, phase, retry in zip(BOARDS, phases, retries):
            jobs = []
            if log and board == 'terrarium':
                jobs.append(Work(DUSK - 14, 24, 'journal'))
            for at, event in network.trace:
                is_link_event = '=' in event
                if is_link_event and (board == 'terrarium' or event.startswith('peer=')):
                    jobs.append(Work(at, 4, 'link_driver'))
                elif not is_link_event and board == requester:
                    jobs.append(Work(at, 3, 'request_driver'))
            cutoffs = (DUSK, DUSK + 24) if board == 'terrarium' else (DUSK,)
            run = check_board(board, phase, cutoffs, intervals[retry], jobs, network)
            if log and board == 'terrarium':
                start, end, _ = next(item for item in run.reservations if item[2] == 0)
                saw_flash_across_dusk |= start < DUSK < end
                if retry:
                    assert start >= intervals[True][-1][1]
                    saw_retry_with_writer_waiting = True
        cases += 1
    assert saw_flash_across_dusk and saw_retry_with_writer_waiting and saw_pending_after_dusk

    # Each clock can expire separately while the links are down. A lost ACK
    # cannot refresh either clock. The board with expired time goes dark early.
    for expired in BOARDS:
        expiry = DUSK - 220
        network = run_network(outage_ms=DUSK - 2, phase='ack', reply_ms=DUSK + 4,
                              timeout_ms=DUSK + 20, initial_references={
                                  name: ClockReference(0, expiry) if name == expired else fresh
                                  for name in BOARDS})
        for board in BOARDS:
            cutoffs = (DUSK, DUSK) if board == 'terrarium' else (DUSK,)
            run = check_board(board, 0, cutoffs, intervals[True], (), network)
            first_zero = next(at for at, _, value in run.outputs if value == 0)
            if board == expired:
                assert expiry <= first_zero <= expiry + PERIOD_MS + 1 + CYCLE_MS
            else:
                assert DUSK <= first_zero
        cases += 1
    return cases


if __name__ == '__main__':
    print(f'PASS: {check()} two-board dusk scenarios, three PWM outputs checked per case')
