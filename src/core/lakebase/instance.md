# Lakebase resources

The POC uses Lakebase Autoscaling (Databricks CLI `postgres` group):

- Project: `projects/ontology-poc`
- Branch: `projects/ontology-poc/branches/production`
- Endpoint: `projects/ontology-poc/branches/production/endpoints/primary`
- Database resource: `projects/ontology-poc/branches/production/databases/ontology-poc`
- PostgreSQL database: `ontology_poc`
- Capacity: 1 CU min/max (the smallest project default)

Provisioning commands:

```bash
databricks postgres create-project ontology-poc \
  --json '{"spec":{"display_name":"Ontology Data Layer POC"}}' \
  --profile fevm-serverless

databricks postgres create-database projects/ontology-poc/branches/production \
  --database-id ontology-poc \
  --json '{
    "name":"projects/ontology-poc/branches/production/databases/ontology-poc",
    "spec":{
      "postgres_database":"ontology_poc",
      "role":"projects/ontology-poc/branches/production/roles/ryan-zheng"
    }
  }' \
  --profile fevm-serverless
```

All direct PostgreSQL connections use short-lived OAuth credentials from
`databricks postgres generate-database-credential` and `sslmode=require`.
