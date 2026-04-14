# Prologix GPIB scan is slow and desynchronizes

## Symptom

`Sim900Params._scan_gpib` (and `AndoAQ8201AParams._scan_gpib`) in
`lab_wizard/lib/instruments/` iterate GPIB addresses 0..29 calling
`get_idn(controller, address)` and are observed to:

1. Take much longer per address than the reference implementation
   (~0.5s vs. ~0.1s).
2. Report instruments at the **wrong** GPIB address — e.g. a SIM900 that is
   physically wired to address 2 is reported at address 6. The offset
   corresponds to how many iterations the real response is delayed by.

Environment where this reproduces: RHEL 8 over SSH, Prologix GPIB-USB
controller, SIM900 rack, pyserial.

## Reference implementation (works correctly, ~0.1s per attempt)

The legacy script that scans the GPIB bus correctly and quickly lives outside
this repo:

- `/Users/andrew/Documents/src-copy/snspd_measure/snspd_measure/IDN_searcher.py`
  — top-level loop that reconstructs a `GPIBmodule` per address and calls
  `getIDN()`. 30 addresses + USB enumeration completes in ~3 seconds total.
- `/Users/andrew/Documents/src-copy/snspd_measure/snspd_measure/inst/serialInst.py`
  — contains `GPIBmodule.connect()` and `getIDN()`. **Read these to confirm
  the exact Prologix init sequence and query protocol used.** The expectation
  is that it sets the controller into `++auto 1` (auto-read) mode on connect,
  so a query is a single write+readline with no `time.sleep` in between.

## Our buggy implementation

Current controller transport:
[lab_wizard/lib/instruments/general/prologix_comm.py](../lab_wizard/lib/instruments/general/prologix_comm.py)

```python
class PrologixControllerDep:
    def __init__(self, serial_dep, *, read_delay_s: float = 0.1):
        self.serial_dep = serial_dep
        self.read_delay_s = read_delay_s

    def query_instrument(self, address: int, command: str) -> bytes:
        self.write_instrument(address, command)   # ++addr N\n<cmd>\n
        time.sleep(self.read_delay_s)             # (A) hard floor
        return self.read_instrument(address)      # ++addr N\n++read eoi\n + readline

    def read_instrument(self, address: int) -> bytes:
        self.serial_dep.write(f"++addr {address}\n++read eoi\n")
        line = self.serial_dep.readline()
        if line:
            return line
        return self.serial_dep.read()
```

Three problems, roughly in order of impact:

### 1. Manual-read mode + mandatory sleep (the speed killer)

We operate the Prologix in **manual** read mode: every query does
`write(*IDN?)` → sleep → `write(++read eoi)` → readline. The `time.sleep`
sets a hard floor on per-address cost regardless of how fast the instrument
replies. 30 addresses × 0.1s sleep = 3s of pure sleep, before any readline
timeouts on silent addresses.

`IDN_searcher.py` almost certainly uses `++auto 1` (auto-read after query),
which collapses a query to a single write+readline. That single readline
returns as soon as the Prologix has the reply, so responsive addresses are
near-instant and silent addresses cost only one pyserial `readline` timeout.

**Fix:** on controller init, send `++mode 1`, `++auto 1`,
`++read_tmo_ms <N>` once. Then rewrite `query_instrument` to:

```python
def query_instrument(self, address: int, command: str) -> bytes:
    self.write_instrument(address, command)
    return self.serial_dep.readline()
```

No sleep, no second write.

### 2. Timeout mismatch causes cross-address desync (the accuracy bug)

In manual mode, `++read eoi` makes the Prologix wait **up to its own
`++read_tmo_ms`** (default ~1200ms) for data from the addressed instrument.
pyserial's `readline()` is configured with `timeout = params.timeout` (now
0.1s). So:

1. We send `*IDN?` to address 2. The SIM900 is slow to respond (~100–300ms).
2. We sleep 0.1s, then write `++read eoi`. Prologix begins a GPIB read,
   waiting up to 1.2s for the SIM900 reply.
