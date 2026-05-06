from __future__ import annotations

import argparse
import hashlib
import shutil
import zipfile
from pathlib import Path

APP_NAME = "160Grab"
REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_ROOT = REPO_ROOT / "dist"
DEFAULT_PYINSTALLER_DIR = DIST_ROOT / APP_NAME
DEFAULT_RELEASE_ROOT = DIST_ROOT / "release"
CONFIG_TEMPLATE = REPO_ROOT / "config" / "example.yaml"
RELEASE_README = Path(__file__).resolve().parent / "README-release.md"
MACOS_LAUNCHER = Path(__file__).resolve().parent / "160Grab.command"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage frozen release assets.")
    parser.add_argument("--platform", choices=["windows", "macos"], required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--pyinstaller-dir", default=str(DEFAULT_PYINSTALLER_DIR))
    parser.add_argument("--release-root", default=str(DEFAULT_RELEASE_ROOT))
    return parser.parse_args()


def bundle_name(platform: str, arch: str) -> str:
    return f"{APP_NAME}-{platform}-{arch}"


def binary_name(platform: str) -> str:
    return f"{APP_NAME}.exe" if platform == "windows" else APP_NAME


def stage_release_tree(
    *,
    platform: str,
    arch: str,
    pyinstaller_dir: Path,
    release_root: Path,
) -> tuple[Path, Path]:
    if not pyinstaller_dir.exists():
        raise FileNotFoundError(f"PyInstaller output was not found: {pyinstaller_dir}")

    name = bundle_name(platform, arch)
    staged_dir = release_root / name
    if staged_dir.exists():
        shutil.rmtree(staged_dir)

    release_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pyinstaller_dir, staged_dir)

    shutil.copyfile(CONFIG_TEMPLATE, staged_dir / "config.yaml")
    shutil.copyfile(RELEASE_README, staged_dir / "README-release.md")

    if platform == "macos":
        launcher_path = staged_dir / MACOS_LAUNCHER.name
        shutil.copyfile(MACOS_LAUNCHER, launcher_path)
        launcher_path.chmod(0o755)

    bundled_binary = staged_dir / binary_name(platform)
    if not bundled_binary.exists():
        raise FileNotFoundError(f"Frozen binary was not found: {bundled_binary}")

    return staged_dir, bundled_binary


def build_zip(source_dir: Path, zip_path: Path) -> Path:
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            arcname = Path(source_dir.name) / path.relative_to(source_dir)
            archive.write(path, arcname)

    return zip_path


def write_sha256(target: Path) -> Path:
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    checksum_path = target.with_suffix(target.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    return checksum_path


def main() -> None:
    args = parse_args()
    pyinstaller_dir = Path(args.pyinstaller_dir).resolve()
    release_root = Path(args.release_root).resolve()

    staged_dir, _ = stage_release_tree(
        platform=args.platform,
        arch=args.arch,
        pyinstaller_dir=pyinstaller_dir,
        release_root=release_root,
    )
    zip_path = release_root / f"{staged_dir.name}.zip"
    build_zip(staged_dir, zip_path)
    write_sha256(zip_path)
    print(f"Staged release assets at {staged_dir}")
    print(f"Created release archive {zip_path}")


if __name__ == "__main__":
    main()
