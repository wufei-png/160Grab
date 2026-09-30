"""Private application files, with anchored no-follow operations on POSIX.

Existing directories are never recursively chmod'ed. Windows rejects observed
reparse points but mode bits do not establish a Windows ACL.
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path


def absolute_path(path: str | Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def _no_link(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise OSError("Linked application path is not allowed")


def _leaf(name: str) -> str:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("Invalid application filename")
    return name


class PrivateDirectory:
    def __init__(self, path: Path, fd: int | None):
        self.path, self.fd = path, fd

    def _open(self, name: str, flags: int) -> int:
        name = _leaf(name)
        if self.fd is not None:
            fd = os.open(
                name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.fd
            )
        else:
            target = self.path / name
            if target.exists() or target.is_symlink():
                _no_link(target)
            fd = os.open(target, flags, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise OSError("Application file must be a regular unlinked file")
        return fd

    def create(self, name: str, data: bytes) -> Path:
        fd = self._open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return self.path / name

    def append(self, name: str, data: bytes) -> None:
        fd = self._open(name, os.O_WRONLY | os.O_APPEND)
        with os.fdopen(fd, "ab") as handle:
            handle.write(data)

    def read(self, name: str) -> bytes:
        fd = self._open(name, os.O_RDONLY)
        with os.fdopen(fd, "rb") as handle:
            return handle.read()

    def mkdir(self, name: str) -> Path:
        name = _leaf(name)
        if self.fd is not None:
            os.mkdir(name, 0o700, dir_fd=self.fd)
        else:
            (self.path / name).mkdir(mode=0o700)
        return self.path / name

    def names(self) -> list[str]:
        return os.listdir(self.fd if self.fd is not None else self.path)

    def stat(self, name: str):
        name = _leaf(name)
        if self.fd is not None:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        return (self.path / name).lstat()

    def unlink(self, name: str) -> None:
        name = _leaf(name)
        if self.fd is not None:
            os.unlink(name, dir_fd=self.fd)
        else:
            (self.path / name).unlink()


@contextmanager
def private_directory(path: str | Path, *, create: bool = True):
    target = absolute_path(path)
    if os.name == "posix":
        fd = os.open(target.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in target.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = next_fd
            yield PrivateDirectory(target, fd)
        finally:
            os.close(fd)
    else:
        current = Path(target.anchor)
        for part in target.parts[1:]:
            current /= part
            if create:
                current.mkdir(mode=0o700, exist_ok=True)
            _no_link(current)
            if not current.is_dir():
                raise OSError("Application directory is unavailable")
        yield PrivateDirectory(target, None)


def ensure_private_directory(path: str | Path) -> Path:
    with private_directory(path) as directory:
        return directory.path
