"""Resolve a human-readable version string for dev builds and frozen binaries."""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

_EMBEDDED_NAME = "_embedded_version.txt"


def _repo_root_from_here() -> Path:
    # src/grab/version_info.py -> repo root
    return Path(__file__).resolve().parents[2]


def format_git_commit_revision(repo_root: Path) -> str | None:
    """Short commit hash and optional -dirty; never uses tags."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    short = proc.stdout.strip()
    if not short:
        return None
    try:
        dirty_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        dirty_proc = None
    dirty = ""
    if dirty_proc is not None and dirty_proc.stdout.strip():
        dirty = "-dirty"
    return f"{short}{dirty}"


def resolve_version_string() -> str:
    """Version shown at startup: git commit in dev, embedded file in PyInstaller builds."""
    override = os.getenv("GRAB_VERSION_OVERRIDE", "").strip()
    if override:
        return override

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            embedded = Path(meipass) / _EMBEDDED_NAME
            if embedded.is_file():
                text = embedded.read_text(encoding="utf-8").strip()
                if text:
                    return text

    commit_line = format_git_commit_revision(_repo_root_from_here())
    if commit_line:
        return commit_line

    try:
        return importlib.metadata.version("160grab")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
