# Ralph Loop Status

iteration: 1
max_iterations: 10
completion_promise: none

## Progress

### Iteration 1 - Entity Classification Fix

**Problems Identified:**
1. LLCs/organizations classified as "person" (e.g., "N/A Fund GP, LLC" -> person)
2. Placeholder prefixes not cleaned (N/A, ---, [none])
3. "LLC" at start of names instead of end ("LLC Sydecar")
4. SPV names not extracting underlying company ("SpaceX Dec 2025 a Series of...")
5. OFFICER_OF relationship type not recognized in Company/Candidate pages

**Fixes Applied:**
1. `src/ingestion/edgar_form_d.py`:
   - Added `_clean_entity_name()` - strips placeholder prefixes, fixes LLC prefix
   - Added `_is_organization_name()` - detects org indicators (LLC, Inc, Ltd, L.P., etc.)
   - Added `_extract_underlying_company()` - extracts "SpaceX" from SPV names
   - Related persons with org indicators now classified as "company"
   - Funding attributed to underlying company when extracted

2. `scripts/kg_viewer.py`:
   - Companies page now checks RAISED_FUNDING (SEC) + FUNDED_BY (news)
   - Candidates page now checks OFFICER_OF (SEC) + CEO_OF/etc (news)

**Commits:**
- ce045ae: Fix Companies/Candidates pages to include SEC data
- 10e7316: Fix SEC Form D entity classification and name cleaning

**Remaining Issues:**
- Existing data in DB still has old issues (need re-fetch or migration)
- LLM-based entity classification for ambiguous cases (future enhancement)
- "EquipmentShare Q2 3" pattern needs better regex for underlying company extraction
