# FieldStock
## Local Inventory and Compatibility Assistant

## 1. Project Summary
FieldStock is a local-first inventory application for IT parts and hardware stock. Its purpose is to help a warehouse, lab, or field operations team track what parts are available, where they are stored, what has been used, and which parts may be valid replacements or compatible options for a given server or hardware request.

The system is designed to run offline on a local machine using Python, SQLite, and Streamlit. It should be fast to build, simple to operate, and easy to extend later with smarter matching and compatibility rules.

## 2. Core Problem
Many teams manage part inventory in Excel exports or ERP reports, but that data is difficult to search and operationally weak for day-to-day use. Users often need answers to questions like:

- Do we have this part in stock?
- Where is it located?
- What was used and when?
- What could replace this part?
- What parts could work in a specific server model?

A spreadsheet can store the raw data, but it does not provide reliable inventory workflows, search quality, or compatibility intelligence.

## 3. Product Goal
Build a lightweight desktop-style application that provides:

- Reliable local inventory tracking
- Excel import from existing operational reports
- Search by part number, description, and stock location
- Transaction history for stock movement
- Suggested alternate parts
- Compatibility-based recommendations for hardware searches such as "300GB 2.5 HDD for HP G8"

## 4. Product Scope
This project should be delivered in phases so the first version solves the operational problem before adding intelligence.

### Phase 1: Inventory Truth
The first release should focus on accurate stock data and reliable workflows.

Features:
- Import inventory from Excel
- Normalize part and location data
- Track current stock by location
- Search parts by number and description
- Record stock usage and adjustments
- Keep an audit trail of transactions

### Phase 2: Alternate Suggestions
The second release should help users find likely substitutes for a part.

Features:
- Fuzzy part number matching
- Description similarity search
- Ranked alternate suggestions
- Confidence scoring
- Explainable reasons for suggestions

### Phase 3: Compatibility Search
The third release should support compatibility-driven discovery.

Features:
- Search by request intent instead of exact part number
- Structured hardware attributes such as capacity, form factor, interface, and vendor
- Server compatibility profiles
- Rule-based filtering and ranking
- Explainable compatibility results

## 5. Important Product Distinction
The system should treat these as separate concepts:

### Exact Part Match
The user wants the exact known part number.

### Alternate Part
The user wants a substitute or near-equivalent part.

### Compatible Part
The user wants anything that should work for a given server, chassis, or hardware profile.

This distinction matters because fuzzy text matching can help with alternate suggestions, but compatibility requires structured attributes and rules. These should not be merged into one feature internally.

## 6. Target Users
Primary users:
- Warehouse staff
- Lab technicians
- Field support teams
- Inventory coordinators
- Operations managers

Expected user behavior:
- Import ERP or Excel reports
- Search quickly while handling stock requests
- Check quantity and location
- Record usage
- Ask for substitutes or compatible parts when exact stock is not available

## 7. Functional Requirements

### Inventory Management
- Store parts, descriptions, units of measure, and optional manufacturer metadata
- Store warehouse and bin or location data
- Maintain stock balances by part and location
- Prevent invalid negative adjustments unless explicitly allowed
- Log every stock movement

### Excel Import
- Accept ERP-style Excel exports
- Support explicit column mapping
- Support snapshot import mode
- Support delta import mode when valid
- Provide preview before commit
- Detect duplicate or reprocessed files when possible

### Search
- Exact part number search
- Partial part number search
- Description keyword search
- Full-text search across descriptions
- Filters by warehouse, location, and stock availability

### Alternates
- Suggest similar part numbers
- Suggest similar descriptions
- Rank results by confidence
- Show why a part was suggested

### Compatibility
- Support queries like:
  - 300GB 2.5 HDD for HP G8
  - SSD for Dell R730
  - Memory for ProLiant G8
- Match against structured compatibility rules
- Return all valid options from local inventory
- Explain the match criteria

## 8. Non-Goals for the First Release
The MVP should not try to solve everything at once.

Out of scope for Phase 1:
- Multi-user cloud sync
- Complex role-based permissions
- Large ML infrastructure
- Automatic embeddings pipeline
- Full ERP integration
- Advanced forecasting
- Purchasing workflows

## 9. Technology Stack

### Application Layer
- Python 3.10+
- Streamlit for the local UI

Why:
- Fastest path to a usable internal tool
- Easy to iterate in VS Code
- Good fit for forms, tables, filters, and operator workflows

### Data Layer
- SQLite

Why:
- Local-first
- No server dependency
- Simple deployment
- Supports transactional integrity and full-text search

### Data Import
- pandas
- openpyxl

Why:
- Reliable Excel ingestion
- Easy normalization and transformation

### Search and Matching
- RapidFuzz for fuzzy part-number and string similarity
- SQLite FTS5 for description search
- scikit-learn with TF-IDF and cosine similarity for lightweight description-based ranking

Why:
- Strong offline performance
- Good balance between capability and simplicity

### Optional Later Enhancements
- sentence-transformers for embedding-based semantic similarity
- FAISS or Annoy for vector search
- PyInstaller for packaging
- pytest for testing
- ruff and black for code quality

## 10. High-Level Architecture

### Frontend
A Streamlit interface with focused operational screens:

