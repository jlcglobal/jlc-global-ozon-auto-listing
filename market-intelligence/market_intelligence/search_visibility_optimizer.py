"""Local dry-run optimizer for Ozon seller search visibility data."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
TOKEN_RE = re.compile(r"[А-Яа-яЁё]+")
DEFAULT_BATCH_SIZE = 50
DEFAULT_PAGE_SIZE = 1000
DEFAULT_TITLE_SIGNAL_DAYS = 15
DEFAULT_TAG_REPLACEMENT_LIMIT = 8
OZON_HASHTAG_ATTRIBUTE_ID = 23171
OZON_ANNOTATION_ATTRIBUTE_ID = 4191
OZON_SUBJECT_TAG_MAX_BODY_LENGTH = 30
OZON_POLICY_BRAND_FRAGMENTS = {
    "apple", "iphone", "ipad", "magsafe", "samsung", "xiaomi", "redmi", "huawei", "honor",
    "lenovo", "asus", "acer", "bosch", "philips", "dyson", "polaris", "vitek", "bork",
    "tohatsu", "nibbi", "dozawa", "collonil", "zenden", "ingco", "mindeo", "pantasy",
    "astroboy", "pirateflag", "happyhair", "natureza", "civitarese", "evoque", "felps",
    "copacabana", "leomax", "homeelement", "ксиоми", "сяоми", "хуавей", "айфон",
    "самсунг", "поларис", "витек", "борк", "нибби", "дозава", "зенден", "ингко",
    "миндео", "пантаси", "астробой", "леомакс", "анапа", "оралби", "филипс",
    "икеа", "икея", "пинтерест", "аквалунг", "акваданг", "ямаха", "макита",
    "камолее", "камоле", "камл", "сони", "мерседес", "лего", "редбулл",
    "булл", "щеняч", "патруль", "пионер", "бмв", "штиль", "формула",
    "маршал", "маршалл", "marshall", "патибокс", "амвей", "амвэй", "amway",
    "espring", "эспринг", "royal", "jbl", "джибиэль", "tcl", "тсл",
    "озон", "ozon", "яндекс", "yamaha", "tefal", "тефаль",
    "малютк", "стэп", "honda", "хонда", "dio", "дио", "дайсон", "вихр",
    "керхер", "karcher", "dji", "swissoak", "shouldcat", "пума", "puma",
    "keycharger", "quickcharge", "stationcharger", "gan", "switch", "nintendo",
    "нинтендо", "свитч", "gopro", "гоупро", "гоу про", "mijia", "brevio",
    "гардарика", "кукмара", "cartethyia", "картетия", "мин чао",
    "глория джинс", "глорияджинс", "gloria jeans", "gloriajeans",
    "босс", "boss",
}
OZON_POLICY_MARKETING_FRAGMENTS = {
    "скидк", "распродаж", "акци", "промокод", "дешев", "недорог", "лучший", "лучш",
    "топ", "хит", "премиум", "premium", "оригинал", "original", "официаль", "сертифик",
    "гарант", "возврат", "доставка", "магазин", "чат", "отзыв", "рекомендуем",
    "новинк",
    "идеальн", "профессиональн", "качественн", "долговечн", "выгодн", "бренд",
}
OZON_POLICY_ADULT_FRAGMENTS = {
    "интимн", "длясекса", "сексигр", "жмжсекс", "порно", "эротик",
    "мастурбатор", "мастурбац", "лубрикант",
}
OZON_POLICY_HASHTAG_MODERATION_FRAGMENTS = {
    # Ozon's hashtag moderation can read this word-boundary join as obscene.
    "краналюмин",
    # Hidden/mini-camera wording can be rejected as prohibited goods.
    "миникамер",
    "разводник",
    "боевоймолот",
    "молотбоевой",
    "оружиетора",
}
OZON_POLICY_BAD_TEXT_FRAGMENTS = {
    "друшлак", "белыи", "часи",
}
OZON_POLICY_INTRO_RISK_FRAGMENTS = {
    *OZON_POLICY_BRAND_FRAGMENTS,
    *OZON_POLICY_MARKETING_FRAGMENTS,
    *OZON_POLICY_ADULT_FRAGMENTS,
    *OZON_POLICY_BAD_TEXT_FRAGMENTS,
    "сервис", "импортер", "пишите", "обратиться", "негатив", "проблем", "сертификац",
}


def _tag_body_length(tag: Any) -> int:
    return len(str(tag or "").strip().lstrip("#"))


def _valid_subject_tag_length(tag: Any) -> bool:
    return 0 < _tag_body_length(tag) <= OZON_SUBJECT_TAG_MAX_BODY_LENGTH


def _compact_policy_text(value: Any) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", str(value or "").casefold())


def _spaced_policy_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _contains_policy_fragment(value: Any, fragments: Iterable[str]) -> bool:
    compact = _compact_policy_text(value)
    spaced = f" {_spaced_policy_text(value)} "
    for fragment in fragments:
        frag = str(fragment or "").casefold().strip()
        if not frag:
            continue
        compact_frag = _compact_policy_text(frag)
        if compact_frag and compact_frag in compact:
            return True
        if " " in frag and f" {frag} " in spaced:
            return True
    return False


def _policy_fragments_for_text(fragments: Iterable[str], text: Any) -> set[str]:
    compact_text = _compact_policy_text(text)
    filtered = {str(item) for item in fragments}
    # Avoid blocking normal Russian product words that merely contain short
    # brand or marketing fragments after punctuation/space compaction.
    if any(fragment in compact_text for fragment in ("аудио", "радио", "светодиод")):
        filtered.discard("дио")
    if "уборк" in compact_text:
        filtered.discard("борк")
    if "топлив" in compact_text:
        filtered.discard("топ")
    if "хранен" in compact_text or "хранения" in compact_text:
        filtered.discard("вихр")
    if "возвраткров" in compact_text or "возвратомкров" in compact_text:
        filtered.discard("возврат")
    return filtered


def _tag_policy_block_reason(value: Any) -> str:
    text = str(value or "").strip().lstrip("#")
    if not _valid_subject_tag_length(text):
        return "length"
    compact_text = _compact_policy_text(text)
    brand_fragments = _policy_fragments_for_text(OZON_POLICY_BRAND_FRAGMENTS, text)
    if _contains_policy_fragment(text, brand_fragments):
        return "brand"
    marketing_fragments = _policy_fragments_for_text(OZON_POLICY_MARKETING_FRAGMENTS, text)
    # ``перчатки`` (gloves) contains the compact substring ``чат``; keep the
    # chat-marketing guard without blocking normal product tags for gloves.
    if "перчат" in compact_text or "печат" in compact_text:
        marketing_fragments = {item for item in marketing_fragments if item != "чат"}
    if _contains_policy_fragment(text, marketing_fragments):
        return "marketing"
    if _contains_policy_fragment(text, OZON_POLICY_ADULT_FRAGMENTS):
        return "adult"
    if _contains_policy_fragment(text, OZON_POLICY_BAD_TEXT_FRAGMENTS):
        return "bad_text"
    if _contains_policy_fragment(text, OZON_POLICY_HASHTAG_MODERATION_FRAGMENTS):
        return "platform_moderation"
    return ""


def _query_policy_block_reason(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "empty"
    compact_text = _compact_policy_text(text)
    brand_fragments = _policy_fragments_for_text(OZON_POLICY_BRAND_FRAGMENTS, text)
    if _contains_policy_fragment(text, brand_fragments):
        return "brand"
    marketing_fragments = _policy_fragments_for_text(OZON_POLICY_MARKETING_FRAGMENTS, text)
    if "перчат" in compact_text or "печат" in compact_text:
        marketing_fragments = {item for item in marketing_fragments if item != "чат"}
    if _contains_policy_fragment(text, marketing_fragments):
        return "marketing"
    if _contains_policy_fragment(text, OZON_POLICY_ADULT_FRAGMENTS):
        return "adult"
    if _contains_policy_fragment(text, OZON_POLICY_BAD_TEXT_FRAGMENTS):
        return "bad_text"
    return ""


def _intro_policy_risky(value: Any) -> bool:
    text = str(value or "")
    if len(text) > 900:
        return True
    if re.search(r"</?[a-z][^>]*>", text, flags=re.IGNORECASE):
        return True
    if re.search(r"[*_]{2,}", text):
        return True
    return _contains_policy_fragment(text, OZON_POLICY_INTRO_RISK_FRAGMENTS)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _number(value: Any) -> float:
    if isinstance(value, bool) or value in {None, "", "unknown"}:
        return 0.0
    text = str(value).replace("\u00a0", "").replace(" ", "").replace("₽", "").replace("%", "").replace(",", ".")
    try:
        return max(0.0, float(text))
    except ValueError:
        match = re.search(r"\d+(?:\.\d+)?", text)
        if not match:
            return 0.0
        return max(0.0, float(match.group(0)))


def _timestamp_rank(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def _normalized_query(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    return text if CYRILLIC_RE.search(text) else ""


def _query_value(row: Mapping[str, Any]) -> float:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    impressions = _number(_first_value(
        row.get("unique_search_users"),
        row.get("search_users"),
        row.get("search_count"),
        row.get("count"),
        row.get("searches"),
        row.get("requests"),
        row.get("impressions"),
        row.get("shows"),
        row.get("views"),
        row.get("popularity"),
        metrics.get("impressions"),
        metrics.get("search_count"),
    ))
    clicks = _number(_first_value(row.get("unique_view_users"), row.get("view_users"), row.get("clicks"), metrics.get("clicks")))
    orders = _number(row.get("orders") or row.get("ordered_units"))
    revenue = _number(row.get("revenue_rub") or row.get("ordered_amount_rub") or row.get("sales_rub"))
    return round((orders * 100.0) + (revenue * 0.02) + (clicks * 5.0) + (impressions * 0.05), 3)


def _title_contains(title: str, query: str) -> bool:
    title_text = f" {title.casefold()} "
    query_text = f" {query.casefold()} "
    return query_text in title_text or all(f" {token} " in title_text for token in TOKEN_RE.findall(query))


def _hashtag_candidates(query: str) -> Iterable[str]:
    normalized = _normalized_query(query)
    if normalized and len(TOKEN_RE.findall(normalized)) >= 2:
        yield normalized


def _compile_tags(queries: Sequence[str], max_count: int) -> List[str]:
    try:
        from russian_seo_rules import canonical_hashtag
    except ModuleNotFoundError:
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "scripts"))
        from russian_seo_rules import canonical_hashtag

    tags: List[str] = []
    seen: set[str] = set()
    for query in queries:
        for candidate in _hashtag_candidates(query):
            tag = canonical_hashtag(candidate)
            if not tag:
                continue
            if not _valid_subject_tag_length(tag):
                continue
            if _tag_policy_block_reason(tag):
                continue
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            tags.append(tag)
            if len(tags) >= max_count:
                return tags
    return tags


def _tag_search_score(tag: str, query_rows: Sequence[Mapping[str, Any]]) -> float:
    tag_text = str(tag or "").lstrip("#").casefold()
    tag_compact = re.sub(r"[^0-9a-zа-яё]+", "", tag_text)
    tag_tokens = set(TOKEN_RE.findall(tag_text))
    if not tag_tokens and not tag_compact:
        return 0.0
    score = 0.0
    for row in query_rows:
        query_text = str(row.get("query") or "").casefold()
        query_compact = re.sub(r"[^0-9a-zа-яё]+", "", query_text)
        query_tokens = set(TOKEN_RE.findall(query_text))
        if not query_tokens:
            continue
        if tag_compact and tag_compact == query_compact:
            score += _number(row.get("value_score"))
        elif tag_text and tag_text in query_text:
            score += _number(row.get("value_score"))
        elif tag_tokens.issubset(query_tokens):
            score += _number(row.get("value_score"))
    return round(score, 3)


def _query_search_count(row: Mapping[str, Any]) -> float:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    return max(
        _number(row.get("count")),
        _number(row.get("impressions")),
        _number(row.get("searches")),
        _number(row.get("search_users")),
        _number(row.get("unique_search_users")),
        _number(metrics.get("impressions")),
        _number(metrics.get("search_count")),
    )


def _intro_keyword_terms(rows: Sequence[Mapping[str, Any]], *, max_count: int = 3) -> List[str]:
    terms: List[str] = []
    seen: set[str] = set()
    ranked = sorted(
        [row for row in rows if isinstance(row, Mapping)],
        key=lambda row: (_query_search_count(row), _number(row.get("value_score"))),
        reverse=True,
    )
    for row in ranked:
        query = _normalized_query(row.get("query") or row.get("phrase") or row.get("keyword"))
        if not query:
            continue
        tokens = TOKEN_RE.findall(query)
        if len(tokens) < 2:
            continue
        if _query_policy_block_reason(query):
            continue
        if len(query) > 90:
            query = " ".join(tokens[:8])
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(query)
        if len(terms) >= max_count:
            break
    return terms


def _intro_supplement(terms: Sequence[str]) -> str:
    clean_terms = [str(term or "").strip() for term in terms if str(term or "").strip()]
    if not clean_terms:
        return ""
    return f"По назначению это {clean_terms[0]}."


def _recommended_intro(title: str, current_intro: str, terms: Sequence[str]) -> Dict[str, Any]:
    intro_text = re.sub(r"\s+", " ", str(current_intro or "").strip())
    intro_key = intro_text.casefold()
    safe_terms = [str(term or "").strip() for term in terms if str(term or "").strip() and not _query_policy_block_reason(term)]
    missing_terms = [
        str(term or "").strip()
        for term in safe_terms
        if str(term or "").strip() and str(term or "").strip().casefold() not in intro_key
    ]
    supplement = _intro_supplement(missing_terms[:3])
    if not supplement:
        return {
            "current_intro": intro_text,
            "recommended_intro": intro_text,
            "intro_supplement": "",
            "intro_update_available": False,
        }
    if intro_text:
        recommended = f"{intro_text}\n\n{supplement}"
    else:
        title_text = re.sub(r"\s+", " ", str(title or "").strip())
        base = "" if _query_policy_block_reason(title_text) else title_text[:110].rstrip(" ,.;")
        recommended = f"{base}. {supplement}".strip()
    return {
        "current_intro": intro_text,
        "recommended_intro": recommended,
        "intro_supplement": supplement,
        "intro_update_available": True,
    }


def _normalize_tag_values(value: Any) -> List[str]:
    try:
        from russian_seo_rules import canonical_hashtag
    except ModuleNotFoundError:
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "scripts"))
        from russian_seo_rules import canonical_hashtag

    if value is None or value == "" or value == "unknown":
        return []
    raw_values: List[str] = []

    def add_raw_text(raw: Any) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        if "#" in text and re.search(r"\s", text):
            raw_values.extend(piece.strip() for piece in text.split() if piece.strip())
        else:
            raw_values.append(text)

    if isinstance(value, str):
        for part in re.split(r"[,;\n]+", value):
            add_raw_text(part)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            if isinstance(item, Mapping):
                text = item.get("value") or item.get("name") or item.get("tag")
            else:
                text = item
            add_raw_text(text)
    tags: List[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        tag = canonical_hashtag(str(raw).lstrip("#"))
        if not tag:
            text = str(raw or "").strip()
            if re.fullmatch(r"#?[А-Яа-яЁё]+", text):
                tag = "#" + text.lstrip("#").casefold()
        if not tag:
            continue
        if not _valid_subject_tag_length(tag):
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


TRIAL_SOURCE_FIELDS = (
    ("ozon_autocomplete", "Ozon下拉词"),
    ("ozon_autocomplete_terms", "Ozon下拉词"),
    ("ozon_dropdown_terms", "Ozon下拉词"),
    ("autocomplete_queries", "Ozon下拉词"),
    ("search_suggestions", "Ozon下拉词"),
    ("suggested_queries", "Ozon下拉词"),
    ("competitor_terms", "竞品词"),
    ("competitor_queries", "竞品词"),
    ("competitor_keywords", "竞品词"),
    ("competitor_titles", "竞品词"),
    ("ozon_competitor_terms", "竞品词"),
    ("ozon_competitor_keywords", "竞品词"),
    ("category_attribute_terms", "类目属性词"),
    ("attribute_terms", "类目属性词"),
    ("category_keywords", "类目属性词"),
    ("trial_reference_terms", "试错词"),
)
TRIAL_SKIP_ATTRIBUTE_IDS = {"85", str(OZON_HASHTAG_ATTRIBUTE_ID), "4191", "9048", "11254"}
TRIAL_STOP_TERMS = {
    "нет бренда",
    "без бренда",
    "китай",
    "unknown",
}


def _trial_raw_rows(value: Any) -> List[Any]:
    if isinstance(value, Mapping):
        for key in ("items", "rows", "queries", "terms", "keywords", "suggestions", "data", "results"):
            rows = value.get(key)
            if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
                return list(rows)
        if any(key in value for key in ("query", "phrase", "keyword", "word", "name", "title", "term", "value")):
            return [value]
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    if isinstance(value, str):
        return [part for part in re.split(r"[,;\n]+", value) if part.strip()]
    return []


def _trial_query_text(raw: Any) -> str:
    if isinstance(raw, Mapping):
        raw_value = (
            raw.get("query")
            or raw.get("phrase")
            or raw.get("keyword")
            or raw.get("word")
            or raw.get("name")
            or raw.get("title")
            or raw.get("term")
            or raw.get("value")
        )
    else:
        raw_value = raw
    text = str(raw_value or "").strip()
    if not text or text.startswith("{") or text.startswith("["):
        return ""
    if not CYRILLIC_RE.search(text):
        return ""
    if len(text) > 120:
        text = " ".join(TOKEN_RE.findall(text)[:8])
    normalized = _normalized_query(text)
    if not normalized or normalized in TRIAL_STOP_TERMS:
        return ""
    if _number(normalized) > 0 and len(TOKEN_RE.findall(normalized)) == 0:
        return ""
    return normalized


def _trial_source_rows(value: Any, *, source_kind: str, source_label: str, weight: float = 1.0) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, raw in enumerate(_trial_raw_rows(value)):
        query = _trial_query_text(raw)
        if not query:
            continue
        count = 0.0
        if isinstance(raw, Mapping):
            for key in ("count", "search_count", "searches", "requests", "impressions", "shows", "popularity"):
                count = _number(raw.get(key))
                if count > 0:
                    break
        score = round(max(count * 0.05, max(0.05, weight) / (index + 1)), 3)
        row: Dict[str, Any] = {
            "query": query,
            "source": source_kind,
            "source_label": source_label,
            "value_score": score,
        }
        if count > 0:
            row["count"] = count
            row["metrics"] = {"search_count": count}
        rows.append(row)
    return rows


def _category_attribute_trial_rows(item: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for attribute in item.get("product_attributes") or []:
        if not isinstance(attribute, Mapping):
            continue
        attribute_id = str(attribute.get("id") or attribute.get("attribute_id") or "")
        if attribute_id in TRIAL_SKIP_ATTRIBUTE_IDS:
            continue
        for value in attribute.get("values") or []:
            query = _trial_query_text(value)
            if not query:
                continue
            rows.append({
                "query": query,
                "source": "category_attribute_terms",
                "source_label": "类目属性词",
                "attribute_id": attribute_id,
                "attribute_name": attribute.get("name") or attribute.get("attribute_name") or "",
                "value_score": 0.5,
            })
    return rows


def _normalize_trial_reference_rows(item: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for source_kind, source_label in TRIAL_SOURCE_FIELDS:
        if source_kind not in item:
            continue
        weight = 1.2 if "autocomplete" in source_kind or "dropdown" in source_kind else 1.0
        rows.extend(_trial_source_rows(item.get(source_kind), source_kind=source_kind, source_label=source_label, weight=weight))
    rows.extend(_category_attribute_trial_rows(item))

    by_key: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        query = str(row.get("query") or "")
        key = query.casefold()
        existing = by_key.get(key)
        if existing is None or _number(row.get("value_score")) > _number(existing.get("value_score")):
            by_key[key] = row
    return sorted(by_key.values(), key=lambda row: (_number(row.get("value_score")), row.get("query") or ""), reverse=True)


def _wordstat_raw_rows(value: Any) -> List[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        for key in (
            "topRequests", "top_requests", "items", "rows", "queries",
            "words", "data", "results",
        ):
            rows = value.get(key)
            if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
                return [row for row in rows if isinstance(row, Mapping)]
            if isinstance(rows, Mapping):
                nested_rows = _wordstat_raw_rows(rows)
                if nested_rows:
                    return nested_rows
        if any(key in value for key in ("phrase", "query", "keyword", "word", "name", "query_ru")):
            return [value]
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _normalize_yandex_wordstat_rows(*sources: Any) -> List[Dict[str, Any]]:
    by_query: Dict[str, Dict[str, Any]] = {}
    for source in sources:
        for raw in _wordstat_raw_rows(source):
            query = _normalized_query(
                raw.get("phrase")
                or raw.get("query")
                or raw.get("keyword")
                or raw.get("word")
                or raw.get("name")
                or raw.get("query_ru")
            )
            if not query:
                continue
            count = 0.0
            for key in ("count", "search_count", "searches", "requests", "impressions", "shows", "popularity"):
                count = _number(raw.get(key))
                if count > 0:
                    break
            if count <= 0:
                continue
            period_days = int(_number(raw.get("period_days") or raw.get("window_days") or 30) or 30)
            row = {
                "query": query,
                "count": count,
                "metrics": {"search_count": count},
                "value_score": round(count * 0.05, 3),
                "period_days": period_days,
                "source": "yandex_wordstat",
            }
            existing = by_query.get(query)
            if existing is None or count > _number(existing.get("count")):
                by_query[query] = row
    return sorted(by_query.values(), key=lambda row: row["count"], reverse=True)


def normalize_yandex_wordstat_rows(*sources: Any) -> List[Dict[str, Any]]:
    """Normalize already structured Yandex Wordstat rows for local planning."""
    return _normalize_yandex_wordstat_rows(*sources)


def normalize_seerfar_keyword_rows(*sources: Any) -> List[Dict[str, Any]]:
    """Normalize visible Seerfar keyword-miner rows without using its Open API.

    Seerfar names its primary demand metric ``月搜热度``.  It is deliberately
    preserved as a named metric instead of being presented as Ozon's unique
    search-user count.
    """
    by_query: Dict[str, Dict[str, Any]] = {}
    metric_fields = {
        "monthly_search_heat": ("monthly_search_heat", "monthly_heat", "search_heat", "月搜热度"),
        "monthly_growth_percent": ("monthly_growth_percent", "monthly_growth", "月搜增长"),
        "relevance": ("relevance", "相关度"),
        "cart_add_count": ("cart_add_count", "add_to_cart_count", "加购数"),
        "cart_conversion_percent": ("cart_conversion_percent", "cart_conversion", "加购转化率"),
        "title_density_percent": ("title_density_percent", "title_density", "标题密度"),
        "average_price_rub": ("average_price_rub", "average_price", "平均价格"),
        "competitor_count": ("competitor_count", "competitors", "竞品数"),
        "product_count": ("product_count", "products", "商品数"),
        "competitor_seller_count": ("competitor_seller_count", "competitor_sellers", "竞对数"),
        "ad_competitor_count": ("ad_competitor_count", "ad_competitors", "广告竞品数"),
        "product_visibility": ("product_visibility", "visibility", "商品可见度"),
        "market_space": ("market_space", "市场空间"),
        "conversion_concentration_percent": ("conversion_concentration_percent", "conversion_concentration", "转化集中度"),
        "return_cancel_rate_percent": ("return_cancel_rate_percent", "return_cancel_rate", "退货取消率"),
    }
    for source in sources:
        source_mode_fallback = (
            str(source.get("source_mode") or source.get("mode") or source.get("source") or "").strip()
            if isinstance(source, Mapping) else ""
        )
        for raw in _wordstat_raw_rows(source):
            query = _normalized_query(
                raw.get("query")
                or raw.get("keyword")
                or raw.get("phrase")
                or raw.get("word")
                or raw.get("关键词")
            )
            if not query:
                continue
            raw_metrics = raw.get("metrics") if isinstance(raw.get("metrics"), Mapping) else {}
            source_mode = str(
                raw.get("source_mode") or raw.get("mode") or raw.get("source") or source_mode_fallback or "keyword_miner"
            ).strip()
            is_reverse = source_mode == "keyword_reverse" or source_mode == "seerfar_keyword_reverse"
            metrics: Dict[str, float] = {}
            for target, candidates in metric_fields.items():
                for candidate in candidates:
                    value = _number(raw.get(candidate) if candidate in raw else raw_metrics.get(candidate))
                    if value > 0:
                        metrics[target] = value
                        break
            heat = _number(
                raw.get("search_count") if is_reverse else metrics.get("monthly_search_heat")
                or raw.get("count")
                or raw_metrics.get("search_count")
            )
            if heat <= 0:
                continue
            if is_reverse:
                metrics["search_count"] = heat
                metrics["seerfar_reverse_search_count"] = heat
            related_product_urls: List[str] = []
            raw_related = raw.get("related_product_urls") or raw.get("competitor_urls") or raw.get("related_products") or []
            if isinstance(raw_related, str):
                raw_related = [raw_related]
            if isinstance(raw_related, Sequence) and not isinstance(raw_related, (str, bytes, bytearray)):
                for value in raw_related:
                    url = str(value.get("url") if isinstance(value, Mapping) else value or "").strip()
                    if re.match(r"^https://(?:www\.)?ozon\.ru/product/\d+", url) and url not in related_product_urls:
                        related_product_urls.append(url)
            row = {
                "query": query,
                "count": heat,
                "metrics": {"search_count": heat, **metrics},
                "value_score": round(heat * 0.05 + _number(metrics.get("relevance")) * 0.1, 3),
                "period_days": 30,
                "source": "seerfar_keyword_reverse" if is_reverse else "seerfar_keyword_miner",
                "source_label": "Seerfar竞品反查" if is_reverse else "Seerfar关键词挖掘",
                "updated_frequency": "visible_result" if is_reverse else "weekly",
            }
            if related_product_urls:
                row["related_product_urls"] = related_product_urls[:10]
            existing = by_query.get(query)
            if existing is None or heat > _number(existing.get("count")):
                by_query[query] = row
    return sorted(by_query.values(), key=lambda row: (_number(row.get("count")), _number((row.get("metrics") or {}).get("relevance"))), reverse=True)


def parse_yandex_wordstat_text(text: str, *, period_days: int = 30) -> List[Dict[str, Any]]:
    """Parse copied Wordstat rows without using the paid API.

    Accepted examples:
      органайзер для кухни 12000
      органайзер для кухни\t12 000
      "органайзер для кухни";12000
    """
    raw_text = str(text or "").strip()
    if not raw_text:
        return []
    try:
        structured = json.loads(raw_text)
    except json.JSONDecodeError:
        structured = None
    if structured is not None:
        return _normalize_yandex_wordstat_rows(structured)

    raw_rows: List[Dict[str, Any]] = []

    def add_parts(parts: Sequence[str]) -> bool:
        clean_parts = [str(part or "").strip().strip('"\'«»') for part in parts if str(part or "").strip()]
        if len(clean_parts) < 2:
            return False
        for index in range(len(clean_parts) - 1, 0, -1):
            count = _number(clean_parts[index])
            if count <= 0:
                continue
            phrase = " ".join(clean_parts[:index]).strip().strip('"\'«»')
            if phrase:
                raw_rows.append({"phrase": phrase, "count": count, "period_days": period_days})
                return True
        return False

    for raw_line in raw_text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue
        parsed = False
        for delimiter in ("\t", ";", ","):
            if delimiter not in line:
                continue
            try:
                parts = next(csv.reader(io.StringIO(line), delimiter=delimiter))
            except csv.Error:
                parts = line.split(delimiter)
            if add_parts(parts):
                parsed = True
                break
        if parsed:
            continue
        match = re.match(r"(.+?)\s+([0-9][0-9\s.,]*)$", line)
        if match and add_parts([match.group(1), match.group(2)]):
            continue

    return _normalize_yandex_wordstat_rows(raw_rows)


def parse_ozon_product_query_text(
    text: str,
    *,
    period_days: int = DEFAULT_TITLE_SIGNAL_DAYS,
    source_kind: str = "competitor_product_query",
    source_label: str = "竞品词",
) -> List[Dict[str, Any]]:
    """Parse copied/downloaded Ozon product-query rows into reference terms."""
    raw_text = str(text or "").strip()
    if not raw_text:
        return []
    rows: List[Dict[str, Any]] = []

    def add_row(query_value: Any, count_value: Any = 0) -> bool:
        query = _trial_query_text(query_value)
        if not query:
            return False
        count = _number(count_value)
        rows.append({
            "query": query,
            "count": count,
            "metrics": {"search_count": count},
            "value_score": round(max(count * 0.05, 0.05), 3),
            "period_days": int(period_days),
            "source": source_kind,
            "source_label": source_label,
        })
        return True

    def add_parts(parts: Sequence[str]) -> bool:
        clean_parts = [str(part or "").strip() for part in parts if str(part or "").strip()]
        if len(clean_parts) < 2:
            return False
        query_index = -1
        for index, part in enumerate(clean_parts):
            if CYRILLIC_RE.search(part) and "поис" not in part.casefold():
                query_index = index
                break
        if query_index < 0:
            return False
        for value in clean_parts[query_index + 1:]:
            count = _number(value)
            if count > 0:
                return add_row(clean_parts[query_index], count)
        return add_row(clean_parts[query_index], 0)

    for raw_line in raw_text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue
        parsed = False
        for delimiter in ("\t", ";"):
            if delimiter not in line:
                continue
            try:
                parts = next(csv.reader(io.StringIO(line), delimiter=delimiter))
            except csv.Error:
                parts = line.split(delimiter)
            if add_parts(parts):
                parsed = True
                break
        if parsed:
            continue
        compact = re.sub(r"^\s*\d+[.)]?\s*", "", line)
        compact = re.split(r"\s+Premium\b", compact, maxsplit=1)[0].strip()
        compact = re.sub(r"\s+\d+\s*₽.*$", "", compact).strip()
        match = re.match(r"(.+?)\s+([0-9][0-9\s.,]*)$", compact)
        if match:
            add_row(match.group(1), match.group(2))
            continue
        add_row(compact, 0)

    by_query: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        query = str(row.get("query") or "")
        if not query:
            continue
        existing = by_query.get(query)
        if existing is None or _number(row.get("count")) > _number(existing.get("count")):
            by_query[query] = row
    return sorted(by_query.values(), key=lambda row: (_number(row.get("count")), row.get("query") or ""), reverse=True)


def _existing_subject_tags(item: Mapping[str, Any]) -> Optional[List[str]]:
    for key in ("existing_subject_tags", "current_subject_tags", "subject_tags", "tags"):
        if key in item:
            return _normalize_tag_values(item.get(key))
    return None


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", "unknown"):
            return value
    return ""


def _image_urls(*sources: Mapping[str, Any]) -> List[str]:
    urls: List[str] = []
    seen: set[str] = set()
    for source in sources:
        for key in ("primary_image", "image_url", "image", "cover_image"):
            value = source.get(key)
            values = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else [value]
            for entry in values:
                if isinstance(entry, Mapping):
                    entry_value = entry.get("url") or entry.get("file_name") or entry.get("src")
                else:
                    entry_value = entry
                if isinstance(entry_value, str) and entry_value.strip():
                    url = entry_value.strip()
                    if url not in seen:
                        seen.add(url)
                        urls.append(url)
        for key in ("images", "image_urls", "pictures"):
            raw = source.get(key)
            if isinstance(raw, str):
                values = [raw]
            elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
                values = raw
            else:
                values = []
            for entry in values:
                if isinstance(entry, Mapping):
                    value = entry.get("url") or entry.get("file_name") or entry.get("src")
                else:
                    value = entry
                if isinstance(value, str) and value.strip() and value.strip() not in seen:
                    seen.add(value.strip())
                    urls.append(value.strip())
    return urls


def _stock_value(*sources: Mapping[str, Any]) -> Any:
    for source in sources:
        for key in ("stock", "stocks", "available_stock", "present"):
            value = source.get(key)
            if isinstance(value, Mapping):
                nested = _first_value(value.get("present"), value.get("available"), value.get("stock"))
                if nested != "":
                    return nested
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                total = sum(_number(row.get("present") or row.get("available") or row.get("stock")) for row in value if isinstance(row, Mapping))
                if total:
                    return int(total)
            elif value not in (None, "", "unknown"):
                return value
    return ""


def _response_items(response: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result = response.get("result") if isinstance(response.get("result"), Mapping) else response
    raw_items = result.get("items") if isinstance(result, Mapping) else None
    if raw_items is None and isinstance(response.get("result"), Sequence) and not isinstance(response.get("result"), (str, bytes, bytearray)):
        raw_items = response.get("result")
    if raw_items is None:
        raw_items = response.get("items")
    return [dict(item) for item in raw_items or [] if isinstance(item, Mapping)]


def _attribute_value_texts(attribute: Mapping[str, Any]) -> List[str]:
    texts: List[str] = []
    for value in attribute.get("values") or []:
        if isinstance(value, Mapping):
            raw = _first_value(value.get("value"), value.get("name"), value.get("text"), value.get("value_id"))
        else:
            raw = value
        text = str(raw or "").strip()
        if text:
            texts.append(text)
    return texts


def _attributes_payload(item: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [dict(attribute) for attribute in item.get("attributes") or [] if isinstance(attribute, Mapping)]


def _all_attribute_payloads(item: Mapping[str, Any]) -> List[Dict[str, Any]]:
    attrs: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for key in ("attributes", "product_attributes"):
        for attribute in item.get(key) or []:
            if not isinstance(attribute, Mapping):
                continue
            attr_id = str(attribute.get("id") or attribute.get("attribute_id") or "")
            name = str(attribute.get("name") or attribute.get("attribute_name") or "")
            dedupe_key = (attr_id, name)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            attrs.append(dict(attribute))
    return attrs


def _subject_tags_from_attributes(item: Mapping[str, Any]) -> Optional[List[str]]:
    for attribute in _all_attribute_payloads(item):
        attribute_id = str(attribute.get("id") or attribute.get("attribute_id") or "")
        name = str(attribute.get("name") or attribute.get("attribute_name") or "").casefold()
        if attribute_id == str(OZON_HASHTAG_ATTRIBUTE_ID) or "хешт" in name or "hashtag" in name or "тег" in name:
            return _normalize_tag_values(_attribute_value_texts(attribute))
    return None


def _current_intro_from_attributes(item: Mapping[str, Any]) -> str:
    direct = str(item.get("current_intro") or item.get("intro") or item.get("annotation") or "").strip()
    if direct:
        return re.sub(r"\s+", " ", direct)
    for attribute in _all_attribute_payloads(item):
        attribute_id = str(attribute.get("id") or attribute.get("attribute_id") or "")
        name = str(attribute.get("name") or attribute.get("attribute_name") or "").casefold()
        if attribute_id != str(OZON_ANNOTATION_ATTRIBUTE_ID) and "аннотац" not in name and "annotation" not in name:
            continue
        for value in _attribute_value_texts(attribute):
            text = re.sub(r"\s+", " ", str(value or "").strip())
            if text:
                return text
    return ""


def _numeric_attribute_value(attribute: Mapping[str, Any]) -> float:
    for value in _attribute_value_texts(attribute):
        number = _number(value)
        if number > 0:
            return number
    return 0.0


def _to_mm(value: Any, unit_hint: str = "") -> Any:
    number = _number(value)
    if number <= 0:
        return ""
    hint = unit_hint.casefold()
    if "см" in hint or "cm" in hint:
        return round(number * 10, 1)
    return round(number, 1)


def _to_grams(value: Any, unit_hint: str = "") -> Any:
    number = _number(value)
    if number <= 0:
        return ""
    hint = unit_hint.casefold()
    if "кг" in hint or "kg" in hint:
        return round(number * 1000, 1)
    return round(number, 1)


def _measurements_from_sources(*sources: Mapping[str, Any]) -> Dict[str, Any]:
    measurements: Dict[str, Any] = {}
    for source in sources:
        unit_hint = str(source.get("dimension_unit") or source.get("dimensions_unit") or "")
        weight_unit = str(source.get("weight_unit") or "")
        values = {
            "length_mm": _first_value(source.get("depth"), source.get("length"), source.get("length_mm")),
            "width_mm": _first_value(source.get("width"), source.get("width_mm")),
            "height_mm": _first_value(source.get("height"), source.get("height_mm")),
        }
        for key, value in values.items():
            if key not in measurements:
                converted = _to_mm(value, unit_hint)
                if converted:
                    measurements[key] = converted
        if "weight_g" not in measurements:
            converted_weight = _to_grams(_first_value(source.get("weight"), source.get("weight_g")), weight_unit)
            if converted_weight:
                measurements["weight_g"] = converted_weight
        nested = source.get("dimensions") if isinstance(source.get("dimensions"), Mapping) else {}
        if nested:
            nested_unit = str(nested.get("unit") or unit_hint)
            nested_values = {
                "length_mm": _first_value(nested.get("depth"), nested.get("length"), nested.get("length_mm")),
                "width_mm": _first_value(nested.get("width"), nested.get("width_mm")),
                "height_mm": _first_value(nested.get("height"), nested.get("height_mm")),
            }
            for key, value in nested_values.items():
                if key not in measurements:
                    converted = _to_mm(value, nested_unit)
                    if converted:
                        measurements[key] = converted
    return measurements


def _measurements_from_attributes(item: Mapping[str, Any]) -> Dict[str, Any]:
    measurements: Dict[str, Any] = {}
    for attribute in _attributes_payload(item):
        name = str(attribute.get("name") or attribute.get("attribute_name") or "").casefold()
        value = _numeric_attribute_value(attribute)
        if value <= 0:
            continue
        if ("вес" in name or "масса" in name or "weight" in name) and "weight_g" not in measurements:
            measurements["weight_g"] = _to_grams(value, name)
        elif any(token in name for token in ("длина", "глубина", "depth", "length")) and "length_mm" not in measurements:
            measurements["length_mm"] = _to_mm(value, name)
        elif ("ширина" in name or "width" in name) and "width_mm" not in measurements:
            measurements["width_mm"] = _to_mm(value, name)
        elif ("высота" in name or "height" in name) and "height_mm" not in measurements:
            measurements["height_mm"] = _to_mm(value, name)
    return {key: value for key, value in measurements.items() if value}


def _product_measurements(*sources: Mapping[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for source in sources:
        for key, value in _measurements_from_sources(source).items():
            merged.setdefault(key, value)
        for key, value in _measurements_from_attributes(source).items():
            merged.setdefault(key, value)
    if merged:
        merged["unit"] = "mm/g"
        merged["source"] = "ozon_product_info"
    return merged


def _compact_attributes(item: Mapping[str, Any]) -> List[Dict[str, Any]]:
    compact: List[Dict[str, Any]] = []
    for attribute in _attributes_payload(item):
        attribute_id = attribute.get("id") or attribute.get("attribute_id")
        name = attribute.get("name") or attribute.get("attribute_name") or str(attribute_id or "")
        values = _attribute_value_texts(attribute)
        if not values and attribute_id != OZON_HASHTAG_ATTRIBUTE_ID:
            continue
        compact.append({
            "id": attribute_id,
            "name": name,
            "values": values[:20],
        })
    return compact


def normalize_visibility_item(item: Mapping[str, Any]) -> Dict[str, Any]:
    queries: List[Dict[str, Any]] = []
    for raw_query in item.get("queries") or item.get("search_queries") or []:
        if not isinstance(raw_query, Mapping):
            continue
        query = _normalized_query(raw_query.get("query") or raw_query.get("name") or raw_query.get("query_ru"))
        if not query:
            continue
        raw_metrics = raw_query.get("metrics") if isinstance(raw_query.get("metrics"), Mapping) else {}
        search_count = _first_value(
            raw_query.get("unique_search_users"),
            raw_query.get("search_users"),
            raw_query.get("search_count"),
            raw_query.get("count"),
            raw_query.get("searches"),
            raw_query.get("requests"),
            raw_query.get("impressions"),
            raw_query.get("shows"),
            raw_query.get("views"),
            raw_query.get("popularity"),
            raw_metrics.get("impressions"),
            raw_metrics.get("search_count"),
        )
        view_count = _first_value(
            raw_query.get("unique_view_users"),
            raw_query.get("view_users"),
            raw_query.get("clicks"),
            raw_metrics.get("clicks"),
        )
        metrics = {
            "impressions": _number(search_count),
            "clicks": _number(view_count),
            "orders": _number(raw_query.get("orders") or raw_query.get("ordered_units")),
            "revenue_rub": _number(raw_query.get("revenue_rub") or raw_query.get("ordered_amount_rub") or raw_query.get("sales_rub")),
        }
        queries.append({
            "query": query,
            "metrics": metrics,
            "value_score": _query_value({**raw_query, **metrics}),
        })
    queries.sort(key=lambda row: row["value_score"], reverse=True)
    yandex_queries = _normalize_yandex_wordstat_rows(
        item.get("yandex_wordstat"),
        item.get("wordstat"),
        item.get("external_wordstat"),
        item.get("external_queries"),
    )
    seerfar_mining_queries = normalize_seerfar_keyword_rows({
        "source_mode": "keyword_miner", "items": item.get("seerfar_keyword_mining") or [],
    })
    seerfar_reverse_queries = normalize_seerfar_keyword_rows({
        "source_mode": "keyword_reverse", "items": item.get("seerfar_keyword_reverse") or [],
    })
    seerfar_queries = [*seerfar_reverse_queries, *seerfar_mining_queries]
    offer_ids = [str(value).strip() for value in item.get("offer_ids") or [] if str(value).strip()]
    offer_id = str(item.get("offer_id") or "").strip()
    if offer_id and offer_id not in offer_ids:
        offer_ids.insert(0, offer_id)
    product_id = str(item.get("product_id") or item.get("local_product_id") or item.get("source_product_id") or "").strip()
    if not product_id:
        seed = "|".join(offer_ids) or str(item.get("title") or item)
        product_id = "ozon-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    normalized: Dict[str, Any] = {
        "product_id": product_id,
        "offer_ids": offer_ids,
        "title": str(item.get("title") or item.get("title_ru") or "").strip(),
        "sku": str(item.get("sku") or "").strip(),
        "image_url": str(item.get("image_url") or "").strip(),
        "images": _image_urls(item),
        "price": _first_value(item.get("price"), item.get("marketing_price"), item.get("old_price")),
        "currency": str(item.get("currency") or item.get("currency_code") or "").strip(),
        "stock": _stock_value(item),
        "created_at": str(_first_value(
            item.get("created_at"),
            item.get("created"),
            item.get("creation_date"),
            item.get("created_date"),
            item.get("date_created"),
        )).strip(),
        "updated_at": str(item.get("updated_at") or item.get("modified_at") or "").strip(),
        "order_count": _number(item.get("order_count") or item.get("orders") or item.get("ordered_units")),
        "category_name": str(item.get("category_name") or item.get("category") or "").strip(),
        "brand": str(item.get("brand") or item.get("brand_name") or "").strip(),
        "source_url": str(item.get("source_url") or item.get("url") or "").strip(),
        "measurements": item.get("measurements") if isinstance(item.get("measurements"), Mapping) else _product_measurements(item),
        "product_attributes": item.get("product_attributes") if isinstance(item.get("product_attributes"), list) else _compact_attributes(item),
        "current_intro": _current_intro_from_attributes(item),
        "queries": queries,
        "yandex_queries": yandex_queries,
        "seerfar_queries": seerfar_queries,
        "seerfar_mining_queries": seerfar_mining_queries,
        "seerfar_reverse_queries": seerfar_reverse_queries,
        "totals": {
            "impressions": round(sum(row["metrics"]["impressions"] for row in queries), 3),
            "clicks": round(sum(row["metrics"]["clicks"] for row in queries), 3),
            "orders": round(sum(row["metrics"]["orders"] for row in queries), 3),
            "revenue_rub": round(sum(row["metrics"]["revenue_rub"] for row in queries), 3),
            "query_count": len(queries),
        },
    }
    trial_reference_terms = _normalize_trial_reference_rows({**item, "product_attributes": normalized["product_attributes"]})
    normalized["trial_reference_terms"] = trial_reference_terms
    normalized["reference_totals"] = {
        "yandex_wordstat_searches": round(sum(_number(row.get("count")) for row in yandex_queries), 3),
        "yandex_wordstat_query_count": len(yandex_queries),
        "seerfar_keyword_mining_search_heat": round(sum(_number(row.get("count")) for row in seerfar_mining_queries), 3),
        "seerfar_keyword_mining_query_count": len(seerfar_mining_queries),
        "seerfar_keyword_reverse_searches": round(sum(_number(row.get("count")) for row in seerfar_reverse_queries), 3),
        "seerfar_keyword_reverse_query_count": len(seerfar_reverse_queries),
        "trial_reference_searches": round(sum(_number(row.get("count")) for row in trial_reference_terms), 3),
        "trial_reference_query_count": len(trial_reference_terms),
    }
    existing_tags = _existing_subject_tags(item)
    if existing_tags is None:
        existing_tags = _subject_tags_from_attributes(item)
    if existing_tags is not None:
        normalized["existing_subject_tags"] = existing_tags
    return normalized


def classify_risk_layer(item: Mapping[str, Any]) -> str:
    totals = item.get("totals") or {}
    if _number(item.get("order_count")) > 0 or _number(totals.get("orders")) > 0 or _number(totals.get("revenue_rub")) > 0:
        return "stable_seller"
    impressions = _number(totals.get("impressions"))
    clicks = _number(totals.get("clicks"))
    if impressions <= 0 and clicks <= 0:
        return "insufficient_data"
    ctr = clicks / impressions if impressions else 0.0
    if impressions < 200 or clicks < 10 or ctr < 0.01:
        return "title_optimization_candidate"
    return "tag_only_candidate"


def build_visibility_action(item: Mapping[str, Any], *, max_tags: int = 30) -> Dict[str, Any]:
    layer = classify_risk_layer(item)
    title = str(item.get("title") or "")
    ozon_query_rows = [row for row in item.get("queries") or [] if isinstance(row, Mapping)]
    counted_ozon_query_rows = [row for row in ozon_query_rows if _query_search_count(row) > 0]
    queries = [str(row.get("query") or "") for row in counted_ozon_query_rows if str(row.get("query") or "").strip()]
    yandex_query_rows = [row for row in item.get("yandex_queries") or [] if isinstance(row, Mapping)]
    yandex_queries = [str(row.get("query") or "") for row in yandex_query_rows if str(row.get("query") or "").strip()]
    seerfar_mining_query_rows = [row for row in item.get("seerfar_mining_queries") or [] if isinstance(row, Mapping)]
    seerfar_reverse_query_rows = [row for row in item.get("seerfar_reverse_queries") or [] if isinstance(row, Mapping)]
    seerfar_query_rows = [*seerfar_reverse_query_rows, *seerfar_mining_query_rows]
    seerfar_queries = [str(row.get("query") or "") for row in seerfar_query_rows if str(row.get("query") or "").strip()]
    trial_query_rows = [row for row in item.get("trial_reference_terms") or [] if isinstance(row, Mapping)]
    counted_trial_query_rows = [row for row in trial_query_rows if _query_search_count(row) > 0]
    trial_queries = [str(row.get("query") or "") for row in counted_trial_query_rows if str(row.get("query") or "").strip()]
    has_real_search_source = bool(queries or seerfar_queries or yandex_queries)
    has_trial_source = bool(trial_queries)
    has_upload_source = bool(has_real_search_source or has_trial_source)
    has_query_without_count = bool(ozon_query_rows)
    data_source_status = (
        "search_source" if has_real_search_source
        else "trial_source" if has_trial_source
        else "query_without_count" if has_query_without_count
        else "title_inference_only"
    )
    title_terms: List[str] = []
    if layer == "title_optimization_candidate":
        for query in queries:
            if not _title_contains(title, query):
                title_terms.append(query)
            if len(title_terms) >= 3:
                break
    existing_tags = item.get("existing_subject_tags") if "existing_subject_tags" in item else None
    raw_existing_tag_count = len(existing_tags) if isinstance(existing_tags, list) else None
    blocked_existing_tags = [tag for tag in existing_tags if _tag_policy_block_reason(tag)] if isinstance(existing_tags, list) else []
    if isinstance(existing_tags, list):
        existing_tags = [tag for tag in existing_tags if not _tag_policy_block_reason(tag)]
    existing_tag_count = len(existing_tags) if isinstance(existing_tags, list) else None
    missing_tag_count = max(0, max_tags - existing_tag_count) if existing_tag_count is not None else None
    tag_query_sources = [*queries, *seerfar_queries, *yandex_queries, *trial_queries]
    intro_terms = _intro_keyword_terms([*counted_ozon_query_rows, *seerfar_query_rows, *yandex_query_rows, *counted_trial_query_rows])
    current_intro = str(item.get("current_intro") or "")
    intro_recommendation = _recommended_intro(title, current_intro, intro_terms)
    recommended_intro = str(intro_recommendation.get("recommended_intro") or "")
    intro_update_available = bool(intro_recommendation.get("intro_update_available"))
    compile_sources = tag_query_sources
    compiled_tags = _compile_tags(compile_sources, max_tags * 2)
    existing_keys = {str(tag).casefold() for tag in existing_tags} if isinstance(existing_tags, list) else set()
    new_tag_candidates = [tag for tag in compiled_tags if tag.casefold() not in existing_keys]
    subject_tag_strategy = "fill_missing"
    subject_tags_to_remove: List[str] = []
    replacement_count = 0
    if existing_tag_count is not None and existing_tag_count >= max_tags:
        subject_tag_strategy = "replace_low_search"
        scored_rows = [*counted_ozon_query_rows, *seerfar_query_rows, *yandex_query_rows, *counted_trial_query_rows]
        candidate_scores = [
            (tag, _tag_search_score(tag, scored_rows))
            for tag in new_tag_candidates[:DEFAULT_TAG_REPLACEMENT_LIMIT]
        ]
        replacement_pairs: List[tuple[str, str]] = []
        if isinstance(existing_tags, list):
            scored_existing = sorted(
                [
                    (tag, _tag_search_score(tag, scored_rows), index)
                    for index, tag in enumerate(existing_tags)
                ],
                key=lambda item: (item[1], -item[2]),
            )
            used_existing: set[str] = set()
            for candidate, candidate_score in candidate_scores:
                if candidate_score <= 0:
                    continue
                removable = next((
                    (tag, score)
                    for tag, score, _index in scored_existing
                    if tag.casefold() not in used_existing and score > 0 and candidate_score > score
                ), None)
                if removable is None:
                    continue
                removable_tag, _score = removable
                replacement_pairs.append((candidate, removable_tag))
                used_existing.add(removable_tag.casefold())
        tag_terms = [candidate for candidate, _removed in replacement_pairs]
        subject_tags_to_remove = [removed for _candidate, removed in replacement_pairs]
        replacement_count = len(tag_terms)
    else:
        tag_limit = missing_tag_count if missing_tag_count is not None else max_tags
        tag_terms = new_tag_candidates[:max(0, tag_limit)]
    subject_tag_suggestion_available = bool(tag_terms)
    subject_tag_update_required = has_upload_source and bool(tag_terms) and (
        missing_tag_count is None or missing_tag_count > 0 or subject_tag_strategy == "replace_low_search"
    )
    locked_title = layer == "stable_seller"
    if layer == "insufficient_data":
        allowed_changes = ["subject_tags"] if subject_tag_update_required else []
    else:
        allowed_changes = ["subject_tags"] if locked_title else ["title", "subject_tags"]
    if locked_title and not subject_tag_update_required:
        allowed_changes = []
    elif not locked_title and layer == "tag_only_candidate" and not subject_tag_update_required:
        allowed_changes = []
    if has_upload_source and intro_update_available and "intro" not in allowed_changes:
        allowed_changes.append("intro")
    totals = item.get("totals") or {}
    order_count = _number(item.get("order_count")) or _number(totals.get("orders"))
    return {
        "product_id": item["product_id"],
        "current_title": title,
        "offer_ids": item.get("offer_ids") or [],
        "sku": item.get("sku") or "",
        "image_url": item.get("image_url") or ((item.get("images") or [""])[0] if isinstance(item.get("images"), list) else ""),
        "images": item.get("images") or [],
        "price": item.get("price") or "",
        "currency": item.get("currency") or "",
        "stock": item.get("stock") or "",
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
        "order_count": order_count,
        "category_name": item.get("category_name") or "",
        "brand": item.get("brand") or "",
        "source_url": item.get("source_url") or "",
        "measurements": item.get("measurements") if isinstance(item.get("measurements"), Mapping) else {},
        "product_attributes": item.get("product_attributes") if isinstance(item.get("product_attributes"), list) else [],
        "risk_layer": layer,
        "allowed_changes": allowed_changes,
        "blocked_changes": ["title"] if locked_title else [],
        "title_locked": locked_title,
        "title_terms": title_terms,
        "intro_terms": intro_terms,
        "current_intro": current_intro,
        "recommended_intro": recommended_intro,
        "intro_supplement": intro_recommendation.get("intro_supplement") or "",
        "intro_update_available": intro_update_available,
        "subject_tags": tag_terms,
        "existing_subject_tags": existing_tags if isinstance(existing_tags, list) else None,
        "existing_subject_tag_count": existing_tag_count,
        "raw_existing_subject_tag_count": raw_existing_tag_count,
        "blocked_existing_subject_tag_count": len(blocked_existing_tags),
        "missing_subject_tag_count": missing_tag_count,
        "subject_tag_strategy": subject_tag_strategy,
        "subject_tags_to_remove": subject_tags_to_remove,
        "subject_tag_replacement_count": replacement_count,
        "subject_tag_update_required": subject_tag_update_required,
        "subject_tag_suggestion_available": subject_tag_suggestion_available,
        "data_source_status": data_source_status,
        "evidence": {
            "top_queries": ozon_query_rows[:10],
            "top_seerfar_keyword_mining": seerfar_mining_query_rows[:10],
            "top_seerfar_keyword_reverse": seerfar_reverse_query_rows[:10],
            "top_yandex_wordstat": yandex_query_rows[:10],
            "top_trial_terms": trial_query_rows[:10],
            "totals": totals,
            "reference_totals": item.get("reference_totals") if isinstance(item.get("reference_totals"), Mapping) else {},
            "data_source_status": data_source_status,
        },
        "reason_cn": _reason_cn(
            layer,
            locked_title,
            existing_tag_count,
            missing_tag_count,
            subject_tag_update_required,
            subject_tag_suggestion_available=subject_tag_suggestion_available,
            has_search_source=has_upload_source,
            data_source_status=data_source_status,
            subject_tag_strategy=subject_tag_strategy,
            replacement_count=replacement_count,
            seerfar_mining_query_count=len(seerfar_mining_query_rows),
            seerfar_reverse_query_count=len(seerfar_reverse_query_rows),
            yandex_query_count=len(yandex_query_rows),
            trial_source_labels=sorted({str(row.get("source_label") or "试错词") for row in counted_trial_query_rows if str(row.get("source_label") or "").strip()}),
        ),
    }


def _reason_cn(
    layer: str,
    locked_title: bool,
    existing_tag_count: Optional[int] = None,
    missing_tag_count: Optional[int] = None,
    subject_tag_update_required: bool = True,
    *,
    subject_tag_suggestion_available: bool = True,
    has_search_source: bool = True,
    data_source_status: str = "search_source",
    subject_tag_strategy: str = "fill_missing",
    replacement_count: int = 0,
    seerfar_mining_query_count: int = 0,
    seerfar_reverse_query_count: int = 0,
    yandex_query_count: int = 0,
    trial_source_labels: Optional[Sequence[str]] = None,
) -> str:
    references = []
    if seerfar_reverse_query_count:
        references.append("Seerfar竞品反查")
    if seerfar_mining_query_count:
        references.append("Seerfar 月搜热度")
    if yandex_query_count:
        references.append("Yandex Wordstat")
    reference_text = f"，含{'、'.join(references)}参考词" if references else ""
    if not has_search_source:
        if data_source_status == "query_without_count":
            return "已读取到搜索词，但没有搜索人数；只展示候选词，不自动上传标签或简介。"
        if subject_tag_suggestion_available:
            return "仅根据标题和商品信息识别商品，暂无 Ozon/Yandex 搜索数据来源；只展示标签建议，不自动上传。"
        return "暂无 Ozon/Yandex 搜索数据来源；不自动生成可上传建议。"
    if data_source_status == "trial_source":
        label_text = "、".join([label for label in (trial_source_labels or []) if label]) or "试错词"
        if subject_tag_strategy == "replace_low_search" and replacement_count > 0:
            return f"暂无成交搜索量，本轮用{label_text}中 {replacement_count} 个试错词替换低搜索标签，并把完整词补进简介；手动确认后上传。"
        if subject_tag_update_required:
            return f"暂无成交搜索量，本轮用{label_text}补充主题标签，并把完整词补进简介；手动确认后上传。"
        return f"暂无成交搜索量，已识别{label_text}，可优先补简介。"
    if subject_tag_strategy == "replace_low_search" and replacement_count > 0:
        title_text = "标题不自动改" if locked_title or layer == "insufficient_data" else "标题可进入候选"
        return f"当前已有 30 个主题标签，本轮用 {replacement_count} 个高搜索词替换低搜索标签，并把完整词补进简介{reference_text}；{title_text}。"
    if existing_tag_count is not None and not subject_tag_update_required:
        if existing_tag_count >= 30:
            return "当前已有 30 个主题标签，但本轮没有读到可替换的高搜索词。"
        return f"当前已有 {existing_tag_count} 个主题标签，本轮没有读到新的可补搜索词。"
    if existing_tag_count == 0 and subject_tag_update_required:
        title_text = "标题不自动改" if locked_title or layer == "insufficient_data" else "标题可进入候选"
        return f"当前没有主题标签，照搬高搜索词补充标签，并把完整词补进简介{reference_text}；{title_text}。"
    gap_text = f"当前已有 {existing_tag_count} 个主题标签，还差 {missing_tag_count} 个到 30 个。" if existing_tag_count is not None else "未读取到当前主题标签数量，先只生成可补齐候选。"
    if locked_title:
        return f"该商品已有订单或成交金额，标题不自动改；{gap_text}"
    if layer == "title_optimization_candidate":
        return "该商品低曝光、低点击或无订单，允许进入标题优化候选批次。"
    if layer == "tag_only_candidate":
        return f"该商品有搜索数据但未达到改标题条件，先建议主题标签和简介补齐；{gap_text}"
    return "该商品搜索数据不足，本轮不建议自动改标题或标签。"


def _chunk(items: Sequence[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [list(items[index:index + size]) for index in range(0, len(items), size)]


def build_search_visibility_plan(
    source: Mapping[str, Any],
    *,
    generated_at: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Dict[str, Any]:
    items = [normalize_visibility_item(item) for item in source.get("items") or source.get("products") or [] if isinstance(item, Mapping)]
    actions = [build_visibility_action(item) for item in items]
    actions.sort(
        key=lambda item: (
            _timestamp_rank(item.get("created_at") or item.get("updated_at")),
            _timestamp_rank(item.get("updated_at")),
            _number(item.get("product_id")),
        ),
        reverse=True,
    )
    layers = {
        "stable_seller": [item for item in actions if item["risk_layer"] == "stable_seller"],
        "title_optimization_candidate": [item for item in actions if item["risk_layer"] == "title_optimization_candidate"],
        "tag_only_candidate": [item for item in actions if item["risk_layer"] == "tag_only_candidate"],
        "insufficient_data": [item for item in actions if item["risk_layer"] == "insufficient_data"],
    }
    batches = []
    for layer, layer_items in layers.items():
        actionable_items = [item for item in layer_items if item.get("allowed_changes")]
        for index, group in enumerate(_chunk(actionable_items, max(1, int(batch_size))), start=1):
            batches.append({
                "batch_id": f"{layer}-{index}",
                "risk_layer": layer,
                "product_count": len(group),
                "product_ids": [item["product_id"] for item in group],
                "allowed_changes": sorted({change for item in group for change in item.get("allowed_changes", [])}),
            })
    return {
        "schema_version": "1.0.0",
        "mode": "dry_run",
        "source": "ozon_seller_search_visibility",
        "shop_id": str(source.get("shop_id") or source.get("store_id") or "unknown"),
        "period_days": int(source.get("period_days") or source.get("window_days") or DEFAULT_TITLE_SIGNAL_DAYS),
        "recommended_schedule_days": 7,
        "generated_at": generated_at or _now(),
        "summary": {
            "products": len(actions),
            "stable_tag_only": sum(1 for item in layers["stable_seller"] if item.get("subject_tag_update_required")),
            "title_optimization_candidates": len(layers["title_optimization_candidate"]),
            "tag_only_candidates": sum(1 for item in layers["tag_only_candidate"] if item.get("subject_tag_update_required")),
            "insufficient_data": len(layers["insufficient_data"]),
        },
        "batches": batches,
        "actions": actions,
        "safety": {
            "dry_run_only": True,
            "write_api_calls": 0,
            "inventory_api_calls": 0,
            "stable_seller_title_locked": True,
            "requires_explicit_write_scope_before_ozon_update": True,
        },
    }


def _chunks(values: Sequence[Any], size: int) -> Iterable[List[Any]]:
    for index in range(0, len(values), size):
        yield list(values[index:index + size])


def _query_rows(response: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw_items = response.get("queries")
    if raw_items is None:
        raw_items = response.get("items")
    if raw_items is None:
        result = response.get("result") if isinstance(response.get("result"), Mapping) else {}
        raw_items = result.get("queries") or result.get("items") or []
    rows = []
    for item in raw_items or []:
        if not isinstance(item, Mapping):
            continue
        query = item.get("query") or item.get("name") or item.get("query_ru")
        if not query:
            continue
        search_users = _first_value(
            item.get("unique_search_users"),
            item.get("search_users"),
            item.get("search_count"),
            item.get("count"),
            item.get("searches"),
            item.get("requests"),
            item.get("impressions"),
            item.get("shows"),
            item.get("views"),
            item.get("popularity"),
        )
        view_users = _first_value(item.get("unique_view_users"), item.get("view_users"), item.get("clicks"))
        rows.append({
            "query": query,
            "sku": item.get("sku"),
            "impressions": search_users,
            "clicks": view_users,
            "orders": item.get("order_count") or item.get("orders") or item.get("ordered_units"),
            "revenue_rub": item.get("gmv") or item.get("revenue_rub") or item.get("ordered_amount_rub") or item.get("sales_rub"),
            "search_users": search_users,
            "view_users": view_users,
            "position": item.get("position"),
            "query_index": item.get("query_index"),
            "view_conversion": item.get("view_conversion"),
        })
    return rows


def _posting_rows(response: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result = response.get("result") if isinstance(response.get("result"), Mapping) else response.get("result")
    if isinstance(result, Mapping):
        raw_items = result.get("postings") or result.get("items") or []
    elif isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        raw_items = result
    else:
        raw_items = response.get("postings") or response.get("items") or []
    return [dict(item) for item in raw_items or [] if isinstance(item, Mapping)]


def _posting_products(posting: Mapping[str, Any]) -> List[Dict[str, Any]]:
    products = posting.get("products") or posting.get("items") or []
    return [dict(item) for item in products or [] if isinstance(item, Mapping)]


def _collect_order_counts(client: Any, *, date_from: str, date_to: str) -> Dict[str, Any]:
    if not hasattr(client, "list_fbo_postings") or not hasattr(client, "list_fbs_postings"):
        return {"by_offer_id": {}, "by_sku": {}, "by_product_id": {}, "errors": [], "read_api_calls": 0}

    by_offer_id: Dict[str, float] = {}
    by_sku: Dict[str, float] = {}
    by_product_id: Dict[str, float] = {}
    errors: List[Dict[str, Any]] = []
    read_api_calls = 0
    since = date_from
    to = date_to
    for source_kind, method_name in (("fbo", "list_fbo_postings"), ("fbs", "list_fbs_postings")):
        offset = 0
        while True:
            try:
                response = getattr(client, method_name)(since=since, to=to, limit=1000, offset=offset)
                read_api_calls += 1
            except Exception as exc:
                errors.append({"source_kind": source_kind, "error_type": type(exc).__name__})
                break
            postings = _posting_rows(response)
            if not postings:
                break
            for posting in postings:
                for product in _posting_products(posting):
                    quantity = _number(product.get("quantity") or product.get("qty")) or 1.0
                    offer_id = str(product.get("offer_id") or "").strip()
                    sku = str(product.get("sku") or "").strip()
                    product_id = str(product.get("product_id") or product.get("id") or "").strip()
                    if offer_id:
                        by_offer_id[offer_id] = by_offer_id.get(offer_id, 0.0) + quantity
                    if sku:
                        by_sku[sku] = by_sku.get(sku, 0.0) + quantity
                    if product_id:
                        by_product_id[product_id] = by_product_id.get(product_id, 0.0) + quantity
            if len(postings) < 1000:
                break
            offset += len(postings)
    return {
        "by_offer_id": by_offer_id,
        "by_sku": by_sku,
        "by_product_id": by_product_id,
        "errors": errors,
        "read_api_calls": read_api_calls,
    }


def _matched_order_count(
    order_counts: Mapping[str, Any],
    *,
    product_id: Any,
    offer_id: str,
    sku: Any,
) -> float:
    by_offer_id = order_counts.get("by_offer_id") if isinstance(order_counts.get("by_offer_id"), Mapping) else {}
    by_sku = order_counts.get("by_sku") if isinstance(order_counts.get("by_sku"), Mapping) else {}
    by_product_id = order_counts.get("by_product_id") if isinstance(order_counts.get("by_product_id"), Mapping) else {}
    return max(
        _number(by_offer_id.get(str(offer_id or "").strip())),
        _number(by_sku.get(str(sku or "").strip())),
        _number(by_product_id.get(str(product_id or "").strip())),
    )


def collect_seller_search_visibility(
    client: Any,
    *,
    shop_id: str,
    date_from: str,
    date_to: str,
    order_date_from: Optional[str] = None,
    order_date_to: Optional[str] = None,
    order_period_days: Optional[int] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_products: int = 50,
    period_days: int = 7,
) -> Dict[str, Any]:
    """Read seller catalog and product-query data through the read-only client.

    The client allowlist still owns API safety.  This function deliberately
    returns a local source payload for dry-run planning; it never updates Ozon.
    """
    if not str(shop_id or "").strip():
        raise ValueError("shop_id is required")
    # ``0`` means the complete store catalog.  The caller still fetches the
    # Seller API page by page, so this does not create one oversized request.
    limit_products = None if int(max_products) <= 0 else max(1, int(max_products))
    catalog_items: List[Dict[str, Any]] = []
    seen_catalog_keys: set[str] = set()
    last_id = ""
    while limit_products is None or len(catalog_items) < limit_products:
        remaining = int(page_size) if limit_products is None else limit_products - len(catalog_items)
        page_limit = max(1, min(int(page_size), remaining))
        page = client.list_products(limit=page_limit, last_id=last_id)
        result = page.get("result") if isinstance(page.get("result"), Mapping) else {}
        items = result.get("items") or []
        for item in items:
            if limit_products is not None and len(catalog_items) >= limit_products:
                break
            if not isinstance(item, Mapping):
                continue
            key = str(item.get("product_id") or item.get("offer_id") or item)
            if key in seen_catalog_keys:
                continue
            seen_catalog_keys.add(key)
            catalog_items.append(dict(item))
        next_id = str(result.get("last_id") or "")
        if not next_id or next_id == last_id:
            break
        last_id = next_id

    product_ids = sorted({int(item.get("product_id")) for item in catalog_items if str(item.get("product_id") or "").isdigit()})
    info_by_product_id: Dict[int, Dict[str, Any]] = {}
    for group in _chunks(product_ids, max(1, min(int(page_size), 1000))):
        info = client.get_product_info(group)
        for item in _response_items(info):
            product_id = item.get("id") or item.get("product_id")
            if str(product_id or "").isdigit():
                info_by_product_id[int(product_id)] = dict(item)

    attributes_by_product_id: Dict[int, Dict[str, Any]] = {}
    attributes_by_offer_id: Dict[str, Dict[str, Any]] = {}
    offer_ids = [
        str(item.get("offer_id") or "").strip()
        for item in catalog_items
        if str(item.get("offer_id") or "").strip()
    ]
    if hasattr(client, "get_product_attributes") and offer_ids:
        for group in _chunks(offer_ids, max(1, min(int(page_size), 100))):
            attributes_response = client.get_product_attributes(offer_ids=group)
            for attribute_item in _response_items(attributes_response):
                product_id = attribute_item.get("id") or attribute_item.get("product_id")
                offer_id = str(attribute_item.get("offer_id") or "").strip()
                if str(product_id or "").isdigit():
                    attributes_by_product_id[int(product_id)] = dict(attribute_item)
                if offer_id:
                    attributes_by_offer_id[offer_id] = dict(attribute_item)

    queries_by_sku: Dict[str, List[Dict[str, Any]]] = {}
    query_errors: List[Dict[str, Any]] = []
    sku_values = sorted({int(info.get("sku")) for info in info_by_product_id.values() if str(info.get("sku") or "").isdigit()})
    if sku_values:
        query_page_size = min(100, max(1, int(page_size)))
        limit_by_sku = min(15, query_page_size)
        query_batch_size = max(1, query_page_size // limit_by_sku)
        for group in _chunks(sku_values, query_batch_size):
            try:
                if hasattr(client, "get_product_query_details"):
                    response = client.get_product_query_details(
                        group,
                        date_from=date_from,
                        date_to=date_to,
                        limit_by_sku=limit_by_sku,
                        page_size=query_page_size,
                    )
                else:
                    response = client.get_product_queries(group, date_from=date_from, date_to=date_to, page_size=query_page_size)
                fallback_sku = str(group[0]) if len(group) == 1 else ""
                for row in _query_rows(response):
                    sku_key = str(row.get("sku") or fallback_sku or "").strip()
                    if not sku_key:
                        continue
                    queries_by_sku.setdefault(sku_key, []).append(row)
            except Exception as exc:  # Search analytics can be unavailable while catalog reads still work.
                query_errors.append({
                    "sku_count": len(group),
                    "error_type": type(exc).__name__,
                })

    order_counts = _collect_order_counts(
        client,
        date_from=order_date_from or date_from,
        date_to=order_date_to or date_to,
    )
    source_items: List[Dict[str, Any]] = []
    for item in catalog_items:
        product_id = item.get("product_id")
        info = info_by_product_id.get(int(product_id)) if str(product_id or "").isdigit() else {}
        offer_id = str(item.get("offer_id") or info.get("offer_id") or "").strip()
        attribute_item = (
            attributes_by_product_id.get(int(product_id)) if str(product_id or "").isdigit() else None
        ) or attributes_by_offer_id.get(offer_id) or {}
        sku = info.get("sku")
        queries = queries_by_sku.get(str(sku or ""), [])
        images = _image_urls(info, item, attribute_item)
        existing_tags = (
            _subject_tags_from_attributes(attribute_item)
            or info.get("subject_tags")
            or info.get("tags")
            or info.get("hashtags")
            or item.get("subject_tags")
            or item.get("tags")
        )
        order_count = _matched_order_count(order_counts, product_id=product_id, offer_id=offer_id, sku=sku)
        source_items.append({
            "product_id": str(product_id or ""),
            "offer_id": offer_id,
            "title": item.get("name") or info.get("name") or info.get("title") or "",
            "sku": sku or "",
            "image_url": (images or [""])[0],
            "images": images,
            "price": _first_value(
                info.get("marketing_price"),
                info.get("price"),
                info.get("old_price"),
                item.get("price"),
                item.get("marketing_price"),
            ),
            "currency": _first_value(info.get("currency_code"), item.get("currency_code"), info.get("currency"), item.get("currency")),
            "stock": _stock_value(info, item),
            "created_at": _first_value(
                info.get("created_at"),
                info.get("created"),
                info.get("creation_date"),
                info.get("created_date"),
                info.get("date_created"),
                item.get("created_at"),
                item.get("created"),
                item.get("creation_date"),
                item.get("created_date"),
                item.get("date_created"),
            ),
            "updated_at": _first_value(info.get("updated_at"), info.get("modified_at"), item.get("updated_at"), item.get("modified_at")),
            "category_name": _first_value(info.get("category_name"), info.get("category"), item.get("category_name"), item.get("category")),
            "brand": _first_value(info.get("brand"), info.get("brand_name"), item.get("brand"), item.get("brand_name")),
            "source_url": _first_value(info.get("url"), item.get("url")),
            "measurements": _product_measurements(info, item, attribute_item),
            "product_attributes": _compact_attributes(attribute_item),
            "existing_subject_tags": existing_tags,
            "order_count": order_count,
            "queries": queries,
        })

    return {
        "schema_version": "1.0.0",
        "source": "ozon_seller_search_visibility",
        "shop_id": str(shop_id).strip(),
        "period_days": int(period_days),
        "order_period_days": int(order_period_days or period_days),
        "order_date_from": order_date_from or date_from,
        "order_date_to": order_date_to or date_to,
        "date_from": date_from,
        "date_to": date_to,
        "product_limit": limit_products,
        "items": source_items,
        "query_errors": query_errors,
        "order_errors": order_counts.get("errors") or [],
        "order_read_api_calls": order_counts.get("read_api_calls") or 0,
        "safety": {
            "read_only": True,
            "write_api_calls": 0,
            "inventory_api_calls": 0,
        },
    }
