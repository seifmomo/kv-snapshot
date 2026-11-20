"""Thread-safe in-memory key-value store.

The store is deliberately small.  All durability logic lives in snapshot.py so
that the in-memory data structure and the on-disk format can be studied (and
tested) separately.
"""

from __future__ import annotations

import threading

MAX_KEY_BYTES = 256
MAX_VALUE_BYTES = 64 * 1024  # 64 KiB


class StoreError(Exception):
    """Base class for errors raised by the key-value store."""


class ValidationError(StoreError):
    """The caller supplied invalid data."""


class KeyNotFound(StoreError):
    """The requested key is not present in the store."""


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def validate_key(key: object) -> str:
    """Validate a key and return it unchanged.

    A key must be a non-empty string, must not contain control characters, and
    must encode to at most MAX_KEY_BYTES bytes in UTF-8.
    """

    if not isinstance(key, str):
        raise ValidationError("Key must be a string.")
    if not key or not key.strip():
        raise ValidationError("Key must not be empty.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in key):
        raise ValidationError("Key must not contain control characters.")
    size = _utf8_size(key)
    if size > MAX_KEY_BYTES:
        raise ValidationError(
            f"Key is too long ({size} bytes; maximum is {MAX_KEY_BYTES})."
        )
    return key


def validate_value(value: object) -> str:
    """Validate a value.  Empty values are allowed; values are UTF-8 text."""

    if not isinstance(value, str):
        raise ValidationError("Value must be a string.")
    size = _utf8_size(value)
    if size > MAX_VALUE_BYTES:
        raise ValidationError(
            f"Value is too long ({size} bytes; maximum is {MAX_VALUE_BYTES})."
        )
    return value


class KVStore:
    """A dict protected by one lock.

    One lock is enough for this project: the critical sections are tiny, and a
    single lock is much easier to reason about than a custom read/write lock.
    Disk I/O never happens while this lock is held.
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: str) -> bool:
        """Insert or update a key.

        Returns True when a new key was created and False when an existing key
        was overwritten.
        """

        key = validate_key(key)
        value = validate_value(value)
        with self._lock:
            created = key not in self._data
            self._data[key] = value
            return created

    def get(self, key: str) -> str:
        key = validate_key(key)
        with self._lock:
            try:
                return self._data[key]
            except KeyError:
                raise KeyNotFound(f"Key not found: {key}") from None

    def exists(self, key: str) -> bool:
        key = validate_key(key)
        with self._lock:
            return key in self._data

    def delete(self, key: str) -> bool:
        """Delete a key.  Returns True if it existed."""

        key = validate_key(key)
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def items(self) -> list[tuple[str, str]]:
        """Return a sorted copy of all entries."""

        with self._lock:
            pairs = list(self._data.items())
        return sorted(pairs, key=lambda item: item[0].lower())

    def snapshot(self) -> dict[str, str]:
        """Return a consistent copy of the current state.

        The copy is made while holding the lock, then the lock is released
        before any slow serialization or disk work happens.
        """

        with self._lock:
            return dict(self._data)

    def replace_all(self, entries: dict[str, str]) -> int:
        """Validate a complete data set and swap it in atomically.

        Validation happens before the lock is taken.  If anything is invalid,
        the live data is untouched.
        """

        if not isinstance(entries, dict):
            raise ValidationError("Snapshot data must be a dictionary.")
        clean: dict[str, str] = {}
        for key, value in entries.items():
            clean[validate_key(key)] = validate_value(value)
        with self._lock:
            self._data = clean
        return len(clean)

    def clear(self) -> int:
        with self._lock:
            count = len(self._data)
            self._data = {}
            return count

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
