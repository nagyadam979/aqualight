"""Two-slot OTA specification with a staged, local AUTO activation gate.

Image capability stands for the signed, target-specific AUTO contract: the
image understands the same persistent segment record and enforces it before
any automatic positive output. This models boot policy, not ESP bootloader
implementation, flash writes, signature verification, or clock arithmetic.
"""

from dataclasses import dataclass, replace
from itertools import product


CONTRACT = 1


@dataclass(frozen=True)
class Image:
    name: str
    target: str
    contract: int
    bounded_time_messages: bool = False
    signed: bool = True


@dataclass(frozen=True)
class Installed:
    image: Image
    accepted: bool = False


@dataclass(frozen=True)
class Board:
    target: str
    slots: tuple[Installed | None, Installed | None]
    recovery: Installed | None = None
    confirmed: int = 0
    running: int = 0  # 0/1 are OTA slots; 2 is the optional recovery image.
    phase: str = 'idle'
    incoming: Image | None = None
    armed: bool = False
    floor: int | None = 0
    live_segment: int | None = None

    @property
    def inactive(self):
        return 1 - self.confirmed

    @property
    def running_image(self):
        installed = self.recovery if self.running == 2 else self.slots[self.running]
        assert installed is not None
        return installed.image


def compatible(board, image):
    return image.signed and image.target == board.target and image.contract == CONTRACT


def with_slot(board, slot, value):
    slots = list(board.slots)
    slots[slot] = value
    return replace(board, slots=tuple(slots))


def arm(board):
    images = board.slots if board.recovery is None else (*board.slots, board.recovery)
    if board.phase != 'idle' or board.floor is None or not all(
        value is not None and value.accepted and compatible(board, value.image)
        for value in images
    ):
        return board
    return replace(board, armed=True)


def begin(board, image):
    if board.phase != 'idle' or board.running != board.confirmed:
        return board
    if not image.signed or image.target != board.target:
        return board
    # Reject an incompatible payload before touching the inactive slot. Even
    # if recovery chooses a complete inactive image, its guard must be intact.
    if (board.armed or compatible(board, board.running_image)) and not compatible(board, image):
        return board
    return with_slot(replace(board, phase='writing', incoming=image), board.inactive, None)


def verify(board):
    assert board.phase == 'writing' and board.incoming is not None
    # A "verified" slot represents a completely written, hash-verified image.
    return with_slot(replace(board, phase='verified', incoming=None), board.inactive,
                     Installed(board.incoming))


def select_trial(board):
    assert board.phase == 'verified'
    return replace(board, phase='pending')


def reboot(board, *, choose_recovery=False):
    if choose_recovery:
        assert board.recovery is not None
        if board.phase == 'trial':
            board = with_slot(board, board.inactive, None)
        return replace(board, running=2, phase='idle', incoming=None, live_segment=None)
    if board.phase == 'pending':
        return replace(board, running=board.inactive, phase='trial', live_segment=None)
    if board.phase == 'trial':
        board = with_slot(board, board.inactive, None)
    return replace(board, running=board.confirmed, phase='idle', incoming=None,
                   live_segment=None)


def accept_trial(board):
    assert board.phase == 'trial'
    slot = board.slots[board.running]
    assert slot is not None
    return with_slot(replace(board, phase='idle', confirmed=board.running),
                     board.running, replace(slot, accepted=True))


def start_segment(board, segment, *, bounded_time=True):
    """Run a fresh AUTO cycle, with abstract completed dual-write when needed."""
    if not bounded_time or segment is None:
        return replace(board, live_segment=None), False
    if not compatible(board, board.running_image):
        # Represents the old application. It trusts a usable apparent time.
        return board, True
    if not board.armed or board.floor is None or board.phase == 'trial':
        # A bridge waits for staged activation. Unreadable durable state or a
        # compatible trial also stays dark for local startup validation.
        return board, False
    if board.live_segment == segment:
        return board, True
    if segment <= board.floor:
        return replace(board, live_segment=None), False
    return replace(board, floor=segment, live_segment=segment), True


def armed_invariant(board):
    assert board.armed
    installed = (*board.slots, board.recovery)
    assert all(slot is None or compatible(board, slot.image) for slot in installed)
    assert compatible(board, board.running_image)
    assert board.slots[board.confirmed] is not None
    assert board.slots[board.confirmed].accepted


def image_set(target):
    return (Image('legacy', target, 0), Image('bridge', target, CONTRACT, True),
            Image('guarded-release', target, CONTRACT, True))


def install_and_accept(board, image):
    interrupted = begin(board, image)
    assert interrupted.phase == 'writing'
    return accept_trial(reboot(select_trial(verify(interrupted))))


def check_activation():
    cases = 0
    for target in ('terrarium', 'strip'):
        legacy, bridge, _ = image_set(target)
        for recovery in (None, Installed(legacy, True), Installed(bridge, True)):
            board = Board(target, (Installed(legacy, True), Installed(legacy, True)), recovery)
            assert not arm(board).armed
            board = install_and_accept(board, bridge)
            assert not arm(board).armed
            assert not start_segment(board, 1)[1]
            board = install_and_accept(board, bridge)
            board = arm(board)
            assert board.armed == (recovery is None or recovery.image == bridge)
            if board.armed:
                armed_invariant(board)
            cases += 1
    return cases


