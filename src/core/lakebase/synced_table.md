# Continuous employee sync

The synced table uses the existing managed UC catalog as requested:

- UC online table: `serverless_stable_vfpvf8_catalog.object_layer.employee_sync`
- Source: `serverless_stable_vfpvf8_catalog.object_layer.employee_base`
- Lakebase target: `ontology_poc.object_layer.employee_sync`
- Mode: `CONTINUOUS`
- Primary key: `employee_id`
- Pipeline ID: `37065b54-49ee-4955-8a08-cb326086ba8b`

No additional Unity Catalog catalog is created. Because a Lakebase Autoscaling
synced-table ID maps its UC schema to the PostgreSQL schema, the target is in
Lakebase schema `object_layer` rather than `public`.

```bash
databricks postgres create-synced-table \
  serverless_stable_vfpvf8_catalog.object_layer.employee_sync \
  --json '{
    "spec": {
      "branch": "projects/ontology-poc/branches/production",
      "postgres_database": "ontology_poc",
      "source_table_full_name":
        "serverless_stable_vfpvf8_catalog.object_layer.employee_base",
      "primary_key_columns": ["employee_id"],
      "scheduling_policy": "CONTINUOUS",
      "new_pipeline_spec": {
        "storage_catalog": "serverless_stable_vfpvf8_catalog",
        "storage_schema": "object_layer"
      },
      "create_database_objects_if_missing": true
    }
  }' \
  --profile fevm-serverless
```
