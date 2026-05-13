"""Write packaging/_embedded_version.txt for PyInstaller (commit CI / git / package version)."""

from __future__ import annotations

import importlib.metadata
import os
import sys
from pathlib import Path

from grab.version_info import format_git_commit_revision

OUT_REL = Path("packaging") / "_embedded_version.txt"


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_path = repo_root / OUT_REL

    text = format_git_commit_revision(repo_root) or ""
    if not text:
        sha = os.environ.get("GITHUB_SHA", "").strip()
        if sha.isascii() and all(c in "0123456789abcdefABCDEF" for c in sha):
            text = sha[:12].lower()
    if not text:
        try:
            text = importlib.metadata.version("160grab")
        except importlib.metadata.PackageNotFoundError:
            text = "unknown"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {out_path.relative_to(repo_root)}: {text!r}")


if __name__ == "__main__":
    main()
    sys.exit(0)
