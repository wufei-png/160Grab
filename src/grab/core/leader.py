"""One leader per local OS user, independent of browser profile and target.

The kernel lock is authoritative; metadata is never used as a storage CAS.
A stale live owner fences itself before releasing. Never unlink the lock inode.
"""

import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from grab.utils.private_files import private_directory

if os.name == "posix":
    import pwd

    # Key the default by OS identity, independent of config/profile/HOME overrides.
    DEFAULT_LEADER_DIR = (
        Path(pwd.getpwuid(os.getuid()).pw_dir) / ".160grab/coordination"
    )
else:
    DEFAULT_LEADER_DIR = Path.home() / ".160grab/coordination"
_current = ContextVar("grab_leader", default=None)


class LeaderLost(RuntimeError):
    def __init__(self):
        super().__init__("Automatic operation paused; local leader unavailable")


def _synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._mutex:
            return method(self, *args, **kwargs)

    return wrapped


class LocalLeader:
    def __init__(self, root=DEFAULT_LEADER_DIR, *, ttl=30, clock=time.monotonic):
        if ttl <= 0:
            raise ValueError("Invalid leader TTL")
        self.root, self.ttl, self.clock = root, ttl, clock
        self.nonce = secrets.token_hex(16)
        self.fd = None
        self.deadline = 0
        self._directory_context = None
        self._mutex = threading.RLock()

    @_synchronized
    def acquire(self):
        if self.fd is not None:
            raise LeaderLost()
        try:
            self._directory_context = private_directory(self.root)
            self.directory = self._directory_context.__enter__()
            self.fd = self.directory._open("leader.lock", os.O_RDWR | os.O_CREAT)
            if os.name == "posix":
                import fcntl

                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif os.name == "nt":
                import msvcrt

                # Keep byte zero present; lock that byte for the whole lifetime.
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b" ")
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                raise LeaderLost()
            self.deadline = self.clock() + self.ttl
            self._write_owner()
            return self
        except (OSError, ValueError, LeaderLost) as exc:
            self.release()
            raise LeaderLost() from exc

    @_synchronized
    def _write_owner(self):
        raw = json.dumps({"version": 1, "owner": self.nonce}).encode()
        os.lseek(self.fd, 0, os.SEEK_SET)
        if os.write(self.fd, raw) != len(raw):
            raise LeaderLost()
        os.ftruncate(self.fd, len(raw))

    @_synchronized
    def check(self):
        try:
            if self.fd is None or self.clock() >= self.deadline:
                raise LeaderLost()
            opened = os.fstat(self.fd)
            with private_directory(self.root, create=False) as current_directory:
                named = current_directory.stat("leader.lock")
            if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                raise LeaderLost()
            os.lseek(self.fd, 0, os.SEEK_SET)
            if json.loads(os.read(self.fd, 256)) != {"version": 1, "owner": self.nonce}:
                raise LeaderLost()
        except (OSError, ValueError, TypeError) as exc:
            raise LeaderLost() from exc

    @_synchronized
    def renew(self):
        self.check()  # An expired owner cannot renew itself back into leadership.
        self.deadline = self.clock() + self.ttl

    @_synchronized
    def release(self):
        if self.fd is not None:
            os.close(self.fd)  # Kernel releases the lock, also on process crash.
            self.fd = None
        if self._directory_context is not None:
            context = self._directory_context
            self._directory_context = None
            context.__exit__(None, None, None)

    def heartbeat(self, stop):
        # input() intentionally blocks asyncio during manual login/authorization.
        # A separate thread renews there; a truly suspended process cannot renew.
        while not stop.wait(self.ttl / 3):
            try:
                self.renew()
            except LeaderLost:
                self.release()
                return


def check_active_leader():
    if _current.get() is not None:
        check_leader()


def check_leader():
    leader = _current.get()
    if leader is None:
        raise LeaderLost()
    leader.check()


@asynccontextmanager
async def leader_scope(leader=None):
    existing = _current.get()
    if existing is not None:
        existing.check()
        yield existing
        return
    leader = (leader or LocalLeader()).acquire()
    token = _current.set(leader)
    stop = threading.Event()
    heartbeat = threading.Thread(
        target=leader.heartbeat, args=(stop,), name="grab-leader", daemon=True
    )
    try:
        heartbeat.start()
        yield leader
    finally:
        stop.set()
        if heartbeat.ident is not None:
            heartbeat.join()
        _current.reset(token)
        leader.release()


def exclusive_operation(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        async with leader_scope():
            return await function(*args, **kwargs)

    return wrapped
