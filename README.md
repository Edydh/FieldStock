# FieldStock

Local-first inventory and compatibility assistant for IT parts.

## Overview
FieldStock is a lightweight internal tool for managing hardware inventory from Excel-based operational data. It is intended to help warehouse, lab, and field teams answer a few high-value questions quickly:

- Do we have this part?
- Where is it stored?
- What was used and when?
- What can replace this part?
- What parts are compatible with a given server or platform?

The project is designed to run offline and locally using Python, SQLite, and Streamlit.

## Goals
- Build a reliable local inventory system from existing Excel exports
- Make stock search faster and safer than using spreadsheets alone
- Track stock movement with an audit trail
- Suggest likely alternate parts when exact stock is unavailable
- Support compatibility-based part lookup for hardware requests

## Product Boundaries
FieldStock should keep these concepts separate:

- Exact match: the known part number the user requested
- Alternate part: a substitute or near-equivalent part
- Compatible part: a part that should work for a given server or hardware profile

This matters because fuzzy matching can help with alternates, but compatibility requires structured attributes and explicit rules.

## Planned Releases

### Release 1: Inventory Truth
- Import inventory from Excel
- Normalize part and location data
- Track stock by location
- Search by part number and description
- Record usage and adjustments
- Maintain a transaction history

### Release 2: Alternate Suggestions
- Fuzzy part number matching
- Description similarity ranking
- Confidence scoring
- Explainable alternate recommendations

### Release 3: Compatibility Search
- Query by request intent instead of exact part number
- Structured part attributes
- Server compatibility profiles
- Rule-based filtering and ranking
- Explainable compatibility results

## Tech Stack
- Python 3.10+
- Streamlit
- SQLite
- pandas
- openpyxl
- RapidFuzz
- scikit-learn
- SQLite FTS5

Optional later additions:
- sentence-transformers
- FAISS or Annoy
- PyInstaller
- pytest
- ruff
- black

## Proposed Project Structure
```text
FieldStock/
  README.md
  FieldStock_Project_Brief.md
  app.py
  requirements.txt
  db/
    schema.sql
  modules/
    db.py
    import_excel.py
    search.py
    alternates.py
    compatibility.py
    utils.py
  tests/
```

## Core Data Model

### Main tables
- parts
- locations
- inventory_balances
- inventory_transactions
- part_alternates
- compatibility_profiles
- part_attributes

### Responsibilities
- parts: master part records
- locations: warehouse and storage locations
- inventory_balances: current quantity by part and location
- inventory_transactions: audit trail of stock movement
- part_alternates: approved or suggested substitute relationships
- compatibility_profiles: server and platform rules
- part_attributes: normalized technical attributes for compatibility matching

## Import Strategy
The import workflow should be explicit and safe.

Recommended Excel mapping:
- part_number -> Product identification
- description -> Product name
- warehouse -> Warehouse
- location_code -> Location
- uom -> Inventory unit
- snapshot_qty_preferred -> Total available
- snapshot_qty_fallback -> Available physical

Recommended behavior:
- Require the user to confirm snapshot vs delta import
- Show a preview before committing changes
- Default ERP full exports to snapshot mode unless the source is clearly transactional
- Log import metadata for traceability

## Matching Approach

### Alternate Matching
Use layered scoring:
- exact normalized part number
- fuzzy part number similarity
- keyword overlap in description
- TF-IDF cosine similarity
- manual alternate boosts when curated

### Compatibility Matching
Use structured filtering and rules, not fuzzy text alone.

Example attributes:
- capacity
- form factor
- interface
- speed
- RPM
- drive type
- memory type
- generation

Compatibility flow:
- parse the request
- identify component type and constraints
- load the compatibility profile
- filter by hard requirements
- rank by fit and stock availability
- explain why each result matches

## Risks
- Import mode confusion can corrupt stock counts if snapshot and delta are mixed
- Similar-looking parts are not always compatible parts
- Free-text descriptions may be incomplete or inconsistent
- Embeddings and advanced ML can distract from the primary goal of inventory accuracy

## Success Criteria
- Users can import inventory without confusion
- Users trust stock balances and transaction history
- Users find parts faster than with Excel alone
- Users can identify likely substitutes when exact stock is missing
- Users can answer compatibility questions from local inventory

## Status
Phase 1 MVP in progress.

Current implementation includes:
- SQLite schema initialization
- Excel snapshot import with preview and column mapping
- Inventory search by part number and description
- Transaction logging for snapshot imports
- Streamlit UI for import, search, and stock adjustment workflows
- Automated pytest coverage for inventory import, search, and adjustment rules

Not implemented yet:
- Alternate part suggestions
- Compatibility search
- Delta import mode

## Getting Started
FieldStock currently runs as a local Streamlit app backed by SQLite.

### 1. Create the virtual environment

From Command Prompt:

```cmd
py -3.12 -m venv .venv
```

From PowerShell:

```powershell
py -3.12 -m venv .venv
```

### 2. Activate the virtual environment

From Command Prompt:

```cmd
.venv\Scripts\activate.bat
```

From PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then activate again:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

With the virtual environment activated:

```powershell
pip install -r requirements.txt
```

Or without activation:

```powershell
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
```

### 4. Start the application

With the virtual environment activated:

```powershell
streamlit run app.py
```

Or use the project interpreter directly. This is the most reliable option in PowerShell if `python` resolves to the Microsoft Store alias:

```powershell
& ".venv\Scripts\python.exe" -m streamlit run app.py
```

### 5. Open the app

Streamlit starts the app locally at:

```text
http://localhost:8501
```

### Quick Start

If `.venv` already exists, the shortest working PowerShell flow is:

```powershell
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".venv\Scripts\python.exe" -m streamlit run app.py
```

### Run Tests

After installing dependencies, run:

```powershell
& ".venv\Scripts\python.exe" -m pytest
```

## Reference
The longer product and technical brief is available in [FieldStock_Project_Brief.md](FieldStock_Project_Brief.md).