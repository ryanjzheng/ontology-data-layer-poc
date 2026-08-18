# Generalization Plan — Object-Type Scaffolding Tool

**Audience:** an implementing agent with this repo and no prior chat context. Fresh-agent-implementable: decisions locked, spec formalized, artifacts enumerated.

**Read first:** `IMPLEMENTATION_PLAN.md` (the single-entity `employee` build — this tool *templatizes it*) and `CONTEXT.md` (architecture + V2 mapping).

**Goal:** turn the hand-built single-object-type data layer into a **generator**. Given a declarative spec for an object type (schema, PK, column classes, computed expressions, target catalog + target Lakebase), the tool scaffolds all the necessary UC tables, Lakebase objects, reconciliation notebooks/jobs, GOLD MV, action wiring, and grants — as files merged into a Databricks Asset Bundle you then `bundle deploy`.

**Target workspace:** `fevm-serverless` (`--profile fevm-serverless`, never auto-select). Skills: `databricks-dabs`, `databricks-lakebase`, `databricks-jobs`, `databricks-pipelines`, `databricks-unity-catalog`.

**Dependency:** the single-entity POC in `IMPLEMENTATION_PLAN.md` is the *reference implementation*. Build it first (or build it as the reference during Phase G1). The tool's correctness test is: **it regenerates that reference bundle from a spec.**

---

## 1. Locked decisions (with rationale + rejected alternative)

| Decision | Choice | Why / rejected |
|---|---|---|
| Input | **Declarative YAML spec, one per object type** | Version-controlled single source of truth; diffable; drives every artifact. **Rejected:** table-introspection as *primary* (needs the table first — kept as an optional `from-table` helper that emits a spec); Python builder (not declarative). |
| Generator | **Python + Jinja2 script that emits a DAB**, then `databricks bundle deploy` | Full control over the Lakebase/Postgres pieces DABs can't express. **Rejected:** native `bundle init` template (Go text/template — awkward for Lakebase, no logic); "both" (more moving parts than needed). |
| Scope | **One object type per invocation, repeatable** | Simplest v1; multi-object emerges from repeated runs + include-globs. **Rejected:** whole-ontology-in-one-config and Object+Link generation — deferred (§8). |
| Lakebase | **Assume the Database Instance exists; tool creates per-object `*_sync` + `*_write` + overlay** | Instance provisioning is slow, shared, not per-object. **Rejected:** tool provisions the instance (turnkey but wrong granularity); emit-scripts-only (too manual). |
| Generated vs generic | **Generate declarative artifacts (DDL, GOLD MV, YAML, grants, per-entity manifest); keep executable logic (apply-bridge, action layer, ETL simulator) as GENERIC modules parameterized by the manifest** | Minimizes generated code surface; one bridge/action codebase to maintain, not one per entity. |

---

## 2. The object spec (formalized)

`objects/<entity>.yaml`:
```yaml
entity: employee                     # object-type api name → table prefix
primary_key: employee_id             # string, or a list for composite keys
target:
  catalog: ontology_poc
  schema: object_layer
  lakebase_instance: ont-poc         # MUST already exist
  lakebase_database: ontology_poc
  lakebase_schema: public
apply:
  cadence_minutes: 2
  engine: notebook_merge             # notebook_merge | sdp
identities:
  app_sp:    <app-service-principal>
  bridge_sp: <bridge-service-principal>
  bi_group:  <bi-readers-group>
options:
  create_enabled: true
  pk_namespace: "app-"               # prefix for app-created object PKs
  seed_synthetic: true               # also generate ETL simulator + demo
properties:
  employee_id:  { type: string,        class: key }
  first_name:   { type: string,        class: source }
  department:   { type: string,        class: source }
  hire_date:    { type: date,          class: source }
  salary:       { type: decimal(12,2), class: editable }
  status:       { type: string,        class: editable }
  is_high_risk: { type: boolean,       class: computed, expr: "salary > 200000" }
```

**Column classes:** `key` (PK) · `source` (upstream-owned, writable only on create) · `editable` (user Action target) · `computed` (derived in GOLD only, never stored, not writable).

**Validation the generator MUST enforce (`scaffold validate`):**
- exactly one `key` (or a composite list); PK type known.
- every `computed` has `expr` and is excluded from BASE / `*_write` / `*_app_changes`.
- `expr` references only columns that exist in this entity (reject cross-entity/Postgres-only funcs — GOLD uses **Spark SQL** dialect).
- every non-computed `type` is in the type map (§4); unknown type → **hard error**, never silent.
- `lakebase_instance` presence is asserted at deploy time (tool does not create it).

