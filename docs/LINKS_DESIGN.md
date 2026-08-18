# Link Types on the UC ↔ Lakebase Data Layer — Design

**Status:** Design (not built). Links were deferred in `IMPLEMENTATION_PLAN.md` §10 and
`CONTEXT.md` §8; this doc specifies how they slot onto the existing object-side pipeline.
**Companion docs:** `CONTEXT.md` (object-side architecture), `PALANTIR_FOUNDRY_ONTOLOGY_ARCHITECTURE.md`
(Palantir reference), `GENERALIZATION_PLAN.md` (config-driven multi-type generation).

---

## 0. TL;DR — the one insight

**A Link Type is just an Object Type whose primary key is a tuple of foreign keys.**

Everything we already built for objects — `BASE → *_sync → *_write → *_overlay →
*_app_changes → GOLD`, sparse per-column edits, reconcile-on-read with edit-wins, `is_new`
create, `is_deleted` tombstone, revert-to-BASE, refresh resilience — **generalizes to links
verbatim**. We do **not** need a new storage primitive. We need to parameterize the existing
6-step pipeline over a second table *shape*: a link table keyed by `(from_id, to_id)` instead
of a single object PK.

This is exactly why `GENERALIZATION_PLAN.md` §165 calls links "a second generated shape."

---

## 1. How Palantir backs links (grounded facts)

From Palantir's link-types docs (palantir.com/docs/foundry/object-link-types/…) and this
repo's Palantir reference (`PALANTIR_FOUNDRY_ONTOLOGY_ARCHITECTURE.md` §1, §12):

- A **Link Type** is a semantic relationship between two Object Types; a **link** is one
  instance of that relationship between two specific objects.
- **Two backing mechanisms:**
  1. **Foreign-key links** — for **one-to-one and one-to-many**. The object's backing
     datasource carries a **foreign-key column** referencing the other object's primary key.
     No separate dataset. The FK lives on the **"many" side** (each many-row points at one
     one-row).
  2. **Many-to-many links backed by a dataset** — *"datasources back the link types
     themselves."* A dedicated **join/mapping dataset**, one row per link instance, with a
     foreign key to each side's PK. This dataset can also carry **link properties**
     (attributes on the relationship, e.g. `role`, `start_date`).
- **Cardinality:** one-to-one, one-to-many, many-to-many.
- **Links are editable via Actions.** An Action's **edit batch** groups object *and* link
  modifications in one transaction; link create/delete are **link edits**, stored as
  writeback in the edit store — **separate from the backing dataset**, exactly like object
  property edits in Object Storage V2.

**Consequence for us:** two shapes to support — (A) FK links, which are *already* just an
object property under our model, and (B) M:N link tables, which are a clone of the object
pipeline keyed by a pair.

---

## 2. Shape A — Foreign-key links (1:N, 1:1) — nearly free

A one-to-many link (e.g. **Employee → Department**) is a foreign-key **property on the many
side**. Under our data layer, an FK column is *just another property* and inherits the entire
reconciliation machinery. There is **no new table.**

### 2.1 What changes
1. Add the FK column to the object's every layer (BASE / `*_write` / `*_overlay` /
   `*_app_changes` / GOLD), e.g. `department_id STRING` on `employee_*`.
2. Stand up the **target** object type (`department_base`, `department_sync`, …) via the
   normal generalization path — a Department is a plain Object Type.
3. **Make the FK editable** so links can be created/changed/removed by the app.
   Extend the AppKit route validation in
   `app/ontology-object-demo/server/routes/lakebase/employee-routes.ts` with a
   distinct link-property schema (for example `department_id`), editable and
   revertable. Keeping link fields distinct from ordinary editable properties
   lets the app run link-specific validation — see §4.

### 2.2 Link operations map onto existing object ops
| Link op | Under Shape A |
|---|---|
| **Create link** Employee→Dept | App PATCH sets `department_id="dept-42"` |
| **Change link target** | App PATCH replaces it with `department_id="dept-99"` |
| **Remove link** | App revert endpoint NULLs the override so `COALESCE` falls back to BASE FK; a future unlink endpoint can represent explicit null |
| **Traverse** | Join in GOLD/overlay (§2.3) |

### 2.3 Traversal view
Traversal is a join across the two objects' reconciled surfaces — hot path on the Lakebase
overlays, analytics on GOLD:

