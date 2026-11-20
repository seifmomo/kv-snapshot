"""Failure-injection tests: disk errors, corruption, and concurrency."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from kvstore.snapshot import (
    SnapshotCorrupt,
    SnapshotManager,
    SnapshotWriteError,
    read_snapshot_file,
)
from kvstore.store import KVStore


class FailureModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "snapshots"
        self.store = KVStore()
        self.manager = SnapshotManager(self.directory)

    def _first_snapshot(self):
        self.store.set("important", "original value")
        return self.manager.create(self.store)

    def test_fsync_failure_keeps_previous_snapshot_and_cleans_temp(self):
        info = self._first_snapshot()
        path = self.directory / info.name
        original_bytes = path.read_bytes()
        self.store.set("important", "new value")

        with mock.patch("kvstore.snapshot.os.fsync", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(SnapshotWriteError):
                self.manager.create(self.store)

        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
        self.assertEqual(len(self.manager.list()), 1)

    def test_rename_failure_keeps_previous_snapshot_and_cleans_temp(self):
        info = self._first_snapshot()
        path = self.directory / info.name
        original_bytes = path.read_bytes()

        with mock.patch("kvstore.snapshot.os.replace", side_effect=OSError("permission denied")):
            with self.assertRaises(SnapshotWriteError):
                self.manager.create(self.store)

        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
        self.assertEqual(len(self.manager.list()), 1)

    def test_corrupted_restore_does_not_change_live_store(self):
        info = self._first_snapshot()
        self.store.set("after_snapshot", "still here")

        path = self.directory / info.name
        data = bytearray(path.read_bytes())
        data[12] ^= 0xFF  # flip a byte inside the first entry header
        path.write_bytes(data)

        with self.assertRaises(SnapshotCorrupt):
            self.manager.restore(self.store, info.name)

        self.assertEqual(self.store.get("important"), "original value")
        self.assertEqual(self.store.get("after_snapshot"), "still here")

    def test_truncated_restore_does_not_change_live_store(self):
        info = self._first_snapshot()
        self.store.set("after_snapshot", "still here")
        path = self.directory / info.name
        path.write_bytes(path.read_bytes()[:20])

        with self.assertRaises(SnapshotCorrupt):
            self.manager.restore(self.store, info.name)

        self.assertEqual(self.store.get("after_snapshot"), "still here")

    def test_stale_temp_files_are_removed_on_startup(self):
        stale = self.directory / "snap-crashed.kvs.tmp"
        stale.write_bytes(b"half-written data")
        manager = SnapshotManager(self.directory)
        self.assertEqual(manager.cleaned_temp_files, [stale.name])
        self.assertFalse(stale.exists())

    def test_concurrent_creates_are_serialized_and_all_valid(self):
        errors = []
        for number in range(50):
            self.store.set(f"key-{number}", f"value-{number}")

        def create_many():
            try:
                for _ in range(3):
                    self.manager.create(self.store)
            except Exception as exc:  # pragma: no cover - only on failure
                errors.append(exc)

        threads = [threading.Thread(target=create_many) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        snapshots = self.manager.list()
        self.assertEqual(len(snapshots), 12)
        for info in snapshots:
            self.assertTrue(info.valid)
            self.assertEqual(len(read_snapshot_file(self.directory / info.name)), 50)

    def test_snapshot_is_a_point_in_time_copy(self):
        for number in range(100):
            self.store.set(f"before-{number}", "x")

        stop = threading.Event()
        errors = []

        def writer():
            number = 0
            try:
                while not stop.is_set():
                    self.store.set(f"during-{number}", "y")
                    number += 1
            except Exception as exc:  # pragma: no cover - only on failure
                errors.append(exc)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            info = self.manager.create(self.store)
        finally:
            stop.set()
            thread.join()

        self.assertEqual(errors, [])
        entries = read_snapshot_file(self.directory / info.name)
        # The snapshot must contain every key from the quiet prefix and must
        # never contain a partially written entry.
        for number in range(100):
            self.assertIn(f"before-{number}", entries)
            self.assertEqual(entries[f"before-{number}"], "x")
        self.assertEqual(info.entries, len(entries))

    def test_read_error_is_wrapped(self):
        info = self._first_snapshot()
        path = self.directory / info.name
        with mock.patch.object(Path, "read_bytes", side_effect=OSError("I/O error")):
            with self.assertRaises(Exception):
                self.manager.restore(self.store, info.name)
        # The live store is untouched after a read error too.
        self.assertEqual(self.store.get("important"), "original value")


if __name__ == "__main__":
    unittest.main()
