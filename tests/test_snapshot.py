"""Tests for the binary snapshot format and the snapshot manager."""

import hashlib
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import kvstore.snapshot as snapshot_module
from kvstore.snapshot import (
    MAGIC,
    MAX_ENTRIES,
    VERSION,
    SnapshotCorrupt,
    SnapshotManager,
    SnapshotNotFound,
    SnapshotWriteError,
    decode_snapshot,
    encode_snapshot,
    read_snapshot_file,
    write_snapshot_file,
)
from kvstore.store import KVStore, ValidationError


def _with_checksum(body: bytes) -> bytes:
    return body + hashlib.sha256(body).digest()


class SnapshotFormatTests(unittest.TestCase):
    def test_round_trip_in_memory(self):
        entries = {"alpha": "one", "beta": "two", "empty": ""}
        data = encode_snapshot(entries)
        self.assertEqual(data[:4], MAGIC)
        self.assertEqual(len(data[-32:]), 32)
        self.assertEqual(decode_snapshot(data), entries)

    def test_empty_store_round_trip(self):
        data = encode_snapshot({})
        self.assertEqual(decode_snapshot(data), {})

    def test_unicode_round_trip(self):
        entries = {"اسم": "قيمة", "emoji": "🔐🚀"}
        self.assertEqual(decode_snapshot(encode_snapshot(entries)), entries)

    def test_deterministic_output(self):
        entries = {"b": "2", "a": "1"}
        self.assertEqual(encode_snapshot(entries), encode_snapshot(dict(reversed(list(entries.items())))))

    def test_bad_checksum_is_rejected(self):
        data = bytearray(encode_snapshot({"key": "value"}))
        data[-1] ^= 0x01
        with self.assertRaisesRegex(SnapshotCorrupt, "Checksum"):
            decode_snapshot(bytes(data))

    def test_bad_magic_is_rejected(self):
        body = snapshot_module._HEADER.pack(b"BAD!", VERSION, 0)
        with self.assertRaisesRegex(SnapshotCorrupt, "magic"):
            decode_snapshot(_with_checksum(body))

    def test_unsupported_version_is_rejected(self):
        body = snapshot_module._HEADER.pack(MAGIC, 99, 0)
        with self.assertRaisesRegex(SnapshotCorrupt, "version"):
            decode_snapshot(_with_checksum(body))

    def test_truncated_file_is_rejected(self):
        data = encode_snapshot({"key": "value"})
        with self.assertRaises(SnapshotCorrupt):
            decode_snapshot(data[:10])

    def test_trailing_bytes_are_rejected(self):
        body = snapshot_module._HEADER.pack(MAGIC, VERSION, 0) + b"surprise"
        with self.assertRaisesRegex(SnapshotCorrupt, "trailing"):
            decode_snapshot(_with_checksum(body))

    def test_too_many_entries_is_rejected(self):
        body = snapshot_module._HEADER.pack(MAGIC, VERSION, MAX_ENTRIES + 1)
        with self.assertRaisesRegex(SnapshotCorrupt, "count"):
            decode_snapshot(_with_checksum(body))

    def test_invalid_key_length_is_rejected(self):
        body = (
            snapshot_module._HEADER.pack(MAGIC, VERSION, 1)
            + snapshot_module._ENTRY_HEADER.pack(0, 1)
            + b"x"
        )
        with self.assertRaisesRegex(SnapshotCorrupt, "key length"):
            decode_snapshot(_with_checksum(body))

    def test_invalid_value_length_is_rejected(self):
        body = (
            snapshot_module._HEADER.pack(MAGIC, VERSION, 1)
            + snapshot_module._ENTRY_HEADER.pack(3, 999_999)
            + b"key"
        )
        with self.assertRaisesRegex(SnapshotCorrupt, "value length"):
            decode_snapshot(_with_checksum(body))

    def test_entry_points_past_end_is_rejected(self):
        body = (
            snapshot_module._HEADER.pack(MAGIC, VERSION, 1)
            + snapshot_module._ENTRY_HEADER.pack(10, 10)
            + b"short"
        )
        with self.assertRaisesRegex(SnapshotCorrupt, "past the end"):
            decode_snapshot(_with_checksum(body))

    def test_duplicate_keys_are_rejected(self):
        entry = snapshot_module._ENTRY_HEADER.pack(3, 1) + b"key" + b"1"
        body = snapshot_module._HEADER.pack(MAGIC, VERSION, 2) + entry + entry
        with self.assertRaisesRegex(SnapshotCorrupt, "Duplicate"):
            decode_snapshot(_with_checksum(body))

    def test_invalid_utf8_is_rejected(self):
        entry = snapshot_module._ENTRY_HEADER.pack(2, 1) + b"\xff\xfe" + b"x"
        body = snapshot_module._HEADER.pack(MAGIC, VERSION, 1) + entry
        with self.assertRaisesRegex(SnapshotCorrupt, "UTF-8"):
            decode_snapshot(_with_checksum(body))

    def test_encode_rejects_invalid_entries(self):
        with self.assertRaises(ValidationError):
            encode_snapshot({"": "value"})


class SnapshotFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = KVStore()

    def test_write_and_read_file(self):
        path = self.directory / "snap-test.kvs"
        entries = {"a": "1", "b": "2"}
        size = write_snapshot_file(path, entries)
        self.assertEqual(size, path.stat().st_size)
        self.assertEqual(read_snapshot_file(path), entries)

    def test_temp_file_is_gone_after_success(self):
        path = self.directory / "snap-test.kvs"
        write_snapshot_file(path, {"key": "value"})
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_read_missing_file(self):
        with self.assertRaises(SnapshotNotFound):
            read_snapshot_file(self.directory / "missing.kvs")

    def test_manager_create_list_restore(self):
        manager = SnapshotManager(self.directory / "snapshots")
        self.store.set("name", "Seif")
        self.store.set("city", "Cairo")
        info = manager.create(self.store)
        self.assertTrue(info.name.startswith("snap-"))
        self.assertTrue(info.name.endswith(".kvs"))
        self.assertEqual(info.entries, 2)
        self.assertGreater(info.size, 44)

        listed = manager.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].name, info.name)
        self.assertTrue(listed[0].valid)

        self.store.set("extra", "later")
        restored = manager.restore(self.store, info.name)
        self.assertEqual(restored.entries, 2)
        self.assertFalse(self.store.exists("extra"))
        self.assertEqual(self.store.get("name"), "Seif")

    def test_manager_creates_unique_names(self):
        manager = SnapshotManager(self.directory / "snapshots")
        first = manager.create(self.store)
        second = manager.create(self.store)
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(len(manager.list()), 2)

    def test_manager_lists_newest_first(self):
        manager = SnapshotManager(self.directory / "snapshots")
        first = manager.create(self.store)
        self.store.set("key", "value")
        second = manager.create(self.store)
        self.assertEqual(manager.list()[0].name, second.name)
        self.assertIn(first.name, [info.name for info in manager.list()])

    def test_manager_orders_same_microsecond_collisions(self):
        manager = SnapshotManager(self.directory / "snapshots")
        data = encode_snapshot({"key": "value"})
        names = [
            "snap-20260101-000000-000001.kvs",
            "snap-20260101-000000-000001-2.kvs",
            "snap-20260101-000000-000001-3.kvs",
            "snap-20260101-000000-000001-10.kvs",
        ]
        for name in names:
            (manager.directory / name).write_bytes(data)

        listed = [info.name for info in manager.list()]
        self.assertEqual(
            listed,
            [
                "snap-20260101-000000-000001-10.kvs",
                "snap-20260101-000000-000001-3.kvs",
                "snap-20260101-000000-000001-2.kvs",
                "snap-20260101-000000-000001.kvs",
            ],
        )

    def test_restore_latest(self):
        manager = SnapshotManager(self.directory / "snapshots")
        self.store.set("old", "value")
        manager.create(self.store)
        self.store.set("new", "value")
        latest = manager.create(self.store)
        self.store.clear()
        info = manager.restore_latest(self.store)
        self.assertEqual(info.name, latest.name)
        self.assertEqual(self.store.get("new"), "value")

    def test_restore_latest_with_no_snapshots(self):
        manager = SnapshotManager(self.directory / "snapshots")
        with self.assertRaises(SnapshotNotFound):
            manager.restore_latest(self.store)

    def test_path_traversal_is_rejected(self):
        manager = SnapshotManager(self.directory / "snapshots")
        for name in ["../secret.kvs", "sub/dir.kvs", "file.txt", "", ".kvs"]:
            with self.assertRaises(ValidationError, msg=name):
                manager.restore(self.store, name)

    def test_missing_snapshot_error(self):
        manager = SnapshotManager(self.directory / "snapshots")
        with self.assertRaises(SnapshotNotFound):
            manager.restore(self.store, "snap-does-not-exist.kvs")

    def test_list_marks_invalid_header(self):
        manager = SnapshotManager(self.directory / "snapshots")
        path = manager.directory / "snap-broken.kvs"
        path.write_bytes(b"not a snapshot")
        listed = manager.list()
        self.assertEqual(len(listed), 1)
        self.assertFalse(listed[0].valid)
        self.assertEqual(listed[0].entries, 0)


if __name__ == "__main__":
    unittest.main()
