import pathlib

from lab_wizard.lib.utilities.config_io import (
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
    _write(inst_dir / "dbay_key_10~2E7~2E0~2E4~3A8345.yml", """
type: dbay
ip_address: 10.7.0.4
ip_port: 8345
children:
  "1":
    kind: dac4D
    ref: dbay_key_10~2E7~2E0~2E4~3A8345/dac4D_key_1.yml
""")

    _write(inst_dir / "dbay_key_10~2E7~2E0~2E4~3A8345" / "dac4D_key_1.yml", """
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

    # 3. Load instruments
    instruments = load_instruments(cfg)

    # Verify loaded structure
    dbay_key = "10.7.0.4:8345"
    assert dbay_key in instruments
    dbay = instruments[dbay_key]
    assert isinstance(dbay, DBayParams)

    # Active child should be present
    assert "1" in dbay.children
    assert dbay.children["1"].name == "ActiveDAC"

    # Only one child loaded (the orphan is not in the parent's ref map)
    assert len(dbay.children) == 1

    # 4. Save instruments back to disk
    save_instruments_to_config(instruments, cfg)

    # 5. Orphan file must still exist — save does not delete unreferenced files
    assert orphan_path.exists(), "Orphaned module file should not be deleted during save"

    # Active file must still exist (possibly at the canonical path written by save)
    canonical_child = inst_dir / "dbay_key_10~2E7~2E0~2E4~3A8345" / "dac4D_key_1.yml"
    assert canonical_child.exists()


def test_enabled_flag(tmp_path: pathlib.Path):
    """
    Test that 'enabled: false' prevents loading of instruments/modules.
    """
    cfg = tmp_path / "config"
    inst_dir = cfg / "instruments"

    # New layout: prologix YAML directly under instruments/
    _write(inst_dir / "prologix_gpib_key_~2Fdev~2FttyUSB0.yml", """
type: prologix_gpib
port: /dev/ttyUSB0
enabled: true
children:
  "5":
    kind: sim900
    ref: prologix_gpib_key_~2Fdev~2FttyUSB0/sim900_key_5.yml
""")

    _write(
        inst_dir / "prologix_gpib_key_~2Fdev~2FttyUSB0" / "sim900_key_5.yml", """
type: sim900
children:
  "1":
    kind: sim928
    ref: prologix_gpib_key_~2Fdev~2FttyUSB0/sim900_key_5/sim928_key_1.yml
""")

    sim928_path = (
        inst_dir
        / "prologix_gpib_key_~2Fdev~2FttyUSB0"
        / "sim900_key_5"
        / "sim928_key_1.yml"
    )
    _write(sim928_path, """
type: sim928
enabled: false
""")

    # A disabled top-level instrument
    _write(inst_dir / "dbay_key_1~2E2~2E3~2E4~3A8345.yml", """
type: dbay
ip_address: 1.2.3.4
ip_port: 8345
enabled: false
""")

    # Load
    instruments = load_instruments(cfg)

    # Prologix should be loaded
    assert "/dev/ttyUSB0" in instruments
    prologix = instruments["/dev/ttyUSB0"]

    # Sim900 should be loaded as a child
    assert "5" in prologix.children
    sim900 = prologix.children["5"]

    # Sim928 should NOT be loaded (enabled: false)
    assert "1" not in sim900.children

    # Disabled DBay should NOT be loaded
    assert "1.2.3.4:8345" not in instruments

    # 4. Save and verify disabled sim928 file is untouched (save never writes it)
    save_instruments_to_config(instruments, cfg)

    assert sim928_path.exists(), "Disabled module file should still exist after save"
    content = sim928_path.read_text()
    assert "enabled: false" in content


if __name__ == "__main__":
    # Manual run helper
    test_orphan_module_preservation(pathlib.Path("test_output"))
    test_enabled_flag(pathlib.Path("test_output"))
    print("Tests passed!")