---

## 3. What the generator emits (per entity)

Rendered into the bundle repo; file names namespaced by `<entity>` so repeated runs never collide:

| Artifact | Path | Templated or generic |
|---|---|---|
| BASE table DDL (+CDF) | `src/core/ddl/<entity>_base.sql` | **templated** (column list) |
| `*_app_changes` DDL (+CDF, full column set + control cols) | `src/core/ddl/<entity>_app_changes.sql` | **templated** |
| GOLD MV (FULL OUTER, per-column COALESCE, computed exprs, tombstone filter) | `src/core/ddl/<entity>_gold.sql` | **templated** |
| Lakebase `*_write` DDL | `src/core/lakebase/<entity>_write.sql` | **templated** (Postgres types) |
| Lakebase overlay view (FULL OUTER) | `src/core/lakebase/<entity>_overlay.sql` | **templated** |
| Synced Table (Continuous) config | `src/core/lakebase/<entity>_synced.json` | **templated** (BASE→`*_sync`) |
| Grants (§4 of the impl plan) | `src/core/ddl/<entity>_grants.sql` | **templated** (identities) |
| Per-entity **manifest** (table names, PK, editable/source col sets, lakebase conn) | `manifests/<entity>.json` | **templated** — drives the generic modules |
| Job: apply-bridge (+ETL sim if seeded) | `resources/<entity>.job.yml` | **templated** (params → generic notebook) |
| Pipeline (if `engine: sdp`) | `resources/<entity>.pipeline.yml` | **templated** |
| App action routes + UI | `app/<app>/server/routes/lakebase/<entity>-routes.ts` + page | **templated** (create/edit/revert/delete behaviors from impl-plan §7) |

**Generic (shipped once, NOT per-entity):**
- `runtime/apply_bridge.py` — reads `manifests/<entity>.json`, builds the MERGE column list dynamically, runs `*_write` → `*_app_changes`. Parameterized by job widgets.
- AppKit route helpers — generic create/edit/revert/delete SQL driven by the
  manifest (editable vs source-column enforcement, `pk_namespace`, tombstones).
  The Databricks App is the only action surface and writes only to `*_write`.
- `runtime/etl_simulator.py` — synthetic data from the spec's schema; re-runnable refresh mode.

**Bundle wiring:** root `databricks.yml` uses `include: [resources/*.yml]` so each new entity's job/pipeline is picked up automatically — the generator never rewrites hand-edited root config beyond ensuring that glob exists.

---

## 4. Type map (Spark/Delta ↔ Postgres)

| Spec type | Delta (UC) | Postgres (`*_write`) |
|---|---|---|
| string | STRING | TEXT |
| int / bigint | INT / BIGINT | INTEGER / BIGINT |
| double | DOUBLE | DOUBLE PRECISION |
| decimal(p,s) | DECIMAL(p,s) | NUMERIC(p,s) |
| boolean | BOOLEAN | BOOLEAN |
| date | DATE | DATE |
| timestamp | TIMESTAMP | TIMESTAMPTZ |
| struct / array / map | (as declared) | JSONB — **advanced, flag as deferred** |

Computed columns never map to Postgres (they exist only in GOLD).

---

## 5. Generator architecture

```
scaffold/                     # the tool (Python package)
  cli.py                      # scaffold gen|validate|from-table  [--out DIR] [--profile ...]
  spec.py                     # load + validate spec (pydantic/jsonschema)
  types.py                    # the §4 type map
  render.py                   # Jinja2 render of templates/ with spec context
  templates/                  # one Jinja template per §3 "templated" artifact
objects/                      # input specs (employee.yaml, asset.yaml, ...)
runtime/                      # the §3 generic modules (shipped as-is)
manifests/                    # generated per-entity manifests
resources/ src/ demo/         # generated bundle artifacts
databricks.yml                # root bundle (include: resources/*.yml)
```

CLI surface:
- `scaffold validate objects/<e>.yaml` — schema/type/expr checks, no output.
- `scaffold gen objects/<e>.yaml [--dry-run]` — render all §3 files (dry-run prints the plan).
- `scaffold from-table <catalog.schema.table> --editable ... --computed ...` — (optional helper) introspect a UC table into a starter spec.
- Deploy is manual and documented: `databricks bundle validate` → `databricks bundle deploy --profile fevm-serverless` → run the Lakebase post-deploy step (create `*_sync`, run `*_write`/overlay DDL). Optionally a `scaffold deploy` wrapper orders these.