```sql
-- GOLD-level traversal (BI): employee with its resolved department
CREATE OR REPLACE VIEW <ns>.employee_with_department AS
SELECT e.*, d.department_name, d.cost_center
FROM <ns>.employee_gold e
LEFT JOIN <ns>.department_gold d
  ON e.department_id = d.department_id;   -- d already excludes tombstoned depts
```

Because both `*_gold` views already filter tombstones, a link to a **deleted** department
resolves to NULLs on the department side (dangling-link handling; §7).

**Effort: low.** No new pipeline. This alone demonstrates a one-to-many Link Type with
link edits, refresh resilience on the FK, and traversal.

---

## 3. Shape B — Many-to-many link table (the general case)

A many-to-many link (e.g. **Employee ↔ Project**, with link properties `role`,
`start_date`) is its **own backing dataset** — so it gets its **own full pipeline**, an exact
clone of the object pipeline, keyed by the **pair** `(employee_id, project_id)`.

### 3.1 UC `assignment_base` (upstream-owned source, CDF on) — mirrors `setup_ontology.py`
```sql
CREATE TABLE IF NOT EXISTS <ns>.assignment_base (
  employee_id STRING NOT NULL,   -- FK → employee PK
  project_id  STRING NOT NULL,   -- FK → project PK
  role        STRING,            -- link property
  start_date  DATE,              -- link property
  _etl_run_id STRING,
  _etl_ts     TIMESTAMP
) USING DELTA
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');
-- logical PK: (employee_id, project_id)
```

### 3.2 Lakebase `assignment_sync` (Continuous mirror, read-only) + `assignment_write`
```sql
CREATE TABLE IF NOT EXISTS public.assignment_write (
  employee_id TEXT NOT NULL,
  project_id  TEXT NOT NULL,
  role        TEXT,
  start_date  DATE,
  is_new      BOOLEAN NOT NULL DEFAULT false,   -- link created by the app
  is_deleted  BOOLEAN NOT NULL DEFAULT false,   -- link "unlinked" (tombstone)
  editor      TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (employee_id, project_id)          -- composite PK = the pair
);
CREATE INDEX IF NOT EXISTS assignment_write_updated_at_idx
  ON public.assignment_write (updated_at);
```

### 3.3 `assignment_overlay` — FULL OUTER + COALESCE on the **pair**
```sql
CREATE OR REPLACE VIEW public.assignment_overlay AS
SELECT
  COALESCE(w.employee_id, s.employee_id) AS employee_id,
  COALESCE(w.project_id,  s.project_id)  AS project_id,
  COALESCE(w.role,        s.role)        AS role,
  COALESCE(w.start_date,  s.start_date)  AS start_date
FROM object_layer.assignment_sync s
FULL OUTER JOIN public.assignment_write w
  ON s.employee_id = w.employee_id AND s.project_id = w.project_id
WHERE COALESCE(w.is_deleted, false) = false;
```

### 3.4 `assignment_app_changes` (UC Delta, CDF) via the bridge MERGE on the pair
Same as `apply_bridge.py`, but the MERGE `ON` clause matches both key columns:
```sql
MERGE INTO <ns>.assignment_app_changes t
USING _write_src s
  ON t.employee_id = s.employee_id AND t.project_id = s.project_id
WHEN MATCHED AND (t.src_updated_at IS NULL OR s.updated_at > t.src_updated_at) THEN UPDATE SET …
WHEN NOT MATCHED THEN INSERT …
```

### 3.5 `assignment_gold` — reconciled link truth = BASE ⊕ APP_CHANGES
```sql
CREATE OR REPLACE VIEW <ns>.assignment_gold AS
SELECT
  COALESCE(a.employee_id, b.employee_id) AS employee_id,
  COALESCE(a.project_id,  b.project_id)  AS project_id,
  COALESCE(a.role,        b.role)        AS role,
  COALESCE(a.start_date,  b.start_date)  AS start_date
FROM <ns>.assignment_base b
FULL OUTER JOIN <ns>.assignment_app_changes a
  ON b.employee_id = a.employee_id AND b.project_id = a.project_id
WHERE COALESCE(a.is_deleted, false) = false;
```

**Everything else — CDF requirement, Continuous sync, 1–2 min bridge cadence, immediate
overlay vs lagged GOLD — is identical to the object pipeline.** Refresh resilience holds:
upstream re-ingesting `assignment_base` cannot clobber app-created/edited links, because the
edit store (`assignment_write` → `assignment_app_changes`) wins on read.

---

