# Implementation Plan: Codebase Cleanups + DBay Migration

---

## 1. Remove `parent_class` from `Dac4DParams` and `Dac16DParams`

**Files**: `lab_wizard/lib/instruments/dbay/modules/dac4d.py`, `dac16d.py`

Delete the `parent_class` property from both `Dac4DParams` and `Dac16DParams`. These are `ChildParams` subclasses, not `Child` instrument subclasses — the base class `ChildParams` has no abstract `parent_class` requirement. Only `Child` instruments need to implement it.

---

## 2. Document the `parent_class` contract

The `parent_class` abstract property on `Child` looks dead but is secretly used by `params_discovery.py`'s regex scanner to infer parent-child relationships at scan time (never called at runtime). This needs to be made obvious.

**In `parent_child.py`** — expand the docstring on `parent_class`:
```python
@property
@abstractmethod
def parent_class(self) -> str:
    """Fully-qualified class name of the expected parent instrument class.

    !! This property is read statically by params_discovery.py (see
    _PARENT_CLASS_RETURN regex) to build the parent-child metadata used
    by the wizard UI and get_parent_chain(). It is NOT called at runtime.

    Removing or renaming this property breaks instrument discovery silently.
    The return string MUST be a fully-qualified dotted path, e.g.:
        "lab_wizard.lib.instruments.sim900.sim900.Sim900"
    !!
    """
```

**In `params_discovery.py`** — add a comment above `_PARENT_CLASS_RETURN`:
```python
# Reads the return string of each Child's `parent_class` property from source.
# This is the canonical way parent-child relationships are discovered —
# see Child.parent_class docstring in parent_child.py.
_PARENT_CLASS_RETURN = re.compile(...)
```

---

## 3. Change `kind` → `type` in config_io.py

**File**: `lab_wizard/lib/utilities/config_io.py`

In `_attach_children()` (line 238-241), the code reads `kind` and `ref` from child entries:
```python
kind = cast(Optional[str], entry.get("kind"))
ref = cast(Optional[str], entry.get("ref"))
```

Change `kind` to `type` for consistency with the project YAML format:
```python
type_str = cast(Optional[str], entry.get("type"))
ref = cast(Optional[str], entry.get("ref"))
```

Also update the validation check and `child_type_str` that uses the loaded params (already derived from the actual params object, so that line stays). Update `_save_node_recursive` to write `type` instead of `kind` in child refs:
```python
child_refs[str(key)] = {"type": str(c_type), "ref": ref}
```

The existing `config/instruments/*.yml` files will be stale (they say `kind:`) but the user intends to delete and rebuild them, so no migration needed.

---

## 4. DBay Migration to `dbay` library

### Background

The current `DBay` implementation:
- Uses a custom `Comm` class (HTTP only)
- Has dual child creation: `make_child()` (params-backed) + `load_full_state()` (raw dict, no params)
- `Dac4D`/`Dac16D` constructors take `(data: dict, comm: Comm)` — inconsistent with other children

The new `dbay` library (already installed, v0.2.0):
- `DBayClient(mode="gui", server_address=..., port=...)` — stateful HTTP
- `DBayClient(mode="direct", direct_host=..., direct_port=...)` — stateless UDP/Serial
- Module access: `client.module(slot, expected="dac4D")` (GUI) or `client.attach_module(slot, dac4D)` (direct)
- Module API: `module.set_voltage(channel_index, voltage)`, `module.set_voltage_shared(...)`, etc.

### Plan

#### 4a. `DBayParams` — add mode and direct-mode fields

```python
class DBayParams(IPLike, ParentParams["DBay", "DBayClient", DBayChildParams], CanInstantiate["DBay"]):
    type: Literal["dbay"] = "dbay"
    mode: Literal["gui", "direct"] = "gui"
    # GUI mode: ip_address + ip_port from IPLike
    # Direct mode:
    direct_port: int = Field(default=8880, description="UDP port for direct mode")
    direct_transport: Literal["udp", "serial"] = "udp"
    serial_port: str | None = None
    baudrate: int = 115200
    retain_changes: bool = Field(default=True, description="GUI mode: revert on cleanup if False")
    children: dict[str, DBayChildParams] = Field(default_factory=dict)
```

`ip_address` and `ip_port` are already in `IPLike`; they serve as the GUI server address. For direct UDP mode, `ip_address` + `direct_port` give the hardware address.

#### 4b. `DBay` instrument — use `DBayClient` as the dep

