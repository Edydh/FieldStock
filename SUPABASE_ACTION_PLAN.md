# Supabase Backend Action Plan

## Goal
Introduce Supabase as a supported backend for FieldStock without breaking the current SQLite-first workflow, and do it in a way that preserves inventory integrity, import safety, and compatibility-search behavior.

## Recommended Approach
Do not replace SQLite in one step.

Use a staged migration:
- Phase 1: define a backend abstraction and keep SQLite as the default
- Phase 2: stand up Supabase schema and parity tests
- Phase 3: support Supabase as an opt-in backend
- Phase 4: validate multi-user and hosted operation
- Phase 5: decide whether SQLite remains a supported local mode

Reason:
- The current codebase is written directly against `sqlite3.Connection`
- Inventory imports and stock adjustments depend on transactional correctness
- SQLite-specific features are present today, especially FTS5 and trigger-based maintenance
- A direct rewrite to Supabase would create too much regression risk in one slice

## Current Constraints
Current backend shape from [modules/db.py](c:/Dev/FieldStock/modules/db.py) and [db/schema.sql](c:/Dev/FieldStock/db/schema.sql):
- Direct `sqlite3` usage across the app and modules
- Local database file at `db/fieldstock.db`
- SQLite DDL, indexes, triggers, and FTS5 virtual table
- Inventory workflows assume local transactions with immediate consistency
- No auth, tenant separation, or remote secret management yet

Supabase impact areas:
- Postgres replaces SQLite
- Supabase auth and row-level security must be considered explicitly
- Full-text search must move from SQLite FTS5 to Postgres search or trigram indexes
- Connection handling, retries, and error reporting must tolerate network behavior

## Security And Login Position
Security becomes mandatory once FieldStock moves from local SQLite to a hosted Supabase backend.

That does not automatically mean full end-user login must ship on day one, but it does mean the migration must include a deliberate security model.

Required from the first hosted version:
- protected database credentials
- encrypted connections
- least-privilege backend access
- clear separation between application configuration and secrets
- audit awareness for writes such as imports and stock adjustments

Product decision to make explicitly:
- whether the first Supabase rollout is a shared internal app without named sign-in
- or a named-user app with per-user authentication from the start

Recommended phased position:
- Phase A: secure shared internal app with controlled backend credentials and trusted internal access
- Phase B: named-user login with Supabase Auth or equivalent identity provider
- Phase C: role-based or row-level restrictions only if operationally needed

Reason:
- A hosted database changes the threat model immediately, but mixing full auth redesign into the first backend migration can slow delivery and expand risk unnecessarily.

## Architecture Decision
Add a database access seam before adding Supabase behavior.

Target state:
- `modules/db.py` becomes backend selection and connection/bootstrap logic
- Data operations move behind repository or service functions instead of direct inline SQL in UI and workflow modules
- Backend choice is controlled by environment, for example `FIELDSTOCK_BACKEND=sqlite|supabase`
- SQLite remains the safe local fallback during migration

Reason:
- Without this seam, every workflow module will need parallel SQLite and Supabase rewrites at the same time.

## Execution Plan

### 1. Inventory all SQLite touchpoints
Deliverable:
- A written inventory of every module and function that executes SQL directly

Include:
- `modules/db.py`
- inventory import workflows
- search workflows
- compatibility import and search workflows
- any tests that assume `sqlite3.Row` or in-memory SQLite

Output should classify each query as:
- read-only lookup
- upsert
- transaction-sensitive write
- schema/bootstrap
- SQLite-specific feature

Reason:
- This defines the true migration surface and prevents missing high-risk transaction paths.

### 2. Define the backend abstraction
Deliverables:
- Backend interface or repository layer design
- Decision on implementation style: repository classes or grouped service functions

Minimum capabilities:
- initialize schema or verify backend readiness
- start transactional inventory import workflows
- upsert parts, locations, aliases, balances, transactions
- read inventory search results
- read and write compatibility/reference data
- support recent import-run and recent transaction reporting

