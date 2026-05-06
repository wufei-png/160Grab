import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from grab.utils.profile_name import validate_profile_name

PROFILE_MARKER_FILENAME = ".160grab-profile.json"


@dataclass(frozen=True)
class BrowserProfile:
    name: str
    path: Path

    @property
    def marker_path(self) -> Path:
        return self.path / PROFILE_MARKER_FILENAME


@dataclass(frozen=True)
class ResolvedBrowserProfile:
    profile: BrowserProfile
    source: Literal["configured", "auto-detected", "selected"]


def expand_profiles_root_dir(value: str | Path) -> Path:
    return Path(value).expanduser()


def ensure_profiles_root_dir(root_dir: str | Path) -> Path:
    resolved = expand_profiles_root_dir(root_dir)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def create_profile(
    root_dir: str | Path,
    profile_name: str | None = None,
) -> BrowserProfile:
    resolved_root = ensure_profiles_root_dir(root_dir)
    if profile_name is None:
        profile_name = _next_auto_profile_name(resolved_root)
    else:
        profile_name = validate_profile_name(profile_name)

    profile_dir = resolved_root / profile_name
    if profile_dir.exists():
        raise ValueError(f"Profile '{profile_name}' already exists.")

    profile_dir.mkdir(parents=False)
    metadata = {
        "version": 1,
        "profile_name": profile_name,
        "created_at": datetime.now(UTC).isoformat(),
    }
    (profile_dir / PROFILE_MARKER_FILENAME).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return BrowserProfile(name=profile_name, path=profile_dir)


def list_profiles(root_dir: str | Path) -> list[BrowserProfile]:
    resolved_root = expand_profiles_root_dir(root_dir)
    if not resolved_root.exists():
        return []

    profiles: list[BrowserProfile] = []
    for child in sorted(resolved_root.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        marker_path = child / PROFILE_MARKER_FILENAME
        if not marker_path.exists():
            continue
        try:
            metadata = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        profile_name = str(metadata.get("profile_name") or "").strip()
        if not profile_name or profile_name != child.name:
            continue
        profiles.append(BrowserProfile(name=child.name, path=child))
    return profiles


def load_profile(root_dir: str | Path, profile_name: str) -> BrowserProfile:
    resolved_root = ensure_profiles_root_dir(root_dir)
    validated_name = validate_profile_name(profile_name)
    profile_dir = resolved_root / validated_name
    if not profile_dir.exists():
        available = ", ".join(profile.name for profile in list_profiles(resolved_root))
        suffix = f" Available profiles: {available}" if available else ""
        raise ValueError(
            f"Profile '{validated_name}' does not exist under {resolved_root}.{suffix}"
        )
    if not profile_dir.is_dir():
        raise ValueError(f"Profile path is not a directory: {profile_dir}")

    marker_path = profile_dir / PROFILE_MARKER_FILENAME
    if not marker_path.exists():
        raise ValueError(
            f"Profile '{validated_name}' is not a valid 160Grab profile: "
            f"missing {PROFILE_MARKER_FILENAME}."
        )

    try:
        metadata = json.loads(marker_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Profile '{validated_name}' is not a valid 160Grab profile: "
            f"invalid {PROFILE_MARKER_FILENAME}."
        ) from exc

    if not isinstance(metadata, dict) or metadata.get("profile_name") != validated_name:
        raise ValueError(
            f"Profile '{validated_name}' is not a valid 160Grab profile: "
            f"marker content does not match the directory name."
        )

    return BrowserProfile(name=validated_name, path=profile_dir)


def resolve_profile_for_run(
    *,
    root_dir: str | Path,
    configured_profile_name: str | None,
    config_path: str | Path,
    prompt_text: Callable[[str], str] = input,
    notify: Callable[[str], None] = print,
    is_interactive: bool = True,
    persist_profile_name: Callable[[str | Path, str], None] | None = None,
) -> ResolvedBrowserProfile:
    resolved_root = ensure_profiles_root_dir(root_dir)
    if configured_profile_name is not None:
        return ResolvedBrowserProfile(
            profile=load_profile(resolved_root, configured_profile_name),
            source="configured",
        )

    profiles = list_profiles(resolved_root)
    if not profiles:
        raise ValueError(
            f"No browser profiles found under {resolved_root}. "
            "Run `uv run python main.py config.yaml --create-profile` first."
        )

    if len(profiles) == 1:
        profile = profiles[0]
        notify(
            "ℹ️ 自动检测到唯一 profile: "
            f"{profile.name} ({profile.path})"
        )
        return ResolvedBrowserProfile(profile=profile, source="auto-detected")

    if not is_interactive:
        raise ValueError(
            "Multiple browser profiles are available but the terminal is non-interactive. "
            "Set browser.profile_name in config.yaml."
        )

    profile = _prompt_profile_selection(profiles, prompt_text=prompt_text, notify=notify)
    _maybe_persist_selection(
        profile=profile,
        config_path=config_path,
        prompt_text=prompt_text,
        notify=notify,
        persist_profile_name=persist_profile_name,
    )
    return ResolvedBrowserProfile(profile=profile, source="selected")


def _next_auto_profile_name(root_dir: Path) -> str:
    index = 1
    while True:
        candidate = f"profile_{index}"
        if not (root_dir / candidate).exists():
            return candidate
        index += 1


def _prompt_profile_selection(
    profiles: list[BrowserProfile],
    *,
    prompt_text: Callable[[str], str],
    notify: Callable[[str], None],
) -> BrowserProfile:
    notify("检测到多个可用 profile，请选择一个:")
    for index, profile in enumerate(profiles, start=1):
        notify(f"{index}. {profile.name} - {profile.path}")

    while True:
        raw_value = prompt_text("请输入要使用的 profile 编号: ").strip()
        if not raw_value.isdigit():
            notify("❌ 输入无效，请输入列表中的数字编号。")
            continue
        selected_index = int(raw_value)
        if not (1 <= selected_index <= len(profiles)):
            notify("❌ 输入超出范围，请重新选择。")
            continue
        return profiles[selected_index - 1]


def _maybe_persist_selection(
    *,
    profile: BrowserProfile,
    config_path: str | Path,
    prompt_text: Callable[[str], str],
    notify: Callable[[str], None],
    persist_profile_name: Callable[[str | Path, str], None] | None,
) -> None:
    if persist_profile_name is None:
        return

    answer = prompt_text(
        f"是否将 profile '{profile.name}' 写回当前配置文件 {config_path}? [y/N] "
    )
    if _is_affirmative(answer):
        persist_profile_name(config_path, profile.name)
        notify(f"✅ 已将 browser.profile_name 持久化为 {profile.name}")


def _is_affirmative(answer: str) -> bool:
    return answer.strip().lower() in {"y", "yes"}
