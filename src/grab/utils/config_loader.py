from pathlib import Path

from loguru import logger
from ruamel.yaml import YAML

from grab.models.schemas import GrabConfig


def load_config(path: str | Path) -> GrabConfig:
    yaml = YAML(typ="safe")
    data = yaml.load(Path(path).read_text(encoding="utf-8")) or {}
    config = GrabConfig.model_validate(data)
    for field in ("username", "password", "ocr"):
        if getattr(config, field):
            logger.warning(
                "Deprecated top-level config field '{}' is ignored by manual login; "
                "remove it from your configuration.",
                field,
            )
    return config
