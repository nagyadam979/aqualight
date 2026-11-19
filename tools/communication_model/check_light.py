"""Executable requirement model, not firmware validation. Import is inert.
Policy choices: manual is absolute; OFF persists. A saved AUTO program boots
dark until synchronized. Reset ends a manual override. OTA keeps control alive.
"""
from dataclasses import dataclass, replace
from itertools import permutations, product

@dataclass(frozen=True)
class State:
    valid: bool = True
    night: bool = False
    running: bool = True
    mode: str = 'AUTO'
    manual: int = 0
    pwm: int = 100
    ota: str = 'IDLE'


def expected(s):
    if s.mode == 'OFF':
        return 0
    if s.mode == 'MANUAL':
        return s.manual
    return 100 if s.valid and not s.night else 0


def allowed(s, event):
    if event == 'ota_begin':
        return s.ota == 'IDLE'
    if event in ('ota_abort', 'ota_verify'):
        return s.ota == 'TRANSFER'
    if event == 'ota_reboot':
        return s.ota == 'VERIFIED'
    if event == 'watchdog':
        return not s.running
    if event == 'cycle':
        return s.running
    return True


def step(s, event):
    if not allowed(s, event):
        raise ValueError('precondition failed: ' + event)
    if event == 'evening':
        return replace(s, night=True)
    if event == 'sync':
        return replace(s, valid=True)
    if event in ('off', 'manual_60', 'auto'):
        mode = {'off': 'OFF', 'manual_60': 'MANUAL', 'auto': 'AUTO'}[event]
        # Identical absolute command is an idempotent no-op.
        if s.mode == mode and (mode != 'MANUAL' or s.manual == 60):
            return s
        return replace(s, mode=mode, manual=60 if mode == 'MANUAL' else s.manual)
    if event == 'cycle':
        return replace(s, pwm=expected(s))
    if event == 'stall':
        return replace(s, running=False)
    if event in ('watchdog', 'cold_boot', 'ota_reboot'):
        return State(valid=False, night=s.night, pwm=0,
                     mode='AUTO' if s.mode == 'AUTO' else 'OFF')
    if event == 'ota_begin':
        return replace(s, ota='TRANSFER')
    if event == 'ota_abort':
        return replace(s, ota='IDLE')
    if event == 'ota_verify':
        return replace(s, ota='VERIFIED')
    raise ValueError(event)


def run_trace(events):
    state = State()
    history = []
    for event in events:
        before = state
        state = step(state, event)
        history.append((event, before, state))
    return history


def oracle(history):
    for event, before, after in history:
        if event in ('cold_boot', 'ota_reboot', 'watchdog'):
            assert after.pwm == 0, 'reset must be dark'
            if before.mode != 'AUTO':
                assert after.mode == 'OFF', 'saved OFF or interrupted manual must boot OFF'
        if after.mode == 'AUTO' and not after.valid:
            assert after.pwm == 0, 'AUTO without time must remain dark'
        if event == 'cycle':
            # Independent mode table used as expected behavior.
            target = {'OFF': 0, 'MANUAL': after.manual,
                      'AUTO': 100 if after.valid and not after.night else 0}[after.mode]
            assert after.pwm == target, 'cycle must apply current state'
        if event == 'manual_60' and before.mode == 'MANUAL' and before.manual == 60:
            assert before == after, 'absolute command repeat must be idempotent'
        if before.ota == 'TRANSFER' and event == 'cycle':
            assert after.ota == 'TRANSFER', 'control must run during transfer'


def check():
    traces = set()
    # Only a control cycle computes and writes a scheduled output. Radio and
    # OTA events may occur on either side of a cycle, never enqueue a PWM value.
    for outcome in (('ota_begin', 'ota_abort'), ('ota_begin', 'ota_verify', 'ota_reboot')):
        for order in permutations(('evening', 'cycle') + outcome):
            if tuple(event for event in order if event.startswith('ota_')) == outcome:
                traces.add(order + ('cycle',))
    for order in permutations(('evening', 'stall', 'watchdog')):
        if order.index('stall') < order.index('watchdog'):
            traces.add(order + ('sync', 'cycle'))
    for mode in ('auto', 'off', 'manual_60'):
        traces.add((mode, 'cycle', 'cold_boot', 'sync', 'cycle'))
        traces.add((mode, 'cycle', mode, 'cycle', 'off', 'cycle'))
    for trace in sorted(traces):
        history = run_trace(trace)
        oracle(history)
        if 'evening' in trace:
            assert history[-1][2].pwm == 0, trace
    print(f'PASS: {len(traces)} guarded control, reset and OTA traces')
    return len(traces)


def run_watchdog(channels, writes_at_dusk, completion_at, activity_times,
                 *, feed_on_activity=False):
    """One board stalls partway through dusk; the other keeps running.

    Times are virtual. The target starts with a missed-cycle deadline derived
    from its last full write. Only a full cycle can extend the deadline.
    """
    outputs = [100] * channels
    peer = [100] * (3 - channels)
    resets = []
    deadline = 300
    for now in range(301):
        # The deadline is absolute even if a completion arrives at that instant.
        if now == deadline:
            outputs = [0] * channels
            resets.append(now)
        if now == 100:
            peer = [0] * len(peer)
            outputs[:writes_at_dusk] = [0] * writes_at_dusk
        if completion_at == now < deadline:
            outputs = [0] * channels
            deadline = now + 300
        if feed_on_activity and now in activity_times:
            deadline = now + 300
    return outputs, peer, resets


def check_watchdog():
    cases = 0
    activities = ((), (150, 200, 250), (299, 300))
    for channels in (1, 2):
        for completed, all_written_at, busy in product(
                range(channels + 1), (None, 299, 300), activities):
            outputs, peer, resets = run_watchdog(channels, completed, all_written_at, busy)
            assert outputs == [0] * channels and peer == [0] * (3 - channels)
            assert resets == ([] if all_written_at == 299 else [300])
            cases += 1
    outputs, peer, resets = run_watchdog(2, 1, None, activities[1], feed_on_activity=True)
    assert outputs == [0, 100] and peer == [0] and not resets
    print(f'PASS: {cases} watchdog deadline cases; activity-fed watchdog leaves one channel lit')
    return cases


if __name__ == '__main__':
    check()
    check_watchdog()
