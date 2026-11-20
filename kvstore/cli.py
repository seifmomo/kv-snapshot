"""Interactive command-line client for the same store and snapshot engine."""

from __future__ import annotations

import argparse
import shlex

from .snapshot import SnapshotCorrupt, SnapshotError, SnapshotManager
from .store import KeyNotFound, KVStore, StoreError

HELP = """\
Commands:
  set <key> <value>     Insert or update an entry
  get <key>             Print one value
  del <key>             Delete an entry
  list                  Show every entry
  snapshot              Create a new snapshot
  snapshots             List snapshot files
  restore <file>        Restore a snapshot (validated first)
  status                Show sizes and paths
  help                  Show this help
  quit                  Exit

Wrap values containing spaces in quotes:
  set greeting "hello from the CLI"
"""


def _short(text: str, width: int = 60) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= width else text[: width - 3] + "..."


def _print_entries(store: KVStore) -> None:
    entries = store.items()
    if not entries:
        print("(store is empty)")
        return
    key_width = max(3, min(30, max(len(key) for key, _ in entries)))
    print(f"{'KEY'.ljust(key_width)}  VALUE")
    print(f"{'-' * key_width}  {'-' * 40}")
    for key, value in entries:
        shown_key = key if len(key) <= key_width else key[: key_width - 3] + "..."
        print(f"{shown_key.ljust(key_width)}  {_short(value)}")
    print(f"\n{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")


def _print_snapshots(manager: SnapshotManager) -> None:
    snapshots = manager.list()
    if not snapshots:
        print("(no snapshots yet)")
        return
    print(f"{'SNAPSHOT':48} {'ENTRIES':>7} {'SIZE':>10}")
    print("-" * 68)
    for info in snapshots:
        print(f"{info.name:48} {info.entries:>7} {info.size:>10,} B")
    print(f"\n{len(snapshots)} snapshot(s) in {manager.directory}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KV Snapshot command-line client.")
    parser.add_argument("--data-dir", default="snapshots", help="Snapshot directory.")
    parser.add_argument(
        "--load-latest",
        action="store_true",
        help="Restore the newest snapshot on startup.",
    )
    args = parser.parse_args(argv)

    store = KVStore()
    manager = SnapshotManager(args.data_dir)
    if args.load_latest:
        try:
            info = manager.restore_latest(store)
            print(f"Restored {info.entries} entries from {info.name}")
        except SnapshotError as exc:
            print(f"Could not restore: {exc}")

    print("KV Snapshot CLI. Type 'help' for commands.")
    print(f"Data directory: {manager.directory}")

    while True:
        try:
            line = input("kv> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        try:
            parts = shlex.split(line)
        except ValueError as exc:
            print(f"error: {exc}")
            continue

        command = parts[0].lower()
        try:
            if command in {"quit", "exit"}:
                break
            if command == "help":
                print(HELP)
            elif command == "set":
                if len(parts) < 3:
                    print("usage: set <key> <value>")
                    continue
                value = " ".join(parts[2:])
                created = store.set(parts[1], value)
                print("created" if created else "updated")
            elif command == "get":
                if len(parts) != 2:
                    print("usage: get <key>")
                    continue
                print(store.get(parts[1]))
            elif command == "del":
                if len(parts) != 2:
                    print("usage: del <key>")
                    continue
                print("deleted" if store.delete(parts[1]) else "key not found")
            elif command == "list":
                _print_entries(store)
            elif command == "snapshot":
                info = manager.create(store)
                print(f"created {info.name} ({info.entries} entries, {info.size} bytes)")
            elif command == "snapshots":
                _print_snapshots(manager)
            elif command == "restore":
                if len(parts) != 2:
                    print("usage: restore <file>")
                    continue
                info = manager.restore(store, parts[1])
                print(f"restored {info.entries} entries from {info.name}")
            elif command == "status":
                print(f"entries:   {len(store)}")
                print(f"snapshots: {len(manager.list())}")
                print(f"data dir:  {manager.directory}")
            else:
                print(f"unknown command: {command}. Type 'help'.")
        except (StoreError, SnapshotCorrupt, SnapshotError) as exc:
            print(f"error: {exc}")

    print("bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
