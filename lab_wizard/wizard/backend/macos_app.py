"""A minimal ``Lab Wizard.app`` so macOS shows the wizard's own Dock icon.

macOS takes a window's Dock icon from the app bundle its process runs from.
A bare ``python`` has no bundle, so the Dock shows the icon of whatever
launched it (Terminal, VS Code). Setting the icon at runtime
(``webview.start(icon=...)``) stopped working in macOS 27, so the window
process runs from this bundle instead. pywebview's docs also say a bundle is
how the icon is set on macOS.

The bundle is a tiny venv in app form, kept inside the project's venv:
``Contents/MacOS`` holds a link to the venv's base Python, and
``Contents/pyvenv.cfg`` plus ``Contents/lib`` (a link to the venv's ``lib``)
make it import exactly what the venv does, editable installs included.

``setup.sh`` builds it (``python -m lab_wizard.wizard.backend.macos_app``).
The wizard only uses it if present and current; otherwise the window opens
from plain Python with the launcher's icon.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("lab_wizard.wizard.backend.macos_app")

ICNS_PATH = Path(__file__).with_name("icon.icns")
APP_PATH = Path(sys.prefix) / "Lab Wizard.app"
EXECUTABLE = APP_PATH / "Contents" / "MacOS" / "LabWizard"
_STAMP_KEY = "LabWizardSource"


def window_executable() -> str | None:
    """Return the Python inside ``Lab Wizard.app``, if built for this venv."""
    if sys.platform != "darwin":
        return None
    if _read_stamp() != _stamp():
        logger.info(
            "%s is missing or out of date; the Dock will show the launcher's "
            "icon. Rebuild it with: uv run python -m %s",
            APP_PATH,
            __name__,
        )
        return None
    return str(EXECUTABLE)


def build() -> None:
    """(Re)build ``Lab Wizard.app`` in this venv and check that it runs."""
    base_python = Path(os.path.realpath(sys.executable))
    shutil.rmtree(APP_PATH, ignore_errors=True)
    contents = APP_PATH / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    try:
        # A hard link is instant and takes no space; copy across volumes.
        os.link(base_python, EXECUTABLE)
    except OSError:
        shutil.copy2(base_python, EXECUTABLE)
    (contents / "pyvenv.cfg").write_text(
        f"home = {base_python.parent}\ninclude-system-site-packages = false\n"
    )
    (contents / "lib").symlink_to(Path(sys.prefix) / "lib")
    shutil.copy2(ICNS_PATH, contents / "Resources" / "icon.icns")
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(
            {
                "CFBundleExecutable": EXECUTABLE.name,
                "CFBundleIconFile": "icon",
                "CFBundleIdentifier": "org.labwizard.wizard",
                "CFBundleName": "Lab Wizard",
                "CFBundleDisplayName": "Lab Wizard",
                "CFBundlePackageType": "APPL",
                "NSHighResolutionCapable": True,
                _STAMP_KEY: _stamp(),
            },
            f,
        )
    # Some Pythons load libpython relative to their own path and can't run
    # from here; drop the bundle so the wizard falls back to plain Python.
    check = subprocess.run(
        [str(EXECUTABLE), "-c", "import webview, lab_wizard.wizard"],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        shutil.rmtree(APP_PATH, ignore_errors=True)
        raise RuntimeError(f"Python does not run from the app bundle:\n{check.stderr}")


def _stamp() -> str:
    """What the bundle was built from; a change to any of these needs a rebuild."""
    sources = [Path(os.path.realpath(sys.executable)), ICNS_PATH]
    return ";".join(
        f"{path}:{path.stat().st_size}:{path.stat().st_mtime_ns}" for path in sources
    )


def _read_stamp() -> str | None:
    try:
        with open(APP_PATH / "Contents" / "Info.plist", "rb") as f:
            return plistlib.load(f).get(_STAMP_KEY)
    except (OSError, plistlib.InvalidFileException):
        return None


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("Lab Wizard.app is only for macOS.")
    build()
    print(f"Built {APP_PATH}")
