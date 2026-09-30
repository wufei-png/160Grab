import os

import pytest

from grab.observability.reporter import JsonlEventSink
from grab.utils.private_files import private_directory
from grab.utils.profile_manager import create_profile, list_profiles, load_profile


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
def test_private_new_directories_files_and_existing_modes(tmp_path):
    os.chmod(tmp_path, 0o755)
    root = tmp_path / "app" / "logs"
    sink = JsonlEventSink(root, "a" * 12)
    sink.write({"event": "run_started"})
    assert (tmp_path / "app").stat().st_mode & 0o777 == 0o700
    assert root.stat().st_mode & 0o777 == 0o700
    assert sink.path.stat().st_mode & 0o777 == 0o600
    assert tmp_path.stat().st_mode & 0o777 == 0o755
    profile = create_profile(tmp_path / "profiles", "synthetic")
    assert profile.path.stat().st_mode & 0o777 == 0o700
    assert profile.marker_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX link contract")
def test_linked_roots_and_leaf_files_cannot_write_outside(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        JsonlEventSink(link / "logs", "a" * 12)
    assert not (outside / "logs").exists()
    root = tmp_path / "logs"
    sink = JsonlEventSink(root, "a" * 12)
    sink.path.unlink()
    victim = outside / "victim"
    victim.write_text("untouched")
    sink.path.symlink_to(victim)
    with pytest.raises(OSError):
        sink.write({"event": "run_started"})
    assert victim.read_text() == "untouched"
    assert list(outside.iterdir()) == [victim]


@pytest.mark.skipif(os.name != "posix", reason="POSIX link contract")
def test_profile_links_and_marker_links_are_rejected(tmp_path):
    root = tmp_path / "profiles"
    profile = create_profile(root, "original")
    (root / "linked").symlink_to(profile.path, target_is_directory=True)
    assert [p.name for p in list_profiles(root)] == ["original"]
    with pytest.raises(OSError):
        load_profile(root, "linked")
    marker = profile.marker_path.read_bytes()
    profile.marker_path.unlink()
    outside_marker = tmp_path / "marker"
    outside_marker.write_bytes(marker)
    profile.marker_path.symlink_to(outside_marker)
    assert list_profiles(root) == []
    with pytest.raises(OSError):
        load_profile(root, "original")


def test_hardlink_file_is_rejected(tmp_path):
    victim = tmp_path / "victim"
    victim.write_bytes(b"unchanged")
    os.link(victim, tmp_path / "link")
    with private_directory(tmp_path) as directory:
        with pytest.raises(OSError):
            directory.append("link", b"poison")
    assert victim.read_bytes() == b"unchanged"


def test_invalid_log_ref_cannot_inject_filename(tmp_path):
    with pytest.raises(ValueError):
        JsonlEventSink(tmp_path, "../escape")
    assert list(tmp_path.iterdir()) == []
