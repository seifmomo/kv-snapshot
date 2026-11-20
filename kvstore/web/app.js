(() => {
  "use strict";

  const MAX_KEY_BYTES = 256;
  const MAX_VALUE_BYTES = 64 * 1024;

  const state = {
    entries: [],
    snapshots: [],
    status: null,
    filter: "",
    editingKey: null,
    log: [],
    loading: false,
    connected: false,
    confirmResolve: null,
  };

  const $ = (id) => document.getElementById(id);
  const numberFormat = new Intl.NumberFormat();
  const dateFormat = new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  const encoder = new TextEncoder();
  let toastTimer = null;

  const els = {
    statEntries: $("statEntries"),
    statSnapshots: $("statSnapshots"),
    lastSaved: $("lastSaved"),
    subtitle: $("subtitle"),
    healthText: $("healthText"),
    statusDot: document.querySelector(".status-dot"),
    alert: $("alert"),
    alertText: $("alertText"),
    alertClose: $("alertClose"),
    entriesList: $("entriesList"),
    entriesHint: $("entriesHint"),
    emptyEntries: $("emptyEntries"),
    emptyEntriesTitle: $("emptyEntriesTitle"),
    emptyEntriesText: $("emptyEntriesText"),
    searchInput: $("searchInput"),
    snapshotList: $("snapshotList"),
    emptySnapshots: $("emptySnapshots"),
    snapshotsHint: $("snapshotsHint"),
    snapshotsPill: $("snapshotsPill"),
    activityList: $("activityList"),
    dataDir: $("dataDir"),
    uptime: $("uptime"),
    refreshBtn: $("refreshBtn"),
    snapshotBtn: $("snapshotBtn"),
    newBtn: $("newBtn"),
    clearLogBtn: $("clearLogBtn"),
    entryDialog: $("entryDialog"),
    entryForm: $("entryForm"),
    entryKicker: $("entryKicker"),
    entryDialogTitle: $("entryDialogTitle"),
    entryClose: $("entryClose"),
    entryCancel: $("entryCancel"),
    entrySave: $("entrySave"),
    keyInput: $("keyInput"),
    valueInput: $("valueInput"),
    keyCounter: $("keyCounter"),
    valueCounter: $("valueCounter"),
    entryError: $("entryError"),
    confirmDialog: $("confirmDialog"),
    confirmTitle: $("confirmTitle"),
    confirmMessage: $("confirmMessage"),
    confirmOk: $("confirmOk"),
    confirmCancel: $("confirmCancel"),
    confirmClose: $("confirmClose"),
    toast: $("toast"),
  };

  // ------------------------------------------------------------------
  // Small helpers
  // ------------------------------------------------------------------
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function byteLength(text) {
    return encoder.encode(text).length;
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "unknown";
    return dateFormat.format(date);
  }

  function formatUptime(seconds) {
    if (!Number.isFinite(seconds)) return "—";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    if (mins < 1) return `${secs}s`;
    if (mins < 60) return `${mins}m ${secs}s`;
    return `${Math.floor(mins / 60)}h ${mins % 60}m`;
  }

  function plural(count, singular, pluralForm = `${singular}s`) {
    return `${numberFormat.format(count)} ${count === 1 ? singular : pluralForm}`;
  }

  async function api(path, options = {}) {
    const settings = { ...options, headers: { ...(options.headers || {}) } };
    if (settings.body !== undefined && !settings.headers["Content-Type"]) {
      settings.headers["Content-Type"] = "application/json";
    }

    let response;
    try {
      response = await fetch(path, settings);
    } catch {
      throw new Error("Cannot reach the server. Is it still running?");
    }

    let data = null;
    try {
      data = await response.json();
    } catch {
      // API routes always return JSON; fall through to a generic message.
    }

    if (!response.ok || !data || data.ok !== true) {
      const message =
        data && data.error && data.error.message
          ? data.error.message
          : `Request failed (HTTP ${response.status}).`;
      throw new Error(message);
    }
    return data;
  }

  function showAlert(message) {
    els.alertText.textContent = message;
    els.alert.hidden = false;
  }

  function hideAlert() {
    els.alert.hidden = true;
  }

  function toast(message, isError = false) {
    els.toast.textContent = message;
    els.toast.classList.toggle("error", isError);
    els.toast.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      els.toast.classList.remove("show");
    }, 3400);
  }

  function log(message, kind = "info") {
    const time = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    state.log.unshift({ time, message, kind });
    if (state.log.length > 40) state.log.length = 40;
    renderActivity();
  }

  function setLoading(loading) {
    state.loading = loading;
    els.refreshBtn.disabled = loading;
    els.snapshotBtn.disabled = loading;
    els.newBtn.disabled = loading;
  }

  function setOnline(online) {
    state.connected = online;
    els.statusDot.classList.toggle("offline", !online);
    els.healthText.textContent = online ? "API connected" : "Server offline";
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------
  function renderSummary() {
    const status = state.status;
    if (!status) return;
    const latest = status.latest_snapshot;

    els.statEntries.textContent = numberFormat.format(status.entries);
    els.statSnapshots.textContent = numberFormat.format(status.snapshots);
    els.lastSaved.textContent = latest ? formatDate(latest.created_at) : "Nothing yet";
    els.lastSaved.title = latest ? latest.name : "No snapshot has been saved yet";
    els.dataDir.textContent = status.data_dir;
    els.dataDir.title = status.data_dir;
    els.uptime.textContent = formatUptime(status.uptime_seconds);

    if (!status.entries) {
      els.subtitle.textContent =
        "The store is empty. Write a key, or restore a copy from the archive.";
    } else if (latest) {
      els.subtitle.textContent = `${plural(status.entries, "key")} in memory. The latest copy was saved on ${formatDate(latest.created_at)}.`;
    } else {
      els.subtitle.textContent = `${plural(status.entries, "key")} in memory, not yet saved. A snapshot is a decision.`;
    }
  }

  function renderEntries() {
    const filter = state.filter.trim().toLowerCase();
    const rows = state.entries.filter((entry) => {
      if (!filter) return true;
      return (
        entry.key.toLowerCase().includes(filter) ||
        entry.value.toLowerCase().includes(filter)
      );
    });

    els.entriesList.replaceChildren();

    for (const entry of rows) {
      const row = element("div", "entry");

      const keyWrap = element("div", "entry-key");
      const key = element("code", "", entry.key);
      key.title = entry.key;
      keyWrap.append(key);

      const value = element("div", "entry-value", entry.value || "empty value");
      if (!entry.value) value.classList.add("is-empty");
      value.title = entry.value;

      const actions = element("div", "entry-actions");
      const edit = element("button", "row-btn", "Edit");
      edit.type = "button";
      edit.dataset.action = "edit";
      edit.dataset.key = entry.key;
      const remove = element("button", "row-btn delete", "Delete");
      remove.type = "button";
      remove.dataset.action = "delete";
      remove.dataset.key = entry.key;
      actions.append(edit, remove);

      row.append(keyWrap, value, actions);
      els.entriesList.append(row);
    }

    const isEmpty = rows.length === 0;
    els.emptyEntries.hidden = !isEmpty;
    if (isEmpty) {
      if (state.entries.length === 0) {
        els.emptyEntriesTitle.textContent = "Nothing here yet";
        els.emptyEntriesText.textContent =
          "Write the first key–value pair. It stays in memory until you decide to save a copy.";
      } else {
        els.emptyEntriesTitle.textContent = "Nothing matches";
        els.emptyEntriesText.textContent =
          "Try another word, or clear the search field.";
      }
    }

    els.entriesHint.textContent = filter
      ? `${plural(rows.length, "match", "matches")} of ${plural(state.entries.length, "key")}`
      : `${plural(state.entries.length, "key")} in memory`;
  }

  function renderSnapshots() {
    els.snapshotList.replaceChildren();
    const snapshots = state.snapshots;
    els.emptySnapshots.hidden = snapshots.length > 0;
    els.snapshotsPill.textContent = plural(snapshots.length, "file");
    els.snapshotsHint.textContent = snapshots.length
      ? `Newest first · last saved ${formatDate(snapshots[0].created_at)}`
      : "Newest first · checked when restored";

    snapshots.forEach((snapshot, index) => {
      const item = element("div", "snapshot");
      const top = element("div", "snapshot-top");

      const name = element("span", "snapshot-name", snapshot.name);
      name.title = snapshot.name;
      top.append(name);

      if (index === 0) top.append(element("span", "latest-tag", "latest"));

      const restore = element("button", "snapshot-restore", "Restore");
      restore.type = "button";
      restore.dataset.file = snapshot.name;
      restore.disabled = !snapshot.valid;
      restore.title = snapshot.valid
        ? `Restore ${snapshot.name}`
        : "This snapshot header is invalid";
      top.append(restore);

      const meta = element("div", "snapshot-meta");
      meta.append(
        document.createTextNode(
          `${plural(snapshot.entries, "entry", "entries")} · ${formatBytes(
            snapshot.size_bytes
          )} · ${formatDate(snapshot.created_at)}`
        )
      );
      if (!snapshot.valid) {
        meta.append(document.createTextNode(" · "));
        meta.append(element("span", "bad", "unreadable"));
      }

      item.append(top, meta);
      els.snapshotList.append(item);
    });
  }

  function renderActivity() {
    els.activityList.replaceChildren();
    if (state.log.length === 0) {
      els.activityList.append(
        element("li", "activity-empty", "Nothing has happened in this session yet.")
      );
      return;
    }
    for (const entry of state.log) {
      const item = element("li", `kind-${entry.kind}`);
      item.append(
        element("time", "", entry.time),
        element("span", "log-text", entry.message)
      );
      els.activityList.append(item);
    }
  }

  function renderAll() {
    renderSummary();
    renderEntries();
    renderSnapshots();
    renderActivity();
  }

  // ------------------------------------------------------------------
  // Data loading
  // ------------------------------------------------------------------
  async function refreshAll() {
    setLoading(true);
    try {
      const [status, entries, snapshots] = await Promise.all([
        api("/api/status"),
        api("/api/entries"),
        api("/api/snapshots"),
      ]);
      state.status = status;
      state.entries = entries.entries;
      state.snapshots = snapshots.snapshots;
      const firstConnection = !state.connected;
      setOnline(true);
      renderAll();
      if (firstConnection) {
        log(`${plural(status.entries, "key")} read from memory.`);
      }
    } catch (error) {
      setOnline(false);
      showAlert(error.message);
    } finally {
      setLoading(false);
    }
  }

  // ------------------------------------------------------------------
  // Entry dialog
  // ------------------------------------------------------------------
  function updateCounters() {
    const keyBytes = byteLength(els.keyInput.value);
    const valueBytes = byteLength(els.valueInput.value);
    els.keyCounter.textContent = `${numberFormat.format(keyBytes)} / 256 bytes`;
    els.valueCounter.textContent = `${(valueBytes / 1024).toFixed(1)} / 64 KiB`;
    els.keyCounter.classList.toggle("over", keyBytes > MAX_KEY_BYTES);
    els.valueCounter.classList.toggle("over", valueBytes > MAX_VALUE_BYTES);
  }

  function showEntryError(message) {
    els.entryError.textContent = message;
    els.entryError.hidden = false;
  }

  function hideEntryError() {
    els.entryError.hidden = true;
    els.entryError.textContent = "";
  }

  function openEntryDialog(entry = null) {
    hideEntryError();
    state.editingKey = entry ? entry.key : null;
    els.keyInput.disabled = Boolean(entry);
    els.keyInput.value = entry ? entry.key : "";
    els.valueInput.value = entry ? entry.value : "";
    els.entryKicker.textContent = entry ? "Edit entry" : "New entry";
    els.entryDialogTitle.textContent = entry ? "Revise what is kept" : "Write something down";
    els.entrySave.textContent = entry ? "Save changes" : "Save entry";
    updateCounters();
    els.entryDialog.showModal();
    (entry ? els.valueInput : els.keyInput).focus();
  }

  async function saveEntry(event) {
    event.preventDefault();
    hideEntryError();

    const key = els.keyInput.value;
    const value = els.valueInput.value;
    if (!key.trim()) {
      showEntryError("A key cannot be empty.");
      return;
    }
    if (byteLength(key) > MAX_KEY_BYTES) {
      showEntryError("This key is longer than 256 bytes.");
      return;
    }
    if (byteLength(value) > MAX_VALUE_BYTES) {
      showEntryError("This value is larger than 64 KiB.");
      return;
    }

    els.entrySave.disabled = true;
    try {
      const data = await api("/api/entries", {
        method: "POST",
        body: JSON.stringify({ key, value }),
      });
      els.entryDialog.close();
      toast(data.created ? "Entry written." : "Entry revised.");
      log(`${data.created ? "Wrote" : "Revised"} “${data.key}”.`, "success");
      hideAlert();
      await refreshAll();
    } catch (error) {
      showEntryError(error.message);
    } finally {
      els.entrySave.disabled = false;
    }
  }

  // ------------------------------------------------------------------
  // Confirmation
  // ------------------------------------------------------------------
  function confirmAction(title, message, okLabel, dangerous = false) {
    return new Promise((resolve) => {
      state.confirmResolve = resolve;
      els.confirmTitle.textContent = title;
      els.confirmMessage.textContent = message;
      els.confirmOk.textContent = okLabel;
      els.confirmOk.className = `btn ${dangerous ? "btn-clay" : "btn-ink"}`;
      els.confirmDialog.showModal();
    });
  }

  function settleConfirm(result) {
    const resolve = state.confirmResolve;
    state.confirmResolve = null;
    if (els.confirmDialog.open) els.confirmDialog.close();
    if (resolve) resolve(result);
  }

  // ------------------------------------------------------------------
  // Operations
  // ------------------------------------------------------------------
  async function deleteEntry(key) {
    const ok = await confirmAction(
      "Remove this entry?",
      `“${key}” will disappear from memory. Existing snapshots are untouched.`,
      "Remove entry",
      true
    );
    if (!ok) return;

    try {
      await api(`/api/entries/${encodeURIComponent(key)}`, { method: "DELETE" });
      toast("Entry removed.");
      log(`Removed “${key}”.`, "success");
      hideAlert();
      await refreshAll();
    } catch (error) {
      showAlert(error.message);
    }
  }

  async function createSnapshot() {
    els.snapshotBtn.disabled = true;
    try {
      const data = await api("/api/snapshots", { method: "POST" });
      toast(`Saved ${data.snapshot.name}`);
      log(
        `Saved ${data.snapshot.name} · ${plural(
          data.snapshot.entries,
          "entry",
          "entries"
        )}.`,
        "success"
      );
      hideAlert();
      await refreshAll();
    } catch (error) {
      showAlert(error.message);
      toast("The snapshot could not be saved.", true);
    } finally {
      els.snapshotBtn.disabled = false;
    }
  }

  async function restoreSnapshot(filename) {
    const ok = await confirmAction(
      "Restore this copy?",
      `The live store will be replaced by “${filename}”. It is fully checked first; if it is corrupt, the current data is left untouched.`,
      "Restore copy"
    );
    if (!ok) return;

    try {
      const data = await api("/api/restore", {
        method: "POST",
        body: JSON.stringify({ file: filename }),
      });
      toast(`Restored ${plural(data.entries, "entry", "entries")}.`);
      log(`Restored ${data.restored.name}.`, "success");
      hideAlert();
      await refreshAll();
    } catch (error) {
      showAlert(error.message);
      toast("The copy was rejected.", true);
    }
  }

  // ------------------------------------------------------------------
  // Events
  // ------------------------------------------------------------------
  function bindEvents() {
    els.refreshBtn.addEventListener("click", () => refreshAll());
    els.snapshotBtn.addEventListener("click", createSnapshot);
    els.newBtn.addEventListener("click", () => openEntryDialog());
    els.alertClose.addEventListener("click", hideAlert);

    els.searchInput.addEventListener("input", () => {
      state.filter = els.searchInput.value;
      renderEntries();
    });

    els.entriesList.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const entry = state.entries.find((item) => item.key === button.dataset.key);
      if (!entry) return;
      if (button.dataset.action === "edit") {
        openEntryDialog(entry);
      } else if (button.dataset.action === "delete") {
        deleteEntry(entry.key);
      }
    });

    els.snapshotList.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-file]");
      if (button) restoreSnapshot(button.dataset.file);
    });

    els.entryForm.addEventListener("submit", saveEntry);
    els.entryClose.addEventListener("click", () => els.entryDialog.close());
    els.entryCancel.addEventListener("click", () => els.entryDialog.close());
    els.keyInput.addEventListener("input", updateCounters);
    els.valueInput.addEventListener("input", updateCounters);

    els.confirmOk.addEventListener("click", () => settleConfirm(true));
    els.confirmCancel.addEventListener("click", () => settleConfirm(false));
    els.confirmClose.addEventListener("click", () => settleConfirm(false));
    els.confirmDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      settleConfirm(false);
    });

    els.clearLogBtn.addEventListener("click", () => {
      state.log = [];
      renderActivity();
    });

    document.querySelectorAll(".nav a").forEach((link) => {
      link.addEventListener("click", () => {
        document.querySelectorAll(".nav a").forEach((item) => item.classList.remove("active"));
        link.classList.add("active");
      });
    });

    document.addEventListener("keydown", (event) => {
      const typing =
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLTextAreaElement;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        els.searchInput.focus();
        els.searchInput.select();
      } else if (
        event.key.toLowerCase() === "n" &&
        !typing &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey &&
        !els.entryDialog.open &&
        !els.confirmDialog.open
      ) {
        event.preventDefault();
        openEntryDialog();
      }
    });
  }

  async function init() {
    bindEvents();
    renderActivity();
    await refreshAll();
    if (state.connected) {
      log("Ready. Changes stay in memory until a snapshot is saved.");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