## 4. Link Actions (extends the action layer)

Add link endpoints to the Databricks App routes (or generalize the route
builders around a key-column list). Do not add a separate Python action store.
Four operations mirror the object verbs:

| Link action | Write-store effect | Object-side analogue |
|---|---|---|
| `POST /api/links` | INSERT `assignment_write` row, `is_new=true` | App create endpoint |
| `PATCH /api/links/:from/:to` | Sparse override (`ON CONFLICT … DO UPDATE`) | App edit endpoint |
| `POST /api/links/:from/:to/revert` | NULL the override → COALESCE picks BASE | App revert endpoint |
| `DELETE /api/links/:from/:to` | `is_deleted=true` tombstone (**never physical delete**) | App delete endpoint |

**Validation the action layer must add (Palantir Actions validate link targets):**
- **Endpoint existence** — `create_link` checks *both* endpoints exist in their respective
  overlays (`employee_overlay`, `project_overlay`) before inserting. UC/Lakebase do **not**
  enforce cross-table referential integrity; this is our job, exactly as Foundry Actions
  validate the objects a link points at.
- **Cardinality** — for a 1:N FK link (Shape A) the "many→one" direction is structural (one
  FK value); for a modeled 1:1 add a uniqueness check. For M:N, no constraint.
- **No app-PK namespace needed for M:N links** — unlike app-created *objects* (`app-<uuid>`,
  `CONTEXT.md` §9), an app-created link's identity is the **natural pair** of two existing
  object PKs, so collisions with upstream are impossible by construction. (If a link ever
  needs a surrogate `link_id` PK — e.g. multiple parallel edges between the same two objects
  — then reuse the `app-<uuid>` namespacing.)

Grants: the app SP gets `SELECT, INSERT, UPDATE, DELETE` on `public.assignment_write` and
`SELECT` on `assignment_sync` / `assignment_overlay` — same pattern as `grants.sql`. It gets
**no MODIFY on any `*_base`**.

---

## 5. Palantir → Databricks mapping (links)

| Palantir | Databricks (this design) |
|---|---|
| Link Type, foreign-key backed (1:N / 1:1) | FK **property** on the object (`employee.department_id`), reconciled like any property; **no new table** |
| Link Type, many-to-many backed by a dataset | Dedicated link pipeline `assignment_{base,sync,write,overlay,app_changes,gold}`, PK = `(from_id, to_id)` |
| Link properties (role, start_date) | Extra columns on the link table (same reconciliation) |
| Link instance identity | The FK value (Shape A) / the pair `(from_id, to_id)` (Shape B) |
| Create link (link edit) | `is_new=true` row in `*_write` (or set FK) |
| Delete / unlink (link edit) | `is_deleted=true` tombstone in `*_write` (or revert FK) |
| Edit link property | Sparse column override in `*_write` |
| Revert link property | NULL the override → BASE wins |
| Edit batch (objects + links atomic) | One Lakebase Postgres transaction spanning `employee_write` + `assignment_write` |
| Link traversal / navigate | JOIN across `*_gold` (BI) or `*_overlay` (hot path) |
| Backing dataset never touched by actions | `*_base` upstream-only; edits land in `*_write`→`*_app_changes` |
| "Drop all edits" | Truncate `assignment_write` + `assignment_app_changes` → fall back to BASE links |

---

## 6. Object Storage V2 — link capability fidelity

| OSv2 link capability | This design | Fidelity |
|---|---|---|
| Links have their own backing index, separate from object edits | `assignment_base` + `assignment_sync` | ✅ faithful |
| Link edits (create/delete) stored in the internal edit store, not the backing dataset | `assignment_write` (hot) + `assignment_app_changes` (durable) | ✅ faithful |
| Link edits reconciled edit-wins on read | overlay + GOLD FULL OUTER on the pair | ✅ faithful |
| Immediate link-edit visibility | Lakebase overlay (live write wins) | ✅ faithful |
| Editable link properties | columns on the link table, sparse override + revert | ✅ faithful |
| Refresh resilience of links | edit store wins over re-ingested `assignment_base` | ✅ faithful |
| Referential integrity to endpoints | action-layer validation + GOLD tombstone filtering | ⚠ modeled, not native (same class of gap as per-object security in `CONTEXT.md` §11) |

---

## 7. Referential integrity, cascades, dangling links

UC and Lakebase don't cascade; we model it at two layers (matching Foundry, which resolves
this in the object backend + Actions):