def check_ota_failures():
    cases = 0
    for target, recovery, cut, through_recovery in product(
        ('terrarium', 'strip'), (False, True), range(6), (False, True)
    ):
        if through_recovery and not recovery:
            continue
        _, bridge, release = image_set(target)
        good = Installed(bridge, True)
        board = arm(Board(target, (good, good), good if recovery else None))
        board, lit = start_segment(board, 1)
        assert lit
        board, lit = start_segment(board, None)
        assert not lit
        stages = (begin, verify, select_trial, reboot, accept_trial)
        for step in stages[:cut]:
            board = step(board, release) if step is begin else step(board)
            armed_invariant(board)

        if board.phase == 'trial':
            trial_state, lit = start_segment(board, 2)
            assert not lit and trial_state.floor == 1
        was_confirmed = board.confirmed
        board = reboot(board, choose_recovery=through_recovery)
        if board.phase == 'trial':
            board = reboot(board)  # Unconfirmed trial fails its local checks.
        assert board.running == (2 if through_recovery else was_confirmed)
        assert board.live_segment is None and board.floor == 1
        board, lit = start_segment(board, 1)
        assert not lit
        board, lit = start_segment(board, 2)
        assert lit and board.floor == 2
        armed_invariant(board)
        cases += 1
    return cases


def check_rejected_images():
    cases = 0
    for target in ('terrarium', 'strip'):
        legacy, bridge, release = image_set(target)
        good = Installed(bridge, True)
        board = arm(Board(target, (good, good)))
        for image in (legacy, replace(release, signed=False),
                      replace(release, target='wrong'), replace(release, contract=2)):
            assert begin(board, image) == board
            cases += 1
    return cases


def check_durable_state():
    cases = 0
    for target in ('terrarium', 'strip'):
        legacy, bridge, _ = image_set(target)
        good = Installed(bridge, True)
        board = arm(Board(target, (good, good), good))
        board, lit = start_segment(board, 1)
        assert lit
        board = reboot(board, choose_recovery=True)
        for failure in ('activation missing', 'segment unreadable'):
            if failure == 'activation missing':
                damaged = replace(board, armed=False)
            else:
                damaged = replace(board, floor=None)
            assert not start_segment(damaged, 1)[1]
            assert not start_segment(damaged, 2)[1]
            running_ota = reboot(damaged)
            assert begin(running_ota, legacy) == running_ota
            if failure == 'activation missing':
                recovered = arm(running_ota)
                assert recovered.armed and recovered.floor == 1
                assert not start_segment(recovered, 1)[1]
                assert start_segment(recovered, 2)[1]
            else:
                assert arm(running_ota) == running_ota
            cases += 1
    return cases


def check_mixed_versions():
    cases = 0
    for target, local_version, peer_version, peer_has_bound, local_has_bound in product(
        ('terrarium', 'strip'), (1, 2), range(3), (False, True), (False, True)
    ):
        local = image_set(target)[local_version]
        peer = image_set('strip' if target == 'terrarium' else 'terrarium')[peer_version]
        good = Installed(local, True)
        board = arm(Board(target, (good, good)))
        board, lit = start_segment(board, 1)
        assert lit
        board = reboot(board)
        peer_eligible = peer.bounded_time_messages and peer_has_bound
        bounded_time = local_has_bound or peer_eligible
        board, lit = start_segment(board, 1, bounded_time=bounded_time)
        assert not lit and board.floor == 1
        board, lit = start_segment(board, 2, bounded_time=bounded_time)
        assert lit == bounded_time
        assert board.floor == (2 if lit else 1)
        cases += 1
    return cases


def check_mutants():
    legacy, bridge, _ = image_set('strip')
    good = Installed(bridge, True)
    board = arm(Board('strip', (good, good)))
    board, lit = start_segment(board, 7)
    assert lit
    board = reboot(board)
    assert not start_segment(board, 7)[1]
    # A legacy recovery path bypasses even genuine persistent state.
    legacy_rescue = replace(board, recovery=Installed(legacy, True))
    assert start_segment(reboot(legacy_rescue, choose_recovery=True), 7)[1]
    # A signed bridge cannot restore the safety record from old app settings.
    assert start_segment(replace(board, floor=6), 7)[1]
    # When an old clock packet is treated as bounded, a later but already
    # elapsed section can switch on. A same-segment check alone misses this.
    assert not start_segment(board, 8, bounded_time=False)[1]
    assert start_segment(board, 8, bounded_time=True)[1]


if __name__ == '__main__':
    print(f'PASS: {check_activation()} staged activation cases')
    print(f'PASS: {check_ota_failures()} OTA cut / trial rollback / recovery cases')
    print(f'PASS: {check_rejected_images()} rejected image cases')
    print(f'PASS: {check_durable_state()} missing activation / unreadable marker cases')
    print(f'PASS: {check_mixed_versions()} mixed-image clock cases')
    check_mutants()
    print('MUTANTS: legacy boot, stale marker restore and unbounded peer time relight')
