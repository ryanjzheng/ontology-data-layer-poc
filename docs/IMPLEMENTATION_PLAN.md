# Implementation Plan — Ontology Data Layer POC

**Audience:** an implementing agent with this repo and no prior chat context. This plan is **immediately implementable** — decisions are locked, names are chosen, DDL is given. Where a step is CLI/skill-driven, the skill to load is named.

**Read first (in this repo):**
- `CONTEXT.md` — architecture source of truth (§3 flow, §5 demo, §6 phases, §11 V2 mapping).
- `PALANTIR_FOUNDRY_ONTOLOGY_ARCHITECTURE.md` — the behaviors we replicate.

**Target:** workspace `fevm-serverless` → `https://fevm-serverless-stable-vfpvf8.cloud.databricks.com`. **Always pass `--profile fevm-serverless`. Never auto-select a profile.**

**How to work:** route everything through the AI Dev Kit skills. Load `databricks-core` first, then the per-phase skill named below. Package with DABs (`databricks-dabs`).

---

## 0. Workspace ground truth (verified read-only, 2026-08-17)

- Caller is **workspace admin** (`ryan.zheng@databricks.com`); profile valid.
- **Lakebase = Public Preview, zero instances exist** → provision one (this is the long pole; start it first).
- Can **create a new UC catalog + schema** (admin rights).
- One **serverless SQL warehouse** ("Serverless Starter Warehouse", Small, stopped → auto-starts).
- **No pipelines exist** → Lakeflow Declarative Pipelines namespace is free.

---

## 1. Locked decisions (do NOT re-litigate — rationale + rejected alternative given)

| Decision | Choice | Why / rejected alternative |
|---|---|---|
| Architecture pattern | **Doc 2**: UC BASE → Synced `*_sync` → app writes `*_write` → APPLY CHANGES → UC `*_app_changes` → GOLD = BASE ⊕ APP_CHANGES | Maps 1:1 to Palantir "never touch the backing dataset". **Rejected:** Doc 1 (MERGE into a single UC table — conflates source+edits); Lakebase-only (no lakehouse SoT, breaks refresh resilience); app MERGE into BASE (dual-writes the synced table). |
| Demo entity | **`employee`** — editable `salary`,`status`; source-only `first_name`,`department`,`hire_date`; computed `is_high_risk = salary > 200000` | Simple PK; exercises all 5 acceptance behaviors incl. a computed property. |
| UC location | new catalog **`ontology_poc`**, schema **`object_layer`** | Clean grants + one-command teardown. **Rejected:** reusing `serverless_stable_vfpvf8_catalog` (harder to isolate/tear down). |
| Lakebase | **new smallest instance**; database **`ontology_poc`**, schema **`public`** | None exist; smallest is enough for a POC. |
| App identity | dedicated **service principal** (see §4 grants) | Enforces "never touch BASE" via grants, not just discipline. |
| Apply engine (⑤) | **notebook MERGE job, Triggered every 1–2 min** for v1; upgrade to **Triggered SDP APPLY CHANGES** in Phase 5 | Faster to stand up. SDP is the scale target, not the POC blocker. |
| Action layer | **Databricks App only** (`app/ontology-object-demo`) | One visible, authenticated surface avoids duplicate Python and app implementations of the same action semantics. |
| Edit model | **per-column overrides** in the edit store; `NULL` = property not overridden; reconcile with `COALESCE` (edit wins) | One model covers partial edits *and* create; also removes the whole-row shadow-copy step. |
| Record creation | **apps create net-new objects too** (not edit-only) → **FULL OUTER** reconciliation; app-created PKs use an **`app-` namespace** so upstream can never collide | Confirmed requirement. Edit-only (LEFT JOIN) rejected. |
| Edit audit | **Delta CDF on `*_app_changes`** is the audit trail (who/when/before→after) | Near-free; avoids a separate journal table for v1. |
| Concurrency | **last-write-wins per PK** | POC scope; edit-conflict resolution is v2+. |
| Link Types | **out of scope for v1** (single entity, properties only) | Keep the POC to one Object Type end-to-end; add relationships later. |
| Packaging | **DABs** for UC tables, jobs, GOLD MV, (app). Lakebase instance + Synced Table + Postgres DDL managed via `databricks-lakebase` CLI/SDK **alongside** the bundle | Lakebase objects are only partly DABs-expressible today. |

---

## 2. Object model — `employee`

**Primary key:** `employee_id STRING`.

