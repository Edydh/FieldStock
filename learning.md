# Project Learning Log

## Index
- [How To Use This File](#how-to-use-this-file)
- [Update Template](#update-template)
- [2026](#2026)
  - [2026-04-23 - Compatibility and Alias Search Improvements](#2026-04-23---compatibility-and-alias-search-improvements)

## How To Use This File
- Add one new dated entry each time we work on the project.
- Keep each entry short and action-focused.
- Capture what changed, what we learned, and what to do next.
- Update the `Index` section with a link to the new entry.

## Update Template
Copy this block for each new work session:

```markdown
### YYYY-MM-DD - Short Title

What changed:
- ...

What we learned:
- ...

Risks or open questions:
- ...

Next actions:
- ...
```

## 2026

### 2026-04-23 - Compatibility and Alias Search Improvements

What changed:
- Added `Gen8` and `Gen 8` normalization to improve model extraction (`G8`).
- Added a compatibility repair workflow to backfill model links for older imported data.
- Improved Compatibility Search UX so stock-only filtering does not hide total match context.
- Exposed alias/alternative part numbers in compatibility and reference search outputs.
- Added alias-aware matching to Inventory Search.

What we learned:
- Compatibility and alternative part numbers are related but different concepts.
- Data quality and normalization can affect search as much as SQL logic.
- Existing imported data sometimes needs repair jobs, not only parser fixes.
- Inventory search is more useful when it can match reference aliases.

Risks or open questions:
- Some supplier pages do not return stable structured content for automated analysis.
- Unknown part numbers still need source evidence before being marked as aliases.

Next actions:
- Keep adding dated learning entries after each work session.
- Add more tests when introducing new supplier parsing rules.
- Consider adding alias confidence or source evidence display in UI.