- Import
- Inventory Search
- Stock Use / Adjustment
- Transactions
- Alternates
- Compatibility Search

### Backend Modules
Suggested Python modules:

- app.py
- modules/db.py
- modules/import_excel.py
- modules/search.py
- modules/alternates.py
- modules/compatibility.py
- modules/utils.py

### Database Responsibilities
SQLite should manage:
- master part records
- location records
- stock balances
- transaction audit trail
- searchable descriptions
- alternate relationships
- compatibility metadata

## 11. Suggested Data Model

### parts
Stores core part information.

Fields:
- id
- part_number
- description
- manufacturer
- category
- uom
- normalized_part_number
- normalized_description
- created_at
- updated_at

### locations
Stores warehouse and storage information.

Fields:
- id
- warehouse_code
- location_code
- description

### inventory_balances
Stores current stock by part and location.

Fields:
- id
- part_id
- location_id
- qty_on_hand
- row_version
- updated_at

### inventory_transactions
Stores audit history for every movement.

Fields:
- id
- part_id
- location_id
- transaction_type
- qty_change
- reference
- created_by
- created_at
- notes

### part_alternates
Stores known or approved alternate relationships.

Fields:
- id
- part_id
- alternate_part_id
- source
- confidence_score
- status
- created_at

### compatibility_profiles
Stores supported hardware rules by server or platform.

Fields:
- id
- system_family
- model
- component_type
- rule_json
- created_at

### part_attributes
Stores normalized technical attributes extracted from descriptions or curated manually.

Fields:
- id
- part_id
- attribute_name
- attribute_value
- normalized_value

## 12. Excel Import Strategy
The import layer should be explicit and safe.

Recommended mapping based on the earlier example:
- part_number -> Item number
- description -> Product name
- warehouse -> Warehouse
- location_code -> Location
- uom -> Inventory unit
- snapshot_qty_preferred -> Total available
- snapshot_qty_fallback -> Available physical

Recommended behavior:
- Ask the user to confirm import mode instead of guessing silently
- Provide a preview of row count, mapped fields, and quantity interpretation
- Treat ERP full exports as snapshot imports by default unless the source is clearly transactional
- Log import metadata for traceability

## 13. Matching Strategy

### Alternate Matching
Use a layered ranking strategy:

- exact normalized part number
- fuzzy part number similarity
- keyword overlap in description
- TF-IDF cosine similarity
- optional manually approved alternates boost

Output should include:
- score
- match reason
- inventory availability
- location summary

### Compatibility Matching
Use a rules-based approach instead of fuzzy text alone.

Example attributes:
- capacity
- form factor
- interface
- speed
- RPM
- drive type
- memory type
- generation
- vendor restrictions

Example compatibility flow:
- parse the query
- identify requested component type and constraints
- load the server compatibility profile
- filter inventory by hard constraints
- rank by best fit and stock availability
- return explainable results

## 14. User Experience Principles
The interface should prioritize speed and clarity for operational users.

Design principles:
- minimal clicks for common tasks
- clear import confirmation
- visible stock and location summary
- strong search defaults
- explainable alternates and compatibility suggestions
- no hidden logic for critical stock changes

## 15. Risks and Design Warnings

### Risk: Import Mode Confusion
Auto-detecting snapshot versus delta can create inventory corruption if the source file is misunderstood.

Mitigation:
- require explicit import mode confirmation
- show preview before commit

### Risk: Mixing Alternates and Compatibility
A similar-looking part is not always a valid compatible part.

Mitigation:
- keep alternate logic and compatibility logic separate in both data model and UI

### Risk: Overengineering Too Early
Embeddings and advanced ML are attractive, but they do not solve the main operational risk of bad stock data.

Mitigation:
- ship inventory truth first
- add smarter ranking only after data quality is stable

### Risk: Weak Source Descriptions
Free-text descriptions may be inconsistent and incomplete.

Mitigation:
- normalize descriptions
- extract structured attributes
- allow manual curation for key parts

## 16. Testing Strategy
Testing should focus on reliability of stock and matching behavior.

Priority test areas:
- Excel import mapping
- snapshot import correctness
- delta import correctness
- negative stock protection
- transaction audit generation
- fuzzy search quality
- compatibility rule filtering

## 17. Deployment Approach
This should be deployable as a local internal tool with minimal setup.

Options:
- run directly in Python for development
- package later with PyInstaller for internal distribution

## 18. Success Criteria
The project is successful if users can:

- import inventory data without confusion
- trust stock counts and transaction history
- find parts faster than using Excel alone
- identify likely substitutes when exact stock is unavailable
- answer compatibility questions using inventory already on hand

## 19. Recommended Build Order

### Release 1
- SQLite schema
- Excel import
- stock balances
- transaction logging
- search UI

### Release 2
- alternate suggestion engine
- ranking and confidence explanation
- manual alternate approval flow

### Release 3
- structured attributes
- compatibility profiles
- compatibility search UI
- explainable compatibility results

## 20. Summary
FieldStock should begin as a reliable offline inventory system, not as an AI-heavy search experiment. The first priority is accurate inventory truth from Excel-based operational data. Once that is stable, the product can add alternate suggestions and then compatibility intelligence in a controlled, explainable way.

This approach keeps the project practical, buildable, and useful from the first release while leaving room for more advanced search and recommendation features later.