| Property | Type | Class | Notes |
|---|---|---|---|
| `employee_id` | STRING | key | shared across BASE / `*_write` / `*_app_changes` |
| `first_name` | STRING | source-only | never editable |
| `department` | STRING | source-only | never editable |
| `hire_date` | DATE | source-only | never editable |
| `salary` | DECIMAL(12,2) | **editable** | user Action target |
| `status` | STRING | **editable** | e.g. `active` / `on_leave` / `terminated` |
| `is_high_risk` | BOOLEAN | **computed** | derived in GOLD only: `salary > 200000`; never stored in BASE/edits |
| `_etl_run_id` | STRING | source meta | set by ETL simulator each run |
| `_etl_ts` | TIMESTAMP | source meta | set by ETL simulator |
| `is_new` | BOOLEAN | edit-store control | `true` = app-created object (no BASE row); edit-store tables only |

**Create support (confirmed requirement).** Apps also create net-new objects, not just edit existing upstream rows. This is carried through the whole plan:
- The edit store (`*_write` / `*_app_changes`) carries the **full** column set, so a created object supplies its own source-class values.
- Reconciliation is **FULL OUTER JOIN** with per-column `COALESCE` (edit wins) — the overlay and GOLD both.
- App-created PKs are **namespaced** (`app-<uuid>`) so upstream re-ingestion can never collide.
- Source-class columns (`first_name`/`department`/`hire_date`) are writable **only on create**; for existing objects they stay `NULL` in the edit store and BASE always wins them (enforced in the action layer).

---

## 3. Phase-by-phase build

Critical path: **0a → 0c → 1 → 2 → 3**. **Start 0c (Lakebase provisioning) in parallel with 0a immediately** — it is the longest lead. 0b (ETL simulator) and the action layer (§5) run off the side; they gate the *demo*, not the pipeline.

### Phase 0a — UC catalog/schema + BASE (skill: `databricks-unity-catalog`)
Create catalog `ontology_poc`, schema `object_layer`, and the BASE table **with CDF on** (CDF is mandatory — Synced Continuous and APPLY CHANGES both source from it).

```sql
CREATE CATALOG IF NOT EXISTS ontology_poc;
CREATE SCHEMA  IF NOT EXISTS ontology_poc.object_layer;

CREATE TABLE IF NOT EXISTS ontology_poc.object_layer.employee_base (
  employee_id STRING NOT NULL,
  first_name  STRING,
  department  STRING,
  hire_date   DATE,
  salary      DECIMAL(12,2),
  status      STRING,
  _etl_run_id STRING,
  _etl_ts     TIMESTAMP
) USING DELTA
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');
```
**Done when:** table exists, `SHOW TBLPROPERTIES ontology_poc.object_layer.employee_base` shows CDF = true.

### Phase 0b — Upstream ETL simulator (skills: `databricks-data-generation`, `databricks-jobs`)
Notebook `src/notebooks/etl_simulator.py` + a Job. Writes N synthetic employees to `employee_base`; **re-runnable** to mutate salaries of chosen rows (used to demo refresh resilience). Stamp a fresh `_etl_run_id`/`_etl_ts` each run. Parameterize row count + a "bump salaries for ids [...]" mode.
**Done when:** first run populates BASE; a second run changes salary on a target row (verify via `SELECT` + CDF).

### Phase 0c — Lakebase instance + `*_sync` (skill: `databricks-lakebase`) — START FIRST
1. Provision the Lakebase **Database Instance** (smallest). CLI shape: `databricks database create-database-instance --profile fevm-serverless ...` (confirm exact flags via the skill).
2. Register a **Database Catalog** in UC for the instance.
3. Create the **Synced Table (Continuous)** `employee_sync` mirroring `employee_base` (CLI: `create-synced-database-table`). Continuous → ~15s+ lag.
4. Grant the app SP **SELECT** on `employee_sync`.
**Done when:** rows in `employee_base` appear in Lakebase `public.employee_sync`; app SP can `SELECT` it.

