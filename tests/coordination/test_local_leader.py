import asyncio
import os
import subprocess
import sys
import threading

import pytest

import main as main_module
from grab.core.leader import LeaderLost, LocalLeader, check_leader, leader_scope
from grab.transactions.store import AttemptStore
from tests.transactions.test_submission import form, strategy

WORKER = """
import asyncio, sys
from grab.core.leader import LocalLeader, LeaderLost, leader_scope
from grab.transactions.store import AttemptStore
from tests.transactions.test_submission import strategy, form
async def run():
    async with leader_scope(LocalLeader(sys.argv[1])):
        store = AttemptStore(sys.argv[2])
        if store.pending():
            print('pending', flush=True)
        else:
            print('poll', flush=True)
            if sys.argv[3] == 'submit':
                obj = strategy(sys.argv[2])
                result = await obj.submit_open_form(form())
                assert result.state == 'OUTCOME_UNKNOWN' and obj.page.clicks == 1
                print('submit', flush=True)
        await asyncio.sleep(60)
try:
    asyncio.run(run())
except LeaderLost:
    print('blocked', flush=True)
    sys.exit(2)
"""


def worker(tmp_path, action="poll"):
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            WORKER,
            str(tmp_path / "locks"),
            str(tmp_path / "journal"),
            action,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_real_processes_single_leader_crash_takeover_preserves_pending(tmp_path):
    first = worker(tmp_path, "submit")
    try:
        assert first.stdout.readline().strip() == "poll"
        assert first.stdout.readline().strip() == "submit"
        second = worker(tmp_path)
        out, err = second.communicate(timeout=10)
        assert (second.returncode, out.strip(), err) == (2, "blocked", "")
        first.kill()
        first.wait(timeout=10)
        third = worker(tmp_path)
        try:
            assert third.stdout.readline().strip() == "pending"
            assert len(AttemptStore(tmp_path / "journal").pending()) == 1
        finally:
            third.kill()
            third.wait(timeout=10)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=10)


async def test_expired_owner_never_revives_and_readonly_takeover(tmp_path):
    now = [0]
    owner = LocalLeader(tmp_path / "locks", ttl=3, clock=lambda: now[0])
    async with leader_scope(owner):
        store = AttemptStore(tmp_path / "journal")
        store.begin(store.reference("synthetic"))
        now[0] = 4
        with pytest.raises(LeaderLost):
            owner.renew()
        # Heartbeat releases the stale kernel owner; suspended task stays fenced.
        for _ in range(150):
            if owner.fd is None:
                break
            await asyncio.sleep(0.01)
        assert owner.fd is None
        with pytest.raises(LeaderLost):
            check_leader()
        with pytest.raises(LeaderLost):
            store.revoke()
    replacement = LocalLeader(tmp_path / "locks").acquire()
    try:
        assert AttemptStore(tmp_path / "journal").pending()
    finally:
        replacement.release()


async def test_loss_during_prepare_means_no_click_or_journal(tmp_path):
    now = [0]
    obj = strategy(tmp_path / "journal")

    async def expire(_action):
        now[0] = 10

    obj._sleep_page_action = expire
    async with leader_scope(
        LocalLeader(tmp_path / "locks", ttl=3, clock=lambda: now[0])
    ):
        with pytest.raises(LeaderLost):
            await obj.submit_open_form(form())
    assert obj.page.clicks == 0
    assert obj.attempt_store.pending() == []


async def test_cli_global_lock_blocks_other_profile_before_browser(
    tmp_path, monkeypatch, capsys
):
    import grab.core.leader as module

    root = tmp_path / "locks"
    owner = LocalLeader(root).acquire()
    original = LocalLeader
    monkeypatch.setattr(module, "LocalLeader", lambda: original(root))

    def unexpected(*args, **kwargs):
        pytest.fail("Follower cannot start a profile/browser")

    monkeypatch.setattr(main_module, "run_create_profile_flow", unexpected)
    try:
        with pytest.raises(SystemExit) as exc:
            await main_module.main(["--create-profile", "--profile-name", "other"])
        assert exc.value.code == 2
        assert "互斥" in capsys.readouterr().out
    finally:
        owner.release()


def test_owner_nonce_and_lock_inode_tampering_fail_closed(tmp_path):
    owner = LocalLeader(tmp_path).acquire()
    try:
        # Windows rejects writes through another handle while byte zero is locked.
        # Corrupt the owner's metadata through its own handle on every platform.
        os.lseek(owner.fd, 0, os.SEEK_SET)
        os.write(owner.fd, b"{}")
        os.ftruncate(owner.fd, 2)
        with pytest.raises(LeaderLost):
            owner.check()
    finally:
        owner.release()
    owner = LocalLeader(tmp_path).acquire()
    try:
        if os.name == "nt":
            with pytest.raises(PermissionError):
                os.replace(tmp_path / "leader.lock", tmp_path / "moved")
            owner.check()
            return
        os.replace(tmp_path / "leader.lock", tmp_path / "moved")
        (tmp_path / "leader.lock").write_text("{}")
        with pytest.raises(LeaderLost):
            owner.check()
    finally:
        owner.release()


