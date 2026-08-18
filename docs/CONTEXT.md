# Ontology Data Layer POC — Architecture & Context

**Project:** `ontology-data-layer-poc`
**Goal:** Prove out the *data / storage layer* that gives Databricks a Palantir-Foundry-style **Object Type** capability — specifically the interplay between **Unity Catalog (UC)** and **Lakebase** — with a full end-to-end demo on the **`fevm-serverless`** workspace.
**Target workspace:** `https://fevm-serverless-stable-vfpvf8.cloud.databricks.com` (profile `fevm-serverless`)
**Status:** Context / design captured. No Databricks resources built yet.
**Companion doc:** `PALANTIR_FOUNDRY_ONTOLOGY_ARCHITECTURE.md` (full Palantir deep-dive, in this repo).

---

## 0. TL;DR — what we're building and why

We are replicating Palantir Foundry's **Object Type data layer** on Databricks. The single hardest thing Foundry gets right is the **strict separation of upstream source data from user edits**, reconciled at read time with edits taking precedence. That separation is *the* reason "just use Lakebase" is wrong.

**The pattern (canonical):** Upstream owns a UC **BASE** table (read-only from the app). The app writes user edits **only** to a separate change log. The reconciled "current truth" is a UC **GOLD/MV** = `BASE ⊕ APP_CHANGES`, with edits winning per primary key. Lakebase is the **hot OLTP overlay** that makes the interactive app fast; it is *not* the source of truth. The app can also **create net-new objects** upstream never had, so reconciliation is a **FULL OUTER** merge (`COALESCE` per column, edit wins). An edit is undone by **reverting** the override (falls back to BASE) — distinct from **deleting** the object (a tombstone that hides it).

```
Upstream ETL ─► UC BASE ──(Synced Continuous)──► Lakebase *_sync (read-only mirror)
                  │                                        │
                  │                             app reads: *_sync ⊕ *_write (live wins)
                  │                                        │
                  │                             app writes: ONLY *_write  ◄── Action layer
                  │                                        │
                  │                     Lakebase *_write ──(APPLY CHANGES)──► UC *_app_changes  (app-owned Delta, NEVER BASE)
                  │                                                                │
                  └────────────────────► UC GOLD / MV = BASE ⊕ APP_CHANGES ◄───────┘  (BI / SQL / analytics)
```

---

## 1. The mental model we're replicating: Palantir Ontology & Object Types

(Full detail in `PALANTIR_FOUNDRY_ONTOLOGY_ARCHITECTURE.md`. Condensed here.)

