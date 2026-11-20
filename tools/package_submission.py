#!/usr/bin/env python3
"""Create a clean submission zip for the KV Snapshot project.

Usage:
    python tools/package_submission.py

The archive is written to ``dist/KV-Snapshot-Submission.zip`` and contains a
single top-level ``kv-snapshot/`` folder.  Generated caches, local snapshot
files, and the ``dist`` folder itself are excluded.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
ARCHIVE = DIST / "KV-Snapshot-Submission.zip"

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "snapshots",
    "ci-snapshots",
    "dist",
    "demo-data",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".kvs", ".tmp"}


def included_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def main() -> int:
    team_data = json.loads((ROOT / "docs" / "team.json").read_text(encoding="utf-8"))
    pending = [
        member["name"]
        for member in team_data.get("members", [])
        if str(member.get("name", "")).strip().startswith("[")
    ]

    manifest = [
        "KV Snapshot — submission package",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "Tests: 72",
        "Run: python -m kvstore --demo --open",
        "Docs: docs/KV-Snapshot-Project-Report.pdf",
        "      docs/KV-Snapshot-Code-Discussion-Guide.pdf",
        "      docs/KV-Snapshot-Presentation.pptx",
        "",
        (
            f"Team placeholders still present: {len(pending)} "
            "(update docs/team.json and rebuild the PDFs)"
            if pending
            else "Team data: complete"
        ),
        "",
    ]

    files = included_files()
    DIST.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("kv-snapshot/SUBMISSION.txt", "\n".join(manifest))
        for path in files:
            archive.write(path, f"kv-snapshot/{path.relative_to(ROOT).as_posix()}")

    size_mib = ARCHIVE.stat().st_size / (1024 * 1024)
    print(f"Wrote {ARCHIVE.relative_to(ROOT)}")
    print(f"  {len(files) + 1} files, {size_mib:.1f} MiB")
    if pending:
        print(f"  note: {len(pending)} team placeholders remain in docs/team.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
