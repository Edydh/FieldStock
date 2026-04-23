# Tomorrow Action Plan

## Goal
Make FieldStock easy to distribute and run for internal company users on a local laptop or a small internal server.

## Recommended Rollout
Start with laptop-first distribution.

Reason:
- Lowest operational risk
- No shared database contention yet
- Easier to validate with internal testers
- Faster to support while the MVP is still changing

## Execution Order

### 1. Create local launch scripts
Deliverables:
- `launch.ps1`
- `launch.bat`

Behavior:
- Create `.venv` if missing
- Install dependencies if needed
- Launch Streamlit with the project interpreter
- Print the local app URL

### 2. Create tester instructions
Deliverable:
- `TESTING.md`

Content:
- Prerequisites
- Setup steps
- How to start the app
- How to import the Excel inventory file
- Known limitations
- How to reset the local database

### 3. Add safer import visibility
Deliverables:
- Import run metadata table
- Pre-import summary of expected changes

Show before commit:
- Rows to import
- New balances
- Updated balances
- Balances that will be zeroed
- Import reference and timestamp

### 4. Validate on a second machine or clean folder
Test flow:
- Fresh copy of the repo
- Run launcher
- Start app
- Import a full snapshot file
- Validate search and stock adjustment

### 5. Prepare internal release package
Package should include:
- Source files
- `README.md`
- `TESTING.md`
- Launch scripts

Package should exclude:
- `.venv/`
- `.git/`
- `__pycache__/`
- `db/fieldstock.db`

### 6. Evaluate optional internal server mode
Only do this after laptop-first validation.

Server mode notes:
- One Windows machine hosts Streamlit
- One shared SQLite file lives on that machine
- Requires simple backup process
- Suitable only for a small trusted internal group at this stage

## Key Constraints To Document
- Imports are full snapshot imports
- Missing part-location rows in the next snapshot will be zeroed
- Partial filtered files must not be imported as production snapshots
- PowerShell may require the explicit project Python path if aliases interfere

## Definition Of Done
- One-command or near one-command startup for testers
- Clear tester documentation
- Safer import preview before commit
- One successful clean-machine validation
- Internal package ready to share

## First Task Tomorrow
Implement `launch.ps1`, `launch.bat`, and `TESTING.md` before any server work.