# Object Storage Lab

An AppKit 0.38.1 Databricks App for demonstrating the ontology POC's
source/edit separation:

- Reads the immediate effective object view from `public.employee_overlay`.
- Shows source values beside sparse app overrides.
- Writes create/edit/revert/delete actions only to `public.employee_write`.
- Never writes `object_layer.employee_sync` or UC `employee_base`.

## Architecture

```text
React + AppKit UI
       ↓ /api/employees
AppKit server() + lakebase()
       ↓
object_layer.employee_sync ⊕ public.employee_write
       ↓
public.employee_overlay
```

The Databricks App resource uses:

- Project: `projects/ontology-poc`
- Branch: `projects/ontology-poc/branches/production`
- Database: `projects/ontology-poc/branches/production/databases/ontology-poc`

## Validate

```bash
npm install
npm run typecheck
npm run lint
npm run lint:ast-grep
npm run build
databricks apps validate --profile fevm-serverless
```

## Deploy

Deployment creates a dedicated Databricks App service principal. After the
first deployment, grant that principal:

- `SELECT` on `object_layer.employee_sync`
- `SELECT` on `public.employee_overlay`
- `SELECT, INSERT, UPDATE, DELETE` on `public.employee_write`

The app intentionally has no access to UC `employee_base`.