3. `readline()` times out after 0.1s and returns empty — but the **Prologix
   is still mid-read**.
4. We loop to address 3 and write `++addr 3\n*IDN?\n`. Those bytes queue in
   the USB buffer behind the in-progress operation from address 2.
5. The SIM900 eventually replies. The bytes come back via the Prologix's
   buffered output and are consumed by whatever `readline()` is running at
   the time — several iterations later. That's why a real SIM900 at address 2
   appears at "address 6".

**Fix invariant:** `++read_tmo_ms` must be ≤ pyserial `timeout`. If pyserial
returns first, the Prologix is still busy and the two go out of sync. Set
`++read_tmo_ms` explicitly on init and set pyserial `timeout = (N_ms + ~50ms
margin) / 1000`.

Once we're in `++auto 1` mode this stops mattering because the Prologix
handles the read as part of the query command, but set it anyway for safety.

### 3. Timeout param is a no-op

`PrologixGPIBParams.timeout` (float, default 0.1 since the recent edit to
`prologix_gpib.py`) is passed to `LocalSerialDep` as the pyserial timeout,
but it is **not** passed to the Prologix `++read_tmo_ms`. The Prologix keeps
its built-in default. Fix this by sending `++read_tmo_ms int(timeout*1000)`
in the init sequence described in problem (1).

## Call sites to update

- [lab_wizard/lib/instruments/general/prologix_comm.py](../lab_wizard/lib/instruments/general/prologix_comm.py)
  — rewrite `PrologixControllerDep.__init__` to run a one-time `configure()`
  that sends `++mode 1`, `++auto 1`, `++read_tmo_ms <N>`. Rewrite
  `query_instrument` to drop the sleep and the second write. Decide whether
  `read_delay_s` still has a purpose (probably delete it).
- [lab_wizard/lib/instruments/general/prologix_gpib.py](../lab_wizard/lib/instruments/general/prologix_gpib.py)
  `PrologixGPIB.from_params` already passes `read_delay_s=timeout`; make sure
  it passes whatever new timeout knob the controller ends up with. Consider
  bumping the default `PrologixGPIBParams.timeout` up to ~0.15–0.2s so silent
  addresses don't ragged out the Prologix timeout.
- [lab_wizard/lib/instruments/general/discovery.py](../lab_wizard/lib/instruments/general/discovery.py)
  `get_idn` already correctly delegates to `controller_dep.query_instrument`;
  no changes expected, but verify once the controller is fixed.
- [lab_wizard/lib/instruments/sim900/sim900.py](../lab_wizard/lib/instruments/sim900/sim900.py)
  and
  [lab_wizard/lib/instruments/andoAQ8201A/andoAQ8201A.py](../lab_wizard/lib/instruments/andoAQ8201A/andoAQ8201A.py)
  `_scan_gpib` already receives a live parent. No logic changes expected,
  but the whole loop should get much faster and start reporting correct
  addresses once `query_instrument` is fixed.

## Validation

- On the RHEL 8 host, scan with a known SIM900 at GPIB 2. It must be
  reported at address 2, not 6. This is the correctness regression.
- Full 30-address scan of a bus with 1–2 instruments should complete in
  roughly 3–5 seconds (matching `IDN_searcher.py`). If it takes >10s, the
  `time.sleep` / auto-mode change didn't land.
- Re-scanning twice in a row (without restarting the backend) should be
  deterministic — no "first scan finds nothing, second scan finds everything"
  behavior, which would indicate leftover Prologix read state between runs.

## Architectural note for the fixer

Do **not** replicate `IDN_searcher.py`'s "reconstruct the controller per
address" pattern. It works because RHEL serial opens are cheap, but it would
fight the `parent_dep` discovery architecture we just built: discovery
actions receive a live parent instrument via `_walk_parent_chain` in
[lab_wizard/wizard/backend/main.py](../lab_wizard/wizard/backend/main.py),
and they shouldn't know how to re-open their own transports. All of the
needed speed and correctness are reachable with a single shared connection
as long as the Prologix is put into auto-read mode with matching timeouts.