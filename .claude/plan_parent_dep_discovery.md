# Plan: Parent-Dependency Discovery Actions

## Context

When adding a leaf instrument (e.g., SIM900) that requires a parent chain (prologix_gpib → sim900), the current discovery flow asks the user to re-enter parent connection params (port, baudrate, timeout) even though the parent was just resolved. The discovery actions are stateless and don't know about the parent instrument.

**New approach**: Optimistically save the parent to config as soon as it's resolved in the chain. Then the child's discovery action declares a parent dependency (by type string), and the backend loads the real parent from config to use as a live dependency during discovery.

## Changes

### 1. Frontend: Optimistically add parent to config when resolved
**File**: `lab_wizard/wizard/frontend/src/routes/manage_instruments/+page.svelte`

When a parent chain step is resolved (via `selectExistingParent` or `confirmNewParentKey` or discovery selection), immediately POST to save that parent to config before advancing the chain. This ensures the parent exists in `config/instruments/` by the time the child's discovery runs.

- Add an async helper `saveResolvedParent(stepIndex)` that calls the existing `/api/manage-instruments/add` endpoint with a single-step chain for just the parent (only for `create_new` parents — `use_existing` ones are already in config). Each parent is saved individually as soon as it resolves, building the config incrementally. For a 3-level chain [sim928, sim900, prologix_gpib]:
  - prologix_gpib resolves → saved as top-level instrument
  - sim900 resolves → saved as child of prologix_gpib (already in config)
- After `saveResolvedParent()` succeeds, switch the step's `action` to `use_existing` and update `key` to the hash key returned by the server. This way `executeAdd()` at the end just sees `use_existing` parents.
- Call this helper in `selectExistingParent()`, `confirmNewParentKey()`, and `resolveDiscoverySelection()` (when resolving a parent).
- Track optimistically-saved parent keys in a `savedParentKeys: string[]` state array. On wizard cancel, call `/api/manage-instruments/remove` for each (in reverse order, children first) to clean up.

### 2. Discovery descriptor: Declare parent dependency
**Files**:
- `lab_wizard/lib/instruments/sim900/sim900.py`
- `lab_wizard/lib/instruments/andoAQ8201A/andoAQ8201A.py`
- `lab_wizard/lib/instruments/general/discovery.py` (docstring)

Replace the manual `inputs` for parent connection params with a `parent_dep` field:

```python
# sim900.py discovery_actions()
{
    "name": "scan_gpib",
    "label": "Scan GPIB Bus",
    "description": "Search for SIM900 mainframes on a Prologix controller",
    "inputs": [],                          # no manual inputs needed
    "parent_dep": "prologix_gpib",         # NEW: declares parent dependency
    "result_type": "self_candidates",
}
```

Update `run_discovery` signature to accept an optional parent instrument:

```python
@classmethod
def run_discovery(cls, action: str, params: dict[str, Any], *, parent: Any = None) -> dict[str, Any]:
    if action == "scan_gpib":
        if parent is None:
            raise ValueError("scan_gpib requires a prologix_gpib parent")
        return cls._scan_gpib(parent)
    ...
```

Update `_scan_gpib` to accept a `PrologixControllerDep` (or the parent instrument itself):

```python
@classmethod
def _scan_gpib(cls, parent_inst) -> dict[str, Any]:
    controller = parent_inst.dep  # PrologixControllerDep from the live parent
    found = []
    for address in range(30):
        idn = get_idn(controller, address)
        if idn and idn.startswith("Stanford_Research_Systems,SIM900"):
            found.append({"key_fields": {"gpib_address": str(address)}, "idn": idn})
    return {"found": found}
```

Note: the parent instrument is created and destroyed per-request (the serial connection is opened/closed), so no resource leak.

### 3. Backend: Load parent and pass to discovery
**File**: `lab_wizard/wizard/backend/main.py`

