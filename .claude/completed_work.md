# Instrument Discovery UI — Flow Fixes & Dependency Tree Sidebar

**Completed:** April 13, 2026

## Overview

Fixed confusing instrument discovery workflows in the lab_wizard "Add Instrument" wizard. Addressed:

1. DBay redundant confirm step
2. PrologixGPIB invisible USB scan results
3. Sim900/Ando discovery never triggering (parent chain issue)
4. Sim900 discovery result semantics (self_candidates vs children)
5. Added dependency tree sidebar for parent chain visibility

## Files Modified

### Backend (Python)

- **lab_wizard/lib/instruments/general/discovery.py**
  - Updated docstring to document all result_type options including new `self_candidates`

- **lab_wizard/lib/instruments/dbay/dbay.py**
  - `_discover_children()` now returns `parent_key: "{ip_address}:{ip_port}"` alongside children list

- **lab_wizard/lib/instruments/sim900/sim900.py**
  - Changed `result_type` from `"children"` to `"self_candidates"`
  - Removed redundant `type` field from discovered items
  - Changed return format from `{"children": [...]}` to `{"found": [...]}`

- **lab_wizard/lib/instruments/andoAQ8201A/andoAQ8201A.py**
  - Same changes as sim900

### Frontend (TypeScript/Svelte)

- **lab_wizard/wizard/frontend/src/routes/manage_instruments/+page.ts**
  - Added `"self_candidates"` to `DiscoveryAction.result_type` union
  - Exported new `ChainStep` type with `resolved: boolean` field

- **lab_wizard/wizard/frontend/src/routes/manage_instruments/+page.svelte**
  - Updated `selectTypeForAdd()` to initialize `resolved: false` on chain steps
  - Updated `selectExistingParent()` and `confirmNewParentKey()` to mark parents as resolved
  - Rewrote `advanceChain()` to detect and trigger discovery for leaf instruments
  - Added `_propagateParentKeyToDiscoveryInputs()` helper to auto-fill sim900 port from prologix parent
  - Added rendering for `probe` result type (clickable USB port list)
  - Added rendering for `self_candidates` result type (clickable device candidates)
  - Changed "Add & Modules" button to call `executeAdd()` directly (no step 3)
  - Updated `executeAdd()` to use `parent_key` from discovery result
  - Added dependency tree sidebar showing ancestor chain with green/pending status

## Key Design Decisions

1. **self_candidates vs children**: Results where user picks an instance of the instrument being added (not sub-instruments) now return `result_type: "self_candidates"` with `{found: [{key_fields, idn}]}` format.

2. **Port propagation**: Parent key automatically injected into child's discovery inputs via heuristic: if parent's key_hint mentions USB/port/tty AND child's scan action has a `port` input, inject it.

3. **Direct executeAdd()**: Discovery flows for probe/self_candidates now call `executeAdd()` directly from the button onclick, skipping the step 3 confirm screen.

4. **Dependency tree**: Visual chain shows full ancestor hierarchy, updates reactively as parents resolve. Only shown for instruments with parent chains.

## Verification

- ✅ TypeScript/Svelte: 0 errors, 0 warnings
- ✅ Python: All files compile successfully
- ✅ All 12 implementation tasks completed
