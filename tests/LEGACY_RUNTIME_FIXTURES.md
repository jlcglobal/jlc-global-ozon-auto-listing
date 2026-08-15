# Legacy runtime fixture tests

The default test discovery command is the active, self-contained suite. Tests
that read mutable historical `products/P00000x` artifacts are isolated because
those directories are recovery/audit data, not stable fixtures.

Run the isolated compatibility group explicitly with:

```bash
CAF_RUN_LEGACY_FIXTURES=1 ./.venv/bin/python -m unittest discover -s tests -p 'test*.py'
```

The legacy group is diagnostic only. New production gates must be covered by a
self-contained temporary product fixture in the default suite.