- **Create-time:** action layer rejects a link whose endpoint doesn't exist (§4).
- **Read-time / dangling links:** a link to a **deleted** object (endpoint tombstoned) must
  not surface as a live edge. Because each `*_gold`/`*_overlay` already excludes tombstoned
  rows, a traversal join (§2.3) naturally drops or NULLs the dead side. For strict hiding,
  make the link's GOLD a **semi-join** against live endpoints:
  ```sql
  ... FROM assignment_gold a
  WHERE EXISTS (SELECT 1 FROM employee_gold e WHERE e.employee_id = a.employee_id)
    AND EXISTS (SELECT 1 FROM project_gold  p WHERE p.project_id  = a.project_id);
  ```
- **Delete cascade** (delete an object → tombstone its links): keep it **explicit in the
  AppKit delete route**, not a DB trigger — the route can enqueue link
  tombstones for that employee's assignments in the same transaction. Deferrable;
  read-time filtering above is enough for correctness of the graph view.

**NULL-semantics caveat (inherited from `IMPLEMENTATION_PLAN.md` §10):** for Shape A, `NULL`
on the FK currently means *"not overridden → use BASE FK"*, so "unlink" (force the FK to
nothing even though BASE has one) isn't expressible without a per-property patch/sentinel. For
a 1:N unlink, prefer Shape-B modeling of that relationship, or add the explicit-NULL patch
representation flagged in §10. M:N unlink has no such ambiguity — it's a tombstone.

---

## 8. Phasing

| Phase | Work | Done when |
|---|---|---|
| **L0** | Shape A: add `department_id` FK to `employee_*`; stand up `department` object type; add `LINK_PROPERTIES` (editable+revertable); add endpoint-existence validation; traversal view | App can set/change/clear an Employee→Department link; it survives BASE refresh; traversal joins resolve |
| **L1** | Shape B: clone the pipeline for `assignment` (M:N Employee↔Project) with link props `role`/`start_date`; composite-PK overlay + bridge + GOLD | create/edit/revert/delete link work end-to-end; edits win over re-ingested link BASE |
| **L2** | Integrity: dangling-link filtering (semi-join GOLD); optional explicit delete-cascade in the action layer; atomic object+link edit batch in one Postgres txn | Deleted endpoints drop their edges; multi-edit actions are atomic |
| **L3** | Generalization: fold both shapes into the config-driven generator (`GENERALIZATION_PLAN.md`) — a `link` config emits the composite-key pipeline; an `fk_link` config emits an FK column + traversal view | A new link type is declared in config, not hand-DDL'd |

---

## 9. Gotchas (link-specific; object-side gotchas in `CONTEXT.md` §9 still apply)

- **CDF on `*_base`** is required for the link's Continuous sync and APPLY CHANGES sourcing,
  same as objects.
- **Never physically delete a `*_write` link row.** Unlink = `is_deleted=true` tombstone; a
  physical delete strands the stale value in `*_app_changes` and breaks propagation (same trap
  as objects, `CONTEXT.md` §9).
- **Composite key everywhere.** The bridge MERGE `ON`, the overlay/GOLD FULL OUTER join, and
  the Lakebase PRIMARY KEY must all match on **both** key columns — a single-column join
  silently produces a cartesian/duplicate mess.
- **Referential integrity is not free.** Endpoint validation (create-time) and dangling-link
  filtering (read-time) are build tasks; there is no FK enforcement across UC tables or across
  the Lakebase `sync`↔`write` split.
- **FK-link NULL ambiguity** (§7) — don't ship a 1:N "unlink" until the explicit-NULL patch
  representation exists, or model that relationship as Shape B.
- **Route all Databricks work through the AI Dev Kit skills** (`databricks-core` +
  `databricks-lakebase` / `databricks-unity-catalog` / `databricks-pipelines` /
  `databricks-jobs`); always `--profile fevm-serverless`; never auto-select a profile.

---

## 10. Bottom line

Links require **zero new storage primitives**. Shape A (FK, 1:N/1:1) is *already* an object
property and rides the existing reconciliation for free once the FK is made editable. Shape B
(M:N + link properties) is a **mechanical clone of the object pipeline keyed by a pair**. The
only genuinely new engineering is **action-layer referential-integrity validation** and
**read-time dangling-link filtering** — the parts Palantir also handles above the raw backing
dataset. This confirms the object-side data layer was the hard part; links are a
parameterization of it.
