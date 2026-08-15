# Ozon Field Completion

Generates traceable Ozon card fields without calling any Ozon write endpoint.

```bash
python3 ozon-field-completion/cli.py P000004
```

Outputs are written to the product `output/` directory. A missing color-variant image is preserved as `missing` and blocks the final upload check; it is never replaced by a random or generic image.
