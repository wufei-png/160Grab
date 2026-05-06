import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from ruamel.yaml import YAML


def write_browser_profile_name(config_path: str | Path, profile_name: str) -> None:
    yaml = YAML()
    yaml.preserve_quotes = True

    path = Path(config_path)
    with path.open(encoding="utf-8") as handle:
        data = yaml.load(handle) or {}

    browser = data.get("browser")
    if browser is None or not hasattr(browser, "__setitem__"):
        browser = yaml.map()
        data["browser"] = browser

    browser["profile_name"] = profile_name

    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        yaml.dump(data, handle)
        temp_path = Path(handle.name)

    os.replace(temp_path, path)
