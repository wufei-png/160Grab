import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

import main as main_module
from grab.core.leader import LeaderLost, LocalLeader, check_leader, leader_scope
from grab.transactions.store import AttemptStore
from tests.transactions.test_submission import form, strategy

WORKER = '''
import sys, time
from grab.core.leader import LocalLeader, LeaderLost
from grab.transactions.store import AttemptStore
try:
    owner = LocalLeader(sys.argv[1]).acquire()
except LeaderLost:
    print('blocked', flush=True)
    sys.exit(2)
store = AttemptStore(sys.argv[2])
print('pending' if store.pending() else 'poll', flush=True)
if sys.argv[3] == 'submit':
    store.begin(store.reference('synthetic'))
    print('submit', flush=True)
time.sleep(60)
'''


def worker(tmp_path, action='poll'):
    return subprocess.Popen(
        [sys.executable, '-c', WORKER, str(tmp_path / 'locks'), str(tmp_path / 'journal'), action],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_real_processes_single_leader_crash_takeover_preserves_pending(tmp_path):
    first = worker(tmp_path, 'submit')
    try:
        assert first.stdout.readline().strip() == 'poll'
        assert first.stdout.readline().strip() == 'submit'
        second = worker(tmp_path)
        out, err = second.communicate(timeout=10)
        assert (second.returncode, out.strip(), err) == (2, 'blocked', '')
        first.kill()
        first.wait(timeout=10)
        third = worker(tmp_path)
        try:
            assert third.stdout.readline().strip() == 'pending'
            assert len(AttemptStore(tmp_path / 'journal').pending()) == 1
        finally:
            third.kill()
            third.wait(timeout=10)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=10)


async def test_expired_owner_never_revives_and_readonly_takeover(tmp_path):
    now = [0]
    owner = LocalLeader(tmp_path / 'locks', ttl=3, clock=lambda: now[0])
    async with leader_scope(owner):
        store = AttemptStore(tmp_path / 'journal')
        store.begin(store.reference('synthetic'))
        now[0] = 4
        with pytest.raises(LeaderLost):
            owner.renew()
        # Heartbeat releases the stale kernel owner; suspended task stays fenced.
        task = asyncio.create_task(owner.heartbeat())
        await task
        with pytest.raises(LeaderLost):
            check_leader()
        with pytest.raises(LeaderLost):
            store.revoke()
    replacement = LocalLeader(tmp_path / 'locks').acquire()
    try:
        assert AttemptStore(tmp_path / 'journal').pending()
    finally:
        replacement.release()


async def test_loss_during_prepare_means_no_click_or_journal(tmp_path):
    now = [0]
    obj = strategy(tmp_path / 'journal')
    async def expire(_action):
        now[0] = 10
    obj._sleep_page_action = expire
    async with leader_scope(LocalLeader(tmp_path / 'locks', ttl=3, clock=lambda: now[0])):
        with pytest.raises(LeaderLost):
            await obj.submit_open_form(form())
    assert obj.page.clicks == 0
    assert obj.attempt_store.pending() == []


async def test_cli_global_lock_blocks_other_profile_before_browser(tmp_path, monkeypatch, capsys):
    import grab.core.leader as module
    root = tmp_path / 'locks'
    owner = LocalLeader(root).acquire()
    original = LocalLeader
    monkeypatch.setattr(module, 'LocalLeader', lambda: original(root))
    def unexpected(*args, **kwargs):
        pytest.fail('Follower cannot start a profile/browser')
    monkeypatch.setattr(main_module, 'run_create_profile_flow', unexpected)
    try:
        with pytest.raises(SystemExit) as exc:
            await main_module.main(['--create-profile', '--profile-name', 'other'])
        assert exc.value.code == 2
        assert '互斥' in capsys.readouterr().out
    finally:
        owner.release()


def test_owner_nonce_and_lock_inode_tampering_fail_closed(tmp_path):
    owner = LocalLeader(tmp_path).acquire()
    try:
        Path(tmp_path / 'leader.lock').write_text('{}')
        with pytest.raises(LeaderLost):
            owner.check()
    finally:
        owner.release()
    owner = LocalLeader(tmp_path).acquire()
    try:
        os.replace(tmp_path / 'leader.lock', tmp_path / 'moved')
        (tmp_path / 'leader.lock').write_text('{}')
        with pytest.raises(LeaderLost):
            owner.check()
    finally:
        owner.release()


def test_private_lock_rejects_links_and_has_private_modes(tmp_path):
    root = tmp_path / 'locks'
    owner = LocalLeader(root).acquire()
    owner.release()
    if os.name == 'posix':
        assert root.stat().st_mode & 0o777 == 0o700
        assert (root / 'leader.lock').stat().st_mode & 0o777 == 0o600
    (root / 'leader.lock').unlink()
    (root / 'leader.lock').symlink_to(tmp_path / 'outside')
    with pytest.raises(LeaderLost):
        LocalLeader(root).acquire()
