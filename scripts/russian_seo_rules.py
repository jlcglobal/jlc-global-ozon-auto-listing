"""Deterministic Russian Ozon SEO text helpers.

These helpers are intentionally deterministic.  The ecommerce designer owns the
commercial intent, while this module owns marketplace-safe formatting:
titles without repeated core terms and up to 30 valid Ozon search hashtags.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Iterator, List, Sequence

HASHTAG_PATTERN = re.compile(r"^#[А-Яа-яЁё]+$")
SEARCH_KEYWORD_PATTERN = re.compile(r"^[А-Яа-яЁё0-9]+(?:_[А-Яа-яЁё0-9]+)*$")
TAG_TOKEN_PATTERN = re.compile(r"[А-Яа-яЁё]+")
SEARCH_TOKEN_PATTERN = re.compile(r"[А-Яа-яЁё0-9]+")
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")

WEAK_SINGLE_TAGS = {
    "товар", "покупка", "работы", "работа", "воды", "вода", "поездки",
    "поездка", "прогулки", "прогулка", "офиса", "офис", "машины", "машина",
    "дом", "дома", "семьи", "семья", "выбор", "решение", "вещь",
    "товары", "удобство", "практично", "ежедневно", "компактно", "польза",
    "хранение", "порядок", "кухня", "полка", "шкаф", "быт", "аккуратно",
    "пространство", "чистота", "форма", "посуда", "запасы", "прозрачный",
    "крышка", "размер", "набор",
}

BANNED_GENERIC_TAGS = {
    "#товарнаозон", "#удобнаяпокупка", "#актуальныйтовар", "#товар",
    "#покупка", "#хорошийвыбор", "#современныйтовар", "#простаяпокупка",
    "#полезнаяпокупка", "#практичныйтовар", "#выбордлясемьи",
    "#товары", "#удобство", "#практично", "#ежедневно", "#компактно",
    "#польза", "#решение",
}

TAG_REPAIRS = {
    "кружка машины": "кружка для автомобиля",
    "кружка машина": "кружка для автомобиля",
    "кружка автомобиля": "кружка для автомобиля",
    "кружка авто": "кружка для автомобиля",
    "кружка офиса": "кружка для офиса",
    "кружка офис": "кружка для офиса",
    "кружка ручкой": "кружка с ручкой",
    "термокружка ручкой": "термокружка с ручкой",
    "кружка крышкой": "кружка с крышкой",
    "крышка кружки": "крышка для кружки",
    "горячих напитков": "для горячих напитков",
    "холодных напитков": "для холодных напитков",
    "напитки собой": "напитки с собой",
    "товары напитков": "посуда для напитков",
    "работы": "для работы",
    "поездки": "для поездки",
    "прогулки": "для прогулки",
    "смузи": "кружка для смузи",
}


def _iter_texts(value: Any) -> Iterator[str]:
    if value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_texts(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_texts(item)
        return
    text = str(value).strip()
    if text:
        yield text


def product_specific_longtail_candidates(values: Sequence[Any] | None) -> List[str]:
    """Build non-generic Russian long-tail tag candidates from product words.

    This is a deterministic repair layer for bad model output.  It may combine
    terms only when those terms are present in the current product evidence or
    ecommerce design.  It never adds marketplace/generic purchase filler.
    """
    texts: List[str] = []
    for value in values or []:
        for text in _iter_texts(value):
            normalized = re.sub(r"\s+", " ", text).strip(" ,.;:")
            if normalized and normalized.casefold() not in {item.casefold() for item in texts}:
                texts.append(normalized)
    joined = " ".join(texts).casefold()
    joined_tokens = [token.casefold() for token in TAG_TOKEN_PATTERN.findall(joined)]

    def has_token_prefix(*prefixes: str) -> bool:
        return any(
            token == prefix or token.startswith(prefix)
            for token in joined_tokens
            for prefix in prefixes
        )

    candidates: List[str] = []

    def add(value: str) -> None:
        if value and value.casefold() not in {item.casefold() for item in candidates}:
            candidates.append(value)

    if has_token_prefix("контейнер"):
        for value in (
            "контейнер для хранения",
            "контейнер для кухни",
            "контейнер для шкафа",
            "контейнер для полки",
            "контейнер с крышкой",
            "прозрачный контейнер",
            "кухонный контейнер",
            "контейнер для еды",
            "контейнер для запасов",
            "контейнер для посуды",
            "контейнер для дома",
            "контейнер для порядка",
            "контейнер разных размеров",
            "набор контейнеров",
            "органайзер для хранения",
            "органайзер для кухни",
            "органайзер для шкафа",
            "органайзер для полки",
            "органайзер для запасов",
            "организация хранения",
            "организация хранения дома",
            "хранение на кухне",
            "хранение в шкафу",
            "хранение на полке",
            "хранение посуды",
            "хранение запасов",
            "порядок на кухне",
            "порядок в шкафу",
            "разные размеры",
            "контейнер для круп",
            "контейнер для продуктов",
            "контейнер для полок",
            "домашнее хранение",
        ):
            add(value)
    elif has_token_prefix("органайзер") or (
        has_token_prefix("хранен")
        and has_token_prefix("контейнер", "короб", "ящик", "полк", "кофр", "сумк")
    ):
        if has_token_prefix("инструмент", "гараж", "верстак", "авто", "крепеж", "крепёж", "болт", "гайк"):
            for value in (
                "органайзер для инструмента",
                "хранение инструмента",
                "организация крепежа",
                "органайзер для крепежа",
                "порядок в мастерской",
            ):
                add(value)
        elif has_token_prefix("кухн", "шкаф", "полк", "запас", "посуд"):
            for value in (
                "органайзер для хранения",
                "органайзер для кухни",
                "органайзер для шкафа",
                "органайзер для полки",
                "органайзер для запасов",
                "организация хранения",
                "организация хранения дома",
                "хранение на кухне",
                "хранение в шкафу",
                "хранение на полке",
                "хранение посуды",
                "хранение запасов",
                "порядок на кухне",
                "порядок в шкафу",
                "разные размеры",
                "домашнее хранение",
            ):
                add(value)
        else:
            for value in ("органайзер для хранения", "организация хранения"):
                add(value)

    if has_token_prefix("термокруж", "кружка", "стакан"):
        for value in (
            "термокружка с ручкой",
            "термокружка для напитков",
            "термокружка в дорогу",
            "термокружка для автомобиля",
            "термокружка для офиса",
            "термокружка для прогулки",
            "кружка с крышкой",
            "кружка для воды",
            "кружка для кофе",
            "кружка для чая",
            "кружка для автомобиля",
            "кружка для офиса",
            "кружка с ручкой",
            "напитки с собой",
            "посуда для напитков",
        ):
            add(value)

    if (
        has_token_prefix("топлив", "гсм")
        and has_token_prefix("канистр", "бак", "емкост", "ёмкост")
        and not has_token_prefix("опрыскив", "распыл", "мойк", "автомойк", "пеногенератор")
    ):
        for value in (
            "канистра для топлива",
            "канистра для гсм",
            "металлическая канистра",
            "канистра с ручкой",
            "канистра для хранения",
            "емкость для топлива",
            "бак для топлива",
        ):
            add(value)

    if has_token_prefix("блендер"):
        for value in (
            "портативный блендер",
            "блендер для смузи",
            "блендер для напитков",
            "мини блендер",
            "блендер с чашей",
            "кухонный блендер",
        ):
            add(value)

    capacities = re.findall(
        r"\d+(?:[,.]\d+)?\s*(?:л|литр(?:а|ов)?|мл|ml|l)\b",
        joined,
        flags=re.IGNORECASE,
    )
    base_terms = []
    for value in candidates + texts:
        tag = canonical_hashtag(value)
        if tag and "_" in tag and len(base_terms) < 8:
            base_terms.append(value)
    for capacity in capacities:
        add(capacity)
        for base in base_terms[:5]:
            add(f"{base} {capacity}")

    return candidates


def valid_hashtag(value: Any) -> bool:
    tag = str(value or "").strip()
    return bool(HASHTAG_PATTERN.fullmatch(tag) and 2 <= len(tag) <= 30)


def _normalize_capacity_text(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        number = float(match.group(1).replace(",", "."))
        unit = match.group(2).casefold()
        if unit in {"л", "l", "литр", "литра", "литров"}:
            if number >= 10:
                if float(number).is_integer():
                    return f"{int(number)}л"
                return f"{int(round(number * 1000))}мл"
            return f"{int(round(number * 1000))}мл"
        return f"{int(round(number))}мл"

    return re.sub(
        r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(л|l|литр|литра|литров|мл|ml)\b",
        repl,
        text,
        flags=re.IGNORECASE,
    )


def canonical_hashtag(value: Any, *, blocked_terms: Iterable[str] | None = None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    # Ozon search tags in this project are Russian search phrases only.  Do
    # not silently salvage a brand/model/offer such as ``ACME_500`` by merely
    # stripping its invalid characters: the complete candidate is rejected.
    if re.search(r"[A-Za-z0-9_]", raw):
        return None
    raw = CJK_PATTERN.sub(" ", raw)
    raw = raw[1:] if raw.startswith("#") else raw
    raw = _normalize_capacity_text(raw)
    folded_raw = re.sub(r"[_\s]+", " ", raw.casefold()).strip()
    raw = TAG_REPAIRS.get(folded_raw, raw)
    tokens = [token.casefold() for token in TAG_TOKEN_PATTERN.findall(raw)]
    if not tokens:
        return None
    candidates = ["".join(tokens)]
    if len(tokens) == 1 and tokens[0] not in WEAK_SINGLE_TAGS:
        candidates.append(tokens[0])
    blocked = {str(term).casefold() for term in (blocked_terms or []) if str(term).strip()}
    for body in candidates:
        body = re.sub(r"_+", "_", body).strip("_")
        if not body:
            continue
        tag = "#" + body
        if not valid_hashtag(tag):
            continue
        folded = tag.casefold()
        if folded in BANNED_GENERIC_TAGS:
            continue
        if body in WEAK_SINGLE_TAGS:
            continue
        if any(term in folded for term in blocked):
            continue
        return tag
    return None


def compile_hashtags(
    candidates: Sequence[Any] | None,
    *,
    supplements: Sequence[Any] | None = None,
    blocked_terms: Iterable[str] | None = None,
) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        tag = canonical_hashtag(value, blocked_terms=blocked_terms)
        if not tag:
            return
        key = tag.casefold()
        if key in seen:
            return
        seen.add(key)
        result.append(tag)

    for value in list(candidates or []) + list(supplements or []):
        add(value)
        if len(result) >= 30:
            break
    return result[:30]


def canonical_search_keyword(value: Any) -> str | None:
    """Project a natural Russian search phrase to the Ozon keyword field form.

    Titles and descriptions keep normal Russian punctuation.  Only search-keyword
    artifacts use this compact underscore form.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = CJK_PATTERN.sub(" ", raw)
    raw = _normalize_capacity_text(raw)
    tokens = [token.casefold() for token in SEARCH_TOKEN_PATTERN.findall(raw)]
    if not any(re.search(r"[а-яё]", token) for token in tokens):
        return None
    keyword = "_".join(tokens)
    keyword = re.sub(r"_+", "_", keyword).strip("_")
    if not keyword or not SEARCH_KEYWORD_PATTERN.fullmatch(keyword):
        return None
    return keyword


def compile_search_keywords(
    candidates: Sequence[Any] | None,
    *,
    max_count: int = 50,
) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in candidates or []:
        keyword = canonical_search_keyword(value)
        if not keyword:
            continue
        key = keyword.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(keyword)
        if len(result) >= max_count:
            break
    return result


def validate_hashtag_set(tags: Sequence[Any]) -> bool:
    if len(tags) > 30:
        return False
    normalized = [str(tag).strip() for tag in tags]
    if len({tag.casefold() for tag in normalized}) != len(normalized):
        return False
    for tag in normalized:
        if not valid_hashtag(tag):
            return False
        canonical = canonical_hashtag(tag)
        if not canonical or canonical.casefold() != tag.casefold():
            return False
    return True


def remove_duplicate_core_title(title: str) -> str:
    """Remove comma-spliced repeated Russian core term at the start."""
    text = re.sub(r"\s+", " ", str(title or "")).strip(" ,")
    if not text:
        return text
    parts = [part.strip() for part in re.split(r",\s+", text) if part.strip()]
    if len(parts) >= 2:
        first = parts[0].casefold()
        second = parts[1].casefold()
        if first and (second.startswith(first + " ") or second == first):
            parts = parts[1:]
    return ", ".join(parts)
