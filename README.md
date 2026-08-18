# Ontology data-layer POC

Palantir-style object storage on Databricks:

```text
UC employee_base ──continuous sync──> Lakebase object_layer.employee_sync
                                           ⊕ public.employee_write
                                           └─> public.employee_overlay
public.employee_write ──2-minute bridge──> UC employee_app_changes
employee_base ⊕ employee_app_changes ────> UC employee_gold
```

The Databricks App identity can write only `public.employee_write`; it has no
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

## Action surface

Create, edit, revert, and delete objects only through the deployed
[Object Storage Lab](https://ontology-object-demo-7474658463664047.aws.databricksapps.com).
Its AppKit routes live in
`app/ontology-object-demo/server/routes/lakebase/employee-routes.ts`.

Run local checks with `uv run ruff check . && uv run pytest`.
