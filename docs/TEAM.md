# Team Roles and Contributions

> **Status:** working draft. Seif Eldin Mohamed is the team lead. Replace the
> `[Member …]` placeholders in [`team.json`](team.json), then run
> `python tools/build_docs.py` to regenerate the report PDF with the final
> names.

## Team

| # | Member | Role | Main responsibility |
| --- | --- | --- | --- |
| 1 | **Seif Eldin Mohamed** | Team Lead / Integrator | Architecture, integration, core implementation, code review, final delivery |
| 2 | [Member 2] | KV Store Developer | In-memory map, validation, thread safety, get/set/delete/list |
| 3 | [Member 3] | Snapshot Writer | Serialization, temp file, `fsync`, atomic rename |
| 4 | [Member 4] | Restore & Validation | Parsing, checksum verification, safe state swap |
| 5 | [Member 5] | API Developer | REST endpoints, JSON validation, status/error codes |
| 6 | [Member 6] | GUI Developer 1 | Layout, key/value table, add/edit/delete interactions |
| 7 | [Member 7] | GUI Developer 2 | Snapshot/restore controls, status and error display |
| 8 | [Member 8] | QA Lead | Test plan, edge cases, failure injection, demo rehearsal |
| 9 | [Member 9] | QA / Tester | Unit/integration tests, CI, clean-clone build check |
| 10 | [Member 10] | Documentation Lead | Report, README, diagrams, demo script |

Every change should be reviewed by at least one other member before merging.

## Deliverables by area

| Area | Files | Evidence |
| --- | --- | --- |
| In-memory store | `kvstore/store.py` | `tests/test_store.py` |
| Snapshot format/writer | `kvstore/snapshot.py` | `tests/test_snapshot.py` |
| Restore and failures | `kvstore/snapshot.py` | `tests/test_failure_modes.py` |
| REST API | `kvstore/server.py` | `tests/test_api.py` |
| Dashboard | `kvstore/web/` | screenshot + demo |
| CLI | `kvstore/cli.py` | demo script |
| Documentation | `docs/` | report PDF, code guide, slides |
| CI | `.github/workflows/ci.yml` | green Actions run |

## Contribution percentages

Fill this table after the team confirms the final split. Do not publish numbers
that the team has not agreed on.

| Member | Code | Tests | Documentation | Integration | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| Seif Eldin Mohamed |  |  |  |  |  |
| [Member 2] |  |  |  |  |  |
| [Member 3] |  |  |  |  |  |
| [Member 4] |  |  |  |  |  |
| [Member 5] |  |  |  |  |  |
| [Member 6] |  |  |  |  |  |
| [Member 7] |  |  |  |  |  |
| [Member 8] |  |  |  |  |  |
| [Member 9] |  |  |  |  |  |
| [Member 10] |  |  |  |  |  |

## Meeting log template

| Date | Attendees | Decisions | Blockers | Owner |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |
|  |  |  |  |  |
|  |  |  |  |  |

## Suggested split if the team codes together

The project is deliberately layered so ten people can work without editing the
same file:

1. **Store:** `store.py` + `test_store.py`.
2. **Format:** `encode_snapshot` / `decode_snapshot` + format tests.
3. **Writer:** `write_snapshot_file` + failure tests for `fsync` and rename.
4. **Restore:** `SnapshotManager.restore` + corruption tests.
5. **API:** `server.py` routes + API tests.
6. **GUI core:** table, add/edit/delete in `app.js`.
7. **GUI polish:** snapshots, restore confirmation, errors, responsive CSS.
8. **QA lead:** test matrix, failure injection, acceptance checklist.
9. **QA/tester:** CI, clean-clone test, manual regression.
10. **Docs lead:** report, diagrams, demo script, slides.

The team lead owns the interfaces: `KVStore` methods, snapshot file format, API
JSON shape, and the error model. Freezing those interfaces early is what lets
everyone work in parallel.
