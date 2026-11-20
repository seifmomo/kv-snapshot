# Submission Checklist

Use this list before handing the project in or presenting it.

## 1. Team data

- [ ] Open [`docs/team.json`](team.json).
- [ ] Replace every `[Member …]` name and the `"Planned: …"` contribution text.
- [ ] Fill in the institution if the cover should show it.
- [ ] Confirm the contribution percentages in [`docs/TEAM.md`](TEAM.md); do not
      publish numbers the team has not agreed on.
- [ ] Run `python tools/build_docs.py` to rebuild the PDFs and slides with the
      final names.

## 2. Verification

- [ ] `python -m unittest discover -s tests -v` → **72 tests pass**.
- [ ] `python -m kvstore --demo --open` → dashboard loads at
      <http://127.0.0.1:8080>.
- [ ] Create an entry, edit it, delete it.
- [ ] Create two snapshots; confirm both appear in **Saved copies**.
- [ ] Restore the older snapshot; confirm the confirmation text and result.
- [ ] Stop the server, restart with `--load-latest`; confirm the restored state.
- [ ] Corrupt a copy of a `.kvs` file and confirm restore returns an error
      without changing live data (see [`DEMO.md`](DEMO.md)).

## 3. Documents

- [ ] [`KV-Snapshot-Project-Report.pdf`](KV-Snapshot-Project-Report.pdf) opens
      and the contents page numbers match.
- [ ] [`KV-Snapshot-Code-Discussion-Guide.pdf`](KV-Snapshot-Code-Discussion-Guide.pdf)
      opens.
- [ ] [`KV-Snapshot-Presentation.pptx`](KV-Snapshot-Presentation.pptx) opens and
      the speaker notes are present.
- [ ] The report's team table shows the final names, not brackets.
- [ ] The repository URL in the README and slides is correct.

## 4. Repository

- [ ] `git status` is clean.
- [ ] The public repository is reachable at
      <https://github.com/seifmomo/kv-snapshot>.
- [ ] The **CI** badge is green on the default branch.
- [ ] The MIT licence and `.gitignore` are present.
- [ ] No snapshot files, caches, or local paths are committed.

## 5. Package

```bash
python tools/package_submission.py
```

This writes `dist/KV-Snapshot-Submission.zip` with a clean copy of the project
and the generated documents. The `dist/` folder is intentionally ignored by
Git.

## 6. Five-minute demo order

1. Open the dashboard and point out the explicit **in-memory until saved**
   subtitle.
2. Add one entry, then create a snapshot.
3. Change the entry and create a second snapshot.
4. Restore the first snapshot; show that the change is rolled back.
5. Run the corrupt-file demo from [`DEMO.md`](DEMO.md).
6. Show the test summary: **72 tests, OK**.
