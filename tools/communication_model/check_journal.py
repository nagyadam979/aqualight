"""Finite log model: immutable batches, bounded ring, durable server, restart.
Python 3 standard library only. Exhaustive BFS; no timing/hardware claim.
IDs are never reused in this bounded execution; ring capacity is two.
"""
from collections import deque
from dataclasses import dataclass, replace
import struct
import zlib

CAPACITY = 2
LIMIT = 4

@dataclass(frozen=True)
class State:
    next_id: int = 0
    ring: tuple = ()
    batch: tuple = ()
    reply: tuple = ()
    server: frozenset = frozenset()
    acked: frozenset = frozenset()
    lost: frozenset = frozenset()


def transitions(s, broken=False):
    if s.next_id < LIMIT:
        record = s.next_id
        ring = s.ring + (record,)
        lost = s.lost
        if len(ring) > CAPACITY:
            evicted, *tail = ring
            ring = tuple(tail)
            if evicted not in s.acked:
                lost = lost | {evicted}
        yield f'append {record}', replace(s, next_id=record + 1,
                                         ring=ring, lost=frozenset(lost))
    pending = tuple(r for r in s.ring if r not in s.acked)
    if not s.batch and pending:
        yield f'capture batch {pending}', replace(s, batch=pending)
    if s.batch and not s.reply:
        yield 'server commit + reply', replace(
            s, server=s.server | frozenset(s.batch), reply=s.batch)
        yield 'server commit, reply lost', replace(
            s, server=s.server | frozenset(s.batch))
    if s.reply:
        # Correct ACK uses the captured IDs. The mutation wrongly uses the
        # current ring, as a stale physical-position ACK could do.
        ids = s.ring if broken else s.reply
        yield f'persist ACK {ids}', replace(
            s, acked=s.acked | frozenset(ids), batch=(), reply=())
    if s.batch or s.reply:
        yield 'restart / disconnect', replace(s, batch=(), reply=())


def invariant(s):
    # Independent oracle: local acknowledgement must have durable evidence
    # in the server's actual committed-ID set.
    assert s.acked <= s.server, 'ACK claims records absent from server'
    assert len(s.ring) <= CAPACITY
    assert len(set(s.ring)) == len(s.ring)
    # Every generated record is either locally retained, server durable,
    # or explicitly recorded as lost. In-flight state is not durable.
    generated = set(range(s.next_id))
    assert generated <= set(s.ring) | s.server | s.lost
    assert (set(s.ring) | s.server | s.acked | s.lost) <= generated


def check(broken=False):
    initial = State()
    queue = deque([initial])
    paths = {initial: ()}
    edges = 0
    while queue:
        state = queue.popleft()
        for action, nxt in transitions(state, broken):
            edges += 1
            path = paths[state] + (action,)
            try:
                invariant(nxt)
            except AssertionError as exc:
                return len(paths), edges, path, str(exc), nxt
            if nxt not in paths:
                paths[nxt] = path
                queue.append(nxt)
    return len(paths), edges, None, None, None


def check_crash_prefixes():
    # Example two-slot format. Stage in an empty slot and keep the prior
    # committed image. Try every byte-prefix cut and a one-bit change at every
    # written byte, then read the two slots through the recovery parser.
    marker = b"DONE"
    def encode(generation, value):
        payload = struct.pack(">II", generation, value)
        return payload + struct.pack(">I", zlib.crc32(payload)) + marker

    def decode(slot):
        if len(slot) != 16 or slot[-4:] != marker:
            return None
        payload = slot[:8]
        if struct.unpack(">I", slot[8:12])[0] != zlib.crc32(payload):
            return None
        return struct.unpack(">II", payload)

    old = encode(8, 700)
    staged = encode(9, 900)
    cases = 0
    for end in range(len(staged) + 1):
        for damage in (None, *range(end)):
            candidate = bytearray(staged[:end])
            if damage is not None:
                candidate[damage] ^= 1
            images = [decode(slot) for slot in (old, candidate)]
            recovered = max(item for item in images if item is not None)
            intact = end == len(staged) and damage is None
            assert recovered == ((9, 900) if intact else (8, 700))
            cases += 1
    return cases


if __name__ == '__main__':
    count, edges, path, _, _ = check()
    assert path is None
    print(f'CORRECT: PASS; {count} states, {edges} transitions')
    count, edges, path, error, state = check(broken=True)
    assert path is not None, 'Mutation was not detected'
    print(f'BROKEN ACK: counterexample ({len(path)} steps): {error}')
    for n, action in enumerate(path, 1):
        print(f'  {n}. {action}')
    print(f'  server={sorted(state.server)}, acked={sorted(state.acked)}, '
          f'ring={state.ring}')
    print(f'PASS: {check_crash_prefixes()} abstract write-cut and corruption cases')