Design requirement:
- UI code in `app.py` should stop depending on raw SQLite connection objects

Reason:
- This is the key refactor that makes Supabase support realistic.

### 3. Translate the schema from SQLite to Postgres
Deliverables:
- Supabase/Postgres schema SQL
- migration notes mapping SQLite behavior to Postgres behavior

Tables to port first:
- `parts`
- `locations`
- `inventory_balances`
- `inventory_transactions`
- `local_part_aliases`
- `import_runs`
- `reference_sources`
- `system_models`
- `reference_parts`
- `reference_part_aliases`
- `reference_part_attributes`
- `system_part_compatibility`

Key translation tasks:
- replace `INTEGER PRIMARY KEY AUTOINCREMENT` with Postgres identity columns
- convert SQLite `CURRENT_TIMESTAMP` defaults to Postgres equivalents
- recreate unique constraints and indexes
- replace FTS5 with Postgres full-text search or trigram strategy
- replace SQLite triggers with Postgres triggers only where necessary

Important note:
- The `parts_fts` table and related triggers are SQLite-specific and should not be copied verbatim.

### 4. Decide how Supabase will be accessed
Deliverable:
- One explicit backend-access decision

Options:
- Direct Postgres connection from Python
- Supabase Python client using REST/RPC
- Hybrid approach: SQLAlchemy or psycopg for core data, Supabase auth/storage only if needed later

Recommended default:
- Use direct Postgres access from Python for the first implementation

Reason:
- Current workflows are SQL-heavy and transaction-heavy. Direct SQL maps more naturally than forcing every operation through REST-style client calls.

### 5. Implement environment-based configuration
Deliverables:
- environment variable support
- config validation on startup
- clear error messages when backend configuration is incomplete

Minimum variables:
- `FIELDSTOCK_BACKEND`
- `SUPABASE_DB_HOST`
- `SUPABASE_DB_PORT`
- `SUPABASE_DB_NAME`
- `SUPABASE_DB_USER`
- `SUPABASE_DB_PASSWORD`
- optional SSL mode settings

Behavior:
- default to SQLite when variables are absent
- fail fast when Supabase is selected but configuration is incomplete

Security requirement:
- no Supabase credentials should be hard-coded in the repository or Streamlit UI

### 6. Refactor inventory workflows first
Deliverables:
- backend-agnostic implementation for the inventory truth slice

Prioritize:
- part upsert
- location upsert
- inventory balance writes
- transaction history writes
- import-run logging
- inventory search

Reason:
- Inventory truth is the highest-risk data path and should be stabilized before compatibility/reference features are migrated.

### 7. Refactor compatibility/reference workflows second
Deliverables:
- backend-agnostic reference import and compatibility search operations

Prioritize:
- reference source upserts
- reference part and alias upserts
- system model upserts
- compatibility link upserts
- reference search queries
- related compatibility queries

Reason:
- This slice has more breadth and search complexity, but lower operational risk than stock-balance mutation.

### 8. Replace SQLite search features with Postgres equivalents
Deliverables:
- search implementation notes
- Postgres indexes and query strategy

Candidates:
- Postgres full-text search for descriptions
- `pg_trgm` similarity or `ILIKE` plus indexes for part-number and alias lookup
- materialized or computed normalized columns where needed

Reason:
- Search quality and response time are core product value, and the current implementation depends on SQLite-specific FTS behavior.

### 9. Design the security model intentionally
Deliverables:
- backend credential model
- auth scope decision
- secret handling approach
- RLS policy plan

Questions to answer:
- Is this a single-company internal tool with one shared dataset?
- Will there be named users or only shared operator names?
- Will the Streamlit app connect directly to Supabase/Postgres, or should write operations move behind a safer service boundary later?
- Is row-level security needed in phase 1, or can access be limited to trusted internal credentials first?

