# Palantir Foundry Ontology & Object Types Architecture

## Executive Summary

Palantir Foundry's Ontology provides a semantic data layer that separates **source/backing data** from **user edits** through a dual-storage model. This architecture enables users to interact with and modify objects through Actions while preserving the integrity of upstream source data. The system has evolved from Object Storage V1 (using writeback datasets) to Object Storage V2 (using materialized datasets), with V2 offering dramatically improved performance and immediate edit visibility.

---

## 1. ONTOLOGY CONCEPTUAL STRUCTURE

### 1.1 Core Components

**Object Types** — The fundamental semantic entities representing real-world concepts (e.g., Employee, Customer, Order). Each Object Type:
- Has a **displayName** (user-facing) and **apiName** (programmatic identifier)
- Includes metadata: description, status (ACTIVE/INACTIVE), resource ID (RID)
- Defines a **primary key** for uniqueness
- Maps to underlying **backing dataset(s)** that hold instance data
- Contains multiple **properties** of various types

**Properties** — Typed attributes on an Object Type. Types include:
- **Primitive types**: string, integer, boolean, date
- **Struct types**: nested, structured data
- **Reference types**: typed links to other Object Types
- **Enumerations**: predefined value sets
- **Array types**: collections of primitives or objects
- **Media references**: pointers to media datasets/assets
- **Computed properties**: derived values (calculated from other properties)

**Link Types** — Semantic relationships between Object Types, enabling many-to-many and one-to-many relationships. Link Types are separate ontology resources with their own backing datasets.

**Action Types** — Operations that modify objects or links. Actions are the primary mechanism for user edits and writeback, with defined parameters and validation logic.

**Functions** — Executable logic (TypeScript/Python) that can be called by Actions to transform or validate data before writing edits.

### 1.2 Reuse Patterns

**Shared Property Templates (SPTs)** — Reusable property definitions that enforce consistent semantics across multiple Object Types (e.g., "CreatedBy" used on multiple entity types).

**Interfaces** — Contracts describing a set of properties and capabilities. Object Types can implement multiple interfaces, enabling polymorphic behavior in applications.

---

## 2. OBJECT TYPES & BACKING DATASETS

### 2.1 Backing Dataset Model

An Object Type is **logically backed by one or more datasets** that provide the instance-level data:

```
Backing Dataset (Spark/SQL table in Foundry)
    ↓
    ├─ Columns map to Object Type properties
    ├─ One row per object instance
    ├─ Primary key column(s) correspond to Object Type's primaryKey definition
    └─ Data is ingested/refreshed upstream (unchanged by user actions)

Object Type Definition
    ├─ Property mappings (dataset column → object property)
    ├─ Type information (string, integer, reference, etc.)
    ├─ Display names and descriptions
    └─ Actions (optional, define what users can modify)
```

### 2.2 Mapping: Dataset Columns → Object Properties

When you create an Object Type:
1. **Select backing dataset** from Foundry's dataset catalog
2. **Map columns** in that dataset to Object Type properties
3. Palantir's **Object Storage** indexing system ingests this data and makes it queryable via SQL, REST APIs, and UI

Example:
- Backing Dataset "employees_raw": columns `[emp_id, first_name, dept, salary]`
- Object Type "Employee": properties `[employeeId, firstName, department, salary]` (all sourced from backing dataset)

### 2.3 Querying Backing Datasets & Object Types

Via SQL, you can query Object Types using either their RID or API name:

```sql
-- By RID
SELECT employeeId, firstName, department
FROM `ri.ontology.main.object-type.<employee-rid>`
WHERE department = 'Engineering';

-- By API name
SELECT * FROM `ontologyApiName`.`objectTypeApiName`;
```

Both queries operate on the **indexed object data** stored in Object Storage.

---

## 3. CRITICAL: DATA LAYER & USER EDITS MODEL

This is the core architectural innovation enabling Foundry's application layer.

### 3.1 Problem Statement

**Challenge**: How to let users edit object data in UI-driven applications while preserving:
- **Source data integrity** — upstream datasets remain unchanged
- **Auditability** — track what was edited vs. sourced
- **Performance** — edits don't interfere with ETL/ingestion
- **Data refresh resilience** — when source data is re-ingested, edits don't corrupt

