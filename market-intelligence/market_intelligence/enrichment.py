"""Traceable public-image caching and deterministic local keyword enrichment."""

from __future__ import annotations

import hashlib
import html
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .search_queries_import import TRANSLATIONS_ZH, classify_type
from .storage import MarketStore


MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_ROUTE_PREFIX = "/api/workbench/market-intelligence/images"
ALLOWED_IMAGE_HOST_SUFFIXES = (".ozone.ru", ".ozon.ru")
REJECTED_IMAGE_PATH_TERMS = ("abt-challenge", "incident", "captcha", "placeholder", "warn.png")
CATEGORY_SCENARIOS = {
    "home": "для дома",
    "electronics": "для работы",
    "bathroom": "для ванной",
    "kitchen": "для кухни",
    "outdoor": "для отдыха",
    "auto": "для автомобиля",
}
CATEGORY_HINTS = {
    "bathroom": ("ванн", "душ", "смесител", "раковин", "унитаз", "сантех"),
    "kitchen": ("кухон", "посуд", "чайник", "кофе", "аэрогрил", "мультиварк", "блендер", "тостер"),
    "auto": ("автотовар", "автомототех", "автомоб", "канистр", "антифриз"),
    "electronics": ("электроник", "наушник", "смартфон", "планшет", "ноутбук"),
    "outdoor": ("спорт и отдых", "туризм", "сапборд", "бассейн", "самокат", "велосипед"),
    "home": ("дом и сад", "мебель", "хранен", "органайзер", "шкаф", "постель"),
}


class _MetaImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: List[str] = []

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "meta":
            return
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        marker = (values.get("property") or values.get("name") or "").lower()
        if marker in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
            if values.get("content"):
                self.urls.append(html.unescape(values["content"]))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _now_iso(now: Optional[datetime] = None) -> str:
    value = now or datetime.now(timezone.utc).astimezone()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="seconds")


def _clean_phrase(value: Any, max_words: int = 12) -> str:
    words = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-/][0-9A-Za-zА-Яа-яЁё]+)?", str(value or ""))
    return " ".join(words[:max_words]).strip().lower()


def _keyword_type(keyword: str) -> str:
    words = set(keyword.split())
    if words & {"купить", "заказать", "цена", "стоимость", "доставка", "наличии"}:
        return "purchase_intent"
    return classify_type(keyword)


def _local_category_key(product: Mapping[str, Any]) -> str:
    configured = str(product.get("category_key") or "")
    if configured in CATEGORY_SCENARIOS:
        return configured
    facts = dict(product.get("facts") or {})
    context = f"{configured} {facts.get('category_level_1') or ''} {facts.get('category_level_3') or ''}".lower()
    for category_key, terms in CATEGORY_HINTS.items():
        if any(term in context for term in terms):
            return category_key
    return "other"


def _candidate_phrases(product: Mapping[str, Any]) -> List[str]:
    title = _clean_phrase(product.get("title_ru"), max_words=10)
    facts = dict(product.get("facts") or {})
    category_name = _clean_phrase(facts.get("category_level_3"), max_words=6)
    title_words = title.split()
    base = category_name or " ".join(title_words[:4]) or title
    scenario = CATEGORY_SCENARIOS.get(_local_category_key(product), "")
    raw: List[str] = []

    if base:
        raw.append(base)
    if title and len(title_words) <= 6:
        raw.append(title)

    if base:
        raw.extend([
            f"купить {base}",
            f"{base} цена",
            f"заказать {base}",
            f"{base} с доставкой",
            f"{base} ozon",
            f"{base} онлайн",
            f"{base} отзывы",
            f"{base} характеристики",
            f"{base} размеры",
            f"{base} каталог",
            f"{base} в наличии",
            f"купить {base} на ozon",
        ])
        if scenario:
            raw.extend([f"{base} {scenario}", f"купить {base} {scenario}", f"{base} {scenario} цена"])

    stopwords = {"с", "и", "в", "на", "по", "из", "к", "от", "для", "а", "или"}
    base_words = {word for word in base.split() if len(word) >= 4 and word not in stopwords}
    for size in (2, 3, 4):
        for start in range(max(0, len(title_words) - size + 1)):
            window = title_words[start:start + size]
            meaningful = [word for word in window if word not in stopwords and len(word) >= 3]
            carries_product_term = bool(base_words & set(window))
            if (
                window[0] not in stopwords and window[-1] not in stopwords
                and len(meaningful) >= 2 and (carries_product_term or start == 0)
            ):
                raw.append(" ".join(window))

    candidates: List[str] = []
    seen = set()
    for value in raw:
        cleaned = _clean_phrase(value, max_words=12)
        if len(cleaned) < 3 or len(cleaned) > 90 or cleaned in seen:
            continue
        seen.add(cleaned)
        candidates.append(cleaned)
        if len(candidates) >= 30:
            break
    return candidates


