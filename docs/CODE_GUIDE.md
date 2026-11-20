# Code Discussion & Viva Guide

**Project:** KV Snapshot — Atomic Key–Value Store  
**Team Lead:** Seif Eldin Mohamed  
**Purpose:** a study companion for discussing the code with the doctor.

---

## 1. How to use this guide

Read sections 2–5 first: they give you the story and the reading order. Then use
the deep dives while the code is open. Section 10 contains 30 likely questions
with short answers. Section 12 lists small changes you can make live if the
doctor asks, “Can you show me how you would add…?”

The goal is not to memorize every line. The goal is to be able to point at a
file and say **what problem it solves, what the critical line is, and what
happens when it fails**.

---

## 2. The 60-second explanation

> KV Snapshot is an in-memory key–value store. Reads and writes are fast because
> the data lives in a Python dictionary protected by a lock. Durability is a
> separate, explicit operation: a snapshot. To create one, I copy the dictionary
> under the lock, serialize it into a binary file with a SHA-256 trailer, write
> it to a temporary file in the same directory, `fsync` the file, rename it
> atomically to its final unique name, and on POSIX `fsync` the directory. To
> restore, I read the complete file, verify its magic, version, lengths, UTF-8,
> and checksum, parse into a **new** dictionary, and only then swap it into the
> live store. If anything fails, the old snapshots and the live data are
> untouched. There is a web dashboard, a REST API, a CLI, and 72 tests including
> disk-full and corruption cases.

That is the whole project in one breath. Everything else is detail.

---

## 3. Twelve facts to know cold

1. The working data is a Python `dict` in `KVStore._data`.
2. One `threading.Lock` protects the dictionary; disk I/O never happens under it.
3. A snapshot is a **full copy**, not a log.
4. The file magic is `KVSN`; the version is `1`.
5. All integers in the file are **big-endian**.
6. The last 32 bytes are SHA-256 of everything before them.
7. The temp file must be in the **same directory** as the final file.
8. `os.replace` is the atomic rename; `os.fsync` is the durability barrier.
9. Restore validates **before** changing live state.
10. A failed restore cannot partially overwrite the store.
11. Two snapshots at once are serialized by `SnapshotManager._lock`.
12. The checksum detects corruption, not tampering or unauthorized access.

---

## 4. Reading order

If the doctor says “walk me through the code,” use this order:

1. `kvstore/store.py` — what the data is and how it is protected.
2. `kvstore/snapshot.py` — how it becomes bytes, how bytes become a file.
3. `kvstore/server.py` — how a request reaches the core.
4. `kvstore/web/app.js` — how the dashboard calls the API.
5. `tests/test_failure_modes.py` — how the safety claims are tested.
6. `README.md` — how to run it.

Do not start with HTML or CSS. Start with the data and the failure guarantees.

---

## 5. File-by-file walkthrough

### 5.1 `kvstore/store.py` — the in-memory map

**What it owns:** the dictionary, validation rules, and the store lock.

Important constants:

```python
MAX_KEY_BYTES = 256
MAX_VALUE_BYTES = 64 * 1024
```

Why bytes, not characters? A UTF-8 character can take several bytes. The file
format stores byte lengths, so the limits are expressed in bytes for consistency
between memory and disk.

`validate_key()` rejects:

- non-strings
- empty or whitespace-only keys
- control characters (including newlines)
- keys over 256 UTF-8 bytes

`validate_value()` rejects non-strings and values over 64 KiB, but allows the
empty string.

`set()` returns `True` if the key is new and `False` if it replaced an existing
value. The `key not in self._data` check and the assignment happen inside the
same lock, so the return value is accurate under concurrency.

`snapshot()` is the most important method for the project:

```python
with self._lock:
    return dict(self._data)
```

The lock guarantees that no writer can modify the dictionary while the copy is
being built. The copy is independent: later writes do not appear inside it. The
lock is released immediately after the copy; encoding and disk I/O happen
outside it.

`replace_all()` validates the whole incoming dictionary **before** taking the
lock, then swaps the reference:

```python
with self._lock:
    self._data = clean
```

A failed validation raises before the swap, so live data is unchanged.

**If asked “why not use a reader–writer lock?”**  
The critical sections are tiny (dictionary operations). One lock is easier to
reason about and avoids writer starvation or a custom implementation bug. The
project does not have a read-heavy workload that would justify the complexity.

**If asked “doesn’t the GIL make the lock unnecessary?”**  
No. The GIL guarantees that only one thread executes Python bytecode at a time,
but a compound operation such as “check if key exists, then insert” can still be
interleaved between bytecodes. The lock makes the operation atomic at the
application level. It also documents the invariant.