Extend `_DiscoverBody` with an optional ancestor chain:
```python
class _DiscoverBody(_BM):
    type: str
    action: str
    params: dict = _Field(default_factory=dict)
    # NEW: resolved ancestor chain, ordered root-first
    # e.g. [{"type": "prologix_gpib", "key": "a1b2c3d4"}, {"type": "sim900", "key": "e5f6g7h8"}]
    parent_chain: list[dict] = _Field(default_factory=list)
```

Update `api_discover` to:
1. Add `env: Env = Depends(get_env)` to its signature
2. If `parent_chain` is non-empty, walk it top-down to initialize the immediate parent and pass to `run_discovery`
3. If discovery action has `parent_dep` but no parent chain was sent, return an error

```python
@app.post("/api/manage-instruments/discover")
def api_discover(body: _DiscoverBody, env: Env = Depends(get_env)):
    cls = load_params_class(body.type)
    
    parent_inst = None
    if body.parent_chain:
        parent_inst = _walk_parent_chain(body.parent_chain, env)
    
    try:
        return cls.run_discovery(body.action, body.params, parent=parent_inst)
    finally:
        # Clean up: disconnect the root instrument (closes serial/network)
        if parent_inst and hasattr(parent_inst, 'disconnect'):
            parent_inst.disconnect()
```

New helper `_walk_parent_chain` — **reuses the same pattern as `_construct_from_path` in `model_tree.py:138`** which already does chain-walking for `exp.from_attribute()`:

```python
def _walk_parent_chain(chain: list[dict], env: Env):
    """Walk a resolved ancestor chain top-down, initializing each level.
    
    chain is root-first: [{"type": "prologix_gpib", "key": "a1b2"}, {"type": "sim900", "key": "e5f6"}]
    Returns the last (deepest) initialized instrument — the immediate parent of the discovery target.
    
    This mirrors _construct_from_path() in model_tree.py which does the same
    chain-walking for exp.from_attribute(). The only difference is that 
    from_attribute() discovers the path by searching for attribute_name, while
    here the frontend provides the path directly.
    """
    config_dir = _config_dir(env)
    instruments = load_instruments(config_dir)
    
    # Build path as (key, params) tuples — same format as _construct_from_path
    root_key = chain[0]["key"]
    root_params = instruments.get(root_key)
    if root_params is None:
        raise HTTPException(404, f"Top-level {chain[0]['type']} ({root_key}) not found in config")
    
    # Same pattern as _construct_from_path: create_inst root, then make_child down
    current_inst = root_params.create_inst()
    for step in chain[1:]:
        current_inst = current_inst.make_child(step["key"])
    
    return current_inst
```

This handles the general N-level case: for PrologixGPIB (top-level parent of SIM900), the chain is just `[{"type": "prologix_gpib", "key": "a1b2"}]`. For a hypothetical case where SIM928 needs SIM900 as a dependency, the chain would be `[{"type": "prologix_gpib", "key": "a1b2"}, {"type": "sim900", "key": "e5f6"}]`.

**Note**: If we want to share code more directly, we could extract `_construct_from_path`'s core logic into a utility function in `config_io.py` that both `model_tree.py` and `main.py` call. But since it's ~5 lines, keeping two call sites is also fine.

### 4. Frontend: Send parent context with discovery request
**File**: `lab_wizard/wizard/frontend/src/routes/manage_instruments/+page.svelte`

Update `runDiscovery()` to build the resolved ancestor chain from chainSteps and include it in the POST:

```typescript
async function runDiscovery(actionName: string) {
    const targetType = discoveryTargetType ?? selectedType;
    if (!targetType) return;
    
    // Build resolved ancestor chain (root-first) from chainSteps
    // chainSteps is leaf-first: [leaf, parent, grandparent, ...]
    // We need ancestors of the discovery target, ordered root-first
    const targetIndex = chainSteps.findIndex(s => s.type === targetType);
    const parentChain: {type: string, key: string}[] = [];
    if (targetIndex >= 0) {
        // Collect all resolved ancestors above the target (they're at higher indices)
        for (let i = chainSteps.length - 1; i > targetIndex; i--) {
            const step = chainSteps[i];
            if (step.resolved && step.key) {
                parentChain.push({ type: step.type, key: step.key });
            }
        }
    }
    
    const response = await fetchWithConfig('/api/manage-instruments/discover', 'POST', {
        type: targetType,
        action: actionName,
        params: discoveryInputs,
        ...(parentChain.length > 0 ? { parent_chain: parentChain } : {})
    });
    discoveryResult = response;
}
```

