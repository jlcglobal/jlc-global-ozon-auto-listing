# Variant Compatibility Checker

This module first groups every selected SKU from the same 1688 source into one
product group. It then compares SKU differences with the variant fields allowed
by the selected Ozon `(category_id, type_id)` rule.

It writes `output/variant-decision.json` and
`output/variant-grouping-result.json`. Missing marketplace mappings produce
`RULE_REQUIRED` and block the later upload payload; they never split the source
product. The module itself does not call Ozon.

Run against the latest imported snapshot:

```bash
python3 variant-compatibility-checker/cli.py products/P000005
```
