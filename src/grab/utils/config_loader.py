from pathlib import Path

from ruamel.yaml import YAML

from grab.models.schemas import GrabConfig


def load_config(path: str | Path) -> GrabConfig:
    yaml = YAML(typ="safe")
    data = yaml.load(Path(path).read_text(encoding="utf-8")) or {}
    return GrabConfig.model_validate(data)
