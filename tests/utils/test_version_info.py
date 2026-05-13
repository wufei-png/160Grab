import sys
from pathlib import Path

from grab import version_info


def test_resolve_version_string_accepts_env_override(monkeypatch):
    monkeypatch.setenv("GRAB_VERSION_OVERRIDE", "test-override-1")
    assert version_info.resolve_version_string() == "test-override-1"


def test_resolve_version_string_reads_embedded_when_frozen(tmp_path, monkeypatch):
    monkeypatch.delenv("GRAB_VERSION_OVERRIDE", raising=False)
    embedded = tmp_path / "_embedded_version.txt"
    embedded.write_text("v9.9.9-from-bundle\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert version_info.resolve_version_string() == "v9.9.9-from-bundle"


def test_resolve_version_string_prefers_git_commit_when_not_frozen(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GRAB_VERSION_OVERRIDE", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    repo = tmp_path / "repo"
    sub = repo / "src" / "grab"
    sub.mkdir(parents=True)
    (repo / ".git").mkdir()

    fake_module = sub / "version_info.py"
    monkeypatch.setattr(version_info, "__file__", str(fake_module))

    def fake_commit(root: Path) -> str | None:
        assert root == repo
        return "a1b2c3d4e5f6-dirty"

    monkeypatch.setattr(version_info, "format_git_commit_revision", fake_commit)

    assert version_info.resolve_version_string() == "a1b2c3d4e5f6-dirty"


def test_resolve_version_string_falls_back_when_no_git(monkeypatch):
    monkeypatch.delenv("GRAB_VERSION_OVERRIDE", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(version_info, "format_git_commit_revision", lambda _root: None)

    def fake_version(_name: str) -> str:
        return "0.1.0"

    monkeypatch.setattr(
        version_info.importlib.metadata,
        "version",
        fake_version,
    )

    assert version_info.resolve_version_string() == "0.1.0"