### 5.2 `kvstore/snapshot.py` — the format

**What it owns:** bytes, checksum, file naming, atomic write, safe restore.

Header struct:

```python
_HEADER = struct.Struct(">4sB3xI")
_ENTRY_HEADER = struct.Struct(">HI")
```

`>` means big-endian. `4s` is the magic, `B` is the version, `3x` is padding,
`I` is the entry count. Each entry has a 2-byte key length and a 4-byte value
length. Big-endian is a network byte order convention; it makes the format
independent of the machine's native endianness.

`encode_snapshot()`:

1. validates the dictionary and the entry count;
2. sorts keys for deterministic output;
3. packs the header;
4. packs each entry header and its UTF-8 bytes;
5. hashes the body and appends the 32-byte digest.

`decode_snapshot()`:

1. checks the minimum size;
2. splits the last 32 bytes;
3. compares SHA-256 with `hmac.compare_digest`;
4. unpacks and validates magic/version/count;
5. walks entries with an explicit offset;
6. bounds-checks every length before slicing;
7. decodes UTF-8 and validates each key/value;
8. rejects duplicates and trailing bytes;
9. returns a new dictionary.

Why `hmac.compare_digest` instead of `==`? It compares in constant time, which
is the correct habit for digest comparison. In this local project the timing
attack is not realistic, but the code is demonstrating the right pattern.

Why check the checksum before parsing lengths? If a length field is corrupted,
using it before verification could cause a huge slice, an allocation, or a
misleading error. The checksum is the cheapest complete integrity gate.

**If asked “why SHA-256 and not CRC32?”**  
CRC32 is enough for accidental bit flips and is smaller/faster. SHA-256 is in
the standard library, has a strong name recognition, and is simple to use. The
trade-off is a 32-byte trailer instead of 4 bytes. At this project's scale, the
cost is irrelevant.

**If asked “does the checksum cover the checksum?”**  
No. It covers the header and all entries. The trailer is the digest itself.

**If asked “can an attacker modify the file?”**  
Yes, and recompute the checksum. It is not a MAC or signature. The threat model
is accidental corruption, not malicious tampering.

### 5.3 `write_snapshot_file()` — the atomic writer

The critical sequence:

```python
temp_path = path.with_name(path.name + TEMP_SUFFIX)

with open(temp_path, "wb") as handle:
    handle.write(data)
    handle.flush()
    os.fsync(handle.fileno())

os.replace(temp_path, path)
_fsync_directory(path.parent)
```

Why `with`? It closes the file even if `write` or `fsync` fails.

Why `flush()` before `fsync()`? Python's file object has its own buffer. `fsync`
works on the descriptor, so the data must be pushed from the Python buffer to the
operating system first.

Why `os.replace` rather than `os.rename`? `os.replace` is the cross-platform
spelling of an atomic rename, and it can replace an existing destination on both
POSIX and Windows. Here each snapshot has a unique name, so the target normally
does not exist; the guarantee we rely on is that the final name appears only
after a complete file has been fsynced.

Why same directory? A rename across filesystems is not atomic; the OS may
implement it as copy + delete. Keeping the temp file beside the target avoids
that.

Why catch `OSError` and unlink the temp? A failed snapshot should not leave a
misleading partial file. The cleanup is best-effort; if unlink also fails, the
startup cleanup catches it later.

**If asked “what if the process dies between write and rename?”**  
The old snapshot is untouched. A `.tmp` file may remain; `SnapshotManager`
deletes `*.tmp` on startup. It is never treated as a valid snapshot.

**If asked “what if the process dies after rename?”**  
The new file is complete because `fsync` completed before the rename. Older
snapshots remain untouched; the new one is a complete snapshot.

**If asked “what if power is lost after rename but before directory fsync?”**  
On POSIX, the new directory entry may or may not survive. Either outcome is
safe: older snapshots remain, and if the new name exists it is a complete file.
There is never a half-file. On Windows, the directory `fsync` is skipped, which
is documented.

### 5.4 `SnapshotManager` — names, locks, and restore

`_path_for()` rejects anything that is not a plain file name ending in `.kvs`:

```python
or Path(name).name != name
or not name.endswith(SNAPSHOT_SUFFIX)
```

`Path(name).name != name` rejects `../x.kvs`, `sub/x.kvs`, and absolute paths.
The resolved parent check is a second layer of defense.

`create()`:

