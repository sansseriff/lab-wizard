"""Serve the resize lab, and optionally open it in the wizard's own webview.

    uv run python lab_wizard/wizard/frontend/resize-lab/serve.py            # then open the URL in any browser
    uv run python lab_wizard/wizard/frontend/resize-lab/serve.py --window   # also open a pywebview window (WKWebView on macOS)

`/bokeh/` is BokehJS from the frontend's node_modules, the same build the
wizard ships, so nothing is fetched from the internet.
"""

import argparse
import functools
import http.server
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOKEH = HERE.parent / "node_modules" / "@bokeh" / "bokehjs" / "build" / "js"


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/bokeh/"):
            return str(BOKEH / clean.removeprefix("/bokeh/"))
        return super().translate_path(path)

    def end_headers(self) -> None:
        # Cross-origin so the wizard page can load hud.js; no cache so edits show.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--window", action="store_true", help="open the lab in a pywebview window too")
    args = parser.parse_args()

    if not BOKEH.is_dir():
        raise SystemExit(f"BokehJS not found at {BOKEH}; run `bun install` in the frontend first.")

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", args.port), functools.partial(Handler, directory=str(HERE))
    )
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Resize lab: {url}")

    if not args.window:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return

    import webview

    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Like backend/main.py's window: default GUI (Cocoa / WKWebView on macOS).
    # debug=True gives the Web Inspector (right-click, Inspect Element).
    webview.create_window("Resize lab (pywebview)", url=url, width=1300, height=900)
    webview.start(debug=True)
    server.shutdown()


if __name__ == "__main__":
    main()
