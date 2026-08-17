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
| Action layer | **thin notebook/SDK write surface** for v1 | POC value is the data layer, not UI. Upgrade to a Databricks App only if a live UI demo is requested. |
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
Create the hot edit log and the overlay. Run this Postgres DDL in the Lakebase DB:
```sql
CREATE TABLE IF NOT EXISTS public.employee_write (
  employee_id TEXT PRIMARY KEY,
  salary      NUMERIC(12,2),      -- NULL = this property not edited
  status      TEXT,               -- NULL = this property not edited
  is_deleted  BOOLEAN NOT NULL DEFAULT false,
  seq         BIGINT GENERATED ALWAYS AS IDENTITY,
  editor      TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Overlay: live write wins over synced source; tombstones hidden.
CREATE OR REPLACE VIEW public.employee_overlay AS
SELECT s.employee_id, s.first_name, s.department, s.hire_date,
       COALESCE(w.salary, s.salary)  AS salary,
       COALESCE(w.status, s.status)  AS status
FROM public.employee_sync s
LEFT JOIN public.employee_write w ON s.employee_id = w.employee_id
WHERE COALESCE(w.is_deleted, false) = false;
```
**Shadow-copy rule (critical, see CONTEXT.md §9):** the action layer must, before an UPDATE/DELETE on a row that exists only in `*_sync`, insert a base copy into `*_write` then apply the edit — otherwise there is nothing to override. (For pure property edits via the overlay's LEFT JOIN this is implicit; keep the rule for app-created objects.)
Grant app SP **SELECT/INSERT/UPDATE/DELETE** on `employee_write` (and SELECT on the overlay).
**Done when:** an edit inserted into `employee_write` immediately changes `employee_overlay` for that id.

### Phase 2 — `*_app_changes` + apply bridge (skills: `databricks-unity-catalog`, `databricks-jobs`)
UC Delta edit landing table (CDF on), **app-owned — never write BASE**:
```sql
CREATE TABLE IF NOT EXISTS ontology_poc.object_layer.employee_app_changes (
  employee_id   STRING NOT NULL,
  salary        DECIMAL(12,2),
  status        STRING,
  is_deleted    BOOLEAN NOT NULL,
  seq           BIGINT,
  editor        STRING,
  synced_at     TIMESTAMP NOT NULL,
  source_run_id STRING
) USING DELTA
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');
```
Notebook `src/notebooks/apply_bridge.py` (Triggered Job, 1–2 min): read `employee_write` from Lakebase (JDBC or Lakebase SDK read), then:
```sql
MERGE INTO ontology_poc.object_layer.employee_app_changes t
USING _write_src s ON t.employee_id = s.employee_id
WHEN MATCHED THEN UPDATE SET
  salary = s.salary, status = s.status, is_deleted = s.is_deleted,
  seq = s.seq, editor = s.editor, synced_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT
  (employee_id, salary, status, is_deleted, seq, editor, synced_at)
  VALUES (s.employee_id, s.salary, s.status, s.is_deleted, s.seq, s.editor, current_timestamp());
```
**Never target `employee_base`.** **Done when:** a dry run then a live run lands the edit in `employee_app_changes`.

### Phase 3 — GOLD / MV (skill: `databricks-unity-catalog` / `databricks-metric-views`)
```sql
CREATE MATERIALIZED VIEW ontology_poc.object_layer.employee_gold AS
SELECT b.employee_id, b.first_name, b.department, b.hire_date,
       COALESCE(a.salary, b.salary) AS salary,
       COALESCE(a.status, b.status) AS status,
       (COALESCE(a.salary, b.salary) > 200000) AS is_high_risk
FROM ontology_poc.object_layer.employee_base b
LEFT JOIN ontology_poc.object_layer.employee_app_changes a
  ON b.employee_id = a.employee_id
WHERE COALESCE(a.is_deleted, false) = false;
```
(Use `FULL OUTER JOIN` instead of `LEFT` only if the app can create net-new objects — out of scope for v1.) Point BI at `employee_gold`, **not** BASE.
**Done when:** an edited row shows the edited value in GOLD after the next bridge run; BASE is unchanged.

### Phase 4 — Archive / purge (skill: `databricks-jobs`)
Append a step to the Phase-2 job: after a successful apply, archive + purge applied rows/tombstones from `employee_write` so it stays small; keep live entity rows needed for the overlay. **Done when:** `employee_write` row count stays bounded across many edits.

### Phase 5 (optional) — MERGE → Triggered SDP APPLY CHANGES (skill: `databricks-pipelines`)
Replace the notebook MERGE bridge with a Triggered Lakeflow Declarative Pipeline using `APPLY CHANGES INTO ... KEYS(employee_id) SEQUENCE BY seq APPLY AS DELETE WHEN is_deleted = true`. **Done when:** lag SLA met with the pipeline; notebook bridge retired.

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

## 5. Action layer (thin write surface)
Notebook/SDK (`src/action/apply_action.py`) exposing an `apply_action(employee_id, {salary?, status?}, editor)` that:
1. (shadow-copy if the id is sync-only) — insert a base copy into `employee_write`.
2. `INSERT ... ON CONFLICT (employee_id) DO UPDATE` the edited properties into `employee_write`, bump `updated_at`.
3. Read-back via `employee_overlay` to confirm immediate visibility.
Writes **only** to `employee_write`. Tombstone = set `is_deleted = true` (delete-edit path).

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
  action/
    apply_action.py
  lakebase/                    # managed via databricks-lakebase CLI/SDK, NOT DABs
    instance.md                # provisioning steps + flags
    employee_write.sql
    employee_overlay.sql
    synced_table.md            # create-synced-database-table config
demo/
  run_demo.py                  # the 5-behavior acceptance script (§7)
```
Deploy: `databricks bundle deploy --profile fevm-serverless` (confirm via skill). Lakebase objects run through their own CLI/SDK steps.

---

## 7. Acceptance — the 5 behaviors (build `demo/run_demo.py`)
Prove CONTEXT.md §5 end-to-end, in order:
1. **Edit propagation:** action edits `salary` → immediate in `employee_overlay` → after next bridge run appears in `employee_app_changes` → wins in `employee_gold`.
2. **Refresh resilience:** re-run ETL simulator to change salary of an **edited** row in BASE → GOLD still shows the edit.
3. **Non-edited refresh:** ETL changes a **non-edited** row → new source value flows to GOLD.
4. **Tombstone revert:** set `is_deleted = true` for an edit → after bridge run, GOLD reverts to BASE value.
5. **BASE immutability:** show via `employee_base` history/CDF + the grant check that the app SP never modified BASE.

---

## 8. Gotchas (will bite if skipped)
- **CDF on BASE before** creating the Synced Table and the apply source — else Synced Continuous fails.
- **Grants are the real guarantee** that the app never touches BASE — verify explicitly (#5), don't rely on discipline.
- **Deletes need tombstones** — MERGE is upsert-only; honor `is_deleted` in both the overlay and GOLD.
- **Shadow-copy sync-only rows before edit** (§1 phase), else the overlay has nothing to override for app-created objects.
- **BI lag:** GOLD only reflects edits after each bridge run; the Lakebase overlay is immediate. Document the SLA.
- **Computed props recompute on read** — `is_high_risk` lives only in GOLD; never persist it to BASE/edits.
- **Serverless warehouse is stopped** — first GOLD query pays cold-start (fine for a POC).
- **Lakebase + Synced Table + Postgres DDL are partly outside DABs** — keep those steps documented in `src/lakebase/`.

## 9. Genuinely open (decide at implementation, with criteria)
- **Lakebase instance size/region flags** — pick smallest available in the workspace's region; confirm exact flags from `databricks-lakebase`.
- **Lakebase read method in the bridge** — JDBC (postgres driver) vs Lakebase SDK read; choose whichever the `databricks-lakebase` skill documents as current/supported on serverless.
- **GOLD as Materialized View vs plain view** — MV if refresh cost/perf matters for the demo; plain view is simpler and always fresh. Default MV; fall back to view if MV refresh wiring is fiddly.