- takes the manager lock;
- copies the store;
- generates a UTC timestamp name;
- writes atomically;
- returns metadata.

The manager lock serializes create and restore. It is separate from the store
lock: the store lock protects the dictionary, the manager lock protects the
snapshot operation and directory.

`restore()`:

- takes the manager lock;
- validates the name;
- checks that the file exists;
- reads and fully parses it;
- calls `store.replace_all()`;
- returns metadata.

The live store is not touched until `replace_all()`; if reading or parsing fails,
the exception propagates and the live store remains exactly as it was.

**If asked “why not hold the store lock during restore?”**  
Validation can take time proportional to the file size. Holding the store lock
would block every read and write. The code validates into a new dictionary and
swaps only at the end. A write that occurs during validation is overwritten by
the snapshot at the swap; that is the documented semantics of a rollback.

`list()` reads only the 12-byte header (`read_entry_count`) so the dashboard does
not hash every file. The full checksum is verified on restore.

### 5.5 `kvstore/server.py` — the API

The server is a `ThreadingHTTPServer`; each request gets a thread. `create_server`
attaches the store, manager, and start time to the server object, so the handler
can access them through `self.server`.

Routing is explicit:

```python
if path == "/api/entries":
    ...
```

No framework, no hidden magic. A reader can see every route in one file.

`_dispatch()` is the error boundary. Core code raises domain exceptions; the
server maps them to HTTP:

- `ValidationError` → 400
- `KeyNotFound` / `SnapshotNotFound` → 404
- `SnapshotCorrupt` → 422
- `SnapshotWriteError` / `SnapshotError` → 500

Unexpected exceptions are logged with a traceback on the server and returned as
a generic 500, so internals are not leaked to the client.

Static serving uses a fixed dict of allowed files. There is no generic
“serve any path under the project” handler, which avoids directory traversal.
The dashboard gets a Content-Security-Policy that allows only same-origin scripts
and styles.

**If asked “why not Flask?”**  
The project is a systems-programming exercise; the standard library keeps the
dependency count at zero and makes the HTTP layer visible. Flask would be fine
in production, but it would hide the request/response mechanics we are studying.

**If asked “is the API secure?”**  
It binds to localhost by default, has no authentication, no TLS, and no CORS
opening. It is a local demo, not a public service. The input limits and path
checks defend against malformed local requests; they are not a substitute for
authentication.

### 5.6 `kvstore/web/app.js` — the dashboard

The dashboard is a client of the API, not a second implementation. It:

- loads status, entries, and snapshots in parallel;
- renders rows with `document.createElement` and `textContent`;
- uses a `<dialog>` for add/edit and confirmation;
- shows errors from the API in a banner and toast;
- logs session actions in the activity panel.

Why `textContent` instead of `innerHTML`? A value such as
`<img src=x onerror=...>` is data, not markup. `textContent` escapes it. This is
the simplest defense against stored XSS.

The snapshot list uses the `valid` flag from the server. If the header is
unreadable, the restore button is disabled. The server still performs full
checksum validation on every restore.

### 5.7 `kvstore/cli.py` — the terminal client

The CLI splits commands with `shlex`, so quoted values work:

```text
set greeting "hello from the CLI"
```

It calls the same `KVStore` and `SnapshotManager`; there is no duplicate storage
logic. It is useful for a projector demo and for testing the core without HTTP.

---

## 6. Concurrency: what is protected by what

| Shared resource | Lock | Held during disk I/O? |
| --- | --- | --- |
| `KVStore._data` | `KVStore._lock` | No |
| Snapshot create/restore sequence | `SnapshotManager._lock` | Yes (intentionally serialized) |
| HTTP request parsing | none (per-request thread) | No |

Two snapshots at once:

```text
Thread A: manager lock acquired -> copy -> write -> rename -> release
Thread B: waits -> copy -> write -> rename -> release
```

A write during a snapshot:

```text
Snapshot thread: store lock -> copy -> release -> encode/write
Writer thread:   waits for store lock only during copy, then writes freely
```

The snapshot contains the state at the moment of the copy, not the state at the
moment the file is renamed. That is the correct meaning of a point-in-time
snapshot.

A write during restore:

```text
Restore thread: read/validate into new dict (no store lock)
Writer thread:  normal write to current dict
Restore thread: store lock -> swap reference -> release
```

The write during validation is lost: the swap replaces the whole dictionary. The
GUI confirmation warns that restore is a rollback. This is a deliberate
semantic choice, not a race bug.

---

## 7. Crash timeline

