"""Static dev server for the planner.

    python serve.py [port]

http.server's default caching makes ES modules stick around after an edit —
the browser keeps executing the old module even after a reload, which silently
hides changes. Everything here is sent with no-store so a refresh always runs
the current files.
"""

from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):  # quieter console
        if "GET /data/" in (fmt % args):
            return
        super().log_message(fmt, *args)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8143
    handler = partial(NoCacheHandler, directory=str(ROOT))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"Course Planner: http://localhost:{port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