### 3.2 The Separation Architecture

Palantir achieves this through **dual storage**: **Source Storage (Backing Dataset)** and **Edits Storage (Materialized Dataset or Edits Layer)**.

```
┌─────────────────────────────────────────────────────────────┐
│                  FOUNDRY DATA LAYER MODEL                    │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  UPSTREAM:                                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Backing Dataset (Source Data, Read-Only from User)  │   │
│  │ Columns: [emp_id, first_name, dept, salary, ...]   │   │
│  │ Refreshed by: ETL pipelines (unchanged by actions)  │   │
│  └──────────────────────────────────────────────────────┘   │
│                            ↑                                  │
│                    Ingested upstream                          │
│                                                               │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  APPLICATION LAYER (Object Storage):                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │          Object Storage (Indexed)                    │   │
│  │  ┌────────────────────────────────────────────────┐ │   │
│  │  │ Backing Index (Source data, indexed for query) │ │   │
│  │  └────────────────────────────────────────────────┘ │   │
│  │                      +                              │   │
│  │  ┌────────────────────────────────────────────────┐ │   │
│  │  │ Edits Layer (User modifications, real-time)   │ │   │
│  │  │ Materialized Dataset (OSv2) or Writeback      │ │   │
│  │  │ Dataset (OSv1) stores ONLY edited properties  │ │   │
│  │  └────────────────────────────────────────────────┘ │   │
│  │                      ↓                               │   │
│  │         Materialized View (Source + Edits)          │   │
│  │         (What users see in the app)                 │   │
│  └──────────────────────────────────────────────────────┘   │
│           ↓                                      ↑            │
│   Applications (Workshop,          Actions (write edits)    │
│   OSDK, REST API)                                            │
│           ↓                                                   │
│   UI / User Decisions                                        │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 User Edits Storage Models

#### **Object Storage V1 (Legacy "Phonograph")**

Uses **Writeback Datasets**:
- **Purpose**: Stores only edited/user-entered fields, keyed by object primary key
- **Structure**: Sparse dataset with columns for edited properties only
- **Storage**: Separate Foundry dataset (write-enabled)
- **Visibility**: User edits are **eventually consistent** (may take time to propagate)
- **Schema Changes**: Breaking schema changes require manual migration of existing edits

**OSv1 Architecture:**
```
Backing Dataset (Source)           Writeback Dataset (Edits Only)
     emp_id  first_name  dept              emp_id  salary  (new_field)
     1       Alice       Eng        +      1       150k    custom_value
     2       Bob         Sales              2       120k    NULL
     3       Carol       HR                 ...     ...     ...
     
Display: Merge(Source, Edits) → show salary from Edits if present, else from Source
```

**Writeback dataset is enabled** by:
1. Creating an empty dataset with same schema as Object Type properties to edit
2. Setting that dataset as the "writeback dataset" in Object Storage configuration
3. Users need **edit permission** on writeback dataset to modify objects

#### **Object Storage V2 (Current Standard)**

Uses **Materialized Datasets** (optional):
- **Purpose**: Store edits and optionally materialize source + edits into a queryable dataset
- **Structure**: Can have multiple materialized datasets for subsets of properties
- **Storage**: Built-in Object Storage (no separate Foundry dataset required)
- **Visibility**: User edits are **immediately visible** after action completes
- **Edit Layer**: Persistent edit store within OSv2, keyed by object primary key
- **Performance**: Supports up to 10,000 objects per Action (vs. lower in OSv1)
- **Schema Migration**: Supports "drop all edits" instruction for breaking changes

**OSv2 Architecture:**
```
Backing Dataset (Source)           Object Storage V2 (Internal)
     emp_id  first_name  dept            ┌─ Edits Store (internal)
     1       Alice       Eng      +      │   emp_id → {salary: 150k, ...}
     2       Bob         Sales    +      │   1 → {salary: 150k, status: "edited"}
     3       Carol       HR       +      │   2 → {salary: 120k, status: "edited"}
     ...     ...         ...            │   ...
                                        └─ (Optional) Materialized Dataset
                                            emp_id  first_name  dept  salary
                                            1       Alice       Eng   150k
                                            2       Bob         Sales 120k
                                            3       Carol       HR    115k

