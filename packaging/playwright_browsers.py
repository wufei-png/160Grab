from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage staged Playwright browsers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "clear-package-local",
        help="Remove package-local browsers before running PyInstaller.",
    )

    sync_parser = subparsers.add_parser(
        "sync-into-bundle",
        help="Copy staged browsers into the frozen bundle tree.",
    )
    sync_parser.add_argument("--source", required=True)
    sync_parser.add_argument("--bundle-root", required=True)

    return parser.parse_args()


def package_local_browser_dir() -> Path:
    return (
        Path(playwright.__file__).resolve().parent
        / "driver"
        / "package"
        / ".local-browsers"
    )


def bundle_browser_dir(bundle_root: Path) -> Path:
    return bundle_root / "playwright" / "driver" / "package" / ".local-browsers"


def clear_package_local() -> None:
    target = package_local_browser_dir()
    if target.exists():
        shutil.rmtree(target)
        print(f"Removed package-local browsers from {target}")


def sync_into_bundle(*, source: Path, bundle_root: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Staged Playwright browsers not found: {source}")

    target = bundle_browser_dir(bundle_root)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    print(f"Copied staged Playwright browsers to {target}")


def main() -> None:
    args = parse_args()
    if args.command == "clear-package-local":
        clear_package_local()
        return

    if args.command == "sync-into-bundle":
        sync_into_bundle(
            source=Path(args.source).resolve(),
            bundle_root=Path(args.bundle_root).resolve(),
        )
        return

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
