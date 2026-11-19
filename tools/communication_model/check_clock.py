"""Finite abstract clock model; standard library only.
Tick = half hour. Windows are atomic attempts, including failure, every hour.
Epoch 0/1 abstracts unknown/known external origin; no daylight/PWM is modeled.
No claim about physical drift, packet latency, competing sources or fairness.
"""
from dataclasses import dataclass, replace
from collections import deque
LIMIT = 3  # First expired age on the abstract half-hour grid.
PERIOD = 2

@dataclass(frozen=True)
class Board:
    epoch: int = 0
    age: int = LIMIT

    @property
    def clock_usable(self):
        return bool(self.epoch) and self.age < LIMIT

@dataclass(frozen=True)
class State:
    a: Board = Board()
    b: Board = Board()
    link: bool = False
    router: bool = False
    channel_ok: bool = True
    ntp: bool = False
    cooldown: int = 0
    # Age since latest external synchronization anywhere in the model.
    # Any board's claimed age must be at least this independently tracked age.
    origin_age: int = LIMIT


def successors(s, mutant=False):
    yield 'tick', replace(s,
        a=replace(s.a, age=min(LIMIT, s.a.age+1)),
        b=replace(s.b, age=min(LIMIT, s.b.age+1)),
        cooldown=max(0, s.cooldown-1),
        origin_age=min(LIMIT, s.origin_age+1))
    for field in ('link', 'router', 'channel_ok', 'ntp'):
        yield 'toggle_'+field, replace(s, **{field: not getattr(s, field)})
    yield 'reset_a', replace(s, a=Board(), cooldown=0)
    yield 'reset_b', replace(s, b=Board())
    if s.cooldown == 0:
        if s.router and s.channel_ok and s.ntp:
            yield 'window_ntp', replace(s, a=Board(1,0), origin_age=0, cooldown=PERIOD)
        else:
            yield 'window_failed', replace(s, cooldown=PERIOD)
    if s.link:
        for source, target in (('a','b'), ('b','a')):
            src, dst = getattr(s, source), getattr(s, target)
            if src.clock_usable and (not dst.clock_usable or src.age < dst.age):
                age = 0 if mutant else src.age
                yield 'relay_'+source+'_'+target, replace(s, **{target: Board(src.epoch,age)})


def check_transition(before, action, after, mutant=False):
    assert 0 <= after.cooldown <= PERIOD
    for board in (after.a, after.b):
        assert board.clock_usable == (board.epoch != 0 and board.age < LIMIT)
        assert 0 <= board.age <= LIMIT
        if board.epoch:
            assert board.age >= after.origin_age, 'clock younger than external-origin oracle'
    if action.startswith('reset_'):
        target = action[-1]
        assert not getattr(after, target).clock_usable
        other = 'b' if target == 'a' else 'a'
        assert getattr(before, other) == getattr(after, other)
    if action.startswith('relay_'):
        _, source, target = action.split('_')
        src, dst = getattr(before, source), getattr(after, target)
        assert dst.age >= src.age, 'relay rejuvenated external synchronization'
        assert dst.epoch == src.epoch
        assert getattr(after, source) == src
    if action.startswith('window_'):
        assert before.cooldown == 0 and after.cooldown == PERIOD
        assert after.b == before.b
        if action == 'window_ntp':
            assert before.router and before.channel_ok and before.ntp
            assert after.a == Board(1,0) and after.origin_age == 0
        else:
            assert not (before.router and before.channel_ok and before.ntp)
            assert before.a == after.a and before.origin_age == after.origin_age
    elif action != 'reset_a':
        expected = max(0,before.cooldown-1) if action == 'tick' else before.cooldown
        assert after.cooldown == expected
    if action == 'tick':
        for old, new in ((before.a, after.a), (before.b, after.b)):
            assert new.age == min(LIMIT, old.age+1)
            if old.age + 1 >= LIMIT:
                assert not new.clock_usable
    if action.startswith('toggle_'):
        assert before.a == after.a and before.b == after.b
    if after.cooldown:
        assert not any(name.startswith('window_') for name, _ in successors(after))


def run_trace(initial, actions):
    state = initial
    for action in actions:
        options = dict(successors(state))
        assert action in options, (action,state)
        nxt = options[action]
        check_transition(state,action,nxt)
        state = nxt
    return state


def recovery_checks():
    traces = {
        'a_to_rebooted_b': ('reset_b','relay_a_b'),
        'b_to_rebooted_a': ('reset_a','relay_b_a'),
        'late_router': ('window_failed','toggle_router','tick','tick','window_ntp','relay_a_b'),
    }
    for name, actions in traces.items():
        initial = State(a=Board(1,0),b=Board(1,0),link=True,origin_age=0)
        if name == 'late_router':
            initial = State(link=True,ntp=True)
        final = run_trace(initial,actions)
        assert final.a.clock_usable and final.b.clock_usable
    # A failed immediate attempt cannot be retried merely because router appears.
    waiting = run_trace(State(ntp=True),('window_failed','toggle_router','tick'))
    assert waiting.cooldown == 1
    assert not any(a.startswith('window_') for a,_ in successors(waiting))
    return traces


def explore(mutant=False):
    initial = State()
    queue = deque([initial])
    paths = {initial: ()}
    transitions = 0
    coverage = set()
    while queue:
        state = queue.popleft()
        for action, nxt in successors(state, mutant):
            transitions += 1
            coverage.add(action)
            path = paths[state] + (action,)
            try:
                check_transition(state,action,nxt,mutant)
            except AssertionError as exc:
                return {'passed':False,'states':len(paths),'transitions':transitions,
                        'counterexample':path,'error':str(exc)}
            if nxt not in paths:
                paths[nxt] = path
                queue.append(nxt)
    assert {'reset_a','reset_b','window_ntp','window_failed','relay_a_b','relay_b_a','tick'} <= coverage
    for a,b in ((True,True),(True,False),(False,True),(False,False)):
        assert any(s.a.clock_usable == a and s.b.clock_usable == b for s in paths)
    assert any(s.a.epoch and not s.a.clock_usable and s.b.epoch and not s.b.clock_usable for s in paths)
    return {'passed':True,'states':len(paths),'transitions':transitions,'actions':sorted(coverage)}


def main():
    good, bad = explore(), explore(mutant=True)
    assert good['passed'] and not bad['passed']
    print('BASE:',good)
    print('MUTANT:',bad)
    print('RECOVERY:',recovery_checks())
    return good,bad

if __name__ == '__main__':
    main()