Must be decided for phase 1:
- how secrets are stored in development and production
- who can perform imports and stock adjustments
- whether read-only access and write access need separate roles
- whether audit fields remain free-text operator names temporarily or map to authenticated users

Recommended first step:
- Start with one shared internal dataset and restricted backend credentials
- Add named-user auth only after data-path parity is proven unless compliance or audit requirements force it earlier

Reason:
- Mixing auth redesign with backend migration adds unnecessary risk.

### 10. Plan login and identity as a separate track
Deliverables:
- login recommendation for the first hosted release
- identity roadmap for later phases

Phase 1 recommendation:
- no mandatory end-user login if the app remains a trusted internal tool for a small controlled group
- capture operator name explicitly in the UI as an interim audit field

Phase 2 recommendation:
- add Supabase Auth for named users
- tie imports, adjustments, and administrative actions to real identities

Phase 3 recommendation:
- enforce role-based permissions such as admin, operator, and read-only user

Reason:
- Login is not only a technical choice; it changes support, onboarding, and audit workflows.

### 11. Build a one-time data migration path
Deliverables:
- export script from local SQLite
- import script into Supabase/Postgres
- row-count and checksum-style verification report

Migration should verify:
- table row counts
- normalized part uniqueness
- inventory balance totals
- transaction history counts
- compatibility link counts
- reference alias counts

Reason:
- A backend migration without deterministic verification is not safe for inventory data.

### 12. Expand the automated test strategy
Deliverables:
- backend-agnostic test coverage
- separate SQLite and Supabase test targets

Needed changes:
- decouple tests from in-memory SQLite assumptions
- introduce integration tests for Supabase/Postgres
- keep fast SQLite unit tests for local development
- add parity tests that run the same workflow against both backends

Reason:
- Backend parity needs executable proof, not manual confidence.

### 13. Run a staged rollout
Stages:
- developer-only Supabase testing
- clean environment validation
- one internal tester on hosted backend
- small-group pilot
- broader adoption decision

Success criteria:
- imports complete without data drift
- stock adjustments remain correct under concurrent use
- search remains acceptably fast
- operator support burden stays reasonable

## Risks
- Transaction semantics may drift between SQLite and Postgres if workflows are not refactored carefully
- Search behavior may regress when moving from SQLite FTS5 to Postgres search
- Supabase network latency will expose assumptions that were safe in local SQLite
- Mixed local and hosted modes can create confusion about the source of truth
- Auth and RLS can stall delivery if they are overdesigned too early
- Security gaps can be introduced if the app reaches Supabase with overprivileged credentials or unclear secret handling
- Delaying the login decision too long can create rework in audit fields, permissions, and operator workflows

## Suggested Milestones

### Milestone 1: Backend design complete
- SQL touchpoint inventory finished
- backend abstraction agreed
- Supabase access method chosen
- config model defined
- security model defined for the first hosted release

### Milestone 2: Inventory truth on Supabase
- schema created in Supabase/Postgres
- inventory import, adjustment, and search run against Supabase
- parity tests passing for core inventory workflows

### Milestone 3: Compatibility workflows on Supabase
- reference import and compatibility search run against Supabase
- EMC and HardDrivesDirect regression cases still pass
- source and alias data remain queryable and correct

### Milestone 4: Hosted pilot
- one internal environment configured
- backup and restore procedure documented
- tester validation completed

### Milestone 5: Security and identity hardening
- first hosted security model implemented
- secrets managed outside the repository
- login decision executed or explicitly deferred by design
- permissions and audit approach documented

## Definition Of Done
- FieldStock can run against either SQLite or Supabase by configuration
- core inventory workflows behave identically across both backends
- compatibility/reference workflows behave identically across both backends
- migration from existing local SQLite data is documented and verified
- operator setup and support documentation are updated for hosted mode
- security responsibilities for hosted mode are documented
- login and permissions are either implemented or explicitly deferred with a documented interim model

## First Task
Create a database migration design note that inventories every direct SQL touchpoint and proposes the backend abstraction boundary before writing any Supabase-specific code.