---

## 6. Build order (phases for the tool)

| Phase | Work | Done when |
|---|---|---|
| **G0** | Formalize spec schema + `scaffold validate` (derive from `employee.yaml`) | `validate` accepts employee.yaml, rejects a malformed one |
| **G1** | Templatize the reference bundle — turn the hand-built `employee` artifacts into Jinja templates | `scaffold gen employee.yaml` reproduces the reference bundle (golden-file test) |
| **G2** | Extract the generic runtime modules (`apply_bridge`, `actions`, `etl_simulator`) + per-entity manifest | Reference `employee` runs off the generic modules, not per-entity code |
| **G3** | Type map + Postgres DDL gen + Synced-table config gen + computed-expr validation | `*_write`/overlay/synced generated correctly for employee |
| **G4** | CLI (`gen`/`validate`/`--dry-run`) + `include: resources/*.yml` merge | Two entities coexist in one bundle without clobbering |
| **G5** | **Second entity end-to-end** (`asset.yaml`: different cols/PK/computed) on `fevm-serverless` | asset pipeline passes all 7 behaviors; proves generalization |
| **G6** (opt) | `scaffold deploy` orchestration (bundle deploy + ordered Lakebase steps) | one command stands a new object up end-to-end |

---

## 7. Acceptance criteria (for the tool)

1. **Regeneration:** `scaffold gen employee.yaml` produces a bundle behavior-equivalent to the hand-built reference in `IMPLEMENTATION_PLAN.md` (golden-file diff on templated artifacts).
2. **Second entity:** a distinct spec (`asset.yaml` — different PK, columns, computed expr, targets) generates a deployable bundle that, once deployed, passes the **same 7 behaviors** from `IMPLEMENTATION_PLAN.md` §7 (edit, create, refresh-resilience, non-edited-refresh, revert, delete, BASE-immutability).
3. **Validation:** malformed specs (missing PK, unknown type, computed w/o expr, expr referencing a missing column) fail `scaffold validate` with a clear message — no partial generation.
4. **Idempotent regen:** re-running for the same entity overwrites only that entity's files; other entities and hand-edited root config are untouched.

---

## 8. Deferred (v2+ of the tool)

- **Whole-ontology config** (many object types in one file, one pass) — v1 is repeatable single-object.
- **Link Types** (relationship tables + link edits/actions) — deferred from the POC; adds a second generated shape.
- **Struct/array/map → JSONB** rich types.
- **Cross-entity computed properties / joins** in `expr`.
- **Tool-provisioned Lakebase instance** and **runtime (service) provisioning** — build-time file generation only for now.

---

## 9. Gotchas

- **Depends on the reference POC.** G1's golden test is meaningless until the single-entity build exists; build it first or in lockstep.
- **Lakebase isn't DABs-native.** `*_sync` (Synced Table), `*_write`, overlay, and grants run through `databricks-lakebase` CLI/SDK + Postgres DDL as a **post-`bundle deploy` step** — generated as scripts, executed by `scaffold deploy` or by hand. The instance must pre-exist (locked decision).
- **`databricks.yml` merge:** rely on `include: resources/*.yml`; never machine-rewrite the root file's hand-edited sections.
- **Type-map completeness:** unknown type = hard error. Don't silently pass an unmapped type to Postgres.
- **Computed expr dialect = Spark SQL** (evaluated in GOLD). Reject Postgres-only functions; computed props are GOLD-only, never in the overlay.
- **PK namespacing per entity** (`options.pk_namespace`) so app-created objects never collide with upstream.
- **Revert ≠ delete, no hot-path row deletes** — the generic `actions.py` must preserve this (see `CONTEXT.md` §9 / `IMPLEMENTATION_PLAN.md` §5).
- **Always pass `--profile fevm-serverless`.** Never auto-select a profile.

---

## 10. Open items (decide at implementation)

- **Manifest-driven generic notebook vs templated per-entity notebook** — plan favors generic + manifest (§1); if dynamic MERGE-column construction proves fiddly on serverless, fall back to a templated bridge per entity.
- **Composite primary keys** — spec allows a list; confirm the overlay/GOLD/MERGE templates handle multi-column joins (single-column is the common case).
- **`scaffold deploy` scope** — how much Lakebase orchestration to automate vs document (G6).
- **Spec validation library** — pydantic vs jsonschema (either; pick one and stay on it).