- **Ontology** = a semantic layer over data: **Object Types** (e.g. Employee, Customer), **Link Types** (relationships), **Properties** (typed attributes), **Action Types** (operations that mutate objects), **Functions** (validation/transform logic).
- **Object Type ↔ Backing Dataset:** each Object Type is backed by one or more datasets. Columns → properties, one row per object instance, keyed by a primary key. The backing dataset is refreshed by **upstream ETL and is never touched by user actions.**
- **The data-layer core — dual-storage separation:**
  - **Backing / source layer** — immutable from the app, owned by upstream pipelines.
  - **Edits layer** — sparse store of user modifications only (Object Storage V2's internal edit store, or the legacy V1 "writeback dataset"). Keyed by object PK. Stores only changed properties.
  - **Reconciliation on read:** for each property, *if an edit exists → edit value; else → source value.* **Edits always win.**
- **Actions = writeback:** an Action validates inputs then writes an *edit batch* **exclusively to the edits layer** — never the backing dataset. This is enforced by discipline/architecture, not a DB constraint.
- **Refresh resilience (the key scenario):** when upstream re-ingests the backing dataset, edits persist untouched and continue to take precedence on the next read. Deleting an edit reverts that property to the (possibly newly-refreshed) source value.
- **Computed properties:** derived on read from other properties; not stored, not directly editable; automatically reflect whichever base value won reconciliation.
- **"Drop all edits":** schema-migration escape hatch — discard the edits layer and fall back to pure source.

**Why this matters for us:** these five behaviors (separation, sparse edits, reconcile-on-read with edit precedence, refresh resilience, drop-all-edits) are exactly the acceptance criteria for our data layer. If our Databricks build reproduces them, we have an Object-Type-grade storage layer.

---

## 2. Why not "just Lakebase"? (the central design question)

Lakebase (managed OLTP Postgres) is excellent for the *app*: low-latency reads and fast concurrent writes with row locks. But it cannot be the whole data layer:

| Requirement | Lakebase alone | Why UC is needed |
|---|---|---|
| Source of truth for the *original* object | ✗ divorced from upstream | UC BASE is the lakehouse system of record, owned by upstream ETL |
| Upstream re-ingestion without clobbering edits | ✗ single store → source refresh and edits collide | Separation: BASE (upstream) vs APP_CHANGES (app) reconciled at read |
| BI / SQL / analytics / lineage / governance | ✗ OLTP, not a lakehouse | UC GOLD/MV serves analytics; UC gives lineage, grants, sharing |
| Auditability of "sourced vs edited" | ✗ overwrites lose provenance | APP_CHANGES is an explicit, queryable edit log |
| Interactive app latency | ✓ (this is Lakebase's job) | — |

**Conclusion:** UC and Lakebase play *different* roles. **UC = system of record + serving surface** (BASE for original object, GOLD/MV for reconciled truth). **Lakebase = hot overlay** that makes the app fast (a read mirror of BASE plus a small write buffer). Neither alone is sufficient — the POC is precisely about the interplay.

---

## 3. The Databricks architecture (adopted pattern)

We adopt the canonical pattern from **Doc 2 ("UC ↔ Lakebase Hybrid Architecture")**: *UC BASE + `*_app_changes` + APPLY CHANGES + MV/GOLD.* This maps 1:1 to Palantir's never-touch-the-source principle. (Doc 1's older "Lakebase-write → MERGE into a single UC table" hybrid is prior art and a simpler variant, but it conflates source+edits in one UC table and is **not** used here — see §7.)

### The ①→⑥ flow (same for every Object Type)

| # | From → To | What happens |
|---|---|---|
| ① | Upstream Engine → **UC BASE** | Upstream ETL loads the protected source table (CDF enabled) |
| ② | UC BASE → **Lakebase `*_sync`** | Synced Table (Continuous) — read-only Postgres mirror of BASE |
| ③ | Actor ↔ **Action layer / app** | User / UI / agent reads + writes back |
| ④ | app ↔ **Lakebase** | **Read:** overlay `*_sync ⊕ *_write` (live write wins). **Write:** only into `*_write` |
| ⑤ | Lakebase `*_write` → **UC `*_app_changes`** | APPLY CHANGES into a *new* app-owned Delta table — **never BASE** |
| ⑥ | BASE ⊕ APP_CHANGES → **UC MV / GOLD** | Reconciled latest view for BI / SQL |

### Reconciliation precedence (GOLD/MV + overlay — FULL OUTER, `COALESCE` per column)
1. If the edit row has a tombstone (`is_deleted = true`) → **object hidden** (works for upstream-backed *and* app-created objects).
2. Else, **per column:** a non-NULL override in the edit store → **edit wins**; NULL (never edited, or *reverted*) → **BASE wins**.
3. App-created objects (no BASE row) are surfaced by the FULL OUTER join — the edit store carries every column.

**Revert ≠ delete:** reverting an edit NULLs the override (→ BASE value); deleting the object sets the tombstone (→ hidden).

```sql
SELECT /* coalesce by PK, edit precedence */
FROM base b
FULL OUTER JOIN app_changes a ON b.<PK> = a.<PK>
WHERE coalesce(a.is_deleted, false) = false;
```

### Latencies
- UC → Lakebase (`*_sync`): Synced Continuous, ~15s+ (needs CDF on BASE).
- Lakebase → UC (`*_app_changes`): Triggered APPLY CHANGES every 1–2 min to start (notebook MERGE bridge acceptable first, upgrade to Triggered SDP APPLY CHANGES later).
- App read: immediate (Lakebase overlay). GOLD/BI: lags by the APPLY CHANGES cadence — this mirrors Foundry OSv2 (app-immediate) vs materialized-dataset (lagged).

---

## 4. Palantir → Databricks concept mapping

| Palantir | Databricks (this POC) |
|---|---|
| Backing dataset (source, upstream-owned) | **UC BASE** table (CDF on), read-only from app |
| Edits layer / writeback (sparse edits **+ created objects**) | **Lakebase `*_write`** (hot) → **UC `*_app_changes`** (Delta, app-owned); full column set so app-created objects carry their own source values |
| Merge reconciliation (edits win) | **UC GOLD/MV** = `BASE ⊕ APP_CHANGES` (precedence rules) + Lakebase overlay read (live wins) |
| Object Storage V2 immediate edit visibility | Lakebase overlay read gives the app immediate visibility |
| Action Type / writeback (edits-only) | **Lightweight action layer** — writes only to Lakebase `*_write` |
| Edit batch | Postgres transaction into `*_write`; batched APPLY CHANGES into `*_app_changes` |
| Object primary key | `<PK_COLS>` shared across BASE / `*_write` / `*_app_changes` |
| Revert an edit (fall back to source) | NULL the override column in `*_write` / `*_app_changes` → `COALESCE` picks BASE |
| Delete an object | `is_deleted` tombstone in `*_write` / `*_app_changes`, honored in overlay + GOLD |
| Create a net-new object | App `POST /api/employees` → new row in `*_write`, `app-<uuid>` PK, `is_new=true`; FULL OUTER surfaces it |
| Computed property | Derived column in the GOLD/MV definition |
| "Drop all edits" | Truncate `*_write` + `*_app_changes` (fall back to BASE) |

---

## 5. POC scope & concrete deliverables (on `fevm-serverless`)

**Objective:** one Object Type (**`employee`**), end-to-end, showing upstream refresh, user edits, **object creation**, revert, and delete all coexisting correctly — served to BI via GOLD and to the app via the Lakebase overlay.

Build (concrete DDL + steps in `IMPLEMENTATION_PLAN.md`):

1. **UC catalog + schema** on `fevm-serverless` — catalog/schema TBD (see §8). Holds BASE, APP_CHANGES, GOLD/MV.
2. **UC BASE table** — the "original object", CDF enabled, populated by a simulated upstream ETL job (synthetic data). This is the source of truth for the original object.
3. **Lakebase instance** — Postgres database on `fevm-serverless`, holding:
   - `*_sync` — Synced Table (Continuous) mirror of BASE (read-only).
   - `*_write` — app change log; **full column set** (same PK, `is_new`, `is_deleted`, editable + source-class columns, `updated_at` sequence column).
4. **UC `*_app_changes`** — app-owned Delta table (CDF on), the durable edits layer.
5. **Reconciliation logic (jobs + notebooks) — the heart of the POC:**
   - Notebook/job: **upstream ETL simulator** that refreshes BASE (to demo refresh resilience).
   - Notebook/job: **`*_write` → `*_app_changes`** (APPLY CHANGES, or notebook MERGE bridge first) on a 1–2 min trigger.
   - Notebook/job or MV: **GOLD = BASE ⊕ APP_CHANGES** with precedence.
6. **Databricks App action layer** — the Object Storage Lab reads the overlay
   (`*_sync ⊕ *_write`) and writes **only** to Lakebase `*_write` through
   AppKit routes for create, edit, revert, and delete. It is the sole action
   surface; there is no parallel Python action SDK or CLI.

**App demo (acceptance):**
- App edits a property → appears immediately in overlay read → flows to `*_app_changes` → wins in GOLD.
- App **creates** a net-new object (`app-…`) → immediate in overlay → flows to `*_app_changes` → appears in GOLD, never in BASE.
- Upstream ETL refreshes BASE for an edited row → edit still wins in GOLD (refresh resilience).
- Upstream refreshes a *non-edited* row → new source value shows in GOLD.
- **Revert** an edit → GOLD reverts to the BASE value (edit undone).
- **Delete** an object (tombstone) → object hidden from overlay + GOLD (upstream-backed *and* app-created).
- **BASE is never modified by the app** — verify.

---

## 6. Build order (phased)

Mirrors Doc 2 §4. Each phase has a clear "done when".

| Phase | Work | Done when |
|---|---|---|
| 0 | UC catalog/schema; BASE table + CDF; ETL simulator loads BASE; Synced Continuous → `*_sync`; app SELECT grant | App can read the object graph from Lakebase |
| 1 | `*_write` DDL (full column set); FULL OUTER overlay (`*_sync ⊕ *_write`, per-column); app DML grants | create + edit + revert + delete work through the action layer |
| 2 | Create `*_app_changes`; point APPLY CHANGES (or notebook MERGE) at it — **not BASE** | Dry-run then live apply succeeds |
| 3 | Build GOLD/MV = BASE ⊕ APP_CHANGES (FULL OUTER, per-column, tombstones filtered) | BI reads GOLD; BASE unchanged by app |
| 4 | (Optional) monitor/archive `*_write` — must **not** break propagation (never hot-path-delete rows) | Write-table size monitored |
| 5 | (Optional) upgrade notebook MERGE → Triggered SDP APPLY CHANGES | Lag SLA met |

---

## 7. Decisions & rationale

- **Adopt Doc 2's pattern, not Doc 1's.** Doc 1 merges Lakebase writes *into a single UC table*, which is fine when the app owns all the data but conflates source + edits. The user's framing ("user edits vs upstream data, hence not just Lakebase") requires Palantir-style separation → Doc 2's `*_app_changes` + never-touch-BASE is the correct model. Doc 1 kept as prior art / fallback for app-owned entities.
- **UC = source of truth + serving; Lakebase = hot overlay.** Rationale in §2.
- **Edits are sparse and app-owned end to end.** `*_write` (hot) → `*_app_changes` (durable). BASE is upstream-only.
- **Reconcile on read, edits win.** Matches Foundry exactly; avoids destructive merges into source.
- **Per-column FULL OUTER reconciliation; the app creates objects too.** Edits are sparse column overrides (`NULL` = not overridden); app-created objects (namespaced `app-<uuid>` PK, `is_new=true`) live entirely in the edit store. This removes the whole-row shadow-copy step.
- **Revert ≠ delete.** Revert = NULL the override (→ BASE); delete = tombstone (→ hidden). Both are state changes, never hot-path row deletions (else propagation breaks).
- **Audit via Delta CDF on `*_app_changes`** — the change feed is the who/when/before→after trail (no separate journal for v1).
- **Start with a notebook MERGE bridge for ⑤, upgrade to Triggered SDP APPLY CHANGES later.** Faster to stand up; APPLY CHANGES is the target for lag/scale.
- **Keep the action layer minimal.** POC value is the storage/reconciliation layer, not UI polish.

## Rejected alternatives (do not re-litigate)
- **Lakebase-only store** — rejected: no lakehouse source-of-truth, breaks refresh resilience, no BI/lineage. (This is the whole point of §2.)
- **App MERGE into UC BASE** — rejected: dual-writes the same physical table that Synced Continuous mirrors, destroys "source of truth", violates Palantir separation.
- **UC-only (no Lakebase)** — viable for low write concurrency but loses the fast interactive overlay; not what we're demoing.

---

## 8. Decisions (locked) & genuinely-open items

These are now **locked in `IMPLEMENTATION_PLAN.md` §1**: entity `employee`;
existing UC catalog + `object_layer`; smallest Lakebase project (DB
`ontology_poc`) + dedicated Databricks App SP; AppKit-only action surface;
notebook-MERGE bridge first (SDP later); **record creation supported**; Link
Types deferred (§10 of the plan). Do not re-open them here.

**Genuinely open** (decide at implementation — see `IMPLEMENTATION_PLAN.md` §9): Lakebase instance size/region flags; Lakebase read method in the bridge (JDBC vs SDK); GOLD as Materialized View vs plain view.

---

## 9. Gotchas

- **CDF must be enabled on BASE** for Synced Continuous (②) and for APPLY CHANGES sourcing.
- **Never let the app or sync job write BASE.** Enforced by grants (app SP: no MODIFY on BASE) + discipline.
- **Revert ≠ delete, and neither removes a `*_write` row.** Revert = NULL the override column (→ BASE wins); delete = `is_deleted=true` tombstone (→ object hidden). Both are *state changes*; physically deleting the `*_write` row strands the stale value in `*_app_changes` and breaks propagation.
- **No shadow-copy** — the per-column FULL OUTER overlay makes editing a sync-only row a plain sparse insert.
- **App-created PKs must be namespaced** (`app-<uuid>`) so upstream re-ingestion can never collide with an app-created object.
- **Source-class columns are create-only** — the action layer rejects overrides of upstream-owned columns (`first_name`/`department`/`hire_date`) on existing objects.
- **Orphaned edits on schema change / PK change:** edits keyed to a dropped column or changed PK become unrenderable — plan schema evolution; "drop all edits" is the escape hatch.
- **Graph/BI lag:** GOLD only reflects edits after the next APPLY CHANGES run; app overlay is immediate. Document the SLA for BI consumers.
- **Computed properties recompute on read** — if expensive, materialize into GOLD columns.
- **Route all Databricks work through the AI Dev Kit skills** (`databricks-core` + `databricks-lakebase`, `databricks-jobs`, `databricks-pipelines`, `databricks-unity-catalog`, `databricks-apps`). Always pass `--profile fevm-serverless`; never auto-select a profile.

---

## 10. Key entry points (for the next agent)

- `PALANTIR_FOUNDRY_ONTOLOGY_ARCHITECTURE.md` — full Palantir reference (this repo).
- Doc 1 (source): *Hybrid Lakebase Write + Unity Catalog Read/Sync* — `docs.google.com/document/d/1xjUldl08tmRqdjKC1WX0it3obdygXz26qoHoar9rBVo` (prior-art variant).
- Doc 2 (source, **canonical pattern**): *UC ↔ Lakebase Hybrid Architecture* — `docs.google.com/document/d/1ZAomvqIKRktsm1AdeF6tsFe2_NWYV-VePnBf8mX41K0`.
- Target workspace: `fevm-serverless` profile → `https://fevm-serverless-stable-vfpvf8.cloud.databricks.com`.

---

## 11. Object Storage V2 capability mapping

We model Palantir **Object Storage V2**, not V1. V1 (Phonograph) uses an *external writeback dataset* with eventual consistency (1–5 min) and manual edit migration — strictly worse, and closer to a naive design than ours. Our architecture reproduces V2's three defining traits: a **separate internal edit store** (not the backing dataset), **immediate edit visibility**, and a built-in **"drop all edits"** reset.

**Framing:** V2 is a closed managed service. This is a mapping of *observable behaviors* to Databricks primitives — a behavioral reconstruction, not a reimplementation of V2 internals. It is valid at the **storage / data-layer level only** (see "not in scope" below).

| Object Storage V2 capability | Databricks mechanism (this POC) | Fidelity |
|---|---|---|
| Internal **edit store** — sparse, keyed by PK, not the backing dataset | Lakebase `*_write` (hot) + UC `*_app_changes` (durable) | ✅ faithful |
| **Backing index** — source indexed for query | UC BASE (Delta, CDF) + Lakebase `*_sync` mirror | ✅ faithful |
| **Merge reconciliation** — edits win | Overlay read (`*_sync ⊕ *_write`) + GOLD (`BASE ⊕ APP_CHANGES`) | ✅ faithful |
| **Immediate edit visibility** (<100ms object read) | Lakebase overlay read (live write wins) | ✅ faithful |
| **Materialized datasets** — async queryable source+edits | UC GOLD / MV | ✅ faithful |
| **Apply Action** — writeback to edits only | Action layer writes **only** to `*_write` | ✅ faithful |
| **"Drop all edits"** reset | Truncate `*_write` + `*_app_changes` → fall back to BASE | ✅ faithful |
| Computed properties (recompute on read) | Derived columns in GOLD/MV | ✅ faithful |
| Deletes / edit removal | `is_deleted` tombstones honored in overlay + GOLD | ✅ faithful |
| **Incremental indexing** (billions of objects) | CDF + Synced Continuous + incremental MERGE / APPLY CHANGES | ~ approximate (batch-incremental, not a live index) |
| **Streaming datasources** (low-latency indexing) | Structured Streaming / Zerobus into BASE | ~ approximate (not wired in v1) |
| High **edit throughput** (10k objects/action) | Batched `*_write` txns + batched apply | ~ approximate (untested at scale) |
| **Granular per-object (row-level) permissions** | UC row filters / column masks + Lakebase Postgres RLS | ⚠ gap — must be explicitly modeled; not native/automatic |
| Max ~2000 properties per type | Wide Delta tables | n/a (not a real constraint) |

**Strongest validation of the mapping:** V2 exposes *two* read surfaces — an **immediate object read** and an **async materialized dataset**. Our split reproduces that duality exactly: **Lakebase overlay = immediate object read**, **UC GOLD/MV = materialized dataset (lagged)**. This is why the mapping is principled, not forced.

**Honest divergences:**
- **One logical edit store, two physical tiers.** V2 keeps edits in a single internal store; we split into Lakebase `*_write` (OLTP speed) + UC `*_app_changes` (lakehouse governance). Deliberate, but a divergence.
- **Reconcile timing.** V2's object read is always live-merged; our GOLD is periodically materialized. We match V2's immediacy only on the overlay path.
- **Per-object security** is native in V2; for us it is a build task, not a freebie.

**Not in scope (V2 is only the storage substrate).** The rest of the Ontology sits *above* storage and is out of scope for this data-layer POC: the object-type registry / property-mapping metadata, API names & RIDs, the OSDK, the Functions runtime, the Actions validation engine, and Link Types. This mapping does **not** claim to reproduce the full Ontology.

---

## 12. References
- Palantir Foundry docs: object/link types, object backend (Object Storage V2), object edits & materializations, apply-action API (see `PALANTIR_FOUNDRY_ONTOLOGY_ARCHITECTURE.md` §11 for exact URLs).
- Databricks: Unity Catalog (CDF), Lakebase Synced Tables (Continuous), Lakeflow Declarative Pipelines (APPLY CHANGES), Databricks Apps.
