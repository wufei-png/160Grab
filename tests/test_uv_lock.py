import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "default_index",
    [
        "https://pypi.org/simple",
        "https://pypi.tuna.tsinghua.edu.cn/simple",
    ],
)
def test_lock_is_stable_with_different_default_indexes(tmp_path, default_index):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to check the project lock")

    root = Path(__file__).resolve().parents[1]
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(root / name, tmp_path / name)

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("UV_")
    }
    user_config = tmp_path / "user-config"
    system_config = tmp_path / "system-config"
    for config in (user_config, system_config):
        (config / "uv").mkdir(parents=True)
        (config / "uv" / "uv.toml").write_text("")
    env["XDG_CONFIG_HOME"] = str(user_config)
    env["XDG_CONFIG_DIRS"] = str(system_config)
    if os.name == "nt":
        env["APPDATA"] = str(user_config)
        env["PROGRAMDATA"] = str(system_config)
    env.pop("VIRTUAL_ENV", None)
    result = subprocess.run(
        [
            uv,
            "lock",
            "--locked",
            "--offline",
            "--no-cache",
            "--default-index",
            default_index,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
