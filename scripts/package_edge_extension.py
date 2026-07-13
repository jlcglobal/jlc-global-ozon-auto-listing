#!/usr/bin/env python3
"""Build the existing Edge collector release without keeping a source mirror."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "collector/edge-extension"
RELEASE_DIR = ROOT / "release"
IGNORED_NAMES = {".DS_Store"}
IGNORED_PARTS = {"node_modules", "dist", "build"}


def release_path() -> Path:
    manifest = json.loads((SOURCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"])
    return RELEASE_DIR / f"1688商品采集插件-{version}.zip"


def package_extension() -> Path:
    if not (SOURCE_DIR / "manifest.json").is_file():
        raise FileNotFoundError("未找到Edge采集插件源码")
    output = release_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".zip.tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(SOURCE_DIR.rglob("*")):
            if not path.is_file() or path.name in IGNORED_NAMES:
                continue
            relative = path.relative_to(SOURCE_DIR)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            archive_path = str(Path("edge-extension") / relative)
            info = zipfile.ZipInfo(archive_path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    temporary.replace(output)
    return output


if __name__ == "__main__":
    path = package_extension()
    print(path)
