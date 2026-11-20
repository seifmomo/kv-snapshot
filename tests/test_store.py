"""Unit tests for the in-memory store."""

import threading
import unittest

from kvstore.store import (
    MAX_KEY_BYTES,
    MAX_VALUE_BYTES,
    KeyNotFound,
    KVStore,
    ValidationError,
)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = KVStore()

    def test_set_and_get(self):
        created = self.store.set("name", "Seif")
        self.assertTrue(created)
        self.assertEqual(self.store.get("name"), "Seif")

    def test_overwrite_returns_false_and_keeps_latest_value(self):
        self.store.set("key", "first")
        created = self.store.set("key", "second")
        self.assertFalse(created)
        self.assertEqual(self.store.get("key"), "second")
        self.assertEqual(len(self.store), 1)

    def test_delete(self):
        self.store.set("temporary", "value")
        self.assertTrue(self.store.delete("temporary"))
        self.assertFalse(self.store.delete("temporary"))
        with self.assertRaises(KeyNotFound):
            self.store.get("temporary")

    def test_missing_key_raises(self):
        with self.assertRaises(KeyNotFound):
            self.store.get("does-not-exist")

    def test_empty_key_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.set("", "value")
        with self.assertRaises(ValidationError):
            self.store.set("   ", "value")

    def test_control_characters_rejected_in_key(self):
        with self.assertRaises(ValidationError):
            self.store.set("line\nbreak", "value")

    def test_key_size_limit(self):
        self.store.set("k" * MAX_KEY_BYTES, "value")
        with self.assertRaises(ValidationError):
            self.store.set("k" * (MAX_KEY_BYTES + 1), "value")

    def test_value_size_limit(self):
        self.store.set("large", "x" * MAX_VALUE_BYTES)
        with self.assertRaises(ValidationError):
            self.store.set("too-large", "x" * (MAX_VALUE_BYTES + 1))

    def test_empty_value_is_allowed(self):
        self.store.set("empty", "")
        self.assertEqual(self.store.get("empty"), "")

    def test_non_string_inputs_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.set(123, "value")
        with self.assertRaises(ValidationError):
            self.store.set("key", 123)

    def test_unicode_keys_and_values(self):
        self.store.set("مفتاح", "قيمة عربية")
        self.store.set("emoji", "🚀🔐")
        self.assertEqual(self.store.get("مفتاح"), "قيمة عربية")
        self.assertEqual(self.store.get("emoji"), "🚀🔐")

    def test_items_are_sorted_and_are_a_copy(self):
        self.store.set("banana", "2")
        self.store.set("Apple", "1")
        items = self.store.items()
        self.assertEqual([key for key, _ in items], ["Apple", "banana"])
        items.append(("fake", "entry"))
        self.assertEqual(len(self.store), 2)

    def test_snapshot_is_isolated_from_later_writes(self):
        self.store.set("a", "1")
        copy = self.store.snapshot()
        self.store.set("b", "2")
        self.assertNotIn("b", copy)
        self.assertEqual(len(copy), 1)

    def test_replace_all_swaps_complete_state(self):
        self.store.set("old", "value")
        count = self.store.replace_all({"new": "state", "second": "entry"})
        self.assertEqual(count, 2)
        self.assertFalse(self.store.exists("old"))
        self.assertEqual(self.store.get("new"), "state")

    def test_replace_all_validation_failure_keeps_live_state(self):
        self.store.set("safe", "value")
        with self.assertRaises(ValidationError):
            self.store.replace_all({"safe": "changed", "": "invalid key"})
        self.assertEqual(self.store.get("safe"), "value")

    def test_clear(self):
        self.store.set("a", "1")
        self.store.set("b", "2")
        self.assertEqual(self.store.clear(), 2)
        self.assertEqual(len(self.store), 0)

    def test_concurrent_writes_from_many_threads(self):
        errors = []

        def worker(start):
            try:
                for number in range(100):
                    self.store.set(f"thread-{start}-{number}", str(number))
            except Exception as exc:  # pragma: no cover - only on failure
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.store), 800)
        self.assertEqual(self.store.get("thread-7-99"), "99")

    def test_concurrent_reads_and_writes_do_not_crash(self):
        self.store.set("shared", "initial")

        def writer():
            for number in range(200):
                self.store.set("shared", str(number))

        def reader():
            for _ in range(200):
                self.assertIsInstance(self.store.get("shared"), str)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertIn(self.store.get("shared"), {str(number) for number in range(200)})


if __name__ == "__main__":
    unittest.main()
