"""Import source-preserved Ozon search query rows captured from official free analytics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from .storage import MarketStore


CATEGORY_TERMS = {
    "auto": ("канистр", "бензин"),
    "bathroom": ("смесител", "душев", "раковин", "аэратор"),
    "electronics": ("наушник", "смартфон"),
    "kitchen": ("аэрогрил", "чайник", "холодильник", "кофе"),
    "home": ("вентилятор", "шкаф", "постельное белье", "кондиционер"),
    "outdoor": ("сапборд", "электросамокат", "электровелосипед", "беговая дорожка", "бассейн"),
}

TRANSLATIONS_ZH = {
    "канистра для бензина 20 л": "20升汽油桶", "бензин": "汽油", "канистра для бензина 20 л металлическая": "20升金属汽油桶", "канистра для бензина": "汽油桶",
    "вентилятор напольный": "落地扇", "вентилятор": "风扇", "шкаф для одежды": "衣柜", "кондиционер": "空调", "постельное белье 2 спальное": "双人床上用品",
    "наушники беспроводные": "无线耳机", "наушники": "耳机", "смартфон": "智能手机",
    "сапборд": "桨板", "электросамокат": "电动滑板车", "электровелосипед": "电动自行车", "беговая дорожка": "跑步机", "бассейн каркасный": "支架泳池",
    "чайник электрический": "电热水壶", "холодильник": "冰箱", "кофе": "咖啡",
    "аэрогриль": "空气炸锅", "аэрогриль электрический": "电动空气炸锅", "аэрогриль xiaomi": "小米空气炸锅", "аэрогриль с двумя чашами": "双炸篮空气炸锅",
    "аэрогриль demiand": "Demiand空气炸锅", "аэрогриль с двумя тэнами": "双加热管空气炸锅", "форма для аэрогриля": "空气炸锅烤盘",
    "силиконовая форма для аэрогриля": "空气炸锅硅胶烤盘", "бумага для аэрогриля": "空气炸锅烤纸", "пергамент для аэрогриля": "空气炸锅烘焙纸",
    "аэрогриль электрический 8 л": "8升电动空气炸锅", "для аэрогриля": "空气炸锅配件", "формы для аэрогриля": "空气炸锅烤盘套装",
    "demiand аэрогриль": "Demiand空气炸锅", "аэрогриль kitfort": "Kitfort空气炸锅", "аэрогриль weissgauff": "Weissgauff空气炸锅",
    "решетка для аэрогриля": "空气炸锅烤架", "форма для аэрогриля силиконовая": "空气炸锅硅胶烤盘", "аэрогриль со стеклянной чашей": "玻璃炸篮空气炸锅", "аэрогриль tefal": "特福空气炸锅",
    "смеситель для кухни": "厨房水龙头", "смеситель для ванны с душем": "带淋浴浴缸龙头", "смеситель для ванны": "浴缸龙头", "смеситель для раковины": "面盆龙头",
    "смеситель": "水龙头", "смеситель для раковины в ванную": "浴室面盆龙头", "аэратор для смесителя": "水龙头起泡器",
    "смеситель для кухни с гибким изливом": "柔性出水厨房龙头", "смеситель на кухню для мойки": "厨房水槽龙头", "смеситель в ванную с душем": "带淋浴浴室龙头",
    "тропический душ со смесителем": "带龙头雨淋花洒", "душевая система с тропическим душем и смесителем": "带龙头雨淋花洒套装",
}


def localized_number(value: Any) -> Any:
    text = str(value or "").replace("\u00a0", "").replace(" ", "").replace("₽", "").replace("%", "").replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def classify_category(keyword_ru: str) -> str:
    text = keyword_ru.lower()
    for category_key, terms in CATEGORY_TERMS.items():
        if any(term in text for term in terms):
            return category_key
    return "other"


def classify_type(keyword_ru: str) -> str:
    words = keyword_ru.split()
    if "для" in words:
        return "scenario"
    if len(words) >= 3:
        return "long_tail"
    if len(words) == 2:
        return "attribute"
    return "hot"


def normalize_query_row(row: Mapping[str, Any], rank: int, observed_at: str) -> Dict[str, Any]:
    keyword_ru = str(row.get("query_ru") or "").strip()
    metrics = list(row.get("metrics") or [])
    if not keyword_ru or len(metrics) < 6:
        raise ValueError("Ozon search query row is incomplete")
    digest = hashlib.sha256(keyword_ru.encode("utf-8")).hexdigest()[:20]
    return {
        "keyword_key": f"ozon-query:{digest}",
        "keyword_ru": keyword_ru,
        "keyword_zh": TRANSLATIONS_ZH.get(keyword_ru.lower(), "unknown"),
        "keyword_type": classify_type(keyword_ru),
        "category_key": classify_category(keyword_ru),
        "evidence": {
            "source": "ozon_official_search_queries",
            "source_url": "https://data.ozon.ru/app/search-queries",
            "period_days": 7,
            "official_rank": rank,
            "capture_method": "authenticated_visible_table",
            "translation_method": "local_curated_v1" if keyword_ru.lower() in TRANSLATIONS_ZH else "unknown",
        },
        "metrics": {
            "popularity": localized_number(metrics[0]),
            "add_to_cart_count": localized_number(metrics[1]),
            "add_to_cart_conversion_percent": localized_number(metrics[2]),
            "average_buyer_price_rub": localized_number(metrics[3]),
            "displayed_products": localized_number(metrics[4]),
            "competitors": localized_number(metrics[5]),
        },
        "last_seen_at": observed_at,
    }


def import_search_query_file(path: Path, store: MarketStore) -> Dict[str, Any]:
    source = json.loads(path.read_text(encoding="utf-8"))
    observed_at = str(source["observed_at"])
    rows = list(source.get("rows") or [])
    linked = 0
    for rank, row in enumerate(rows, start=1):
        record = normalize_query_row(row, rank, observed_at)
        store.upsert_keyword(record)
        linked += store.link_keyword_to_matching_products(record["keyword_key"], record["keyword_ru"])
    store.upsert_source_status({
        "source_id": "ozon_search_queries",
        "state": "connected",
        "access_level": "official_read_only",
        "message_zh": "Ozon 官方近7天搜索查询已导入",
        "checked_at": observed_at,
        "details": {"query_count": len(rows), "period_days": 7},
    })
    return {"imported_keywords": len(rows), "product_keyword_links": linked, "observed_at": observed_at}
