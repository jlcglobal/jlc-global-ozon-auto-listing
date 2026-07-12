# Pricing Engine

The pricing engine runs before Ozon draft generation and creates:

- `output/cost-analysis.json`
- `output/pricing-result.json`
- `output/profit-analysis.json`

It reads only the `RETS` worksheet from `pricing-engine/shipping_rules.xlsx`. Other workbook sheets are ignored.

Price calculation uses the lowest-cost eligible RETS route after checking billable package weight, item value and package size limits. Product and package measurements are stored separately. Missing measurements may be estimated, but every package side and gross weight must be strictly greater than the corresponding product measurement. Estimated measurements are marked as estimated, include confidence, never overwrite `input/source.json`, and never become product claims.

The engine performs no network requests and does not call Ozon Seller API or any AI API.