### Phase 1 — `*_write` + overlay read (skill: `databricks-lakebase`)
Create the hot edit log (full column set, so it can hold both sparse edits *and* app-created objects) and the FULL OUTER overlay. Run this Postgres DDL in the Lakebase DB:
```sql
CREATE TABLE IF NOT EXISTS public.employee_write (
  employee_id TEXT PRIMARY KEY,
  -- source-class columns: written ONLY on CREATE of a net-new object; NULL when editing an existing object
  first_name  TEXT,
  department  TEXT,
  hire_date   DATE,
  -- editable columns: NULL = property not overridden
  salary      NUMERIC(12,2),
  status      TEXT,
  -- control
  is_new      BOOLEAN NOT NULL DEFAULT false,  -- true = app-created (no BASE row)
  is_deleted  BOOLEAN NOT NULL DEFAULT false,  -- true = object deleted (tombstone)
  editor      TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()  -- sequence column; bump on EVERY write
);

-- Overlay: FULL OUTER so app-created rows (no sync match) and edits both surface; edit wins per column; tombstones hidden.
CREATE OR REPLACE VIEW public.employee_overlay AS
SELECT
  COALESCE(w.employee_id, s.employee_id) AS employee_id,
  COALESCE(w.first_name,  s.first_name)  AS first_name,
  COALESCE(w.department,  s.department)  AS department,
  COALESCE(w.hire_date,   s.hire_date)   AS hire_date,
  COALESCE(w.salary,      s.salary)      AS salary,
  COALESCE(w.status,      s.status)      AS status
FROM public.employee_sync s
FULL OUTER JOIN public.employee_write w ON s.employee_id = w.employee_id
WHERE COALESCE(w.is_deleted, false) = false;
```
**No shadow-copy needed.** The per-column FULL OUTER model makes editing a sync-only row just an insert of a sparse override row (only the edited columns set; source columns `NULL`) — the join supplies the rest. (The old whole-row-overlay design required shadow-copy; this one does not.)
Grant app SP **SELECT/INSERT/UPDATE/DELETE** on `employee_write` (and SELECT on the overlay).
**Done when:** (a) an edit inserted into `employee_write` immediately changes `employee_overlay` for that id; (b) an app-created row (`app-…`, `is_new=true`) with no sync match appears in `employee_overlay`.

### Phase 2 — `*_app_changes` + apply bridge (skills: `databricks-unity-catalog`, `databricks-jobs`)
UC Delta edit landing table (CDF on — this is also the **audit trail**), full column set, **app-owned — never write BASE**:
```sql
CREATE TABLE IF NOT EXISTS ontology_poc.object_layer.employee_app_changes (
  employee_id    STRING NOT NULL,
  first_name     STRING,
  department     STRING,
  hire_date      DATE,
  salary         DECIMAL(12,2),
  status         STRING,
  is_new         BOOLEAN NOT NULL,
  is_deleted     BOOLEAN NOT NULL,
  editor         STRING,
  src_updated_at TIMESTAMP,     -- from *_write.updated_at (sequence for ordering / SDP)
  synced_at      TIMESTAMP NOT NULL
) USING DELTA
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');
```
Notebook `src/notebooks/apply_bridge.py` (Triggered Job, 1–2 min): read `employee_write` from Lakebase (JDBC or Lakebase SDK read), then:
```sql
MERGE INTO ontology_poc.object_layer.employee_app_changes t
USING _write_src s ON t.employee_id = s.employee_id
WHEN MATCHED THEN UPDATE SET
  first_name = s.first_name, department = s.department, hire_date = s.hire_date,
  salary = s.salary, status = s.status, is_new = s.is_new, is_deleted = s.is_deleted,
  editor = s.editor, src_updated_at = s.updated_at, synced_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT
  (employee_id, first_name, department, hire_date, salary, status, is_new, is_deleted, editor, src_updated_at, synced_at)
  VALUES (s.employee_id, s.first_name, s.department, s.hire_date, s.salary, s.status, s.is_new, s.is_deleted, s.editor, s.updated_at, current_timestamp());
```
**Never target `employee_base`.** **Reverts and deletes propagate as ordinary updates** — the action layer NULLs a column (revert) or sets `is_deleted=true` (delete) but never removes the `*_write` row, so the bridge always carries the latest state. **Done when:** a dry run then a live run lands an edit *and* a create *and* a revert in `employee_app_changes`.