```python
from dbay import DBayClient

class DBay(Parent[DBayClient, DBayChildParams], ParentFactory[DBayParams, "DBay"]):
    def __init__(self, client: DBayClient, params: DBayParams):
        self.client = client
        self.params = params
        self.children: dict[str, Child[DBayClient, DBayChildParams]] = {}

    @property
    def dep(self) -> DBayClient:
        return self.client

    @classmethod
    def from_params(cls, params: DBayParams) -> "DBay":
        if params.mode == "gui":
            client = DBayClient(
                mode="gui",
                server_address=params.ip_address,
                port=params.ip_port,
                retain_changes=params.retain_changes,
            )
        elif params.direct_transport == "serial":
            client = DBayClient(
                mode="direct",
                direct_transport="serial",
                serial_port=params.serial_port,
                baudrate=params.baudrate,
            )
        else:
            client = DBayClient(
                mode="direct",
                direct_host=params.ip_address,
                direct_port=params.direct_port,
            )
        return cls(client, params)

    def make_child(self, key: str) -> Child[DBayClient, Any]:
        if key in self.children:
            return self.children[key]
        params = self.params.children[key]
        slot = int(params.slot)
        if self.params.mode == "gui":
            module = self.client.module(slot)
        else:
            from dbay import dac4D as dac4D_mod, dac16D as dac16D_mod
            if isinstance(params, Dac4DParams):
                module = self.client.attach_module(slot, dac4D_mod)
            elif isinstance(params, Dac16DParams):
                module = self.client.attach_module(slot, dac16D_mod)
            else:
                module = None
        child = params.inst(module, params)
        self.children[key] = child
        return child
```

`load_full_state()`, `get_modules()`, `list_modules()`, `_module_snapshot`, `_full_state_cache` are all removed. The dual-system is eliminated.

#### 4c. `Dac4D` and `Dac16D` — consistent `(module, params)` constructors

Channel objects become thin wrappers around the library module's per-channel commands:

```python
class Dac4DChannel(VSource):
    def __init__(self, module, channel_index: int, params: Dac4DChannelParams):
        self.module = module
        self.channel_index = channel_index
        self.attribute_name = params.attribute_name

    def set_voltage(self, voltage: float) -> bool:
        try:
            self.module.set_voltage(self.channel_index, voltage)
            return True
        except Exception:
            return False

    def turn_on(self) -> bool:
        # GUI mode: activated flag is managed by the library
        # Direct mode: no activation concept, set_voltage is sufficient
        return self.set_voltage(0.0)  # or track last voltage

    def turn_off(self) -> bool:
        return self.set_voltage(0.0)

    def disconnect(self) -> bool:
        return True


class Dac4D(Child[Any, Dac4DParams], ChannelProvider[Dac4DChannel]):
    def __init__(self, module, params: Dac4DParams):
        self.module = module
        self.params = params
        self.channels = [
            Dac4DChannel(module, i, ch_params)
            for i, ch_params in enumerate(params.channels)
        ]

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.dbay.dbay.DBay"

    @property
    def dep(self):
        return self.module
```

`Dac16D` follows the same pattern. `Dac4DState`, `Dac16DState` (Pydantic state models) and `from_module_info()` are removed — the library manages hardware state internally.

The `Comm` class (`dbay/comm.py`) and `addons/vsource.py`, `addons/vsense.py`, `state.py` can all be deleted. The `dbay/modules/empty.py` stays (no-op module for unrecognized slots).

#### 4d. GUI state sync lifecycle helper

The DBay GUI tracks what physical modules are in which slots. The lab_wizard config (`config/instruments/dbay_key_*/`) stores params per-slot. These can diverge (user swaps a module). A sync helper is needed.

**Location**: Add a `/api/manage-instruments/sync-dbay` POST endpoint in `main.py`.

**Request body**: `{ "key": "<dbay_hash_key>" }` — identifies which DBay to sync.

**Logic**:
```python
@app.post("/api/manage-instruments/sync-dbay")
def api_sync_dbay(body: _SyncBody, env: Env = Depends(get_env)):
    """Connect to DBay GUI and sync module slots with stored config."""
    config_dir = _config_dir(env)
    instruments = load_instruments(config_dir)
    dbay_params = instruments.get(body.key)
    if not dbay_params or dbay_params.type != "dbay":
        raise HTTPException(404, "DBay not found")

    # Connect and list modules
    from dbay import DBayClient
    client = DBayClient(mode="gui", server_address=dbay_params.ip_address,
                        port=dbay_params.ip_port, load_state=True)
    modules = client.list_modules()  # returns list of (slot, module_type_str)

    # Build diff: what's in config vs what's actually present
    # Return diff to frontend; let user decide what to add/remove/keep
    current_children = dbay_params.children
    discovered = {slot: mtype for slot, mtype in enumerate(modules) if mtype != "empty"}

    return {"current": _node_to_tree_dict(body.key, dbay_params), "discovered": discovered}
```

The frontend then shows "new modules found / modules removed" and lets the user confirm updates. This keeps sync explicit and user-controlled rather than automatic, which is safer given it writes config files.

---

## 5. Remove `__call__` from all Params classes

**Verification**: `params()` (the callable pattern) is not used anywhere in non-test code. `create_inst()` IS used in `model_tree.py:176` inside `_construct_from_path`. So:
- Remove `__call__` from: `PrologixGPIBParams`, `DBayParams`, `YokogawaAQ2212Params`, `Keysight53220AParams`
- Keep `create_inst()` — it is the `CanInstantiate` interface consumed by `_construct_from_path`