Display: Merged view (Source + Edits) served directly from Object Storage
Visibility: Immediate after action
```

### 3.4 Reconciliation: How Source & Edits Merge

**Display Logic** (when user views an object):

```
FOR EACH property ON OBJECT:
  IF (property has user edit in Edits Layer):
    RETURN edit value
  ELSE IF (property exists in Backing Dataset):
    RETURN source value
  ELSE:
    RETURN NULL / default
```

**When Source Data Refreshes** (new ETL ingestion):

1. **Backing Dataset columns** are updated with new source data
2. **Edits Layer remains unchanged** — edits are persisted independently
3. **On next query/display**, the merge logic applies the above reconciliation
4. **User edits are never overwritten** — they take precedence over source
5. **If source column is deleted** → users lose source data for that property, but edits remain if they exist

**Example scenario:**
```
Day 1:
- Backing Dataset: salary = $100k
- User Action: Sets salary to $150k (edit stored in Edits Layer)
- Display: $150k (edit takes precedence)

Day 2: ETL refreshes salary in backing dataset to $105k
- Backing Dataset: salary = $105k  
- Edits Layer: still salary = $150k
- Display: $150k (edit still takes precedence)

Day 3: User deletes the edit (via "Delete edits" or undo action)
- Backing Dataset: salary = $105k
- Edits Layer: salary = (deleted)
- Display: $105k (now source data shows)
```

### 3.5 Object Storage V2: The "Drop All Edits" Feature

OSv2 provides a **schema migration framework** with explicit edit management:

- **Delete Edits Button** (Ontology Manager) — Discards all user edits for an Object Type and resets to backing dataset state
- **Use case**: After breaking schema change, optionally drop all old edits before reindexing
- For **OSv1**, removing the writeback dataset configuration is the equivalent workaround

---

## 4. ACTIONS & WRITEBACK MECHANISM

### 4.1 Action Type Definition

An Action Type defines:
- **Parameters**: Input fields (e.g., `employeeId`, `newSalary`, `approvalStatus`)
- **Validation Logic**: Pre-execution checks (via Functions or inline rules)
- **Writeback Logic**: Which objects/links to modify and which properties to update
- **Functions** (optional): TypeScript/Python code to execute complex logic
- **Return Type**: What to return after action completes (edits, validation errors, etc.)

### 4.2 Action Execution Flow

```
1. User triggers action in app (e.g., "Set Employee Salary")
   ↓
2. Provide parameters: {employeeId: "123", newSalary: 150000}
   ↓
3. Action validation executes (if defined)
   ↓
4. If validation passes → Execute writeback function
   ↓
5. Create Edit Batch:
   - Object Type: Employee
   - Primary Key: 123
   - Properties to update: {salary: 150000}
   ↓
6. Write Edit to Edits Layer (via Object Storage)
   ↓
7. For Object Storage V2: IMMEDIATE visibility
   For Object Storage V1: EVENTUAL consistency (may take seconds)
   ↓
8. Return result (success/validation errors)
   ↓
9. App displays updated object state (merged: source + edit)
```

### 4.3 Writeback Destination: Source or Edits?

**Actions write to the Edits Layer, NOT the backing dataset:**

- **Backing Dataset** — remains unchanged, owned by upstream ETL
- **Edits Layer** — receives Action modifications
- **Why**: Preserves separation of concerns; allows replaying ETL without data loss

This is the critical architectural principle: **Actions modify the Edits Layer, not source data.**

### 4.4 API: Apply Action

**REST API** (Ontologies API v2):
```
POST /api/v2/ontologies/{ontology}/actions/{action}/applyBatch
```

Key behavior difference between storage versions:
- **Object Storage V2**: Changes are **immediately visible** after action completes
- **Object Storage V1**: Changes are **eventually consistent** (may take time)

**Limitations**:
- Up to 20 actions in one batch (some restrictions)
- OSv2-only actions (no Functions): higher throughput possible
- OAuth2 scopes required: `api:ontologies-read api:ontologies-write`

### 4.5 Code Example: TypeScript v2 Edit Batch

```typescript
import { createEditBatch, Edits } from "@osdk/functions";
import { Aircraft, Employee } from "@ontology/sdk";
import { Client, Osdk } from "@osdk/client";