### Phase 3 — GOLD / MV (skill: `databricks-unity-catalog` / `databricks-metric-views`)
**FULL OUTER JOIN** (create support) with per-column `COALESCE` (edit wins), tombstones filtered:
```sql
CREATE MATERIALIZED VIEW ontology_poc.object_layer.employee_gold AS
SELECT
  COALESCE(a.employee_id, b.employee_id) AS employee_id,
  COALESCE(a.first_name,  b.first_name)  AS first_name,
  COALESCE(a.department,  b.department)  AS department,
  COALESCE(a.hire_date,   b.hire_date)   AS hire_date,
  COALESCE(a.salary,      b.salary)      AS salary,
  COALESCE(a.status,      b.status)      AS status,
  (COALESCE(a.salary, b.salary) > 200000) AS is_high_risk
FROM ontology_poc.object_layer.employee_base b
FULL OUTER JOIN ontology_poc.object_layer.employee_app_changes a
  ON b.employee_id = a.employee_id
WHERE COALESCE(a.is_deleted, false) = false;
```
Row cases: existing+edited → `a` overrides editable cols, `b` supplies source cols; app-created (`b` NULL) → `a` wins; reverted (`a` col NULL) → `b` wins; deleted (`a.is_deleted`) → filtered out. Point BI at `employee_gold`, **not** BASE.
**Done when:** edited, created, reverted, and deleted objects each resolve correctly in GOLD after the next bridge run; BASE is unchanged.

### Phase 4 — Archive / purge (skill: `databricks-jobs`) — optional for v1
If implemented, it must **not break propagation**: never delete a `*_write` row whose latest state isn't yet reflected in `*_app_changes`, and never delete a row that still overrides BASE (its NULLs/tombstone are meaningful state). **Simplest v1 stance: skip physical purge; just monitor `*_write` size.** Real purge belongs with the Phase-5 SDP move (`APPLY AS DELETE` handles tombstones natively). **Done when:** `*_write` size is monitored and a purge (if any) leaves GOLD unchanged.

### Phase 5 (optional) — MERGE → Triggered SDP APPLY CHANGES (skill: `databricks-pipelines`)
Replace the notebook MERGE bridge with a Triggered Lakeflow Declarative Pipeline using `APPLY CHANGES INTO ... KEYS(employee_id) SEQUENCE BY src_updated_at APPLY AS DELETE WHEN is_deleted = true`. **Done when:** lag SLA met with the pipeline; notebook bridge retired.

---

## 4. Grants matrix (enforces "never touch BASE")

| Principal | UC | Lakebase |
|---|---|---|
| **App SP** | SELECT on `employee_gold` (if app reads gold); **NO privileges on `employee_base`** | SELECT on `employee_sync`; SELECT/INSERT/UPDATE/DELETE on `employee_write`; SELECT on `employee_overlay` |
| **Bridge/sync SP** | MODIFY on `employee_app_changes` + `employee_gold`; SELECT on `employee_base` | SELECT on `employee_write` |
| **Synced pipeline** | SELECT + CDF on `employee_base` | owns write into `employee_sync` |
| **BI users/group** | SELECT on `employee_gold` | — |

