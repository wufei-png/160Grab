from pathlib import Path

from ruamel.yaml import YAML

from grab.models.schemas import GrabConfig
from grab.observability.safe_logging import logger


def load_config(path: str | Path) -> GrabConfig:
    yaml = YAML(typ="safe")
    data = yaml.load(Path(path).read_text(encoding="utf-8")) or {}
    config = GrabConfig.model_validate(data)
    for field in ("username", "password", "ocr"):
        if getattr(config, field):
            logger.warning(
                {
                    "username": "Deprecated top-level config field 'username' is ignored by manual login; remove it from your configuration.",
                    "password": "Deprecated top-level config field 'password' is ignored by manual login; remove it from your configuration.",
                    "ocr": "Deprecated top-level config field 'ocr' is ignored by manual login; remove it from your configuration.",
                }[field]
            )
    return config
