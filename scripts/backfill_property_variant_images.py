#!/usr/bin/env python3
"""Backfill property-value image metadata from an already captured raw snapshot."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main(product_dir: Path) -> None:
    source_path = product_dir / "input/source.json"
    snapshot_path = product_dir / "input/raw-snapshot.json"
    source, snapshot = load(source_path), load(snapshot_path)
    raw = {str(item.get("sku_id")): item for item in snapshot.get("sku_raw_data", []) if isinstance(item, dict)}
    groups = {}
    mapped = 0
    for sku in source.get("skus", []):
        original = raw.get(str(sku.get("sku_id")), {})
        data = original.get("source_data", {})
        image_url = original.get("image_url", "unknown")
        prop_id = data.get("sku_image_prop_id", "unknown")
        prop_name = data.get("sku_image_prop_name", "unknown")
        value_id = data.get("sku_image_prop_value_id", "unknown")
        value_name = data.get("sku_image_prop_value", "unknown")
        if image_url != "unknown" and prop_id != "unknown":
            sku.update({
                "variant_image_url": image_url,
                "variant_local_image_path": sku.get("local_image_path", "unknown"),
                "variant_image_source": "sku_property_value",
                "variant_image_prop_id": prop_id,
                "variant_image_value_id": value_id,
                "variant_image_value_name": value_name,
                "sku_image_missing": False,
            })
            mapped += 1
        group = groups.setdefault(prop_id, {"prop_id": prop_id, "prop_name": prop_name, "values": {}})
        group["values"].setdefault(value_id, {
            "value_id": value_id, "value_name": value_name, "image_url": image_url,
            "local_image_path": sku.get("local_image_path", "unknown"),
            "image_source": "sku_property_value", "source_data": data,
        })
    source["sku_property_groups"] = [{**group, "values": list(group["values"].values())} for group in groups.values() if group["prop_id"] != "unknown"]
    all_raw = snapshot.get("sku_raw_data", [])
    mapped_all = [item for item in all_raw if item.get("source_data", {}).get("sku_image_source") == "sku_property_value"]
    property_values = {item.get("source_data", {}).get("sku_image_prop_value_id") for item in mapped_all}
    snapshot["sku_property_image_debug"] = {
        "property_groups": source["sku_property_groups"],
        "color_property_detected": any(item.get("source_data", {}).get("sku_image_prop_name") == "颜色" for item in mapped_all),
        "color_value_count": len(property_values),
        "color_values_with_image": len({item.get("source_data", {}).get("sku_image_prop_value_id") for item in mapped_all if item.get("image_url") != "unknown"}),
        "final_sku_count": len(all_raw),
        "final_skus_mapped_by_property_image": len(mapped_all),
        "final_skus_still_missing_image": len(all_raw) - len(mapped_all),
        "unmapped_values": [],
        "data_sources": ["sku_property_dom", "sku_property_value"],
    }
    write(source_path, source)
    write(snapshot_path, snapshot)
    print(json.dumps({"selected_skus_mapped": mapped, **snapshot["sku_property_image_debug"]}, ensure_ascii=False))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir", type=Path)
    main(parser.parse_args().product_dir)
