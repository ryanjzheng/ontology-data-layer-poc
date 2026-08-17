# Ontology data-layer POC

Palantir-style object storage on Databricks:

```text
UC employee_base ──continuous sync──> Lakebase object_layer.employee_sync
                                           ⊕ public.employee_write
                                           └─> public.employee_overlay
public.employee_write ──2-minute bridge──> UC employee_app_changes
employee_base ⊕ employee_app_changes ────> UC employee_gold
```

The app/action identity can write only `public.employee_write`; it has no
privilege on `employee_base`. Edits are sparse, app-created IDs use the
`app-` namespace, reverts set an override to `NULL`, and deletes use
tombstones.

## Deploy

```bash
databricks bundle deploy -t dev --profile fevm-serverless
databricks bundle run setup_ontology -t dev --profile fevm-serverless
databricks bundle run etl_simulator -t dev --profile fevm-serverless
uv run python src/lakebase/setup.py --profile fevm-serverless
```

## Action examples

```bash
uv run python -m src.action.apply_action list --limit 5
uv run python -m src.action.apply_action edit emp-00001 \
  --salary 250000 --editor ryan.zheng@databricks.com
uv run python -m src.action.apply_action revert emp-00001 salary \
  --editor ryan.zheng@databricks.com
```

Run local checks with `uv run ruff check . && uv run pytest`.
