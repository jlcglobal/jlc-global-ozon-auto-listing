"""Import an official Ozon bestsellers XLSX report without third-party packages."""

from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Tuple
from xml.etree import ElementTree as ET

from .storage import MarketStore


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    if not letters:
        raise ValueError(f"Invalid XLSX cell reference: {reference}")
    value = 0
    for letter in letters.group(0):
        value = value * 26 + ord(letter) - 64
    return value - 1


def _shared_strings(archive: zipfile.ZipFile) -> List[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.itertext()) for node in root.findall(f"{{{MAIN_NS}}}si")]


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheet = workbook.find(f".//{{{MAIN_NS}}}sheet")
    if sheet is None:
        raise ValueError("Ozon report does not contain a worksheet")
    relation_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id")
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relation in relationships.findall(f"{{{PKG_REL_NS}}}Relationship"):
        if relation.attrib.get("Id") == relation_id:
            target = relation.attrib["Target"].lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError("Ozon report worksheet relationship is missing")


def _cell_value(cell: ET.Element, shared: List[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        node = cell.find(f"{{{MAIN_NS}}}is")
        return "".join(node.itertext()) if node is not None else ""
    node = cell.find(f"{{{MAIN_NS}}}v")
    if node is None or node.text is None:
        return None
    text = node.text
    if cell_type == "s":
        return shared[int(text)]
    if cell_type in {"str", "e"}:
        return text
    try:
        value = float(text)
        return int(value) if value.is_integer() else value
    except ValueError:
        return text


def read_rows(path: Path) -> Iterator[List[Any]]:
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        worksheet = ET.fromstring(archive.read(_first_sheet_path(archive)))
    for row in worksheet.findall(f".//{{{MAIN_NS}}}row"):
        values: Dict[int, Any] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            reference = cell.attrib.get("r")
            if reference:
                values[_column_index(reference)] = _cell_value(cell, shared)
        if values:
            width = max(values) + 1
            yield [values.get(index) for index in range(width)]


def _excel_date(value: Any) -> Any:
    if not isinstance(value, (int, float)):
        return value if value not in {None, ""} else "unknown"
    return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat()


def _excel_percent(value: Any) -> Any:
    """Convert Ozon's XLSX percentage cell value to the displayed percentage."""
    if not isinstance(value, (int, float)):
        return value
    return round(float(value) * 100, 6)


def normalize_row(headers: List[str], values: List[Any], observed_at: str) -> Dict[str, Any]:
    row = {headers[index]: values[index] if index < len(values) else None for index in range(len(headers))}
    product_id = row.get("Артикул Ozon")
    if not product_id or not row.get("Название товара"):
        raise ValueError("Ozon report row is missing product ID or title")
    facts = {
        "brand": row.get("Бренд") or "unknown",
        "category_level_1": row.get("Категория 1 уровня") or "unknown",
        "category_level_3": row.get("Категория 3 уровня") or "unknown",
        "super_product": row.get("Super-товар") or "unknown",
        "ordered_amount_rub": row.get("Заказано на сумму, ₽"),
        "ordered_amount_growth_percent": _excel_percent(row.get("Динамика суммы заказов, %")),
        "ordered_units": row.get("Заказано товаров"),
        "lowest_price_rub": row.get("Самая низкая цена, ₽"),
        "average_purchase_price_rub": row.get("Средняя цена покупки, ₽"),
        "lost_sales_rub": row.get("Упущенные продажи, ₽"),
        "days_without_stock": row.get("Дней без остатка, дни"),
        "average_daily_sales_rub": row.get("Среднесуточные продажи, ₽"),
        "average_daily_units": row.get("Среднесуточные продажи, штуки"),
        "fulfillment_model": row.get("Схема работы") or "unknown",
        "product_volume_liters": row.get("Объем товара, л"),
        "ozon_cost_share_fbo_percent": _excel_percent(row.get("Доля затрат на Ozon, FBO, %")),
        "ozon_cost_share_fbs_percent": _excel_percent(row.get("Доля затрат на Ozon, FBS, %")),
        "search_catalog_impressions": row.get("Показы в поиске и каталоге"),
        "product_card_visits": row.get("Посещения карточки товара"),
        "search_to_cart_percent": _excel_percent(row.get("В корзину из поиска и каталога, %")),
        "card_to_cart_percent": _excel_percent(row.get("В корзину из карточки, %")),
        "ad_cost_share_percent": _excel_percent(row.get("Доля рекламных расходов, %")),
        "buyout_share_percent": _excel_percent(row.get("Доля выкупа, %")),
        "product_card_created_on": _excel_date(row.get("Дата создания карточки товара")),
        "seller_article": row.get("Артикул продавца") or "unknown",
    }
    return {
        "product_key": f"ozon:{int(product_id)}",
        "source": "ozon_official_bestsellers_report",
        "source_product_id": str(int(product_id)),
        "category_key": str(facts["category_level_1"]),
        "title_ru": str(row["Название товара"]),
        "title_zh": "unknown",
        "product_url": str(row.get("Ссылка на товар") or f"https://www.ozon.ru/product/{int(product_id)}"),
        "image_url": "unknown",
        "facts": facts,
        "observed_at": observed_at,
    }


def import_bestsellers_report(path: Path, store: MarketStore, observed_at: str) -> Dict[str, Any]:
    rows = read_rows(path)
    first_row = next(rows)
    period_text = str(first_row[0] or "")
    period_match = re.search(r"([0-9]{2}\.[0-9]{2}\.[0-9]{4})\s*-\s*([0-9]{2}\.[0-9]{2}\.[0-9]{4})", period_text)
    if not period_match:
        raise ValueError("Ozon bestsellers report period is missing")
    next(rows)  # sort description
    headers = [str(value or "") for value in next(rows)]
    required = {"Название товара", "Артикул Ozon", "Ссылка на товар", "Схема работы", "Заказано товаров"}
    if not required.issubset(headers):
        raise ValueError("Ozon bestsellers report headers do not match the expected format")
    next(rows)  # metric descriptions
    imported = 0
    observed_on = observed_at[:10]
    for values in rows:
        if not values or values[0] in {None, ""}:
            continue
        record = normalize_row(headers, values, observed_at)
        record["facts"].update({
            "report_period_from": period_match.group(1),
            "report_period_to": period_match.group(2),
        })
        store.upsert_product_snapshot(record, observed_on)
        imported += 1
    return {
        "schema_version": "1.0.0",
        "source": "ozon_official_bestsellers_report",
        "source_path": str(path.resolve()),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "period_from": period_match.group(1),
        "period_to": period_match.group(2),
        "observed_at": observed_at,
        "imported_products": imported,
    }