### 5. Frontend: Hide inputs when parent_dep is declared
**File**: `lab_wizard/wizard/frontend/src/routes/manage_instruments/+page.svelte`

In the step 20 UI, when the current discovery action has `parent_dep` and a parent is resolved in the chain:
- Hide the input fields section entirely (no manual inputs needed since the backend uses the live parent)
- Show a note: "Using connection from parent (prologix_gpib: /dev/ttyUSB0)"
- Add an "Advanced" toggle (`showAdvancedDiscovery` state) that reveals the original input fields if the user needs to override anything (these would be sent in `params` alongside the parent chain)

### 6. Frontend type update
**File**: `lab_wizard/wizard/frontend/src/routes/manage_instruments/+page.ts`

Add `parent_dep` to `DiscoveryAction`:
```typescript
export type DiscoveryAction = {
    name: string;
    label: string;
    description: string;
    inputs: DiscoveryInput[];
    parent_dep?: string;    // NEW
    result_type: 'probe' | 'children' | 'self_candidates' | 'generic';
};
```

### 7. Remove `_propagateParentKeyToDiscoveryInputs`
**File**: `lab_wizard/wizard/frontend/src/routes/manage_instruments/+page.svelte`

Remove the `_propagateParentKeyToDiscoveryInputs()` function and its call in `advanceChain()`. No longer needed — the backend now handles parent dependency resolution.

### 8. Update `executeAdd()` and cancel cleanup
**File**: `lab_wizard/wizard/frontend/src/routes/manage_instruments/+page.svelte`

- `executeAdd()`: Parents were already saved optimistically with `action` switched to `use_existing` and keys updated to hash keys. So `executeAdd()` just saves the leaf — all parents are `use_existing` by this point. On success, clear `savedParentKeys` (they're now permanent).
- Cancel handler: When the wizard is cancelled (Cancel button or `showAddWizard = false`), iterate `savedParentKeys` in reverse order and call `/api/manage-instruments/remove` for each to clean up. Fire-and-forget is acceptable — if cleanup fails, the user can manually remove from the Manage Instruments page.

## File Summary

| File | Change |
|------|--------|
| `sim900/sim900.py` | Add `parent_dep`, remove manual inputs, update `run_discovery`/`_scan_gpib` to use parent inst |
| `andoAQ8201A/andoAQ8201A.py` | Same as sim900 |
| `general/discovery.py` | Docstring: document `parent_dep` key and `parent` kwarg on `run_discovery` |
| `wizard/backend/main.py` | Extend `_DiscoverBody`, add `env` dep, add `_load_parent_instrument`, pass parent to `run_discovery`, cleanup |
| `manage_instruments/+page.ts` | Add `parent_dep?: string` to `DiscoveryAction` type |
| `manage_instruments/+page.svelte` | Add `saveResolvedParent()`, update `runDiscovery()` to send parent context, hide inputs when `parent_dep` set, remove `_propagateParentKeyToDiscoveryInputs`, update `executeAdd()` for optimistic parents |

## Verification

1. `npx svelte-check` — 0 errors
2. `pytest tests/test_manage_instruments.py` — existing tests pass
3. Manual test: Add SIM900 → create new prologix_gpib → after prologix resolves (saved to config), SIM900 discovery auto-runs using the live parent connection — no manual input fields shown
4. Manual test: Add SIM900 with existing prologix → discovery uses existing parent from config
5. Verify parent cleanup: serial connection is closed after discovery completes
