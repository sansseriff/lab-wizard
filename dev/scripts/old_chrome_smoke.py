"""Load every wizard page in Qt5 WebEngine and report the JavaScript errors.

On Linux the wizard's window is Qt5 WebEngine, which is Chrome 87. The
frontend build rewrites what it can and css-compat.ts checks the CSS, but some
problems only show when code runs: a selector querySelector can't parse, a
browser API nothing polyfills. This serves the built frontend, opens each page
in that engine with no window, and prints anything logged as an error.

By default it serves a new, empty workspace so nothing real is touched; pass
--workspace to load pages over a real one's data. Pages are only loaded, not
clicked through, apart from the dialogs listed in INTERACTIONS.

    uv run python dev/scripts/old_chrome_smoke.py [--workspace PATH] [--settle SECONDS]

Exits 1 if any page logged an error.
"""

from __future__ import annotations

import argparse
import atexit
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

STATIC_DIR = Path(__file__).resolve().parents[2] / "lab_wizard/wizard/backend/static"

# Pages to click into after they load, as (path, button text). These open
# dialogs, whose code doesn't run on page load.
INTERACTIONS = [
    ("/instruments/", "Add instrument"),
]

CLICK_BUTTON = """
(() => {
    const button = [...document.querySelectorAll('button')]
        .find((b) => b.textContent.trim().toLowerCase().includes(%r.toLowerCase()));
    if (button) button.click();
    return !!button;
})()
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(port: int) -> None:
    """Runs the wizard's server on a background thread until the process exits."""
    import uvicorn

    from lab_wizard.wizard.backend.app import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()
    import urllib.request

    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    sys.exit(f"The wizard's server didn't start on port {port}")


def pages() -> list[str]:
    paths = []
    for html in sorted(STATIC_DIR.rglob("index.html")):
        relative = html.parent.relative_to(STATIC_DIR).as_posix()
        paths.append("/" if relative == "." else f"/{relative}/")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workspace", help="serve this workspace instead of a new, empty one")
    parser.add_argument("--settle", type=float, default=2.0, help="seconds to let each page run after loading")
    args = parser.parse_args()

    if not STATIC_DIR.is_dir():
        sys.exit(f"No built frontend at {STATIC_DIR}; run setup.sh or `bun run buildall` first")

    from lab_wizard.lib.workspace import WORKSPACE_ENV, initialize_workspace

    if args.workspace:
        os.environ[WORKSPACE_ENV] = str(Path(args.workspace).resolve())
    else:
        scratch = tempfile.mkdtemp(prefix="lab-wizard-smoke-")
        atexit.register(shutil.rmtree, scratch, ignore_errors=True)
        workspace, _ = initialize_workspace(Path(scratch) / "workspace")
        os.environ[WORKSPACE_ENV] = str(workspace.root)

    port = free_port()
    serve(port)

    from PyQt5.QtCore import QEventLoop, QTimer, QUrl
    from PyQt5.QtWebEngineWidgets import QWebEnginePage
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    errors: list[tuple[str, str]] = []
    current = {"path": ""}

    class Page(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
            # A page guarding unsaved edits with beforeunload complains when
            # the next load leaves it unprompted; that's this script, not a bug.
            if level == QWebEnginePage.ErrorMessageLevel and "'beforeunload'" not in message:
                errors.append((current["path"], f"{message} ({source}:{line})"))

    page = Page()

    def wait(seconds: float) -> None:
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec_()

    def run_js(script: str):
        loop = QEventLoop()
        result = {}

        def done(value):
            result["value"] = value
            loop.quit()

        page.runJavaScript(script, done)
        loop.exec_()
        return result.get("value")

    def load(path: str) -> None:
        current["path"] = path
        loop = QEventLoop()
        page.loadFinished.connect(loop.quit)
        page.load(QUrl(f"http://127.0.0.1:{port}{path}"))
        loop.exec_()
        page.loadFinished.disconnect(loop.quit)
        wait(args.settle)

    load("/")
    print(f"Engine: {run_js('navigator.userAgent')}")

    for path in pages():
        load(path)
        print(f"  loaded {path}")
    for path, button in INTERACTIONS:
        load(path)
        current["path"] = f"{path} -> {button}"
        if not run_js(CLICK_BUTTON % button):
            errors.append((current["path"], f"no button labelled {button!r} to click"))
            continue
        wait(args.settle)
        print(f"  clicked {button!r} on {path}")

    app.quit()
    if errors:
        print(f"\n{len(errors)} JavaScript error(s):")
        for path, message in errors:
            print(f"  {path}: {message}")
        return 1
    print("\nNo JavaScript errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