**Verify the guarantee:** confirm the App SP has no MODIFY/write path to `employee_base` (this is acceptance behavior #5).

---

## 5. Action layer (Databricks App)
The AppKit routes in `app/ontology-object-demo/server/routes/lakebase/employee-routes.ts`
are the **only** action surface. They write only to `employee_write`, bump
`updated_at`, and read back through `employee_overlay` for immediate visibility.
There is no parallel Python action SDK or CLI. Four distinct operations remain:

| Op | What it does | Write-store effect |
|---|---|---|
| `POST /api/employees` | new object upstream never had | generate `employee_id = 'app-'||uuid`; `INSERT` full row, `is_new=true`, `is_deleted=false`. Source-class cols required here. |
| `PATCH /api/employees/:id` | override an editable property | `INSERT ... ON CONFLICT DO UPDATE` for `salary` and/or `status`; the request schema rejects source-class columns. |
| `POST /api/employees/:id/revert` | undo an edit → fall back to source | `UPDATE ... SET <prop> = NULL`; `COALESCE` then picks BASE. App-created objects cannot revert to a missing source. |
| `DELETE /api/employees/:id` | hide the object entirely | upsert `is_deleted=true` tombstone for upstream-backed or app-created objects. |

**Never physically `DELETE` a `*_write` row in these ops** — revert/delete are *state changes* (NULL / `is_deleted`); removing the row would strand the stale value in `*_app_changes` and break propagation. Physical purge is the separate, careful Phase-4 step.

---

## 6. DABs layout (skill: `databricks-dabs`)
```
databricks.yml                 # target: fevm-serverless
resources/
  catalog.yml                  # ontology_poc + object_layer (or run DDL in a setup job)
  jobs.yml                     # etl_simulator, apply_bridge (+ archive step)
src/
  ddl/
    employee_base.sql
    employee_app_changes.sql
    employee_gold.sql
  notebooks/
    etl_simulator.py
    apply_bridge.py
  lakebase/                    # managed via databricks-lakebase CLI/SDK, NOT DABs
    instance.md                # provisioning steps + flags
    employee_write.sql
    employee_overlay.sql
    synced_table.md            # create-synced-database-table config
app/
  ontology-object-demo/        # sole create/edit/revert/delete surface
```
Deploy: `databricks bundle deploy --profile fevm-serverless` (confirm via skill). Lakebase objects run through their own CLI/SDK steps.

---

## 7. Acceptance — the behaviors (run through the Databricks App)
Use the Object Storage Lab UI and the ETL/bridge jobs to prove, in order:
1. **Edit propagation:** edit `salary` in the app → immediate in `employee_overlay` → after next bridge run appears in `employee_app_changes` → wins in `employee_gold`.
2. **Create:** create an employee in the app (`app-…`) → immediate in overlay → flows to `employee_app_changes` → appears in GOLD, and **never** in BASE.
3. **Refresh resilience:** re-run ETL to change salary of an **edited** row in BASE → GOLD still shows the edit.
4. **Non-edited refresh:** ETL changes a **non-edited** row → new source value flows to GOLD.
5. **Revert edit:** click **Revert salary** → after bridge run, GOLD `salary` reverts to the **BASE** value (edit undone). *(Revert ≠ delete.)*
6. **Delete object:** delete through the app → tombstone → object hidden from overlay **and** GOLD (test on both an upstream-backed and an app-created object).
7. **BASE immutability:** show via `employee_base` history/CDF + the grant check that the app SP never modified BASE.

---

## 8. Gotchas (will bite if skipped)
- **CDF on BASE before** creating the Synced Table and the apply source — else Synced Continuous fails.
- **Grants are the real guarantee** that the app never touches BASE — verify explicitly (#5), don't rely on discipline.
- **Deletes need tombstones** — MERGE is upsert-only; honor `is_deleted` in both the overlay and GOLD.
- **No shadow-copy** — the per-column FULL OUTER overlay makes editing a sync-only row a plain sparse insert. (Do not reintroduce the old whole-row copy step.)
- **Revert ≠ delete.** Revert = NULL the column (falls back to BASE); delete = `is_deleted=true` (hides the object). Never conflate them, and never physically remove a `*_write` row in the hot path (breaks propagation).
- **App-created PKs must be namespaced** (`app-<uuid>`) so an upstream re-ingest can never collide with an app-created object.
- **Source-class columns are create-only** — the action layer must reject overrides of `first_name`/`department`/`hire_date` on existing objects (upstream owns them).
- **BI lag:** GOLD only reflects edits after each bridge run; the Lakebase overlay is immediate. Document the SLA.
- **Computed props recompute on read** — `is_high_risk` lives only in GOLD; never persist it to BASE/edits.
- **Serverless warehouse is stopped** — first GOLD query pays cold-start (fine for a POC).
- **Lakebase + Synced Table + Postgres DDL are partly outside DABs** — keep those steps documented in `src/lakebase/`.

## 9. Genuinely open (decide at implementation, with criteria)
- **Lakebase instance size/region flags** — pick smallest available in the workspace's region; confirm exact flags from `databricks-lakebase`.
- **Lakebase read method in the bridge** — JDBC (postgres driver) vs Lakebase SDK read; choose whichever the `databricks-lakebase` skill documents as current/supported on serverless.
- **GOLD as Materialized View vs plain view** — MV if refresh cost/perf matters for the demo; plain view is simpler and always fresh. Default MV; fall back to view if MV refresh wiring is fiddly.

---

## 10. Deferred to v2+ (named, not built)
Not required to serve edits + upstream for one entity — listed so scope is explicit and nothing is assumed done. We are inspired by Object Storage V2, not copying it.
- **Link Types** (relationships) + link edits/actions — the biggest cut.
- **Action validation + Functions** (business logic) + **multi-object atomic edit batches** — mostly app-layer, above the storage line.
- **Rich property types** (struct / array / enum / media / geotime); **multiple backing datasets**; Interfaces / Shared Property Templates.
- **Edit-conflict resolution** — v1 is last-write-wins per PK.
- **"Edited to explicit NULL"** on an editable property — `NULL` currently means *not overridden*; disambiguating requires a per-property patch representation.
