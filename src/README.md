# `src/` layout — core pattern vs. demo scaffolding

This tree is split so the **reusable object-storage pattern** is cleanly
separable from **demo-only code**. When porting to a new workspace or a new
object type, study/adapt everything under `core/` and treat `demo/` as
throwaway.

## `core/` — the reusable storage-layer pattern

The Palantir-style source/edit separation (UC BASE ⊕ APP_CHANGES, reconciled at
read time; Lakebase as the hot OLTP overlay). Files are named for the `employee`
demo entity, but they are the canonical instantiation of the pattern — swap the
entity/columns to reuse.

| Path | Role |
|---|---|
| `core/setup_ontology.py` | UC side: creates `employee_base`, `employee_app_changes`, and the `employee_gold` reconciliation view (BASE ⊕ APP_CHANGES, edit-wins, tombstone-aware). |
| `core/apply_bridge.py` | Bridge: reads Lakebase `employee_write`, MERGEs into UC `employee_app_changes` (never touches BASE). |
| `core/ddl/*.sql` | Canonical UC DDL reference (BASE, APP_CHANGES, GOLD view, grants). |
| `core/lakebase/setup.py` | Creates the Lakebase write store + overlay view. **Reads its sibling `.sql` files by directory** — keep them co-located. |
| `core/lakebase/employee_write.sql` | Postgres write store (the sparse edit layer). |
| `core/lakebase/employee_overlay.sql` | Read-time overlay view (`employee_sync` ⊕ `employee_write`, edit-wins). |
| `core/lakebase/grants.sql` | App service-principal grants (write-only to `employee_write`; no BASE access). |
| `core/lakebase/instance.md`, `synced_table.md` | Runbooks for the Lakebase instance + continuous Synced Table (partly outside DABs). |

## `demo/` — demo-only synthetic upstream

Exists purely to give the demo data to react to. In a real deployment the
customer's own upstream ETL fills `employee_base`; replace these entirely.

| Path | Role |
|---|---|
| `demo/etl_simulator.py` | Generates N synthetic employees into `employee_base`; re-runnable to mutate salaries (demonstrates refresh resilience). |
| `demo/etl_trickle.py` | Appends synthetic employees in small live batches to demo continuous sync. |

## Wiring (update together if you move files)

- `resources/*.job.yml` — `notebook_path` points at `../src/core/*.py` and `../src/demo/*.py`.
- `pyproject.toml` — ruff `F821` ignores list the notebook paths (injected `dbutils`/`spark`).
- `tests/test_contract.py` — asserts the pattern's SQL/notebook contracts by path.
