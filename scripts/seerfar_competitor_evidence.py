#!/usr/bin/env python3
"""Save compact public competitor copy evidence from Seerfar keyword rows."""
from __future__ import annotations

import argparse
import html
import json
import re
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


OZON_URL_RE = re.compile(r"^https://(?:www\.)?ozon\.ru/product/\d+", re.IGNORECASE)
TAG_RE = re.compile(r"#[\w\-]+", re.UNICODE)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def clean_text(value: Any, limit: int) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit].strip()


def meta_content(html_text: str, names: Iterable[str]) -> str:
    wanted = {name.casefold() for name in names}
    for tag in re.findall(r"<meta\b[^>]*>", html_text, flags=re.IGNORECASE):
        name_match = re.search(r"(?:name|property)=[\"']([^\"']+)[\"']", tag, flags=re.IGNORECASE)
        value_match = re.search(r"content=[\"']([^\"']*)[\"']", tag, flags=re.IGNORECASE | re.DOTALL)
        if name_match and value_match and name_match.group(1).casefold() in wanted:
            return clean_text(value_match.group(1), 700)
    return ""


def extract_public_copy(source_url: str, html_text: str) -> Dict[str, Any]:
    title = meta_content(html_text, ("og:title", "twitter:title"))
    if not title:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
        title = clean_text(title_match.group(1) if title_match else "", 300)
    description = meta_content(html_text, ("og:description", "description", "twitter:description"))
    tags: List[str] = []
    for tag in TAG_RE.findall(f"{title} {description}"):
        normalized = tag.casefold()
        if normalized not in tags:
            tags.append(normalized)
    return {"source_url": source_url, "title": title, "description": description, "theme_tags": tags[:30]}


def top_competitor_urls(rows: Iterable[Dict[str, Any]], limit: int) -> List[str]:
    urls: List[str] = []
    ranked_rows = sorted((row for row in rows if isinstance(row, dict)), key=lambda row: float(row.get("count") or 0), reverse=True)
    for row in ranked_rows:
        for value in row.get("related_product_urls") or []:
            url = str(value or "").strip()
            if OZON_URL_RE.match(url) and url not in urls:
                urls.append(url)
                if len(urls) >= limit:
                    return urls
    return urls


def download_public_page(url: str) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    })
    with urllib.request.urlopen(request, timeout=12) as response:
        raw = response.read(2 * 1024 * 1024)
        content_type = str(response.headers.get("content-type") or "")
    charset_match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, flags=re.IGNORECASE)
    return raw.decode(charset_match.group(1) if charset_match else "utf-8", errors="replace")


def build_evidence(product_dir: Path, *, limit: int = 3) -> Dict[str, Any]:
    research = load_json(product_dir / "output" / "seerfar-keyword-research.json", {})
    output_path = product_dir / "output" / "seerfar-competitor-evidence.json"
    rows = research.get("rows") if isinstance(research.get("rows"), list) else []
    urls = top_competitor_urls(rows, max(1, min(int(limit), 5)))
    result: Dict[str, Any] = {
        "schema_version": "1.0.0", "product_id": product_dir.name, "status": "unavailable",
        "source": "seerfar_visible_keyword_links_plus_public_ozon_pages",
        "source_notice": "只读取 Seerfar 可见结果中的公开 Ozon 链接；竞品标题、标签和简介仅作关键词覆盖和文案结构参考，禁止照搬。",
        "competitors": [], "warnings": [], "updated_at": now_iso(),
    }
    if research.get("status") != "completed":
        result["reason"] = "Seerfar 关键词结果尚不可用"
    elif not urls:
        result["reason"] = "Seerfar 关键词结果没有可读取的公开竞品链接"
    else:
        for url in urls:
            try:
                item = extract_public_copy(url, download_public_page(url))
            except Exception as exc:
                result["warnings"].append(f"{url}: {type(exc).__name__}")
                continue
            if item["title"] or item["description"] or item["theme_tags"]:
                result["competitors"].append(item)
        if result["competitors"]:
            result["status"] = "completed"
        else:
            result["reason"] = "公开竞品页面暂时未返回可用标题、标签或简介"
    write_json_atomic(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product_dir", type=Path)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    result = build_evidence(args.product_dir, limit=args.limit)
    print(json.dumps({"product_id": args.product_dir.name, "status": result["status"], "competitor_count": len(result["competitors"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
