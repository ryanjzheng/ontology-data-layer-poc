# Link Types on the UC ↔ Lakebase Data Layer — Design

**Status:** Design (not built). Links were deferred in `IMPLEMENTATION_PLAN.md` §10 and
`CONTEXT.md` §8; this doc specifies how they slot onto the existing object-side pipeline.
**Companion docs:** `CONTEXT.md` (object-side architecture), `PALANTIR_FOUNDRY_ONTOLOGY_ARCHITECTURE.md`
(Palantir reference), `GENERALIZATION_PLAN.md` (config-driven multi-type generation).

**New here?** Read §0 for the one-line answer, then **§2 for the conceptual picture** —
how links change (or don't change) our storage layer, with examples. §3–§4 are the
build-level detail (DDL, views, MERGE); §5 onward covers actions, integrity, and phasing.

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

From Palantir's link-types docs and this repo's Palantir reference
(`PALANTIR_FOUNDRY_ONTOLOGY_ARCHITECTURE.md` §1, §12):

- A **Link Type** is a semantic relationship between two Object Types; a **link** is one
  instance of that relationship between two specific objects. Each link type is
  **bidirectional** — both sides are independently traversable (e.g. an employee "works in" a
  department; a department "employs" employees), with no separate reverse definition.
- **Cardinality:** one-to-one, one-to-many, many-to-many.
- **Three backing mechanisms** (this is the part that matters for our storage design):
  1. **Foreign-key links** — for **one-to-one and many-to-one**. The object's backing
     datasource carries a **foreign-key column** referencing the other object's primary key.
     No separate dataset. The FK lives on the **"many" side** (each many-row points at one
     one-row).
  2. **Join-table (mapping-dataset) links** — for **many-to-many**. A dedicated dataset, one
     row per link instance, holding a foreign key to **each** side's PK. In Foundry this join
     table is **bare** — it carries the two keys and nothing else (the Ontology Manager can
     even auto-generate its schema from the two object types' PKs).
  3. **Backing-object-type links** — a **three-entity** pattern for many-to-many links that
     need **properties on the relationship itself** (e.g. `role`, `start_date`). Foundry does
     *not* put edge properties on the bare join table; instead an **intermediary object type**
     holds them, linked many-to-one to each endpoint.
- **Links are editable via Actions.** An Action's **edit batch** groups object *and* link
  modifications in one transaction; link create/delete are **link edits**, written to a
  **writeback dataset** for the link type — **separate from the backing dataset**, exactly like
  object property edits in Object Storage V2.
- **Traversal is resolved at query time**, not materialized — the API resolves a link by
  comparing FK values or scanning the join table on each call (e.g. `Flight.assignedAircraft.get()`).

**Consequence for us — three Palantir patterns collapse into TWO of our shapes:**

| Palantir pattern | Our shape |
|---|---|
| Foreign-key (1:1, N:1) | **Shape A** — the FK is just another object property; no new table |
| Join-table (M:N, no edge props) | **Shape B** — a cloned pipeline keyed by the pair |
| Backing-object-type (M:N + edge props) | **also Shape B** — same pipeline, with the edge props as extra columns |

The reason patterns 2 and 3 merge for us: our link table is itself a **full pipeline**
(`base`/`sync`/`write`/`app_changes` + views), so it can hold property columns natively. We
never need Foundry's separate intermediary entity. That's a **simplification over Palantir**,
not a gap — see §2 and §4.

---

## 2. How links change our storage layer (the high-level view)

This section answers the design question directly: *given our UC ↔ Lakebase data layer, what
actually changes when we add a link?* The short answer — you either **add a column** to tables
you already have, or you **stamp out the same pipeline again** keyed by a pair. Nothing about
the storage model itself changes.

### 2.1 Anchor — the footprint of one Object Type

Every object type (e.g. `employee`) today is **4 physical tables + 2 reconciliation views**:

| Where | Tables | Views |
|---|---|---|
| **Unity Catalog** | `employee_base` (source, CDF) · `employee_app_changes` (durable edits, CDF) | `employee_gold` (reconciled, for BI) |
| **Lakebase** | `employee_sync` (continuous mirror) · `employee_write` (hot edits) | `employee_overlay` (reconciled, live) |

Every question below is: *how does a link change this footprint?*

### 2.2 The three kinds of link, by storage impact

**Type A — Foreign-key link (N:1 / 1:1): add one column, no new pipeline.**
*Example: Employee → Department ("each employee is in one department").*
The link **is** a column (`department_id`) added to the employee tables you already have —
BASE, sync, write, app_changes, and both views. Setting a link is just editing a property:
`PATCH department_id='dept-42'`. It reconciles edit-wins and survives BASE refresh like any
other property. The only extra cost is that the **target** (`department`) must exist as its own
object type (its own 4 tables) — but that's paid **once per entity**, not per link; every FK
pointing at Department reuses it. Traversal is a join view (`employee JOIN department`).

**Type B — Many-to-many, no edge properties: a whole new 4-table pipeline.**
*Example: Employee ↔ Project ("an employee is on many projects; a project has many employees").*
A pair can't live as a column on either side, so the link gets its **own clone of the full
pipeline**, keyed by the pair `(employee_id, project_id)`: `assignment_base` (UC),
`assignment_sync` (LB), `assignment_write` (LB), `assignment_app_changes` (UC), plus
`assignment_overlay` and `assignment_gold`. Columns are minimal — the two FK columns plus
control columns (`is_new`, `is_deleted`, `editor`, `updated_at`). Create a link = insert the
pair (`is_new=true`); unlink = `is_deleted=true` tombstone.

**Type C — Many-to-many *with* edge properties: the same pipeline as B, just wider.**
*Example: Employee ↔ Project, where the relationship carries `role` and `start_date` ("Jane is
lead on Apollo since 2025-01").* Physically **identical to Type B** — the same new
`assignment_*` pipeline — you just add the property columns (`role`, `start_date`) to those
tables. They ride the exact same edit/revert/refresh reconciliation as object properties.
(This is where Palantir would reach for a *third* pattern, the backing object type; we don't —
our Type-B pipeline already holds columns. See §1.)

### 2.3 Table-by-table impact, at a glance

| | Type A (FK) | Type B (M:N bare) | Type C (M:N + props) |
|---|---|---|---|
| New physical tables | **0** | **4** (`assignment_{base,sync,write,app_changes}`) | **4** (same) |
| New views | 1 traversal view | 2 (`_overlay`, `_gold`) | 2 (same) |
| Change to existing object tables | **+1 FK column** on all `employee_*` | none | none |
| Key | single FK value | pair `(from,to)` | pair `(from,to)` |
| Extra columns on the link tables | — | keys + control only | keys + control **+ edge props** |
| Reconciliation machinery | **reused as-is** | **cloned** | **cloned** |
| Prerequisite | target object type must exist | both endpoint object types must exist | both endpoint object types must exist |

**Net:** Type A is *"add a column to what you have."* Types B and C are *"stamp out the same
4-table pipeline again, keyed by a pair"* — C just has more columns than B. The UC↔Lakebase
storage model is only ever **reused** (A) or **cloned** (B/C); it never changes.

The rest of this doc is the build-level detail for the two shapes: **§3 = Shape A (Type A)**,
**§4 = Shape B (Types B and C)**.

---

## 3. Shape A — Foreign-key links (1:N, 1:1) — nearly free

A one-to-many link (e.g. **Employee → Department**) is a foreign-key **property on the many
side**. Under our data layer, an FK column is *just another property* and inherits the entire
reconciliation machinery. There is **no new table.**

### 3.1 What changes
1. Add the FK column to the object's every layer (BASE / `*_write` / `*_overlay` /
   `*_app_changes` / GOLD), e.g. `department_id STRING` on `employee_*`.
2. Stand up the **target** object type (`department_base`, `department_sync`, …) via the
   normal generalization path — a Department is a plain Object Type.
3. **Make the FK editable** so links can be created/changed/removed by the app.
   Extend the AppKit route validation in
   `app/ontology-object-demo/server/routes/lakebase/employee-routes.ts` with a
   distinct link-property schema (for example `department_id`), editable and
   revertable. Keeping link fields distinct from ordinary editable properties
   lets the app run link-specific validation — see §5.

### 3.2 Link operations map onto existing object ops
| Link op | Under Shape A |
|---|---|
| **Create link** Employee→Dept | App PATCH sets `department_id="dept-42"` |
| **Change link target** | App PATCH replaces it with `department_id="dept-99"` |
| **Remove link** | App revert endpoint NULLs the override so `COALESCE` falls back to BASE FK; a future unlink endpoint can represent explicit null |
| **Traverse** | Join in GOLD/overlay (§3.3) |

### 3.3 Traversal view
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
resolves to NULLs on the department side (dangling-link handling; §8).

**Effort: low.** No new pipeline. This alone demonstrates a one-to-many Link Type with
link edits, refresh resilience on the FK, and traversal.

---

## 4. Shape B — Many-to-many link table (the general case)

A many-to-many link (e.g. **Employee ↔ Project**, with link properties `role`,
`start_date`) is its **own backing dataset** — so it gets its **own full pipeline**, an exact
clone of the object pipeline, keyed by the **pair** `(employee_id, project_id)`.

> Shape B covers **both** Type B (bare M:N, keys only) and Type C (M:N with edge properties) —
> Type C simply adds property columns (`role`, `start_date`) to the same tables. This is the
> pipeline that subsumes Palantir's join-table *and* backing-object-type patterns (§1, §2.2).

### 4.1 UC `assignment_base` (upstream-owned source, CDF on) — mirrors `setup_ontology.py`
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

### 4.2 Lakebase `assignment_sync` (Continuous mirror, read-only) + `assignment_write`
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

### 4.3 `assignment_overlay` — FULL OUTER + COALESCE on the **pair**
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

### 4.4 `assignment_app_changes` (UC Delta, CDF) via the bridge MERGE on the pair
Same as `apply_bridge.py`, but the MERGE `ON` clause matches both key columns:
```sql
MERGE INTO <ns>.assignment_app_changes t
USING _write_src s
  ON t.employee_id = s.employee_id AND t.project_id = s.project_id
WHEN MATCHED AND (t.src_updated_at IS NULL OR s.updated_at > t.src_updated_at) THEN UPDATE SET …
WHEN NOT MATCHED THEN INSERT …
```

### 4.5 `assignment_gold` — reconciled link truth = BASE ⊕ APP_CHANGES
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

## 5. Link Actions (extends the action layer)

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

## 6. Palantir → Databricks mapping (links)

| Palantir | Databricks (this design) |
|---|---|
| Link Type, foreign-key backed (1:N / 1:1) | FK **property** on the object (`employee.department_id`), reconciled like any property; **no new table** |
| Link Type, join-table backed (M:N, bare) | Dedicated link pipeline `assignment_{base,sync,write,overlay,app_changes,gold}`, PK = `(from_id, to_id)`, keys only |
| Link Type, backing-object-type (M:N + edge props) | **Same** link pipeline, with edge properties as extra columns — no separate intermediary entity needed |
| Link properties (role, start_date) | Extra columns on the link table (same reconciliation) |
| Link instance identity | The FK value (Shape A) / the pair `(from_id, to_id)` (Shape B) |
| Create link (link edit) | `is_new=true` row in `*_write` (or set FK) |
| Delete / unlink (link edit) | `is_deleted=true` tombstone in `*_write` (or revert FK) |
| Edit link property | Sparse column override in `*_write` |
| Revert link property | NULL the override → BASE wins |
| Edit batch (objects + links atomic) | One Lakebase Postgres transaction spanning `employee_write` + `assignment_write` |
| Link traversal / navigate (resolved at query time) | JOIN across `*_gold` (BI) or `*_overlay` (hot path) |
| Backing dataset never touched by actions | `*_base` upstream-only; edits land in `*_write`→`*_app_changes` |
| "Drop all edits" | Truncate `assignment_write` + `assignment_app_changes` → fall back to BASE links |

---

## 7. Object Storage V2 — link capability fidelity

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

## 8. Referential integrity, cascades, dangling links

UC and Lakebase don't cascade; we model it at two layers (matching Foundry, which resolves
this in the object backend + Actions):

- **Create-time:** action layer rejects a link whose endpoint doesn't exist (§5).
- **Read-time / dangling links:** a link to a **deleted** object (endpoint tombstoned) must
  not surface as a live edge. Because each `*_gold`/`*_overlay` already excludes tombstoned
  rows, a traversal join (§3.3) naturally drops or NULLs the dead side. For strict hiding,
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
representation flagged in `IMPLEMENTATION_PLAN.md` §10. M:N unlink has no such ambiguity — it's a tombstone.

---

## 9. Phasing

| Phase | Work | Done when |
|---|---|---|
| **L0** | Shape A: add `department_id` FK to `employee_*`; stand up `department` object type; add `LINK_PROPERTIES` (editable+revertable); add endpoint-existence validation; traversal view | App can set/change/clear an Employee→Department link; it survives BASE refresh; traversal joins resolve |
| **L1** | Shape B: clone the pipeline for `assignment` (M:N Employee↔Project) with link props `role`/`start_date`; composite-PK overlay + bridge + GOLD | create/edit/revert/delete link work end-to-end; edits win over re-ingested link BASE |
| **L2** | Integrity: dangling-link filtering (semi-join GOLD); optional explicit delete-cascade in the action layer; atomic object+link edit batch in one Postgres txn | Deleted endpoints drop their edges; multi-edit actions are atomic |
| **L3** | Generalization: fold both shapes into the config-driven generator (`GENERALIZATION_PLAN.md`) — a `link` config emits the composite-key pipeline; an `fk_link` config emits an FK column + traversal view | A new link type is declared in config, not hand-DDL'd |

---

## 10. Gotchas (link-specific; object-side gotchas in `CONTEXT.md` §9 still apply)

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
- **FK-link NULL ambiguity** (§8) — don't ship a 1:N "unlink" until the explicit-NULL patch
  representation exists, or model that relationship as Shape B.
- **Route all Databricks work through the AI Dev Kit skills** (`databricks-core` +
  `databricks-lakebase` / `databricks-unity-catalog` / `databricks-pipelines` /
  `databricks-jobs`); always `--profile fevm-serverless`; never auto-select a profile.

---

## 11. Bottom line

Links require **zero new storage primitives**. Shape A (FK, 1:N/1:1) is *already* an object
property and rides the existing reconciliation for free once the FK is made editable. Shape B
(M:N, with or without link properties) is a **mechanical clone of the object pipeline keyed by
a pair**. Palantir's three backing patterns (FK, join-table, backing-object-type) collapse into
these **two shapes** for us, because our link table is a full pipeline that can carry its own
property columns. The only genuinely new engineering is **action-layer referential-integrity
validation** and **read-time dangling-link filtering** — the parts Palantir also handles above
the raw backing dataset. This confirms the object-side data layer was the hard part; links are a
parameterization of it.
