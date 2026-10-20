import pathlib

from lab_wizard.lib.utilities.config_io import (
    instrument_hash,
    load_instruments,
    save_instruments_to_config,
)
from lab_wizard.lib.instruments.dbay.dbay import DBayParams


def _write(p: pathlib.Path, data: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data, encoding="utf-8")


def test_orphan_module_preservation(tmp_path: pathlib.Path):
    """
    Test that a module file that exists on disk but is not referenced by any parent
    (orphaned/inactive) is NOT loaded into memory, but is ALSO NOT deleted when
    saving the configuration back to disk.
    """
    cfg = tmp_path / "config"
    inst_dir = cfg / "instruments"

    # 1. Setup a DBay with one active child.
    #    New layout: dbay YAML lives directly under instruments/.
    #    Children go in a sibling folder named after the parent YAML stem.
    _write(inst_dir / "dbay_key_oldhash.yml", """
type: dbay
ip_address: 10.7.0.4
ip_port: 8345
children:
  "1":
    type: dac4D
    ref: dbay_key_oldhash/dac4D_key_1.yml
""")

    _write(inst_dir / "dbay_key_oldhash" / "dac4D_key_1.yml", """
type: dac4D
name: ActiveDAC
num_channels: 4
""")

    # 2. Create an ORPHANED file (not referenced by any parent)
    orphan_path = inst_dir / "orphan_dac16d.yml"
    _write(orphan_path, """
type: dac16D
name: OrphanDAC
num_channels: 16
""")

    # 3. Load instruments — keys are hash-based
    instruments = load_instruments(cfg)

    dbay_hash = instrument_hash("dbay", "10.7.0.4:8345")
    assert dbay_hash in instruments
    dbay = instruments[dbay_hash]
    assert isinstance(dbay, DBayParams)

    # Active child is keyed by hash of slot "1"
    dac4d_hash = instrument_hash("dac4D", "1")
    assert dac4d_hash in dbay.children
    assert dbay.children[dac4d_hash].name == "ActiveDAC"
    assert len(dbay.children) == 1

    # 4. Save instruments back to disk
    save_instruments_to_config(instruments, cfg)

    # 5. Orphan file must still exist — save does not delete unreferenced files
    assert orphan_path.exists(), "Orphaned module file should not be deleted during save"

    # Active file must exist at the canonical hash-based path
    canonical_child = inst_dir / f"dbay_key_{dbay_hash}" / f"dac4D_key_{dac4d_hash}.yml"
    assert canonical_child.exists()


def test_enabled_flag(tmp_path: pathlib.Path):
    """
    Test that 'enabled: false' prevents loading of instruments/modules.
    """
    cfg = tmp_path / "config"
    inst_dir = cfg / "instruments"

    # New layout: prologix YAML directly under instruments/
    _write(inst_dir / "prologix_gpib_key_oldhash.yml", """
type: prologix_gpib
port: /dev/ttyUSB0
enabled: true
children:
  "5":
    type: sim900
    ref: prologix_gpib_key_oldhash/sim900_key_5.yml
""")

    _write(
        inst_dir / "prologix_gpib_key_oldhash" / "sim900_key_5.yml", """
type: sim900
children:
  "1":
    type: sim928
    ref: prologix_gpib_key_oldhash/sim900_key_5/sim928_key_1.yml
""")

    sim928_path = (
        inst_dir
        / "prologix_gpib_key_oldhash"
        / "sim900_key_5"
        / "sim928_key_1.yml"
    )
    _write(sim928_path, """
type: sim928
enabled: false
""")

    # A disabled top-level instrument
    _write(inst_dir / "dbay_key_oldhash2.yml", """
type: dbay
ip_address: 1.2.3.4
ip_port: 8345
enabled: false
""")

    # Load — keys are now hashes
    instruments = load_instruments(cfg)

    prologix_hash = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
    assert prologix_hash in instruments
    prologix = instruments[prologix_hash]

    # Sim900 child keyed by hash of gpib_address "5" (migrated from raw key "5")
    sim900_hash = instrument_hash("sim900", "5")
    assert sim900_hash in prologix.children
    sim900 = prologix.children[sim900_hash]

    # Sim928 should NOT be loaded (enabled: false)
    sim928_hash = instrument_hash("sim928", "1")
    assert sim928_hash not in sim900.children

    # Disabled DBay should NOT be loaded
    dbay_hash = instrument_hash("dbay", "1.2.3.4:8345")
    assert dbay_hash not in instruments

    # Save and verify disabled sim928 file is untouched (save never writes it)
    save_instruments_to_config(instruments, cfg)

    assert sim928_path.exists(), "Disabled module file should still exist after save"
    content = sim928_path.read_text()
    assert "enabled: false" in content


if __name__ == "__main__":
    # Manual run helper
    test_orphan_module_preservation(pathlib.Path("test_output"))
    test_enabled_flag(pathlib.Path("test_output"))
    print("Tests passed!")
