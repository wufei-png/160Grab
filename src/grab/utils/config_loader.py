from pathlib import Path

import yaml

from grab.models.schemas import GrabConfig


def load_config(path: str | Path) -> GrabConfig:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return GrabConfig.model_validate(data)
