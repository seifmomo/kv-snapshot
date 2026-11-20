# Demo Script

A reliable 5–7 minute demo for the doctor. Commands are shown for Windows
(PowerShell); Linux/macOS equivalents are the same with `python3`.

## 0. Before the room

```powershell
cd "D:\programming\projects\system programming\kv-snapshot"
Remove-Item -Recurse -Force snapshots -ErrorAction SilentlyContinue
python -m unittest discover -s tests
python -m kvstore --demo --open
```

Keep a second terminal open in the project folder.

## 1. Show the interface (1 minute)

1. Point out the sidebar: **keys**, **snapshots**, storage directory, API status.
2. Explain the four steps under **Atomic save**:
   write temp → `fsync` file → rename → `fsync` directory.
3. Show the six demo entries. Say clearly:

> Reads and writes are in memory. Durability is a separate operation:
> a snapshot writes a consistent copy in a crash-safe order.

## 2. Live CRUD (1 minute)

1. Click **New entry**.
2. Key: `demo_owner`, value: `Seif Eldin Mohamed`.
3. Save. The key appears immediately.
4. Click **Edit**, change the value, save.
5. Point out that the change is **not** on disk yet. This is the point of a
   snapshot.

## 3. Snapshot (1 minute)

1. Click **Create snapshot**.
2. Show the new file in the **Snapshots** card.
3. In the second terminal:

```powershell
Get-ChildItem snapshots
Format-Hex (Get-ChildItem snapshots\*.kvs | Sort-Object Name | Select-Object -Last 1).FullName | Select-Object -First 6
```

Point out the first four bytes: `4B 56 53 4E` = `KVSN`.
The last 32 bytes are the SHA-256 trailer.

## 4. Change data, then restore (1 minute)

1. Delete `team_lead` and `course` in the GUI.
2. Show that the store lost those keys, but the snapshot still has them.
3. Click **Restore** on the snapshot and confirm.
4. Show that the deleted keys are back and `demo_owner` is gone.

Say:

> Restore replaces the live state with the snapshot's point-in-time state.
> It is a deliberate rollback, not a merge.

## 5. Corrupt a snapshot (2 minutes)

This is the strongest part of the demo. Create a snapshot first, then corrupt
one byte in a copy **outside** the live file, or corrupt the latest file after
noting that the live store is safe.

```powershell
$file = (Get-ChildItem snapshots\*.kvs | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
$bytes = [System.IO.File]::ReadAllBytes($file)
$bytes[$bytes.Length - 1] = $bytes[$bytes.Length - 1] -bxor 0xFF
[System.IO.File]::WriteAllBytes($file, $bytes)
```

1. In the GUI, click **Restore** on that file.
2. The dashboard shows a red error: **“Checksum does not match; the file is corrupted.”**
3. Show that the live entries are still there.
4. Say:

> The checksum is checked before parsing. Parsing builds a new dictionary.
> Only a fully valid snapshot is swapped in. A failed restore cannot partially
> destroy live state.

5. Delete the corrupt file so the demo can continue:

```powershell
Remove-Item $file
```

## 6. Show the tests (1 minute)

```powershell
python -m unittest discover -s tests -v
```

Highlight the failure-injection tests by name:

- `test_fsync_failure_keeps_previous_snapshot_and_cleans_temp`
- `test_rename_failure_keeps_previous_snapshot_and_cleans_temp`
- `test_corrupted_restore_does_not_change_live_store`
- `test_concurrent_creates_are_serialized_and_all_valid`
- `test_snapshot_is_a_point_in_time_copy`

Current result: **72 tests, all passing**.

## 7. Optional: restart with the latest snapshot

Stop the server with `Ctrl+C`, then:

```powershell
python -m kvstore --load-latest --open
```

The newest snapshot is restored before the server starts serving.

## Recovery if something goes wrong

```powershell
# Stop the server first (Ctrl+C), then start clean:
Remove-Item -Recurse -Force snapshots
python -m kvstore --demo --open
```

If port 8080 is busy:

```powershell
python -m kvstore --demo --port 8090 --open
```

If the browser does not open, visit the URL printed in the terminal.

## What to say if asked “why Python?”

> The project is about operating-system behaviour, not language syntax.
> Python exposes the exact operations we need: `os.fsync`, `os.replace`,
> file descriptors, and directory `fsync`. The code is small enough to defend
> line by line, and the architecture ports directly to C or Go. The GIL does
> not make check-then-write atomic, so the store still has an explicit lock.

## What not to claim

- Do not say “the data is saved automatically.” It is not: a snapshot is an
  explicit operation.
- Do not say “the snapshot is always atomic everywhere.” `os.replace` is atomic
  on one filesystem; that is why the temp file is in the same directory.
- Do not say “the checksum makes the file secret.” It detects accidental
  corruption; it is not encryption or authentication.
