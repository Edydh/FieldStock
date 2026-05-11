# Action Plan

## Goal
Move FieldStock from a developer-driven MVP into a tester-ready internal tool with safer daily operation, cleaner compatibility imports, and a repeatable local rollout path.

## Current State
Already working:
- Excel snapshot imports with preview, balance-zero visibility, and import-run history
- Inventory search and stock adjustment workflows
- Compatibility reference import from website, saved HTML, saved PDF, and manual rows
- HardDrivesDirect product-detail import, including product URL shortcut in the UI
- EMC-style reference parsing with aliases, attributes, and compatible-system extraction

Still missing or weak:
- Fast startup for non-developers
- Operator-facing testing and reset instructions
- Broader validation on a clean machine
- Better visibility into imported reference details and import failures
- A clearer path from imported compatibility data to trusted alternate suggestions

## Recommended Priority
Stay laptop-first until one internal tester can run the app, import inventory, and use compatibility search without developer intervention.

Reason:
- Lowest support risk while the schema and import flows are still evolving
- Easier to debug local SQLite issues than early shared-server issues
- Recent compatibility import work adds value, but the distribution path is still the bottleneck

## Next Steps

### 1. Create local launch scripts
Deliverables:
- `launch.ps1`
- `launch.bat`

Behavior:
- Create `.venv` if missing
- Install `requirements.txt` if dependencies are missing
- Start Streamlit with the project interpreter
- Print the local URL and basic troubleshooting hints

Reason:
- This removes the current setup friction for testers and makes the app runnable without manual terminal steps.

### 2. Create tester documentation
Deliverable:
- `TESTING.md`

Content:
- Prerequisites
- First-run setup
- How to start the app
- How to import an inventory snapshot
- How to import a HardDrivesDirect listing URL or product URL
- How to import saved HTML when a site blocks direct fetches
- How to reset the local database safely
- Known limitations and what feedback to capture

Reason:
- The compatibility import flow is now broad enough that operators need explicit instructions to avoid unsupported usage patterns.

### 3. Add imported-reference inspection in the UI
Deliverables:
- Source drill-down view in the Compatibility Search tab
- Selected source preview for imported parts, aliases, attributes, and compatible models

Show for a selected source or part:
- Canonical part number
- Alias part numbers
- Manufacturer
- Imported attributes
- Compatible systems
- Source URL and update time

Reason:
- Imported data is now useful but still hard to inspect directly. Operators need a way to verify that a source import produced trustworthy reference rows.

### 4. Improve import error handling and operator guidance
Deliverables:
- Clearer UI messages for blocked website fetches, empty parses, and duplicate imports
- Suggested fallback actions when a page should be saved as HTML instead of fetched live

Focus areas:
- HTTP 403 or throttling from supplier sites
- Product pages that parse as zero-row imports
- Category pages that should not be imported directly

Reason:
- The highest-likelihood support issues are import-path misunderstandings, not core database failures.

### 5. Validate on a second machine or clean folder
Test flow:
- Fresh repo copy
- Run launcher
- Start app
- Import a real inventory snapshot
- Import one listing URL and one product-detail URL
- Run stock search, compatibility search, and stock adjustment
- Confirm the tester can recover from a blocked website fetch using saved HTML

Reason:
- This is the fastest way to expose packaging gaps, path assumptions, and missing setup instructions.

### 6. Prepare an internal tester package
Package should include:
- Source files
- `README.md`
- `TESTING.md`
- `launch.ps1`
- `launch.bat`

Package should exclude:
- `.venv/`
- `.git/`
- `__pycache__/`
- `db/fieldstock.db`

Reason:
- Once the startup and testing flow are stable, packaging becomes a straightforward internal handoff instead of a repeated manual setup exercise.

### 7. Plan the next compatibility improvement slice
Candidate deliverables:
- Normalize more vendor and attribute naming during reference import
- Add filtering by attribute in compatibility search
- Surface local inventory matches more clearly against imported references
- Start a curated alternate-part workflow separate from compatibility

Reason:
- Compatibility data import is now ahead of compatibility decision support. The next slice should improve trust and usability, not just add more raw reference rows.

## Key Constraints To Keep Visible
- Inventory imports are snapshot imports and missing part-location rows will be zeroed
- Partial or filtered Excel exports must not be imported as production snapshots
- Website imports can still be blocked by the supplier site and may require saved HTML fallback
- Compatibility and alternate-part logic should remain separate concepts in both UI and data model
- SQLite remains local-first and should not be treated as a multi-user shared-database solution yet

## Definition Of Done For The Next Milestone
- Non-developer testers can start the app with a launcher script
- A tester can follow `TESTING.md` without developer help
- Imported reference sources can be inspected in the UI
- Clean-machine validation is completed successfully
- One internal tester package is ready to share

## First Task
Implement `launch.ps1`, `launch.bat`, and `TESTING.md`, then validate the full flow on a clean folder before taking on more compatibility logic.