```python
# DELETE from each Params class:
def __call__(self) -> "..":
    return self.create_inst()
```

---

## 6. Remove `IVCurveParams` and `PCRCurveParams` from `model_tree.py`

**File**: `lab_wizard/lib/utilities/model_tree.py`

Remove classes `IVCurveParams` and `PCRCurveParams` and the `ExpUnion` type alias.

The `Exp` model's `exp` field currently uses `ExpUnion`. Replace with a dynamic loader or just `SerializeAsAny[BaseModel]` (same pattern used for instruments):
```python
class Exp(BaseModel):
    exp: SerializeAsAny[BaseModel]  # measurement params, type-discriminated at load time
    ...
```

The template file `iv_curve_setup_template.py` defines its own `IVCurveParams` with sweep parameters — that stays. Measurement param classes belong in their template files only.

---

## 7. Remove `Exp.find_all_resources()`

**File**: `lab_wizard/lib/utilities/model_tree.py`

Delete the `find_all_resources` method (lines 244-263). No instrument class implements `has_resource()` or `find_resources()`. The method always returns `{}`.

---

## 8. Document `ParentParams.children` as a required implementation contract

**File**: `lab_wizard/lib/instruments/general/parent_child.py`

Add a clear note in the `ParentParams` docstring and add a `__init_subclass__` check that fires a TypeError for concrete (non-abstract) subclasses that forget to define `children`:

```python
class ParentParams(BaseModel, Params2Inst[PR_co], Generic[PR_co, R, P]):
    """
    ...
    REQUIRED IN EVERY CONCRETE SUBCLASS — define a typed children field:
        children: dict[str, YourChildParamsUnion] = Field(default_factory=dict)

    This cannot be defined here because each subclass needs a different
    Annotated union type for Pydantic's discriminator to work. Forgetting
    it causes a runtime AttributeError on first child access.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Skip abstract classes and this base itself
        if getattr(cls, "__abstractmethods__", None):
            return
        # If no 'children' annotation anywhere in the MRO (excluding this base), warn
        has_children = any(
            "children" in getattr(base, "__annotations__", {})
            for base in cls.__mro__
            if base is not ParentParams
        )
        if not has_children:
            raise TypeError(
                f"{cls.__name__} inherits ParentParams but does not define a "
                "'children' field. Add: "
                "children: dict[str, YourChildUnion] = Field(default_factory=dict)"
            )
```

---

## 9. Fix `params_discovery` one-type-per-file assumption

**File**: `lab_wizard/lib/utilities/params_discovery.py`

Current: `_TYPE_LITERAL.search(content)` finds the first match in the whole file, assigns it to all Params classes found.

Fix: scan per-class by searching for the type literal within each class's body span. Use `finditer` on class matches, then search within the text slice from each class start to the next class start (or end of file):

```python
def _scan_file_for_params(path: Path, instruments_dir: Path) -> list[dict[str, Any]]:
    ...
    class_iter = list(_CLASS_WITH_BASES.finditer(content))
    parent_match = _PARENT_CLASS_RETURN.search(content)
    parent_module = ...  # unchanged

    results = []
    for i, match in enumerate(class_iter):
        class_name, bases_str = match.group(1), match.group(2)
        is_top_level = bool(re.search(r'\bCanInstantiate\b', bases_str))
        is_child = bool(re.search(r'\bChildParams\b', bases_str))
        if not is_top_level and not is_child:
            continue
        # Search for type literal in THIS class's body only
        body_start = match.end()
        body_end = class_iter[i + 1].start() if i + 1 < len(class_iter) else len(content)
        class_body = content[body_start:body_end]
        type_match = _TYPE_LITERAL.search(class_body)
        if not type_match:
            continue  # No type literal → not a registered instrument
        results.append({
            "type_value": type_match.group(2),
            "module": module_path,
            "class_name": class_name,
            "is_top_level": is_top_level,
            "is_child": is_child,
            "parent_module": parent_module,
        })
    return results
```

This correctly handles multiple Params classes per file, each with their own type string.

---

## Execution Order

1. Items 5, 6, 7 — quick deletions, no dependencies
2. Item 3 — `kind` → `type` in config_io.py (isolated)
3. Items 2, 9 — documentation + scanner fix
4. Item 8 — `ParentParams.__init_subclass__` (must come before any dbay changes)
5. Item 1 — remove `parent_class` from `Dac4DParams`/`Dac16DParams` (after #8 so subclass check passes)
6. Item 4 — DBay migration (largest change; can be done last)

## Verification

- Run existing test suite after each group of changes
- For DBay: test `from_params` in both `mode="gui"` and `mode="direct"` with mocked `DBayClient`
- Confirm `params_discovery.clear_cache()` + `get_type_to_module_map()` still discovers all instrument types after scanner fix
- Confirm `load_instruments(config_dir)` works after `kind` → `type` change (old YAMLs deleted anyway)
- Confirm `_construct_from_path` in `model_tree.py` still works with `create_inst()` after `__call__` removal
