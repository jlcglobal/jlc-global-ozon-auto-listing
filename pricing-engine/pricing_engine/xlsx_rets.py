"""Read only the RETS worksheet from the supplied XLSX shipping table."""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.itertext()) for node in root.findall(f"{{{MAIN_NS}}}si")]


def _sheet_path(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relation_id: Optional[str] = None
    for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
        if sheet.attrib.get("name") == sheet_name:
            relation_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id")
            break
    if not relation_id:
        raise ValueError(f"Workbook does not contain required worksheet: {sheet_name}")

    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relation in relationships.findall(f"{{{PKG_REL_NS}}}Relationship"):
        if relation.attrib.get("Id") == relation_id:
            target = relation.attrib["Target"].lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError(f"Worksheet relationship is missing for: {sheet_name}")


def _cell_value(cell: ET.Element, shared: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        node = cell.find(f"{{{MAIN_NS}}}is")
        return "".join(node.itertext()) if node is not None else ""
    value_node = cell.find(f"{{{MAIN_NS}}}v")
    if value_node is None or value_node.text is None:
        return None
    text = value_node.text
    if cell_type == "s":
        return shared[int(text)]
    if cell_type == "str":
        return text
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return text


def load_rets_rules(path: Path) -> Dict[str, Any]:
    path = path.resolve()
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        worksheet_path = _sheet_path(archive, "RETS")
        root = ET.fromstring(archive.read(worksheet_path))
    cells: Dict[str, Any] = {}
    formulas: Dict[str, str] = {}
    for cell in root.findall(f".//{{{MAIN_NS}}}c"):
        reference = cell.attrib.get("r")
        if not reference:
            continue
        cells[reference] = _cell_value(cell, shared)
        formula = cell.find(f"{{{MAIN_NS}}}f")
        if formula is not None and formula.text:
            formulas[reference] = formula.text

    required_headers = {
        "P1": "汇率",
        "T1": "重量(g)",
        "U1": "最终售价(CNY)",
        "AN1": "RETS推荐渠道",
        "AO1": "RETS测算运费(CNY)",
    }
    for reference, expected in required_headers.items():
        if cells.get(reference) != expected:
            raise ValueError(f"RETS worksheet header mismatch at {reference}: expected {expected}")

    exchange_text = str(cells.get("P2") or "")
    exchange_match = re.fullmatch(r"\s*1\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*", exchange_text)
    if not exchange_match:
        raise ValueError("RETS!P2 must contain an exchange rate formatted as 1:N")

    shipping_formula = formulas.get("AO2", "")
    route_pattern = re.compile(
        r'AN2="([^"]+)",([0-9]+(?:\.[0-9]+)?)\+([0-9]+(?:\.[0-9]+)?)\*\(AH2/1000\)'
    )
    route_costs = {
        name: {"base_fee_cny": float(base), "rate_per_kg_cny": float(rate)}
        for name, base, rate in route_pattern.findall(shipping_formula)
    }
    if len(route_costs) != 6:
        raise ValueError("RETS!AO2 did not contain the six expected shipping cost formulas")

    return {
        "workbook_path": str(path),
        "workbook_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "worksheet": "RETS",
        "exchange_rate_rub_per_cny": float(exchange_match.group(1)),
        "exchange_rate_source": "RETS!P2",
        "route_selection_formula": formulas.get("AN2", ""),
        "shipping_formula": shipping_formula,
        "route_costs": route_costs,
        "source_cells": ["RETS!P2", "RETS!AN2", "RETS!AO2"],
    }
