"""HTTP server: a tiny REST API plus the browser dashboard.

Only the Python standard library is used.  The server is intended for local
use (127.0.0.1).  It does not implement user accounts or TLS, which keeps the
project focused on the storage and snapshot ideas.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import __version__
from .snapshot import (
    SnapshotCorrupt,
    SnapshotError,
    SnapshotNotFound,
    SnapshotWriteError,
    SnapshotManager,
)
from .store import (
    KeyNotFound,
    KVStore,
    ValidationError,
)

WEB_ROOT = Path(__file__).with_name("web")
MAX_BODY_BYTES = 2 * 1024 * 1024

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'"
)

DEMO_ENTRIES = {
    "project": "Atomic Snapshot Key-Value Store",
    "team_lead": "Seif Eldin Mohamed",
    "course": "Systems Programming",
    "status": "ready for demo",
    "snapshot_format": "binary + SHA-256",
    "durability": "fsync -> rename -> fsync directory",
}


class ApiProblem(Exception):
    """An error that should be returned as JSON with a specific status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class KVRequestHandler(BaseHTTPRequestHandler):
    server_version = f"KVSnapshot/{__version__}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------
    # Low-level response helpers
    # ------------------------------------------------------------------
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        if getattr(self, "path", "").startswith("/api/"):
            status = args[1] if len(args) > 1 else ""
            print(f"[api] {self.command} {self.path} {status}".rstrip(), flush=True)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        self._send_json(
            status,
            {"ok": False, "error": {"code": code, "message": message}},
        )

    def _read_json(self) -> dict[str, object]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise ApiProblem(400, "bad_request", "Invalid Content-Length.") from exc

        if length <= 0:
            raise ApiProblem(400, "bad_request", "A JSON request body is required.")
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise ApiProblem(413, "body_too_large", "Request body is too large.")

        raw = self.rfile.read(length)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ApiProblem(400, "bad_request", "Body must be UTF-8.") from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ApiProblem(400, "bad_request", "Body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ApiProblem(400, "bad_request", "JSON body must be an object.")
        return payload

    # ------------------------------------------------------------------
    # Static dashboard
    # ------------------------------------------------------------------
    def _serve_static(self, path: str) -> None:
        if path == "/favicon.ico":
            self._send_bytes(204, b"", "image/x-icon")
            return

        entry = STATIC_FILES.get(path)
        if entry is None:
            raise ApiProblem(404, "not_found", "Page not found.")

        filename, content_type = entry
        try:
            body = (WEB_ROOT / filename).read_bytes()
        except OSError as exc:
            raise ApiProblem(500, "static_error", "Dashboard file is missing.") from exc

        self._send_bytes(
            200,
            body,
            content_type,
            {"Content-Security-Policy": CSP} if content_type.startswith("text/html") else None,
        )

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(self._get)

    def _get(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self._serve_static(path)
            return

        if path == "/api/health":
            self._send_json(
                200,
                {"ok": True, "status": "ok", "version": __version__},
            )
            return

        if path == "/api/status":
            snapshots = self.server.snapshots.list()
            latest = snapshots[0].to_dict() if snapshots else None
            self._send_json(
                200,
                {
                    "ok": True,
                    "version": __version__,
                    "entries": len(self.server.store),
                    "snapshots": len(snapshots),
                    "latest_snapshot": latest,
                    "data_dir": str(self.server.snapshots.directory),
                    "uptime_seconds": int(time.time() - self.server.started_at),
                    "cleaned_temp_files": self.server.snapshots.cleaned_temp_files,
                },
            )
            return

        if path == "/api/entries":
            entries = [
                {"key": key, "value": value}
                for key, value in self.server.store.items()
            ]
            self._send_json(
                200,
                {"ok": True, "count": len(entries), "entries": entries},
            )
            return

        prefix = "/api/entries/"
        if path.startswith(prefix):
            key = unquote(path[len(prefix) :])
            if not key:
                raise ApiProblem(400, "validation_error", "Key is required.")
            value = self.server.store.get(key)
            self._send_json(200, {"ok": True, "key": key, "value": value})
            return

        if path == "/api/snapshots":
            snapshots = [info.to_dict() for info in self.server.snapshots.list()]
            self._send_json(
                200,
                {"ok": True, "count": len(snapshots), "snapshots": snapshots},
            )
            return

        raise ApiProblem(404, "not_found", "API endpoint not found.")

    # ------------------------------------------------------------------
    # POST
    # ------------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        self._dispatch(self._post)

    def _post(self) -> None:
        path = urlparse(self.path).path

        if path == "/api/entries":
            payload = self._read_json()
            if "key" not in payload or "value" not in payload:
                raise ApiProblem(
                    400,
                    "validation_error",
                    "Both 'key' and 'value' are required.",
                )
            created = self.server.store.set(payload["key"], payload["value"])
            self._send_json(
                201 if created else 200,
                {
                    "ok": True,
                    "created": created,
                    "key": payload["key"],
                    "value": payload["value"],
                },
            )
            return

        if path == "/api/snapshots":
            info = self.server.snapshots.create(self.server.store)
            self._send_json(201, {"ok": True, "snapshot": info.to_dict()})
            return

        if path == "/api/restore":
            payload = self._read_json()
            filename = payload.get("file", payload.get("name"))
            if not isinstance(filename, str) or not filename:
                raise ApiProblem(
                    400,
                    "validation_error",
                    "The 'file' field with a snapshot name is required.",
                )
            info = self.server.snapshots.restore(self.server.store, filename)
            self._send_json(
                200,
                {
                    "ok": True,
                    "restored": info.to_dict(),
                    "entries": len(self.server.store),
                },
            )
            return

        raise ApiProblem(404, "not_found", "API endpoint not found.")

    # ------------------------------------------------------------------
    # DELETE
    # ------------------------------------------------------------------
    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch(self._delete)

    def _delete(self) -> None:
        path = urlparse(self.path).path
        prefix = "/api/entries/"
        if not path.startswith(prefix):
            raise ApiProblem(404, "not_found", "API endpoint not found.")
        key = unquote(path[len(prefix) :])
        if not key:
            raise ApiProblem(400, "validation_error", "Key is required.")
        if not self.server.store.delete(key):
            raise ApiProblem(404, "not_found", f"Key not found: {key}")
        self._send_json(200, {"ok": True, "deleted": key})

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch(
            lambda: self._send_error_json(
                405, "method_not_allowed", "Use POST /api/entries."
            )
        )

    # ------------------------------------------------------------------
    # Error mapping
    # ------------------------------------------------------------------
    def _dispatch(self, action) -> None:
        try:
            action()
        except ApiProblem as exc:
            self._send_error_json(exc.status, exc.code, exc.message)
        except ValidationError as exc:
            self._send_error_json(400, "validation_error", str(exc))
        except (KeyNotFound, SnapshotNotFound) as exc:
            self._send_error_json(404, "not_found", str(exc))
        except SnapshotCorrupt as exc:
            self._send_error_json(422, "snapshot_corrupt", str(exc))
        except SnapshotWriteError as exc:
            self._send_error_json(500, "snapshot_write_error", str(exc))
        except SnapshotError as exc:
            self._send_error_json(500, "snapshot_error", str(exc))
        except Exception:  # keep the server alive and do not leak internals
            traceback.print_exc()
            self._send_error_json(500, "internal_error", "An unexpected error occurred.")


class KVSnapshotServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(
    store: KVStore,
    snapshots: SnapshotManager,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> KVSnapshotServer:
    server = KVSnapshotServer((host, port), KVRequestHandler)
    server.store = store
    server.snapshots = snapshots
    server.started_at = time.time()
    return server


def _seed_demo_data(store: KVStore) -> int:
    added = 0
    for key, value in DEMO_ENTRIES.items():
        if not store.exists(key):
            store.set(key, value)
            added += 1
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the KV Snapshot web dashboard and REST API."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address.")
    parser.add_argument("--port", type=int, default=8080, help="TCP port.")
    parser.add_argument(
        "--data-dir",
        default="snapshots",
        help="Directory where .kvs snapshot files are stored.",
    )
    parser.add_argument(
        "--load-latest",
        action="store_true",
        help="Restore the newest snapshot before serving requests.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Add a few sample entries when the store is empty.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the dashboard in the default browser.",
    )
    args = parser.parse_args(argv)

    store = KVStore()
    manager = SnapshotManager(args.data_dir)

    if args.load_latest:
        try:
            info = manager.restore_latest(store)
            print(f"[startup] restored {info.entries} entries from {info.name}")
        except SnapshotCorrupt as exc:
            print(f"[startup] latest snapshot is corrupt: {exc}")
        except SnapshotNotFound as exc:
            print(f"[startup] {exc}")

    if args.demo:
        added = _seed_demo_data(store)
        print(f"[demo] added {added} sample entries")

    server = create_server(store, manager, args.host, args.port)
    actual_port = server.server_address[1]
    display_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{display_host}:{actual_port}/"

    print("=" * 62)
    print(f" KV Snapshot {__version__}")
    print(f" Entries: {len(store)}   Snapshots: {len(manager.list())}")
    print(f" Data directory: {manager.directory}")
    if manager.cleaned_temp_files:
        print(f" Removed stale temp files: {', '.join(manager.cleaned_temp_files)}")
    print(f" Dashboard: {url}")
    print(" Press Ctrl+C to stop.")
    print("=" * 62, flush=True)

    if args.open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