type OntologyEdit = Edits.Object<Aircraft> | Edits.Object<Employee>;

export default function myFunction(
    client: Client, 
    aircraft: Osdk.Instance<Aircraft>,
    employee: Osdk.Instance<Employee>
): OntologyEdit[] {
    const batch = createEditBatch<OntologyEdit>(client);
    
    // These writes go to the Edits Layer
    batch.update(aircraft, { businessCapacity: 3 });
    batch.update(employee, { department: "HR" });

    return batch.getEdits();
}
```

---

## 5. COMPUTED PROPERTIES & DATA RECONCILIATION

### 5.1 Computed Properties

Computed (or derived) properties are **calculated on-the-fly** based on other properties:

```
Property: "yearsOfService"
Definition: (currentDate - hireDate) / 365.25 days
Inputs: hireDate (from source or edits), currentDate (system)
Output: Integer calculated each time the object is queried
Storage: NOT stored; recalculated per read
```

### 5.2 Reconciliation Under Data Refresh

**Scenario**: A computed property depends on a source column that gets refreshed.

**Behavior**:
1. Backing dataset updated with new source column value
2. Computed property recalculated on next query using:
   - New source value (if no edit exists)
   - Or overridden edit value (if user edited the base property)
3. Computed property itself cannot be edited directly; edits apply to the base properties

**Example: High-Risk Employee Status**
```
Backing Dataset:
- salary (source): $100k → updates to $250k

Object Type Properties:
- salary (can be edited by user action)
- department (source only, not editable)
- isHighRiskEmployee (computed: salary > $200k)

Day 1: isHighRiskEmployee = false (salary $100k)
Day 2: ETL updates salary to $250k → isHighRiskEmployee = true (recomputed)
Day 3: User action edits salary to $180k → isHighRiskEmployee = false (recomputed)
```

### 5.3 Immutable Source Properties

Properties backed by source columns (not editable) remain read-only:

```
Backing Dataset: [emp_id, first_name, dept, salary, hire_date, ssn]

Object Type Properties:
✓ salary (editable via Action, map to backing dataset column)
✓ department (editable via Action)
✗ firstName (source-only, not editable)
✗ hireDate (source-only, not editable)
✗ ssn (source-only, not editable; may be hidden/masked)
```

---

## 6. MATERIALIZED DATASETS (OSv2)

### 6.1 What Are Materialized Datasets?

In Object Storage V2, a **Materialized Dataset** is an optional, queryable Foundry dataset that:
- Contains the **merged view** of source data + user edits
- Is materialized (persisted) for downstream consumption by transforms, SQL queries, etc.
- Can be created for subsets of properties (not all properties must materialize)
- Replaces the writeback dataset concept from OSv1

**Use Cases**:
- Feeding edited object data downstream to other pipelines
- SQL-based analytics over object data including user edits
- Exporting object state for external systems

### 6.2 OSv1 → OSv2 Migration: Writeback to Materialized

During migration from OSv1 to OSv2:
- **Writeback datasets become materialized datasets** (for backward compatibility)
- Existing user edits are **migrated to OSv2's internal edits store**
- Columns not mapped to object properties are **dropped**
- If you don't migrate a writeback dataset, it **becomes read-only static**

---

## 7. OBJECT STORAGE VERSIONS: V1 vs. V2

| Aspect | Object Storage V1 (Phonograph) | Object Storage V2 (Current) |
|--------|----------------------------------|---------------------------|
| **Edit Storage** | Writeback Datasets (external) | Internal Edits Store or Materialized Datasets |
| **Edit Visibility** | Eventually consistent (delayed) | Immediate (real-time) |
| **Edit Throughput** | Lower | Up to 10,000 objects/action |
| **Properties per Type** | Limited | Max 2000 properties/object type |
| **Indexing** | Standard ingestion | Incremental indexing (billions of objects) |
| **Schema Migrations** | Manual edit migration required | Built-in "drop all edits" framework |
| **Edit Latency** | Seconds to minutes | Milliseconds |
| **Granular Permissions** | Limited | Yes, per-object permissions |
| **Use** | Legacy instances | New standard |
| **Streaming Datasources** | Not supported | Supported (low-latency indexing) |

---

## 8. FOUNDRY DATA FLOW (End-to-End)

```
Source Systems
    ↓ (Connectors: API, database, files, etc.)
