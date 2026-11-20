# API Contract

Base URL: `http://127.0.0.1:8080`

The API is intentionally small and JSON-only. Every success response contains
`"ok": true`; every failure contains an `error` object with a stable `code` and
a human-readable `message`.

```json
{ "ok": true }
```

```json
{
  "ok": false,
  "error": {
    "code": "validation_error",
    "message": "Key must not be empty."
  }
}
```

## Limits

| Item | Limit |
| --- | --- |
| Request body | 2 MiB |
| Key | 1–256 UTF-8 bytes, no control characters |
| Value | 0–65,536 UTF-8 bytes (64 KiB) |
| Entries per snapshot | 100,000 |
| Snapshot name | Plain file name ending in `.kvs`, no path components |

## Endpoints

### `GET /api/health`

```json
{ "ok": true, "status": "ok", "version": "1.0.0" }
```

### `GET /api/status`

```json
{
  "ok": true,
  "version": "1.0.0",
  "entries": 6,
  "snapshots": 3,
  "latest_snapshot": {
    "name": "snap-20261118-111527-316310.kvs",
    "size_bytes": 295,
    "created_at": "2026-11-18T11:15:27.316310+00:00",
    "entries": 6,
    "valid": true
  },
  "data_dir": "D:\\project\\snapshots",
  "uptime_seconds": 49,
  "cleaned_temp_files": []
}
```

### `GET /api/entries`

```json
{
  "ok": true,
  "count": 2,
  "entries": [
    { "key": "course", "value": "Systems Programming" },
    { "key": "owner", "value": "Seif" }
  ]
}
```

Entries are sorted case-insensitively by key.

### `GET /api/entries/{key}`

`{key}` must be URL-encoded. Returns `404` when the key is absent.

```json
{ "ok": true, "key": "owner", "value": "Seif" }
```

### `POST /api/entries`

Creates or updates one entry.

```json
{ "key": "owner", "value": "Seif" }
```

- `201 Created` when the key is new (`"created": true`)
- `200 OK` when an existing key is updated (`"created": false`)

```json
{ "ok": true, "created": true, "key": "owner", "value": "Seif" }
```

### `DELETE /api/entries/{key}`

```json
{ "ok": true, "deleted": "owner" }
```

`404` if the key does not exist.

### `GET /api/snapshots`

```json
{
  "ok": true,
  "count": 2,
  "snapshots": [
    {
      "name": "snap-20261118-111527-316310.kvs",
      "size_bytes": 295,
      "created_at": "2026-11-18T11:15:27.316310+00:00",
      "entries": 6,
      "valid": true
    }
  ]
}
```

Snapshots are newest-first. `valid` is determined from the small file header;
the full checksum is verified on restore.

### `POST /api/snapshots`

Creates a consistent point-in-time snapshot.

- `201 Created`
- `500 snapshot_write_error` if the disk write fails

```json
{
  "ok": true,
  "snapshot": {
    "name": "snap-20261118-111527-316310.kvs",
    "size_bytes": 295,
    "created_at": "2026-11-18T11:15:27.316310+00:00",
    "entries": 6,
    "valid": true
  }
}
```

### `POST /api/restore`

```json
{ "file": "snap-20261118-111527-316310.kvs" }
```

- `200 OK` after the file is fully verified and the store is replaced
- `404 not_found` if the file is missing
- `400 validation_error` for an invalid file name (including `../` traversal)
- `422 snapshot_corrupt` if magic, version, lengths, UTF-8, or checksum fail

```json
{
  "ok": true,
  "restored": { "name": "snap-…kvs", "entries": 6, "…": "…" },
  "entries": 6
}
```

On any error, the live store is unchanged.

## Error codes

| HTTP | `error.code` | Meaning |
| ---: | --- | --- |
| 400 | `bad_request` | Malformed HTTP/JSON |
| 400 | `validation_error` | Key/value/file-name validation failed |
| 404 | `not_found` | Key, snapshot, or route not found |
| 405 | `method_not_allowed` | Unsupported method (for example `PUT`) |
| 413 | `body_too_large` | Body over 2 MiB |
| 422 | `snapshot_corrupt` | Snapshot failed integrity/format checks |
| 500 | `snapshot_write_error` | Disk full, permission, `fsync`, or rename failure |
| 500 | `snapshot_error` | Other snapshot read/write failure |
| 500 | `internal_error` | Unexpected server error (details only in console) |

## curl examples

```bash
# create
curl -X POST http://127.0.0.1:8080/api/entries \
  -H "Content-Type: application/json" \
  -d '{"key":"owner","value":"Seif"}'

# read all
curl http://127.0.0.1:8080/api/entries

# snapshot
curl -X POST http://127.0.0.1:8080/api/snapshots

# restore (use a real file name from the list)
curl -X POST http://127.0.0.1:8080/api/restore \
  -H "Content-Type: application/json" \
  -d '{"file":"snap-20261118-111527-316310.kvs"}'
```

## Design notes

- There is no authentication: the server binds to `127.0.0.1` by default and is
  meant for a local demo.
- The dashboard and API are same-origin, so no CORS headers are needed.
- The API never accepts a path for snapshots, only a file name. The manager
  resolves it inside the snapshot directory and rejects traversal.
- `GET /api/snapshots` does not read every full snapshot; it reads the 12-byte
  header. Restore always performs the complete validation.