| Time | Action | Disk after a crash |
| --- | --- | --- |
| T1 | Older snapshots exist | Older snapshots valid |
| T2 | Temp file created and partially written | Older valid; temp partial (cleaned later) |
| T3 | Temp fully written, `fsync` done | Older valid; temp complete (cleaned later) |
| T4 | `os.replace` done | New snapshot name valid; older snapshots remain |
| T5 | Directory `fsync` done | New directory entry durable |
| T6 | Response sent | New snapshot listed |

The important invariant: **at no point is the final name a partially written
file.** The final name is only ever created by the atomic rename of a complete,
fsynced temp file.

---

## 8. Why the restore validation order matters

A tempting implementation is:

```python
with open(path, "rb") as f:
    store.clear()
    for each entry:
        store.set(...)
```

This is wrong. If the file is corrupt halfway through, the live store has already
been cleared or partially overwritten. The project instead:

1. reads the whole file;
2. verifies the checksum;
3. parses into a **separate** dictionary;
4. validates every key/value;
5. swaps the reference under the lock.

The live state is a single pointer swap, so it cannot be half-old and half-new.

---

## 9. Windows versus Linux

| Topic | Linux/macOS | Windows |
| --- | --- | --- |
| `os.replace` | Atomic `rename(2)` on one filesystem | Atomic replace on one volume (NTFS) |
| File `fsync` | Supported | Supported |
| Directory `fsync` | Supported via a directory descriptor | Not supported; code skips it |
| Path separator | `/` | `\`; `Path` handles both |
| Tests | CI on Ubuntu | Local + CI on Windows |

Be honest about the difference: the protocol is strongest on POSIX. On Windows,
the file contents are fsynced and the rename is atomic, but the directory entry
may not survive a sudden power loss with the same guarantee.

---

## 10. Thirty viva questions

**1. What is the project in one sentence?**  
An in-memory key–value store that persists through checksummed, atomically
written snapshots.

**2. Why not write every change to disk?**  
That would be a different durability model (write-ahead logging). Snapshots
batch changes and keep the write path fast; the assignment focuses on atomic
file replacement.

**3. What does “snapshot” mean here?**  
A complete copy of the store at one point in time, stored in one `.kvs` file.

**4. What is the in-memory data structure?**  
A Python `dict[str, str]` protected by `threading.Lock`.

**5. Why one lock?**  
Small critical sections; simple correctness; disk I/O outside the lock.

**6. Why is a copy made before serialization?**  
To get a consistent point-in-time view without blocking writers during I/O.

**7. What does `os.replace` do?**  
Atomically replaces the destination directory entry with the temp file on the
same filesystem.

**8. Why does the temp file live beside the target?**  
Cross-filesystem rename is not atomic.

**9. Why `fsync` the file before rename?**  
So the new file's contents are durable before the name points to it.

**10. Why `fsync` the directory?**  
To make the rename itself durable on POSIX.

**11. What happens if `fsync` fails?**  
The temp is removed, a `SnapshotWriteError` is raised, the old snapshot and live
store remain intact.

**12. What happens if rename fails?**  
Same as above: temp cleaned, old snapshot intact, 500 response.

**13. What happens if the process crashes mid-write?**  
A `.tmp` file may remain; startup cleanup removes it; the old snapshot is valid.

**14. What is in the 12-byte header?**  
Magic `KVSN`, version, 3 bytes padding, 4-byte entry count.

**15. Why big-endian?**  
A fixed, machine-independent byte order for the format.

**16. Why length-prefix entries?**  
The reader can find the next entry and bounds-check before reading.

**17. Why SHA-256?**  
Reliable corruption detection with a standard-library implementation.

**18. Does the checksum prevent tampering?**  
No. It detects accidental corruption; an attacker can recompute it.

**19. Why verify the checksum before parsing?**  
Corrupted lengths must not be trusted.

**20. Why build a new dictionary on restore?**  
So a validation failure cannot partially mutate live data.

**21. What does a failed restore do to live data?**  
Nothing. The swap happens only after full validation.

**22. Why is restore a rollback, not a merge?**  
It replaces the complete state with the snapshot's state.

**23. What happens to writes during restore validation?**  
They are discarded by the swap, because restore replaces the whole dictionary.
Restore is a deliberate rollback. The confirmation warns the user.

**24. Are two snapshots safe at the same time?**  
Yes. The manager lock serializes them, and both files are complete.

**25. What is a stale temp file?**  
A `.tmp` left by a crash; it is never a valid snapshot and is deleted at startup.

**26. How is path traversal prevented?**  
Only plain names ending in `.kvs` are accepted; `Path(name).name == name` and the
resolved parent must equal the snapshot directory.

**27. What are the size limits?**  
Key 1–256 UTF-8 bytes, value 0–64 KiB, 100,000 entries per snapshot.

**28. What is the API error model?**  
JSON `{"ok": false, "error": {"code", "message"}}` with 400/404/422/500 mapping.

**29. Why Python?**  
It exposes the OS operations directly (`os.fsync`, `os.replace`, descriptors) and
keeps the code short enough to defend; the design ports to C or Go.

**30. What would you improve next?**  
Retention policy, automatic snapshots, append-only log + compaction, encryption,
and a true power-loss test harness.

---

## 11. Whiteboard plan

If asked to explain on a whiteboard, draw five boxes:

```text
[Browser] --JSON--> [HTTP API] --> [KVStore: dict + lock]
                                      |
                                      | copy
                                      v
                               [SnapshotManager]
                                      |
                         temp -> fsync -> rename -> fsync(dir)
                                      |
                                      v
                                [snap-*.kvs]