Foundry Datasets (Raw Data)
    ↓ (Transforms: Spark SQL, Python, etc.)
Clean/Curated Datasets
    ↓ (Ontology Indexing: Object Storage)
Ontology Objects (indexed)
    ├─ Backing Index (source data)
    └─ Edits Layer (user modifications)
    ↓ (Applications: Workshop, OSDK, REST API)
Applications / UI
    ↓ (User Decisions / Actions)
Actions Triggered
    ↓ (Writeback to Edits Layer)
Object State Updated (merged view)
    ↓
    ├─ Optional: Materialize to dataset
    └─ Display to user (edit visible immediately in OSv2)
```

---

## 9. KEY CONCEPTS & TERMINOLOGY

| Term | Definition | Source/Edits Role |
|------|-----------|-------------------|
| **Backing Dataset** | Foundry dataset providing source data for Object Type | Source |
| **Primary Key** | Unique identifier for each object instance | Merger key (how edits connect to source rows) |
| **Object Type** | Semantic entity with properties, backed by dataset(s) | Schema definition |
| **Property** | Typed attribute of an Object Type | Can be source-only or editable (source + edits) |
| **Writeback Dataset** (OSv1) | Dataset storing user edits only | Edits |
| **Materialized Dataset** (OSv2) | Queryable dataset with merged source + edits | Edits (optional materialization) |
| **Edits Layer** (OSv2) | Internal store of user modifications | Edits |
| **Action Type** | Definition of an operation that creates/modifies objects | Writes to Edits Layer |
| **Edit Batch** | Group of object/link modifications submitted as one transaction | Edits |
| **Computed Property** | Property calculated on-the-fly from other properties | Derived; reconciled at read time |
| **Link Type** | Semantic relationship between Object Types | Can be backed by dataset; editable |
| **Object Storage** | Indexing and storage infrastructure for ontology data | Manages both Source and Edits indices |
| **Merge Reconciliation** | Process of combining source and edits for display | Every read: edits take precedence if present |

---

## 10. SEPARATION PRINCIPLES

### 10.1 What Is Separated?

1. **Read Path (Queries)** → Reads both source and edits, merges at display time
2. **Write Path (Actions)** → Writes only to Edits Layer
3. **Source Data Path (ETL)** → Updates backing dataset; never touched by user actions
4. **Storage** → Backing dataset ≠ Edits storage (different systems/permissions)

### 10.2 Key Invariant

**User edits never modify the backing dataset.** This ensures:
- ETL can re-ingest source data without data loss
- Audit trail is clean (source ≠ edits)
- Applications can replay scenarios by toggling edits on/off
- Permissions can differ: source dataset read-only to users, but edit dataset write-enabled

---

## 11. OFFICIAL SOURCES

All information in this document is sourced from official Palantir Foundry documentation:

- **Object & Link Types Overview**: [palantir.com/docs/foundry/object-link-types/type-reference](https://www.palantir.com/docs/foundry/object-link-types/type-reference)
- **Object Type Metadata & Creation**: [palantir.com/docs/foundry/object-link-types/object-type-metadata](https://www.palantir.com/docs/foundry/object-link-types/object-type-metadata)
- **Object Backend & Storage V2**: [palantir.com/docs/foundry/object-backend/overview](https://www.palantir.com/docs/foundry/object-backend/overview)
- **User Edits & Materializations**: [palantir.com/docs/foundry/object-edits/how-edits-applied](https://www.palantir.com/docs/foundry/object-edits/how-edits-applied)
- **Materialized Datasets**: [palantir.com/docs/foundry/object-edits/materializations](https://www.palantir.com/docs/foundry/object-edits/materializations)
- **Allowing User Editing**: [palantir.com/docs/foundry/object-link-types/allow-editing](https://www.palantir.com/docs/foundry/object-link-types/allow-editing)
- **OSv1 → OSv2 Migration**: [palantir.com/docs/foundry/object-backend/osv1-osv2-migration](https://www.palantir.com/docs/foundry/object-backend/osv1-osv2-migration)
- **Apply Action API**: [palantir.com/docs/foundry/api/v2/ontologies-v2-resources/actions/apply-action-batch](https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/actions/apply-action-batch)
- **TypeScript v2 Edit Functions**: [palantir.com/docs/foundry/functions/typescript-v2-migration](https://www.palantir.com/docs/foundry/functions/typescript-v2-migration)
- **Foundry Platform Python SDK**: [github.com/palantir/foundry-platform-python](https://github.com/palantir/foundry-platform-python)
- **SQL Querying Objects**: [palantir.com/docs/foundry/sql-warehousing/ontology-sql](https://www.palantir.com/docs/foundry/sql-warehousing/ontology-sql)
- **Base Types & Properties**: [palantirfoundation.org/docs/foundry/object-link-types/base-types](https://palantirfoundation.org/docs/foundry/object-link-types/base-types)
- **Foundry Data Flow**: [palantir.com/docs/foundry/getting-started/foundry-platform-summary-llm](https://www.palantir.com/docs/foundry/getting-started/foundry-platform-summary-llm)

---

## 12. CLAIMS TRACEABILITY

| Claim | Source Type | Status |
|-------|-----------|--------|
| Object Types backed by datasets | Official docs + API reference | Verified |
| Edits stored separately from source | Official docs (object-edits) | Verified |
| OSv2 provides immediate edit visibility | Official docs (applyBatch API) | Verified |
| OSv1 uses writeback datasets | Official docs (allow-editing) | Verified |
| Edits write to Edits Layer, not source | Official docs (how-edits-applied) | Verified |
| OSv2 supports "drop all edits" migration | Official docs (osv1-osv2-migration) | Verified |
| Materialized datasets replace writeback | Official docs (osv1-osv2-migration) | Verified |
| Edit reconciliation via merge logic | Implied by API examples and edit batch patterns | Reasonable inference |
| User edits take precedence in display | Implied by writeback dataset design | Reasonable inference |

---

## 13. FREQUENTLY ASKED QUESTIONS

**Q: Can Actions modify source datasets directly?**
A: No. Actions write only to the Edits Layer. The backing dataset remains owned by upstream ETL and is read-only to the Ontology layer.

**Q: What happens when source data is refreshed?**
A: The backing dataset updates; existing user edits remain in the Edits Layer. On display, edits take precedence over source data via merge reconciliation.

**Q: How do I undo user edits?**
A: In OSv2, use the "Delete edits" feature in Ontology Manager to reset to backing dataset state. In OSv1, remove the writeback dataset configuration. Individual edits cannot be directly undone; additional edits (updates/deletions) must be applied.

**Q: Can I have multiple materialized datasets for one Object Type?**
A: Yes, in OSv2 you can create multiple materialized datasets for subsets of properties.

**Q: What's the difference between writeback and materialized datasets?**
A: Writeback (OSv1) stores user edits in a separate Foundry dataset. Materialized (OSv2) stores edits internally in Object Storage and optionally materializes the merged view to a queryable dataset.

**Q: How do computed properties handle source/edit reconciliation?**
A: Computed properties are recalculated on every read using current source data (if not edited) plus any edits. They cannot be edited directly.

**Q: Can I query edits separately from source data?**
A: In OSv2, if you materialize the edits to a dataset, yes. In OSv1, the writeback dataset is queryable separately. The Edits Layer itself in OSv2 is not directly queryable via SQL; it's accessed through the Ontology API.

---

**Document Version**: 1.0  
**Last Updated**: 2026-08-17  
**Coverage**: Palantir Foundry as of 2026 Q3
