"""Snapshot file format, atomic writer, safe reader, and snapshot manager.

File layout (all integers are big-endian):

    offset  size  field
    0       4     magic bytes "KVSN"
    4       1     format version (1)
    5       3     reserved padding (zero)
    8       4     number of entries
    12      ...   entries, one after another
    end-32  32    SHA-256 of every byte before the checksum

Each entry:

    2 bytes   key length in bytes
    4 bytes   value length in bytes
    key       UTF-8 bytes
    value     UTF-8 bytes

A file is written to "<name>.tmp", flushed and fsynced, then atomically
renamed to its final unique name.  On POSIX the directory is fsynced afterwards
so the rename itself survives a power loss.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import struct
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .store import (
    KVStore,
    MAX_KEY_BYTES,
    MAX_VALUE_BYTES,
    ValidationError,
    validate_key,
    validate_value,
)

MAGIC = b"KVSN"
VERSION = 1
CHECKSUM_SIZE = 32
MAX_ENTRIES = 100_000
SNAPSHOT_SUFFIX = ".kvs"
TEMP_SUFFIX = ".tmp"

_HEADER = struct.Struct(">4sB3xI")
_ENTRY_HEADER = struct.Struct(">HI")
_MIN_FILE_SIZE = _HEADER.size + CHECKSUM_SIZE
_NAME_PATTERN = re.compile(r"^snap-(\d{8})-(\d{6})-(\d{6})(?:-(\d+))?\.kvs$")


class SnapshotError(Exception):
    """Base class for snapshot errors."""


class SnapshotNotFound(SnapshotError):
    """The requested snapshot file does not exist."""


class SnapshotCorrupt(SnapshotError):
    """The snapshot failed validation."""


class SnapshotWriteError(SnapshotError):
    """The snapshot could not be written to disk."""


@dataclass(frozen=True)
class SnapshotInfo:
    name: str
    size: int
    created_at: float
    entries: int
    valid: bool = True

    def to_dict(self) -> dict[str, object]:
        created = datetime.fromtimestamp(self.created_at, tz=timezone.utc)
        return {
            "name": self.name,
            "size_bytes": self.size,
            "created_at": created.isoformat(),
            "entries": self.entries,
            "valid": self.valid,
        }


def encode_snapshot(entries: dict[str, str]) -> bytes:
    """Serialize a dictionary into the binary snapshot format."""

    if not isinstance(entries, dict):
        raise ValidationError("Snapshot data must be a dictionary.")
    if len(entries) > MAX_ENTRIES:
        raise ValidationError(
            f"Too many entries ({len(entries)}; maximum is {MAX_ENTRIES})."
        )

    clean: dict[str, str] = {}
    for key, value in entries.items():
        clean[validate_key(key)] = validate_value(value)

    chunks = [_HEADER.pack(MAGIC, VERSION, len(clean))]
    # Sorting makes output deterministic, which is useful for tests and diffs.
    for key in sorted(clean):
        key_bytes = key.encode("utf-8")
        value_bytes = clean[key].encode("utf-8")
        chunks.append(_ENTRY_HEADER.pack(len(key_bytes), len(value_bytes)))
        chunks.append(key_bytes)
        chunks.append(value_bytes)

    body = b"".join(chunks)
    return body + hashlib.sha256(body).digest()


def decode_snapshot(data: bytes) -> dict[str, str]:
    """Validate and parse a snapshot held in memory.

    The checksum is verified before any entry is parsed, so corrupted data is
    rejected early.  Bounds checks happen before every slice.
    """

    if not isinstance(data, (bytes, bytearray)):
        raise SnapshotCorrupt("Snapshot data must be bytes.")
    data = bytes(data)

    if len(data) < _MIN_FILE_SIZE:
        raise SnapshotCorrupt("Snapshot file is too small.")

    body = data[:-CHECKSUM_SIZE]
    stored_checksum = data[-CHECKSUM_SIZE:]
    computed_checksum = hashlib.sha256(body).digest()
    if not hmac.compare_digest(stored_checksum, computed_checksum):
        raise SnapshotCorrupt("Checksum does not match; the file is corrupted.")

    magic, version, count = _HEADER.unpack_from(body, 0)
    if magic != MAGIC:
        raise SnapshotCorrupt("Bad magic bytes; this is not a KV snapshot.")
    if version != VERSION:
        raise SnapshotCorrupt(f"Unsupported snapshot version: {version}.")
    if count > MAX_ENTRIES:
        raise SnapshotCorrupt(f"Entry count {count} exceeds the safe limit.")

    offset = _HEADER.size
    entries: dict[str, str] = {}

    for _ in range(count):
        if offset + _ENTRY_HEADER.size > len(body):
            raise SnapshotCorrupt("File ends in the middle of an entry header.")
        key_length, value_length = _ENTRY_HEADER.unpack_from(body, offset)
        offset += _ENTRY_HEADER.size

        if key_length == 0 or key_length > MAX_KEY_BYTES:
            raise SnapshotCorrupt(f"Invalid key length: {key_length}.")
        if value_length > MAX_VALUE_BYTES:
            raise SnapshotCorrupt(f"Invalid value length: {value_length}.")

        end = offset + key_length + value_length
        if end > len(body):
            raise SnapshotCorrupt("Entry length points past the end of the file.")

        try:
            key = body[offset : offset + key_length].decode("utf-8")
            value = body[offset + key_length : end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SnapshotCorrupt("Entry text is not valid UTF-8.") from exc

        try:
            validate_key(key)
            validate_value(value)
        except ValidationError as exc:
            raise SnapshotCorrupt(str(exc)) from exc

        if key in entries:
            raise SnapshotCorrupt(f"Duplicate key in snapshot: {key}")
        entries[key] = value
        offset = end

    if offset != len(body):
        raise SnapshotCorrupt("Unexpected trailing bytes after the last entry.")

    return entries


def read_entry_count(path: Path) -> tuple[int, bool]:
    """Read only the header for the snapshot list.

    The full checksum is checked when a snapshot is restored.  This helper only
    needs to be cheap because the GUI calls it when listing files.
    """

    try:
        with open(path, "rb") as handle:
            header = handle.read(_HEADER.size)
        if len(header) != _HEADER.size:
            return 0, False
        magic, version, count = _HEADER.unpack(header)
        if magic != MAGIC or version != VERSION or count > MAX_ENTRIES:
            return 0, False
        return count, True
    except OSError:
        return 0, False


def _fsync_directory(directory: Path) -> None:
    """Make a rename durable on POSIX systems.

    Windows does not allow opening a directory as a normal file, so this step
    is skipped there.  The atomic rename still works; only the "power loss
    immediately after rename" guarantee is weaker on Windows.
    """

    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(str(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_snapshot_file(path: Path, entries: dict[str, str]) -> int:
    """Write a snapshot atomically and return the number of bytes written."""

    path = Path(path)
    data = encode_snapshot(entries)
    temp_path = path.with_name(path.name + TEMP_SUFFIX)

    try:
        with open(temp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        # Never leave a half-written temp file behind.
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise SnapshotWriteError(f"Could not write snapshot: {exc}") from exc

    return len(data)


def read_snapshot_file(path: Path) -> dict[str, str]:
    """Read and fully validate a snapshot file."""

    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise SnapshotNotFound(f"Snapshot not found: {path.name}") from exc
    except OSError as exc:
        raise SnapshotError(f"Could not read snapshot: {exc}") from exc
    return decode_snapshot(data)


def _snapshot_sort_key(path: Path) -> tuple[str, int, str]:
    """Sort by creation time encoded in the name, then by collision counter.

    Names sort naturally as strings, but a collision suffix such as "-2" does
    not: "snap-...623.kvs" sorts after "snap-...623-2.kvs" in reverse order even
    though the latter is newer. Parsing the timestamp and counter avoids that.
    Files with custom names fall back to their modification time.
    """

    match = _NAME_PATTERN.match(path.name)
    if match:
        stamp = "".join(match.group(1, 2, 3))
        counter = int(match.group(4) or 1)
        return (stamp, counter, path.name)
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime(
            "%Y%m%d-%H%M%S-%f"
        )
    except (OSError, OverflowError, ValueError):
        stamp = ""
    return (stamp, 0, path.name)


class SnapshotManager:
    """Create, list, and restore snapshot files in one directory."""

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.cleaned_temp_files = self._cleanup_temp_files()

    def _cleanup_temp_files(self) -> list[str]:
        removed: list[str] = []
        for path in self.directory.glob("*" + TEMP_SUFFIX):
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                pass
        return removed

    def _path_for(self, name: str) -> Path:
        if (
            not isinstance(name, str)
            or not name
            or name == SNAPSHOT_SUFFIX
            or Path(name).name != name
            or not name.endswith(SNAPSHOT_SUFFIX)
        ):
            raise ValidationError("Invalid snapshot file name.")
        path = (self.directory / name).resolve()
        if path.parent != self.directory:
            raise ValidationError("Invalid snapshot file name.")
        return path

    def _new_name(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        name = f"snap-{stamp}{SNAPSHOT_SUFFIX}"
        counter = 2
        while (self.directory / name).exists():
            name = f"snap-{stamp}-{counter}{SNAPSHOT_SUFFIX}"
            counter += 1
        return name

    def create(self, store: KVStore) -> SnapshotInfo:
        """Take a consistent copy of the store and write it atomically."""

        with self._lock:
            entries = store.snapshot()
            name = self._new_name()
            path = self.directory / name
            size = write_snapshot_file(path, entries)
            stat = path.stat()
            return SnapshotInfo(
                name=name,
                size=size,
                created_at=stat.st_mtime,
                entries=len(entries),
            )

    def restore(self, store: KVStore, name: str) -> SnapshotInfo:
        """Fully validate a snapshot, then swap it into the live store."""

        with self._lock:
            path = self._path_for(name)
            if not path.is_file():
                raise SnapshotNotFound(f"Snapshot not found: {name}")
            entries = read_snapshot_file(path)
            # Nothing in the live store changes if the read or parse failed.
            store.replace_all(entries)
            stat = path.stat()
            return SnapshotInfo(
                name=name,
                size=stat.st_size,
                created_at=stat.st_mtime,
                entries=len(entries),
            )

    def restore_latest(self, store: KVStore) -> SnapshotInfo:
        snapshots = self.list()
        if not snapshots:
            raise SnapshotNotFound("There are no snapshots to restore.")
        return self.restore(store, snapshots[0].name)

    def list(self) -> list[SnapshotInfo]:
        """Return snapshots newest-first.

        The full checksum is verified only on restore.  Listing reads just the
        small header so a large archive opens quickly.
        """

        result: list[SnapshotInfo] = []
        paths = sorted(
            self.directory.glob("*" + SNAPSHOT_SUFFIX),
            key=_snapshot_sort_key,
            reverse=True,
        )
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            count, valid = read_entry_count(path)
            result.append(
                SnapshotInfo(
                    name=path.name,
                    size=stat.st_size,
                    created_at=stat.st_mtime,
                    entries=count,
                    valid=valid,
                )
            )
        return result
