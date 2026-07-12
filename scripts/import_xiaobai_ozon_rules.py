#!/usr/bin/env python3
"""Import and validate a Xiaobai Ozon rule database snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_ROOT = ROOT / "ozon-adapter" / "metadata"
REQUIRED_FILES = {
    "attributes.json",
    "categories.json",
    "sync-errors.json",
    "variants.json",
    "version.json",
}


class RuleImportError(ValueError):
    pass


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rule_key(item: Dict[str, Any]) -> Tuple[str, str]:
    category_id = str(item.get("categoryId", "")).strip()
    type_id = str(item.get("typeId", "")).strip()
    if not category_id or not type_id:
        raise RuleImportError("Every rule must contain categoryId and typeId")
    return category_id, type_id


def unique_keys(items: Iterable[Dict[str, Any]], label: str) -> set[Tuple[str, str]]:
    keys = [rule_key(item) for item in items]
    if len(keys) != len(set(keys)):
        raise RuleImportError(f"{label} contains duplicate categoryId/typeId pairs")
    return set(keys)


def validate_snapshot(snapshot_dir: Path) -> Dict[str, Any]:
    present = {path.name for path in snapshot_dir.iterdir() if path.is_file()}
    allowed_files = REQUIRED_FILES | {"import-report.json"}
    if not REQUIRED_FILES.issubset(present) or not present.issubset(allowed_files):
        raise RuleImportError(
            f"Unexpected snapshot files: expected {sorted(REQUIRED_FILES)}, got {sorted(present)}"
        )

    version = load_json(snapshot_dir / "version.json")
    categories = load_json(snapshot_dir / "categories.json")
    attributes = load_json(snapshot_dir / "attributes.json")
    variants = load_json(snapshot_dir / "variants.json")
    sync_errors = load_json(snapshot_dir / "sync-errors.json")
    if not all(isinstance(value, list) for value in (categories, attributes, variants, sync_errors)):
        raise RuleImportError("Rule data files must contain JSON arrays")

    category_keys = unique_keys(categories, "categories.json")
    attribute_keys = unique_keys(attributes, "attributes.json")
    variant_keys = unique_keys(variants, "variants.json")
    if category_keys != attribute_keys or category_keys != variant_keys:
        raise RuleImportError("Category, attribute and variant key coverage does not match")

    attribute_count = sum(
        len(item.get("requiredAttributes", [])) + len(item.get("optionalAttributes", []))
        for item in attributes
    )
    variant_attribute_count = sum(len(item.get("attributes", [])) for item in variants)
    expected = {
        "categoryCount": len(categories),
        "attributeCount": attribute_count,
        "attributeRuleCount": len(attributes),
        "variantRuleCount": len(variants),
    }
    for field, actual in expected.items():
        if version.get(field) != actual:
            raise RuleImportError(
                f"version.json {field}={version.get(field)!r}, actual value is {actual}"
            )

    return {
        "version": version.get("version"),
        "updated_at": version.get("updatedAt"),
        "category_count": len(categories),
        "attribute_count": attribute_count,
        "attribute_rule_count": len(attributes),
        "variant_rule_count": len(variants),
        "variant_attribute_count": variant_attribute_count,
        "sync_error_count": len(sync_errors),
    }


def import_rule_archive(archive_path: Path, target_root: Path = DEFAULT_TARGET_ROOT) -> Path:
    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise RuleImportError(f"Archive does not exist: {archive_path}")
    target_root.mkdir(parents=True, exist_ok=True)

    # Keep transient extraction outside the repository so concurrent validators
    # never observe a half-imported snapshot.
    with tempfile.TemporaryDirectory(prefix="ozon-rule-import-") as temp_name:
        temp_dir = Path(temp_name)
        with zipfile.ZipFile(archive_path) as archive:
            names = {Path(name).as_posix() for name in archive.namelist() if not name.endswith("/")}
            if names != REQUIRED_FILES:
                raise RuleImportError(
                    f"Archive layout must contain only {sorted(REQUIRED_FILES)}, got {sorted(names)}"
                )
            for name in sorted(names):
                destination = (temp_dir / name).resolve()
                if destination.parent != temp_dir.resolve():
                    raise RuleImportError(f"Unsafe archive path: {name}")
                with archive.open(name) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)

        summary = validate_snapshot(temp_dir)
        version_name = str(summary["version"] or "").strip()
        if not version_name or "/" in version_name or ".." in version_name:
            raise RuleImportError(f"Invalid rule version: {version_name!r}")
        final_dir = target_root / version_name
        if final_dir.exists():
            existing = validate_snapshot(final_dir)
            if existing != summary:
                raise RuleImportError(f"Existing snapshot differs: {final_dir}")
            return final_dir

        report = {
            "schema_version": "1.0.0",
            "status": "valid",
            "source_archive": archive_path.name,
            "source_archive_sha256": sha256(archive_path),
            "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **summary,
            "files": {
                name: {"sha256": sha256(temp_dir / name), "bytes": (temp_dir / name).stat().st_size}
                for name in sorted(REQUIRED_FILES)
            },
        }
        with (temp_dir / "import-report.json").open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        shutil.move(str(temp_dir), str(final_dir))
        return final_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a Xiaobai Ozon rule database archive")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--target-root", type=Path, default=DEFAULT_TARGET_ROOT)
    args = parser.parse_args()
    destination = import_rule_archive(args.archive, args.target_root)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