def test_private_lock_rejects_links_and_has_private_modes(tmp_path):
    root = tmp_path / "locks"
    owner = LocalLeader(root).acquire()
    owner.release()
    if os.name == "posix":
        assert root.stat().st_mode & 0o777 == 0o700
        assert (root / "leader.lock").stat().st_mode & 0o777 == 0o600
    (root / "leader.lock").unlink()
    (root / "leader.lock").symlink_to(tmp_path / "outside")
    with pytest.raises(LeaderLost):
        LocalLeader(root).acquire()


async def test_loss_after_click_preserves_unknown_state_and_pending(tmp_path):
    now = [0]
    obj = strategy(tmp_path / "journal")
    original_locator = obj.page.locator

    def locator(selector):
        control = original_locator(selector)
        original_click = control.click

        async def click(**kwargs):
            await original_click(**kwargs)
            now[0] = 10

        control.click = click
        return control

    obj.page.locator = locator
    async with leader_scope(
        LocalLeader(tmp_path / "locks", ttl=3, clock=lambda: now[0])
    ):
        result = await obj.submit_open_form(form())
        assert result.state == "OUTCOME_UNKNOWN"
        assert obj.page.clicks == 1
    assert obj.attempt_store.pending()[0]["state"] == "SUBMITTING"
    restarted = strategy(tmp_path / "journal")
    assert (await restarted.submit_open_form(form())).state == "OUTCOME_UNKNOWN"
    assert restarted.page.clicks == 0


def test_replaced_lock_directory_fences_old_owner(tmp_path):
    root = tmp_path / "locks"
    owner = LocalLeader(root).acquire()
    try:
        if os.name == "nt":
            with pytest.raises(PermissionError):
                root.rename(tmp_path / "old-locks")
            owner.check()
            return
        root.rename(tmp_path / "old-locks")
        root.mkdir()
        replacement = LocalLeader(root).acquire()
        try:
            with pytest.raises(LeaderLost):
                owner.check()
        finally:
            replacement.release()
    finally:
        owner.release()


async def test_expired_session_refresh_does_not_issue_second_touch(tmp_path):
    from grab.models.schemas import GrabConfig
    from grab.services.session import SessionCaptureService

    now = [0]
    obj = strategy(tmp_path / "journal")
    obj.target.source_url = "https://synthetic.invalid/doctor"
    session = SessionCaptureService(obj.page, GrabConfig())
    touches = []

    async def touch(url, **kwargs):
        touches.append(url)
        now[0] = 10
        return url, None

    session._touch_url_with_browser_page = touch
    async with leader_scope(
        LocalLeader(tmp_path / "locks", ttl=3, clock=lambda: now[0])
    ):
        with pytest.raises(LeaderLost):
            await session.refresh_session_for_polling(obj.target)
    assert len(touches) == 1


async def test_synchronous_consent_wait_keeps_leader_and_single_click(tmp_path):
    from grab.transactions.consent import ConsentManager

    obj = strategy(tmp_path / "journal")
    now = [0.0]
    owner = LocalLeader(tmp_path / "locks", ttl=0.12, clock=lambda: now[0])
    renewed = threading.Event()
    clock_lock = threading.Lock()
    native_renew = owner.renew

    def renew():
        with clock_lock:
            native_renew()
            renewed.set()

    owner.renew = renew

    def prompt(_message):
        # Block asyncio beyond the original TTL, waiting for real thread renewals.
        # The controlled clock avoids a short wall-clock deadline on busy runners.
        for _ in range(3):
            with clock_lock:
                renewed.clear()
                now[0] += 0.08
            assert renewed.wait(5), "Heartbeat did not renew during blocking input"
        assert now[0] > owner.ttl
        return "AUTHORIZE"

    consent = ConsentManager(obj.attempt_store, prompt=prompt, interactive=True)
    obj.authorization = consent.ensure
    obj.consent_manager = consent
    async with leader_scope(owner):
        result = await obj.submit_open_form(form())
        assert result.state == "OUTCOME_UNKNOWN"
        assert obj.page.clicks == 1
        check_leader()


@pytest.mark.parametrize("loss", ["poll", "cooldown"])
async def test_runner_loss_never_reenters_manual_login(tmp_path, loss):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from grab.core.runner import GrabRunner
    from grab.errors import SessionExpiredError
    from grab.models.schemas import BookingResult, GrabConfig
    from grab.services.auth import AuthService
    from tests.core.test_runner import FakeBookingService, FakeSessionService

    owner = LocalLeader(tmp_path / "locks")
    navigations = []

    async def goto(url):
        navigations.append(url)

    class Schedule:
        def set_target(self, target):
            pass

        async def poll(self):
            owner.release()
            raise SessionExpiredError("synthetic expired response after loss")
            yield []

    async def cooldown(_seconds):
        owner.release()

    config = GrabConfig()
    config.browser.session_recovery_cooldown_seconds = 1
    runner = GrabRunner(
        AuthService(SimpleNamespace(goto=goto), config),
        FakeSessionService(config=config),
        SimpleNamespace(wait_until_ready=AsyncMock()),
        Schedule(),
        FakeBookingService(BookingResult(state="AWAITING_MANUAL_CONFIRMATION")),
        sleep=cooldown,
    )
    async with leader_scope(owner):
        if loss == "poll":
            assert (await runner.run()).state == "AWAITING_MANUAL_CONFIRMATION"
        else:
            with pytest.raises(LeaderLost):
                await runner._recover_from_session_expiry(
                    SessionExpiredError("synthetic"), attempt=2
                )
    assert len(navigations) == (1 if loss == "poll" else 0)
