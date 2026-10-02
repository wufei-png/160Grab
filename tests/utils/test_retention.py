import os
from datetime import UTC, datetime, timedelta

import pytest

from grab.utils.profile_manager import create_profile
from grab.utils.retention import cleanup_outputs

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)


def log_name(age):
    return (
        "160grab-log-v1-"
        + (NOW - timedelta(days=age)).strftime("%Y%m%d-%H%M%S")
        + "-"
        + "a" * 12
        + "-"
        + "b" * 32
        + ".jsonl"
    )


def debug_name(age, ext):
    return (
        "160grab-debug-v1-"
        + (NOW - timedelta(hours=age)).strftime("%Y%m%d-%H%M%S-%f")
        + "-"
        + "b" * 32
        + "."
        + ext
    )


def test_retention_dry_run_boundaries_and_unknown_records(tmp_path):
    old, recent, boundary = [tmp_path / log_name(age) for age in (8, 6, 7)]
    for path in (old, recent, boundary):
        path.write_text("{}")
    unknown = tmp_path / "unknown.jsonl"
    unknown.write_text("sensitive synthetic")
    pending = tmp_path / "attempts.v1.json"
    pending.write_text('{"state":"SUBMITTING"}')
    corrupt = tmp_path / "attempts.v2.json"
    corrupt.write_text("broken")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / log_name(8)).write_text("{}")
    result = cleanup_outputs(tmp_path, kind="logs", now=NOW)
    assert result["eligible"] == 1 and result["deleted"] == 0
    assert old.exists()
    result = cleanup_outputs(tmp_path, kind="logs", now=NOW, dry_run=False)
    assert result["deleted"] == 1 and not old.exists()
    assert all(
        path.exists() for path in (recent, boundary, unknown, pending, corrupt, nested)
    )
    assert (nested / log_name(8)).exists()


def test_debug_retention_is_24_hours_and_nonrecursive(tmp_path):
    for hours in (25, 23, 24):
        for ext in ("html", "png", "json"):
            (tmp_path / debug_name(hours, ext)).write_bytes(b"synthetic")
    assert cleanup_outputs(tmp_path, kind="debug", now=NOW)["eligible"] == 3
    assert (
        cleanup_outputs(tmp_path, kind="debug", now=NOW, dry_run=False)["deleted"] == 3
    )
    assert len(list(tmp_path.iterdir())) == 6


def test_profile_and_explicit_active_files_are_protected(tmp_path):
    profile = create_profile(tmp_path / "profiles", "active")
    old = profile.path / log_name(8)
    old.write_text("{}")
    assert (
        cleanup_outputs(profile.path, kind="logs", now=NOW, dry_run=False)["eligible"]
        == 0
    )
    assert old.exists()
    active = tmp_path / log_name(8)
    active.write_text("{}")
    assert (
        cleanup_outputs(
            tmp_path, kind="logs", now=NOW, dry_run=False, protected_paths=(active,)
        )["deleted"]
        == 0
    )
    assert active.exists()
    custom = tmp_path / "profiles" / "unmarked"
    custom.mkdir()
    (custom / log_name(8)).write_text("{}")
    assert (
        cleanup_outputs(
            custom,
            kind="logs",
            now=NOW,
            dry_run=False,
            protected_paths=(tmp_path / "profiles",),
        )["eligible"]
        == 0
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX link contract")
def test_cleanup_never_follows_symlinks_or_hardlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / log_name(8)
    victim.write_text("unchanged")
    root = tmp_path / "logs"
    root.mkdir()
    (root / log_name(8)).symlink_to(victim)
    os.link(victim, root / log_name(9))
    assert cleanup_outputs(root, kind="logs", now=NOW, dry_run=False)["deleted"] == 0
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        cleanup_outputs(linked, kind="logs", now=NOW, dry_run=False)
    assert victim.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor contract")
def test_cleanup_directory_swap_is_anchored(tmp_path, monkeypatch):
    from grab.utils.private_files import PrivateDirectory

    root = tmp_path / "logs"
    root.mkdir()
    (root / log_name(8)).write_text("old")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / log_name(8)
    victim.write_text("unchanged")
    original = PrivateDirectory.unlink

    def swap(self, name):
        root.rename(tmp_path / "moved")
        root.symlink_to(outside, target_is_directory=True)
        original(self, name)

    monkeypatch.setattr(PrivateDirectory, "unlink", swap)
    assert cleanup_outputs(root, kind="logs", now=NOW, dry_run=False)["deleted"] == 1
    assert victim.read_text(encoding="utf-8") == "unchanged"
    assert list((tmp_path / "moved").iterdir()) == []
