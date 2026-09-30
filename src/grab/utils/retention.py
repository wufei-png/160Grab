"""Bounded non-recursive cleanup of recognized application output files."""

from __future__ import annotations

import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grab.utils.private_files import absolute_path, private_directory

LOG_NAME = re.compile(
    r"(?:160grab-log-v1-)?(?P<stamp>\d{8}-\d{6})-[a-f0-9]{12}(?:-[a-f0-9]{32})?\.jsonl"
)
DEBUG_NAME = re.compile(
    r"160grab-debug-v1-(?P<stamp>\d{8}-\d{6}-\d{6})-[a-f0-9]{32}\.(?:json|html|png)"
)


def cleanup_outputs(
    root: str | Path,
    *,
    kind: str,
    dry_run: bool = True,
    protected_paths: tuple[str | Path, ...] = (),
    now: datetime | None = None,
) -> dict[str, int]:
    if kind not in {"logs", "debug"}:
        raise ValueError("Unknown retention kind")
    root = absolute_path(root)
    result = {"eligible": 0, "deleted": 0, "skipped": 0}
    if any(
        root == absolute_path(path) or absolute_path(path) in root.parents
        for path in protected_paths
    ):
        result["skipped"] = 1
        return result
    if not root.exists() and not root.is_symlink():
        return result
    # Profiles are never cleaned, even when a debug/log root is misconfigured.
    for parent in (root, *root.parents):
        marker = parent / ".160grab-profile.json"
        if marker.exists() or marker.is_symlink():
            result["skipped"] = 1
            return result
    cutoff = (now or datetime.now(UTC)) - timedelta(days=7 if kind == "logs" else 1)
    pattern = LOG_NAME if kind == "logs" else DEBUG_NAME
    with private_directory(root, create=False) as directory:
        for name in directory.names():
            match = pattern.fullmatch(name)
            if not match:
                result["skipped"] += 1
                continue
            try:
                created = datetime.strptime(
                    match["stamp"],
                    "%Y%m%d-%H%M%S" if kind == "logs" else "%Y%m%d-%H%M%S-%f",
                ).replace(tzinfo=UTC)
                info = directory.stat(name)
                if (
                    created >= cutoff
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or (os.name == "posix" and info.st_uid != os.getuid())
                    or any(
                        directory.path / name == absolute_path(path)
                        for path in protected_paths
                    )
                ):
                    result["skipped"] += 1
                    continue
                result["eligible"] += 1
                if not dry_run:
                    # The POSIX directory descriptor anchors unlink even if its pathname changes.
                    directory.unlink(name)
                    result["deleted"] += 1
            except (OSError, ValueError):
                result["skipped"] += 1
    return result