def generate_local_keyword_records(
    product: Mapping[str, Any],
    observed_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build source-traceable phrases without claiming official search attribution."""
    timestamp = observed_at or _now_iso()
    source_product_id = str(product["source_product_id"])
    facts = dict(product.get("facts") or {})
    records: List[Dict[str, Any]] = []
    for priority, keyword_ru in enumerate(_candidate_phrases(product), start=1):
        digest = hashlib.sha256(f"{source_product_id}\0{keyword_ru}".encode("utf-8")).hexdigest()[:20]
        records.append({
            "keyword_key": f"local-product:{digest}",
            "keyword_ru": keyword_ru,
            "keyword_zh": TRANSLATIONS_ZH.get(keyword_ru, "unknown"),
            "keyword_type": _keyword_type(keyword_ru),
            "category_key": _local_category_key(product),
            "evidence": {
                "source": "local_product_analysis",
                "method": "title_category_phrase_v1",
                "source_product_id": source_product_id,
                "input_fields": ["title_ru", "category_level_3"],
                "translation_method": "local_curated_v1" if keyword_ru in TRANSLATIONS_ZH else "unknown",
            },
            "metrics": {"popularity": "unknown"},
            "last_seen_at": timestamp,
            "relationship": {
                "method": "local_product_text_analysis_v1",
                "priority": priority,
                "title_ru": str(product.get("title_ru") or "unknown"),
                "category_level_3": str(facts.get("category_level_3") or "unknown"),
            },
        })
    return records


def _matching_terms(value: str) -> set[str]:
    ignored = {"купить", "заказ", "достав", "стоимо", "характ", "размер", "катало", "наличи", "онлайн", "ozon"}
    terms = set()
    for word in _clean_phrase(value, max_words=30).split():
        for number in re.findall(r"\d+", word):
            terms.add(f"#{number}")
        if len(word) >= 4 and not word.isdigit() and word[:6] not in ignored:
            terms.add(word[:6])
    return terms


def _valid_public_image_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    return (
        parsed.scheme == "https"
        and any(host.endswith(suffix) for suffix in ALLOWED_IMAGE_HOST_SUFFIXES)
        and not any(term in path for term in REJECTED_IMAGE_PATH_TERMS)
    )


def extract_public_image_url(page_html: str) -> Optional[str]:
    parser = _MetaImageParser()
    try:
        parser.feed(page_html)
    except Exception:
        pass
    candidates = list(parser.urls)
    normalized = html.unescape(page_html).replace("\\/", "/")
    candidates.extend(re.findall(
        r"https://[A-Za-z0-9.-]+(?:ozone|ozon)\.ru/[^\s\"'<>\\]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\s\"'<>\\]*)?",
        normalized,
        flags=re.IGNORECASE,
    ))
    for candidate in candidates:
        value = candidate.strip()
        if _valid_public_image_url(value):
            return value
    return None


def _image_extension(data: bytes, content_type: str) -> Optional[str]:
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if data.startswith(b"\xff\xd8\xff") or media_type == "image/jpeg":
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n") or media_type == "image/png":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" or media_type == "image/webp":
        return ".webp"
    return None


PageFetcher = Callable[[str, float], str]
ImageFetcher = Callable[[str, float], Tuple[bytes, str]]


class MarketEnricher:
    def __init__(
        self,
        store: MarketStore,
        cache_dir: Path,
        timeout: float = 3.5,
        page_fetcher: Optional[PageFetcher] = None,
        image_fetcher: Optional[ImageFetcher] = None,
        now: Optional[Callable[[], datetime]] = None,
    ):
        self.store = store
        self.cache_dir = Path(cache_dir)
        self.timeout = max(0.5, float(timeout))
        self.page_fetcher = page_fetcher or self._fetch_page
        self.image_fetcher = image_fetcher or self._fetch_image
        self.now = now or (lambda: datetime.now(timezone.utc).astimezone())

    @staticmethod
    def _request(url: str, accept: str) -> urllib.request.Request:
        return urllib.request.Request(url, headers={
            "Accept": accept,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.6",
            "Cache-Control": "no-cache",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        })

    def _fetch_page(self, url: str, timeout: float) -> str:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(self._request(url, "text/html"), timeout=timeout) as response:
            data = response.read(MAX_PAGE_BYTES + 1)
        if len(data) > MAX_PAGE_BYTES:
            raise ValueError("public page is too large")
        return data.decode("utf-8", errors="replace")

    def _fetch_image(self, url: str, timeout: float) -> Tuple[bytes, str]:
        with urllib.request.urlopen(self._request(url, "image/avif,image/webp,image/png,image/jpeg"), timeout=timeout) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
            content_type = str(response.headers.get("Content-Type") or "")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("public image is too large")
        return data, content_type

    def enrich_keywords(self, source_product_id: str) -> Dict[str, Any]:
        product = self.store.get_product(source_product_id)
        if product is None:
            raise KeyError(str(source_product_id))
        timestamp = _now_iso(self.now())
        local_records = generate_local_keyword_records(product, timestamp)
        self.store.delete_product_keyword_links_by_method(
            product["product_key"], "local_product_text_analysis_v1",
        )
        for record in local_records:
            self.store.upsert_keyword(record)
            self.store.link_keyword_to_product(
                product["product_key"], record["keyword_key"], record["relationship"],
            )

        context_terms = _matching_terms(
            f"{product.get('title_ru') or ''} {(product.get('facts') or {}).get('category_level_3') or ''}"
        )
        self.store.delete_product_keyword_links_by_method(
            product["product_key"], "local_title_category_term_match_v1",
        )
        for official in self.store.list_keywords(category_key=_local_category_key(product), limit=100):
            if (official.get("evidence") or {}).get("source") != "ozon_official_search_queries":
                continue
            official_text = str(official.get("keyword_ru") or "")
            official_terms = _matching_terms(official_text)
            matched = sorted(official_terms & context_terms)
            is_accessory_query = _clean_phrase(official_text).startswith("для ")
            if official_terms and official_terms.issubset(context_terms) and not is_accessory_query:
                self.store.link_keyword_to_product(product["product_key"], official["keyword_key"], {
                    "method": "local_title_category_term_match_v1",
                    "matched_stems": matched,
                    "priority": 0,
                })
        state = self.store.update_product_enrichment(product["product_key"], {
            "keyword_state": "ready",
            "keyword_checked_at": timestamp,
        })
        return {"generated": len(local_records), "state": state["keyword_state"]}

    def _recently_checked(self, checked_at: str, hours: int = 6) -> bool:
        if checked_at in {"", "unknown"}:
            return False
        try:
            value = datetime.fromisoformat(checked_at)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        return self.now().astimezone(timezone.utc) - value.astimezone(timezone.utc) < timedelta(hours=hours)

    def enrich_image(self, source_product_id: str, force: bool = False) -> str:
        product = self.store.get_product(source_product_id)
        if product is None:
            raise KeyError(str(source_product_id))
        current = self.store.get_product_enrichment(source_product_id)
        if product.get("image_url") not in {None, "", "unknown"}:
            self.store.update_product_enrichment(product["product_key"], {"image_state": "ready", "image_error": "unknown"})
            return "ready"
        if not force and self._recently_checked(current["image_checked_at"]):
            return "syncing"

        timestamp = _now_iso(self.now())
        self.store.update_product_enrichment(product["product_key"], {
            "image_state": "fetching", "image_checked_at": timestamp, "image_error": "unknown",
        })
        try:
            page_html = self.page_fetcher(str(product["product_url"]), self.timeout)
        except (OSError, TimeoutError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
            self.store.update_product_enrichment(product["product_key"], {
                "image_state": "syncing", "image_checked_at": timestamp,
                "image_error": "public_page_unavailable",
            })
            return "syncing"
        image_source_url = extract_public_image_url(page_html)
        if not image_source_url:
            self.store.update_product_enrichment(product["product_key"], {
                "image_state": "syncing", "image_checked_at": timestamp,
                "image_error": "public_image_not_found",
            })
            return "syncing"
        try:
            image_bytes, content_type = self.image_fetcher(image_source_url, self.timeout)
            extension = _image_extension(image_bytes, content_type)
            if not extension or len(image_bytes) < 256:
                raise ValueError("invalid public image bytes")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            safe_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(source_product_id))
            destination = self.cache_dir / f"{safe_id}{extension}"
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(image_bytes)
            temporary.replace(destination)
        except (OSError, TimeoutError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
            self.store.update_product_enrichment(product["product_key"], {
                "image_state": "syncing", "image_source_url": image_source_url,
                "image_checked_at": timestamp, "image_error": "public_image_download_failed",
            })
            return "syncing"
        local_url = f"{IMAGE_ROUTE_PREFIX}/{urllib.parse.quote(str(source_product_id))}"
        self.store.set_product_image(product["product_key"], local_url)
        self.store.update_product_enrichment(product["product_key"], {
            "image_state": "ready", "image_source_url": image_source_url,
            "image_local_path": str(destination), "image_checked_at": timestamp,
            "image_error": "unknown",
        })
        return "ready"

    def enrich_product(self, source_product_id: str, try_image: bool = True) -> Dict[str, Any]:
        self.enrich_keywords(source_product_id)
        if try_image:
            self.enrich_image(source_product_id)
        product = self.store.get_product(source_product_id)
        if product is None:
            raise KeyError(str(source_product_id))
        state = self.store.get_product_enrichment(source_product_id)
        product["enrichment"] = {
            "image_state": state["image_state"],
            "image_checked_at": state["image_checked_at"],
            "keyword_state": state["keyword_state"],
            "keyword_checked_at": state["keyword_checked_at"],
        }
        return product

    def enrich_batch(self, keyword_limit: int = 500, image_limit: int = 30) -> Dict[str, Any]:
        keyword_products = (
            self.store.list_products_for_enrichment("keyword", keyword_limit)
            if keyword_limit > 0 else []
        )
        keyword_ready = 0
        for product in keyword_products:
            self.enrich_keywords(str(product["source_product_id"]))
            keyword_ready += 1

        image_products = (
            self.store.list_products_for_enrichment("image", image_limit)
            if image_limit > 0 else []
        )
        image_ready = 0
        image_syncing = 0
        for product in image_products:
            state = self.enrich_image(str(product["source_product_id"]), force=True)
            if state == "ready":
                image_ready += 1
            else:
                image_syncing += 1
        return {
            "keyword_processed": keyword_ready,
            "image_processed": len(image_products),
            "image_ready": image_ready,
            "image_syncing": image_syncing,
        }
