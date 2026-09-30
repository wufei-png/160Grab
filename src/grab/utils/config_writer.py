import os
from io import StringIO
from pathlib import Path
from uuid import uuid4

from ruamel.yaml import YAML

from grab.utils.private_files import absolute_path, private_directory


def write_browser_profile_name(config_path: str | Path, profile_name: str) -> None:
    yaml = YAML()
    yaml.preserve_quotes = True

    path = absolute_path(config_path)
    with private_directory(path.parent, create=False) as directory:
        data = yaml.load(directory.read(path.name).decode("utf-8")) or {}

    browser = data.get("browser")
    if browser is None or not hasattr(browser, "__setitem__"):
        browser = yaml.map()
        data["browser"] = browser

    browser["profile_name"] = profile_name

    output = StringIO()
    yaml.dump(data, output)
    with private_directory(path.parent, create=False) as directory:
        name = f".160grab-config-{uuid4().hex}.tmp"
        directory.create(name, output.getvalue().encode("utf-8"))
        try:
            if directory.fd is not None:
                os.replace(
                    name, path.name, src_dir_fd=directory.fd, dst_dir_fd=directory.fd
                )
            else:
                os.replace(directory.path / name, path)
        finally:
            try:
                directory.unlink(name)
            except FileNotFoundError:
                pass
