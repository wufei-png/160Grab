import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML


@pytest.mark.skipif(
    sys.platform not in {"linux", "darwin"}
    or not all(shutil.which(tool) for tool in ("bash", "sha256sum", "sort")),
    reason="the release checksum tools are unavailable",
)
def test_release_checksums_verify_flat_downloads(tmp_path):
    root = Path(__file__).resolve().parents[1]
    workflow = YAML(typ="safe").load(root / ".github/workflows/release.yml")
    step = next(
        item
        for item in workflow["jobs"]["release"]["steps"]
        if item.get("name") == "Generate SHA256SUMS"
    )

    artifacts = tmp_path / "release-artifacts"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    for label in ("macos-arm64", "macos-intel", "windows-x64"):
        artifact = artifacts / label / f"160Grab-{label}.zip"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(label.encode())
        shutil.copyfile(artifact, downloads / artifact.name)

    subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]], cwd=tmp_path, check=True
    )
    shutil.copyfile(artifacts / "SHA256SUMS", downloads / "SHA256SUMS")
    result = subprocess.run(
        ["sha256sum", "--check", "SHA256SUMS"],
        cwd=downloads,
        capture_output=True,
        text=True,
        check=True,
    )
    assert len(result.stdout.splitlines()) == 3