```

Then write the restore arrow in the opposite direction and say:

> Read all -> verify checksum -> parse into a new dict -> swap under lock.

Finally write the failure rule:

> No swap until validation succeeds; no final file until the temp is complete.

That is enough to carry the discussion.

---

## 12. Small live modifications you can make

If the doctor asks you to extend the project, these are safe, local changes.

### A. Increase the maximum value size

`kvstore/store.py`:

```python
MAX_VALUE_BYTES = 128 * 1024
```

Also update the GUI counter in `kvstore/web/app.js` (`MAX_VALUE_BYTES`) and the
tests that use the limit. Explain that the file format's 4-byte length field can
represent the larger value; only the application policy changes.

### B. Change the snapshot prefix

`kvstore/snapshot.py`, `_new_name()`:

```python
name = f"backup-{stamp}{SNAPSHOT_SUFFIX}"
```

The format does not care about the prefix. The manager's `_path_for()` only
requires a plain `.kvs` name.

### C. Remove the directory `fsync` on Windows (already handled)

Point at `_fsync_directory()` and explain the `os.name != "posix"` early return.

### D. Add a `created_by` field

That is a format change, so it requires a version bump. Explain why: old readers
must reject a new version rather than misinterpret bytes. Add the field to the
header, bump `VERSION` to 2, update encode/decode and tests. This is a good answer
to “how would you evolve the format safely?”

### E. Add a `GET /api/snapshots/{name}` endpoint

It would call `manager._path_for(name)` and `read_snapshot_file`, then return the
entries. Point out that exposing `_path_for` through a public method would be
cleaner than calling a private method from the API.

### F. Add snapshot retention

In `SnapshotManager.create()`, after a successful write, list files and delete
all but the newest N. Emphasize that deletion is not atomic with creation; a
failure to delete should not fail the snapshot.

---

## 13. Common traps to avoid in the discussion

- **“The GIL makes it thread-safe.”** Wrong for compound operations. The lock is
  the reason.
- **“Rename makes the data durable.”** Rename makes the name change atomic; the
  file must be fsynced first.
- **“The checksum is security.”** It is integrity against accidents, not
  authentication.
- **“Snapshot happens on every write.”** No; it is explicit.
- **“Restore merges.”** No; it replaces.
- **“Python is slow, so the lock is unnecessary.”** Correctness is independent of
  speed.
- **“The temp file can be anywhere.”** Same filesystem, same directory.
- **“A failed restore might leave half the data.”** The swap is after validation;
  this is the central safety property.

---

## 14. Phrases that sound precise

Use these instead of vague wording:

- “The snapshot is a **consistent point-in-time copy**, not a live view.”
- “The final name is **never opened for writing**.”
- “The temp file is **fsynced before the rename**.”
- “The checksum is verified **before any entry is parsed**.”
- “Live state changes by **one reference swap** after full validation.”
- “Disk I/O happens **outside the store lock**.”
- “The manager lock **serializes snapshot operations**.”
- “A failed operation leaves **the previous valid state intact**.”

---

## 15. If you only have five minutes

1. Open `store.py`, point at `snapshot()` and the lock.
2. Open `snapshot.py`, point at `encode_snapshot()` and the checksum.
3. Point at `write_snapshot_file()` and say the five steps.
4. Point at `restore()` and say “validate, new dict, swap”.
5. Run the corruption test and show the live data is unchanged.

That is the core of the project. The dashboard and API are presentation layers
around these five points.
