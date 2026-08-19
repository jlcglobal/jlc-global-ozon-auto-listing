---
name: ozon-keyword-growth-radar
description: Discover fast-rising Ozon Russia keywords from Seefar keyword mining, separate "growth" (is search demand rising) from "opportunity" (is the rise repeatable and enterable), apply the keyword growth framework thresholds, and emit a decision-dashboard Excel weekly report with keyword rankings, risk exclusions and normalized data. Use when the operator wants keyword momentum, a sourcing shortlist, or a weekly keyword opportunity report.
---

# Ozon Keyword Growth Radar

Weekly keyword scan for Ozon Russia. The deliverable is a readable Excel
workbook, never a bare CSV: one-page decision dashboard, keyword growth and
opportunity rankings, a risk-exclusion table, and the full normalized data.

Data comes from Seefar **keyword mining** (the page your workbench already
integrates via `seerfar-content.ts` / `seerfar-browser-worker.mjs`). There is no
Seefar category API — do not assume `/product-report/category/search/OZON`.

## Workflow

1. **Confirm scope.** Default market is Ozon Russia. Record the export window
   (period) and comparison basis. One ranking per keyword set — never mix
   keyword rows of different source windows.

2. **Fetch keyword data.** Prefer the Seefar keyword-miner export CSV. The
   fetcher maps Seefar columns to a fixed standard schema. When a column is
   missing or hidden, ask the operator to export the table — never invent the
   values. No login/password API is used.

3. **Normalize fields.** Every row maps to the standard schema. Key fields:
   `keyword`, `monthly_search_heat` (absolute volume), `monthly_growth_percent`
   (rate), `market_space`, `conversion_concentration_percent`,
   `competitor_count`, `competitor_seller_count`, `ad_competitor_count`,
   `product_count`, `cart_conversion_percent`, `return_cancel_rate_percent`,
   `average_price_rub`.

4. **Rank.** Run `scripts/rank_keyword_growth.py`. Growth and opportunity are
   two different answers:
   - **Growth** = "is search demand rising?" (monthly search heat absolute
     volume AND monthly growth rate together — never rate alone).
   - **Opportunity** = "is the rise repeatable and enterable?" (market space,
     concentration, competition, return/cancel risk).

5. **Score against `references/keyword-growth-framework.md`.** Market
   conclusions use only Seefar keyword-level market data.

6. **Report.** `scripts/build_readable_weekly_report.mjs` turns the ranked CSV
   into `Ozon关键词增长机会周报.xlsx`. State evidence, confidence, false-growth
   risk and the next verification action for every top keyword.

## Hard rules

- Never fabricate exact values behind a login or a missing column. When data is
  unavailable, ask the operator to export the table.
- Absolute volume and growth rate must both be present; never rank by rate
  alone.
- `conversion_concentration_percent` is conversion concentration (how strongly
  sales concentrate on few products), NOT a top-5 seller share.
- High conversion concentration → caution; low concentration with healthy
  signals → prefer.
- High return/cancel rate is an operational-risk signal, not a profit score.
- Keyword stage gets no profit scoring — only target price and cost
  constraints.

## Output shape

- **结论** — top keyword directions, not a flat list.
- **增长榜** — current heat / growth rate / absolute volume / competition /
  confidence.
- **机会榜** — demand momentum / competition / concentration / stability /
  operational risk / total score.
- **排除项** — low-base spikes, monopoly, supply congestion, high return/cancel.
- **下一步** — products to drill into; data-gap list.

## Files

- `SKILL.md`
- `references/keyword-growth-framework.md`
- `scripts/fetch_seefar_keyword_growth.py`
- `scripts/rank_keyword_growth.py`
- `scripts/build_readable_weekly_report.mjs`

## Usage

```powershell
python <skill-dir>\scripts\fetch_seefar_keyword_growth.py --input-csv <seefar导出>.csv --min-growth 10 --output-dir outputs\ozon-keyword-growth
node <skill-dir>\scripts\build_readable_weekly_report.mjs <output>\keyword_growth_rank.csv <output>\Ozon关键词增长机会周报.xlsx <output>\preview
```
