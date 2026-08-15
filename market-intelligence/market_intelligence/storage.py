"""SQLite persistence for market facts, snapshots, keywords, and source state."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional

from .scoring import calculate_index


SCHEMA_VERSION = "1.1.0"


class MarketStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_status (
                    source_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    access_level TEXT NOT NULL,
                    message_zh TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS products (
                    product_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_product_id TEXT NOT NULL,
                    category_key TEXT NOT NULL,
                    title_ru TEXT NOT NULL,
                    title_zh TEXT NOT NULL,
                    product_url TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    facts_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS product_snapshots (
                    product_key TEXT NOT NULL,
                    observed_on TEXT NOT NULL,
                    facts_json TEXT NOT NULL,
                    PRIMARY KEY (product_key, observed_on),
                    FOREIGN KEY (product_key) REFERENCES products(product_key)
                );
                CREATE TABLE IF NOT EXISTS keywords (
                    keyword_key TEXT PRIMARY KEY,
                    keyword_ru TEXT NOT NULL,
                    keyword_zh TEXT NOT NULL,
                    keyword_type TEXT NOT NULL,
                    category_key TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS product_keywords (
                    product_key TEXT NOT NULL,
                    keyword_key TEXT NOT NULL,
                    relationship_json TEXT NOT NULL,
                    PRIMARY KEY (product_key, keyword_key),
                    FOREIGN KEY (product_key) REFERENCES products(product_key),
                    FOREIGN KEY (keyword_key) REFERENCES keywords(keyword_key)
                );
                CREATE TABLE IF NOT EXISTS product_enrichment (
                    product_key TEXT PRIMARY KEY,
                    image_state TEXT NOT NULL,
                    image_source_url TEXT NOT NULL,
                    image_local_path TEXT NOT NULL,
                    image_checked_at TEXT NOT NULL,
                    image_error TEXT NOT NULL,
                    keyword_state TEXT NOT NULL,
                    keyword_checked_at TEXT NOT NULL,
                    FOREIGN KEY (product_key) REFERENCES products(product_key)
                );
                CREATE TABLE IF NOT EXISTS favorites (
                    operator_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (operator_id, target_type, target_key)
                );
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (SCHEMA_VERSION,),
            )

    def upsert_source_status(self, record: Mapping[str, Any]) -> None:
        required = {"source_id", "state", "access_level", "message_zh", "checked_at"}
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(f"Missing source status fields: {', '.join(missing)}")
        details = dict(record.get("details") or {})
        forbidden = {key for key in details if "key" in key.lower() or "secret" in key.lower() or "token" in key.lower()}
        if forbidden:
            raise ValueError("Source status details must not contain credentials or tokens")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_status(source_id, state, access_level, message_zh, checked_at, details_json)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    state=excluded.state,
                    access_level=excluded.access_level,
                    message_zh=excluded.message_zh,
                    checked_at=excluded.checked_at,
                    details_json=excluded.details_json
                """,
                (
                    str(record["source_id"]),
                    str(record["state"]),
                    str(record["access_level"]),
                    str(record["message_zh"]),
                    str(record["checked_at"]),
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                ),
            )

    def list_source_status(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT source_id, state, access_level, message_zh, checked_at, details_json "
                "FROM source_status ORDER BY source_id"
            ).fetchall()
        return [
            {
                "source_id": row["source_id"],
                "state": row["state"],
                "access_level": row["access_level"],
                "message_zh": row["message_zh"],
                "checked_at": row["checked_at"],
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    def upsert_product_snapshot(self, record: Mapping[str, Any], observed_on: str) -> None:
        required = {
            "product_key", "source", "source_product_id", "category_key",
            "title_ru", "title_zh", "product_url", "image_url", "facts",
            "observed_at",
        }
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(f"Missing market product fields: {', '.join(missing)}")
        facts_json = json.dumps(dict(record["facts"]), ensure_ascii=False, sort_keys=True)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO products(
                    product_key, source, source_product_id, category_key, title_ru,
                    title_zh, product_url, image_url, facts_json, first_seen_at, last_seen_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_key) DO UPDATE SET
                    source=excluded.source,
                    source_product_id=excluded.source_product_id,
                    category_key=excluded.category_key,
                    title_ru=excluded.title_ru,
                    title_zh=excluded.title_zh,
                    product_url=excluded.product_url,
                    image_url=CASE
                        WHEN excluded.image_url = 'unknown' THEN products.image_url
                        ELSE excluded.image_url
                    END,
                    facts_json=excluded.facts_json,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    str(record["product_key"]), str(record["source"]),
                    str(record["source_product_id"]), str(record["category_key"]),
                    str(record["title_ru"]), str(record["title_zh"]),
                    str(record["product_url"]), str(record["image_url"]),
                    facts_json, str(record["observed_at"]), str(record["observed_at"]),
                ),
            )
            connection.execute(
                """
                INSERT INTO product_snapshots(product_key, observed_on, facts_json)
                VALUES(?, ?, ?)
                ON CONFLICT(product_key, observed_on) DO UPDATE SET facts_json=excluded.facts_json
                """,
                (str(record["product_key"]), observed_on, facts_json),
            )

    def counts(self) -> Dict[str, int]:
        with self.connect() as connection:
            return {
                "products": int(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]),
                "snapshots": int(connection.execute("SELECT COUNT(*) FROM product_snapshots").fetchone()[0]),
                "keywords": int(connection.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]),
                "favorites": int(connection.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]),
            }

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if isinstance(value, bool) or value in {None, "", "unknown"}:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _fbs_assessment(cls, title_ru: str, facts: Mapping[str, Any]) -> Dict[str, Any]:
        volume = cls._number(facts.get("product_volume_liters"))
        growth = cls._number(facts.get("ordered_amount_growth_percent"))
        fbs_cost = cls._number(facts.get("ozon_cost_share_fbs_percent"))
        buyout = cls._number(facts.get("buyout_share_percent"))
        search_to_cart = cls._number(facts.get("search_to_cart_percent"))

        components = {
            "package_fit": None if volume is None else max(0.0, min(100.0, 100.0 - volume * 3.2)),
            "demand_trend": None if growth is None else max(0.0, min(100.0, 50.0 + growth / 4.0)),
            "cost_efficiency": None if fbs_cost is None else max(0.0, min(100.0, 100.0 - fbs_cost * 2.0)),
            "buyout": None if buyout is None else max(0.0, min(100.0, buyout)),
            "search_conversion": None if search_to_cart is None else max(0.0, min(100.0, search_to_cart * 4.0)),
        }
        index = calculate_index(
            components,
            {"package_fit": 30, "demand_trend": 25, "cost_efficiency": 20, "buyout": 15, "search_conversion": 10},
        )
        text = f"{title_ru} {facts.get('category_level_3') or ''}".lower()
        risk_terms = {
            "liquid_or_oil": ("масло", "жидк", "шампун", "антифриз", "очистител", "полирол", "краска", "лак "),
            "battery_or_power": ("аккумулятор", "батаре", "power bank"),
            "large_volume": (),
        }
        flags = []
        for code, terms in risk_terms.items():
            if terms and any(term in text for term in terms):
                flags.append(code)
        if volume is not None and volume > 25:
            flags.append("large_volume")
        score = index["score"]
        if flags:
            recommendation = "review_required"
        elif score == "unknown":
            recommendation = "insufficient_data"
        elif float(score) >= 65:
            recommendation = "recommended"
        else:
            recommendation = "caution"
        return {
            **index,
            "recommendation": recommendation,
            "risk_flags": flags,
            "method": "local_rule_v1",
            "notice": "基于官方榜单字段的本地规则判断，危险品、品牌授权和实际包装仍需人工复核",
        }

    @classmethod
    def _product_payload(cls, row: sqlite3.Row) -> Dict[str, Any]:
        facts = json.loads(row["facts_json"])
        return {
            "product_key": row["product_key"],
            "source": row["source"],
            "source_product_id": row["source_product_id"],
            "category_key": row["category_key"],
            "title_ru": row["title_ru"],
            "title_zh": row["title_zh"],
            "product_url": row["product_url"],
            "image_url": row["image_url"],
            "facts": facts,
            "fbs_assessment": cls._fbs_assessment(row["title_ru"], facts),
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
        }

    @staticmethod
    def _category_clause(category_rule: Mapping[str, Any], parameters: List[Any]) -> str:
        clauses = []
        level_1 = [str(item) for item in category_rule.get("source_level_1") or []]
        if level_1:
            placeholders = ",".join("?" for _ in level_1)
            clauses.append(f"json_extract(facts_json, '$.category_level_1') IN ({placeholders})")
            parameters.extend(level_1)
        level_3_terms = [str(item).lower() for item in category_rule.get("source_level_3_contains") or []]
        if level_3_terms:
            clauses.append("(" + " OR ".join(
                "(json_extract(facts_json, '$.category_level_3') LIKE ? OR json_extract(facts_json, '$.category_level_3') LIKE ?)"
                for _ in level_3_terms
            ) + ")")
            for term in level_3_terms:
                parameters.extend((f"%{term}%", f"%{term[:1].upper()}{term[1:]}%"))
        include_clause = "(" + " OR ".join(clauses) + ")" if clauses else "1=1"
        restrictions = [str(item) for item in category_rule.get("source_level_1_restrict") or []]
        if not restrictions:
            return include_clause
        placeholders = ",".join("?" for _ in restrictions)
        parameters.extend(restrictions)
        return f"({include_clause}) AND json_extract(facts_json, '$.category_level_1') IN ({placeholders})"

    def list_ranked_products(
        self,
        category_rule: Mapping[str, Any],
        ranking: str = "hot",
        page: int = 1,
        page_size: int = 24,
        query: str = "",
    ) -> Dict[str, Any]:
        if ranking not in {"hot", "rising"}:
            raise ValueError("ranking must be 'hot' or 'rising'")
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        parameters: List[Any] = []
        where = [self._category_clause(category_rule, parameters)]
        clean_query = str(query or "").strip().lower()
        if clean_query:
            where.append("(lower(title_ru) LIKE ? OR source_product_id = ? OR lower(json_extract(facts_json, '$.category_level_3')) LIKE ?)")
            parameters.extend([f"%{clean_query}%", clean_query, f"%{clean_query}%"])
        if ranking == "rising":
            where.append("json_type(facts_json, '$.ordered_amount_growth_percent') IN ('integer', 'real')")
            order_by = "CAST(json_extract(facts_json, '$.ordered_amount_growth_percent') AS REAL) DESC, CAST(json_extract(facts_json, '$.ordered_units') AS REAL) DESC"
        else:
            order_by = "CAST(json_extract(facts_json, '$.ordered_amount_rub') AS REAL) DESC, CAST(json_extract(facts_json, '$.ordered_units') AS REAL) DESC"
        where_sql = " AND ".join(where)
        with self.connect() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) FROM products WHERE {where_sql}", parameters).fetchone()[0])
            rows = connection.execute(
                f"SELECT * FROM products WHERE {where_sql} ORDER BY {order_by} LIMIT ? OFFSET ?",
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()
        items = []
        for index, row in enumerate(rows, start=(page - 1) * page_size + 1):
            item = self._product_payload(row)
            item["ranking_position"] = index
            item["heat_index"] = round(100.0 - ((index - 1) / max(total - 1, 1)) * 50.0, 1)
            items.append(item)
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "ranking": ranking,
            "category_key": category_rule.get("key", "all"),
        }

    def get_product(self, source_product_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM products WHERE source_product_id = ? LIMIT 1",
                (str(source_product_id),),
            ).fetchone()
            if row is None:
                return None
            keyword_rows = connection.execute(
                """
                SELECT k.keyword_key, k.keyword_ru, k.keyword_zh, k.keyword_type,
                       k.evidence_json, k.metrics_json, pk.relationship_json
                FROM product_keywords pk JOIN keywords k ON k.keyword_key = pk.keyword_key
                WHERE pk.product_key = ?
                ORDER BY
                    CASE WHEN json_extract(k.evidence_json, '$.source') = 'ozon_official_search_queries' THEN 0 ELSE 1 END,
                    COALESCE(CAST(json_extract(k.metrics_json, '$.popularity') AS REAL), 0) DESC,
                    COALESCE(CAST(json_extract(pk.relationship_json, '$.priority') AS REAL), 999),
                    k.keyword_ru
                """,
                (row["product_key"],),
            ).fetchall()
        payload = self._product_payload(row)
        payload["keywords"] = [
            {
                "keyword_key": item["keyword_key"],
                "keyword_ru": item["keyword_ru"],
                "keyword_zh": item["keyword_zh"],
                "keyword_type": item["keyword_type"],
                "evidence": json.loads(item["evidence_json"]),
                "metrics": json.loads(item["metrics_json"]),
                "relationship": json.loads(item["relationship_json"]),
            }
            for item in keyword_rows
        ]
        return payload

    def upsert_keyword(self, record: Mapping[str, Any]) -> None:
        required = {"keyword_key", "keyword_ru", "keyword_zh", "keyword_type", "category_key", "evidence", "metrics", "last_seen_at"}
        missing = sorted(required - set(record))
        if missing:
            raise ValueError(f"Missing keyword fields: {', '.join(missing)}")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO keywords(keyword_key, keyword_ru, keyword_zh, keyword_type, category_key, evidence_json, metrics_json, last_seen_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword_key) DO UPDATE SET
                    keyword_ru=excluded.keyword_ru,
                    keyword_zh=excluded.keyword_zh,
                    keyword_type=excluded.keyword_type,
                    category_key=excluded.category_key,
                    evidence_json=excluded.evidence_json,
                    metrics_json=excluded.metrics_json,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    str(record["keyword_key"]), str(record["keyword_ru"]), str(record["keyword_zh"]),
                    str(record["keyword_type"]), str(record["category_key"]),
                    json.dumps(dict(record["evidence"]), ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(record["metrics"]), ensure_ascii=False, sort_keys=True),
                    str(record["last_seen_at"]),
                ),
            )

    def link_keyword_to_matching_products(self, keyword_key: str, keyword_ru: str) -> int:
        tokens = [token for token in str(keyword_ru).lower().split() if len(token) > 1]
        if not tokens:
            return 0
        with self.connect() as connection:
            products = connection.execute("SELECT product_key, title_ru FROM products").fetchall()
            matches = [row["product_key"] for row in products if all(token in row["title_ru"].lower() for token in tokens)]
            connection.executemany(
                """
                INSERT INTO product_keywords(product_key, keyword_key, relationship_json)
                VALUES(?, ?, ?)
                ON CONFLICT(product_key, keyword_key) DO UPDATE SET relationship_json=excluded.relationship_json
                """,
                [(product_key, str(keyword_key), json.dumps({"method": "title_token_match_v1", "matched_tokens": tokens}, ensure_ascii=False)) for product_key in matches],
            )
        return len(matches)

    def link_keyword_to_product(
        self,
        product_key: str,
        keyword_key: str,
        relationship: Mapping[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO product_keywords(product_key, keyword_key, relationship_json)
                VALUES(?, ?, ?)
                ON CONFLICT(product_key, keyword_key) DO UPDATE SET
                    relationship_json=excluded.relationship_json
                """,
                (
                    str(product_key),
                    str(keyword_key),
                    json.dumps(dict(relationship), ensure_ascii=False, sort_keys=True),
                ),
            )

    def delete_product_keyword_links_by_method(self, product_key: str, method: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM product_keywords
                WHERE product_key = ? AND json_extract(relationship_json, '$.method') = ?
                """,
                (str(product_key), str(method)),
            )
        return int(cursor.rowcount)

    def update_product_enrichment(self, product_key: str, patch: Mapping[str, Any]) -> Dict[str, Any]:
        defaults = {
            "image_state": "pending",
            "image_source_url": "unknown",
            "image_local_path": "unknown",
            "image_checked_at": "unknown",
            "image_error": "unknown",
            "keyword_state": "pending",
            "keyword_checked_at": "unknown",
        }
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM product_enrichment WHERE product_key = ?",
                (str(product_key),),
            ).fetchone()
            if row is not None:
                defaults.update({key: row[key] for key in defaults})
            defaults.update({key: str(value) for key, value in patch.items() if key in defaults})
            connection.execute(
                """
                INSERT INTO product_enrichment(
                    product_key, image_state, image_source_url, image_local_path,
                    image_checked_at, image_error, keyword_state, keyword_checked_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_key) DO UPDATE SET
                    image_state=excluded.image_state,
                    image_source_url=excluded.image_source_url,
                    image_local_path=excluded.image_local_path,
                    image_checked_at=excluded.image_checked_at,
                    image_error=excluded.image_error,
                    keyword_state=excluded.keyword_state,
                    keyword_checked_at=excluded.keyword_checked_at
                """,
                (
                    str(product_key), defaults["image_state"], defaults["image_source_url"],
                    defaults["image_local_path"], defaults["image_checked_at"],
                    defaults["image_error"], defaults["keyword_state"],
                    defaults["keyword_checked_at"],
                ),
            )
        return {"product_key": str(product_key), **defaults}

    def get_product_enrichment(self, source_product_id: str) -> Dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT p.product_key, e.image_state, e.image_source_url,
                       e.image_local_path, e.image_checked_at, e.image_error,
                       e.keyword_state, e.keyword_checked_at
                FROM products p LEFT JOIN product_enrichment e ON e.product_key = p.product_key
                WHERE p.source_product_id = ? LIMIT 1
                """,
                (str(source_product_id),),
            ).fetchone()
        if row is None:
            raise KeyError(str(source_product_id))
        return {
            "product_key": row["product_key"],
            "image_state": row["image_state"] or "pending",
            "image_source_url": row["image_source_url"] or "unknown",
            "image_local_path": row["image_local_path"] or "unknown",
            "image_checked_at": row["image_checked_at"] or "unknown",
            "image_error": row["image_error"] or "unknown",
            "keyword_state": row["keyword_state"] or "pending",
            "keyword_checked_at": row["keyword_checked_at"] or "unknown",
        }

    def set_product_image(self, product_key: str, image_url: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE products SET image_url = ? WHERE product_key = ?",
                (str(image_url), str(product_key)),
            )

    def list_products_for_enrichment(self, target: str, limit: int) -> List[Dict[str, Any]]:
        if target not in {"image", "keyword"}:
            raise ValueError("target must be 'image' or 'keyword'")
        limit = max(1, min(2000, int(limit)))
        if target == "keyword":
            where = "COALESCE(e.keyword_state, 'pending') != 'ready'"
        else:
            where = "p.image_url = 'unknown' AND COALESCE(e.image_state, 'pending') != 'ready'"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT p.* FROM products p
                LEFT JOIN product_enrichment e ON e.product_key = p.product_key
                WHERE {where}
                ORDER BY p.last_seen_at DESC, p.product_key
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._product_payload(row) for row in rows]

    def list_keywords(self, category_key: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        parameters: List[Any] = []
        where = ""
        if category_key:
            where = "WHERE category_key = ?"
            parameters.append(category_key)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM keywords {where} ORDER BY CAST(json_extract(metrics_json, '$.popularity') AS REAL) DESC LIMIT ?",
                [*parameters, limit],
            ).fetchall()
        return [{
            "keyword_key": row["keyword_key"], "keyword_ru": row["keyword_ru"], "keyword_zh": row["keyword_zh"],
            "keyword_type": row["keyword_type"], "category_key": row["category_key"],
            "evidence": json.loads(row["evidence_json"]), "metrics": json.loads(row["metrics_json"]),
            "last_seen_at": row["last_seen_at"],
        } for row in rows]
