"use strict";

const token =
  document.querySelector('meta[name="spacefinder-token"]')?.content || "";

const state = {
  config: null,
  rules: [],
  rulesById: new Map(),
  items: new Map(),
  selected: new Set(),
  groupIds: new Map(),
  scanId: null,
  pollTimer: null,
  busy: false,
  i18n: {},
  historyLoaded: false,
  warnings: [],
};

const $ = (id) => document.getElementById(id);

function el(tag, props = {}, text) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

function t(key, fallback = key, vars = {}) {
  let value = state.i18n[key] ?? fallback;
  for (const [name, replacement] of Object.entries(vars)) {
    value = value.replaceAll(`{${name}}`, String(replacement));
  }
  return value;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Spacefinder-Token", token);
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("Сервер не ответил за 15 секунд");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = {};
  }
  if (!response.ok) {
    const err = data.error || {};
    const error = new Error(err.message || `HTTP ${response.status}`);
    error.code = err.code || "http_error";
    throw error;
  }
  return data;
}

const BYTE_UNITS = ["Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ"];

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1024) return `${value} Б`;
  let number = value;
  let unit = BYTE_UNITS[0];
  for (let index = 1; index < BYTE_UNITS.length; index += 1) {
    number /= 1024;
    unit = BYTE_UNITS[index];
    if (number < 1024) break;
  }
  return `${new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 1,
  }).format(number)} ${unit}`;
}

function formatDate(timestamp) {
  if (!timestamp) return "—";
  return new Date(timestamp * 1000).toLocaleString("ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function showError(error) {
  const message = `${error.code ? `${error.code}: ` : ""}${error.message || error}`;
  const node = $("error");
  node.textContent = message;
  node.hidden = false;
  console.error(error);
}

function clearError() {
  $("error").hidden = true;
  $("error").textContent = "";
}

function setStatus(message) {
  $("status").textContent = message;
}

async function loadInitial() {
  try {
    const [config, rules] = await Promise.all([
      api("/api/config"),
      api("/api/rules"),
    ]);
    state.config = config;
    state.rules = rules;
    state.rulesById = new Map(rules.map((rule) => [rule.id, rule]));

    try {
      const i18nResponse = await fetch("/static/i18n/ru.json");
      if (i18nResponse.ok) state.i18n = await i18nResponse.json();
    } catch {
      // Словарь не критичен: ниже есть русские значения по умолчанию.
    }

    $("dry-run-banner").hidden = !config.dry_run;
    $("permanent-btn").hidden = !config.allow_permanent;
    renderSystemRules();
    await loadDisk();
  } catch (error) {
    showError(error);
  }
}

async function loadDisk() {
  const disk = await api("/api/disk");
  const percent = Math.min(100, Math.max(0, disk.percent));
  $("disk-text").textContent =
    `Занято ${formatBytes(disk.used)} из ${formatBytes(disk.total)} · ` +
    `свободно ${formatBytes(disk.free)}`;
  $("disk-fill").style.width = `${percent}%`;
  $("disk-meter").setAttribute("aria-valuenow", String(Math.round(percent)));
}

function renderSystemRules() {
  const rules = state.rules.filter((rule) => rule.requires_privilege);
  const card = $("system-card");
  const list = $("system-list");
  list.replaceChildren();
  if (!rules.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  for (const rule of rules) {
    const item = el("li");
    item.append(el("strong", {}, rule.title));
    item.append(el("p", { class: "muted" }, rule.description));
    for (const command of rule.commands) {
      const line = el("p");
      line.append(el("code", {}, command));
      line.append(document.createTextNode(" "));
      const copy = el("button", { type: "button", class: "secondary" }, "Копировать");
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(command);
          copy.textContent = "Скопировано";
        } catch {
          copy.textContent = "Не удалось";
        }
      });
      line.append(copy);
      item.append(line);
    }
    list.append(item);
  }
}

async function startScan() {
  if (state.busy) return;
  clearError();
  setStatus("");
  state.busy = true;
  state.scanId = null;
  state.items = new Map();
  state.selected = new Set();
  state.warnings = [];
  updateSelection();
  $("scan-btn").disabled = true;
  $("scan-btn").setAttribute("aria-busy", "true");
  $("progress-card").hidden = false;
  $("progress-text").textContent = t("scan.running", "Сканирование…");
  $("results-card").hidden = true;
  $("groups").replaceChildren();
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  try {
    const ruleIds = state.rules
      .filter((rule) => !rule.requires_privilege)
      .map((rule) => rule.id);
    const { scan_id: scanId } = await api("/api/scans", {
      method: "POST",
      body: JSON.stringify({ rule_ids: ruleIds }),
    });
    state.scanId = scanId;
    schedulePoll(0);
  } catch (error) {
    state.busy = false;
    $("scan-btn").disabled = false;
    $("scan-btn").removeAttribute("aria-busy");
    $("progress-card").hidden = true;
    showError(error);
  }
}

function schedulePoll(delay) {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(poll, delay);
}

async function poll() {
  if (!state.scanId) return;
  const scanId = state.scanId;
  try {
    const scan = await api(`/api/scans/${scanId}`);
    if (state.scanId !== scanId) return;
    const current = scan.progress.current_path
      ? ` · ${scan.progress.current_path}`
      : "";
    $("progress-text").textContent =
      `${t("scan.running", "Сканирование…")} ` +
      `Найдено ${scan.progress.items_found} пунктов · ` +
      `${formatBytes(scan.progress.bytes_found)}${current}`;

    if (["done", "cancelled", "failed"].includes(scan.status)) {
      state.busy = false;
      $("scan-btn").disabled = false;
      $("scan-btn").removeAttribute("aria-busy");
      $("progress-card").hidden = true;
      if (scan.status === "failed") {
        showError(
          new Error(scan.warnings.join("; ") || t("scan.failed", "Сканирование не удалось")),
        );
      } else {
        applyScan(scan);
        $("results-card").hidden = false;
        setStatus(
          scan.status === "cancelled"
            ? t("scan.cancelled", "Сканирование отменено")
            : t("scan.done", "Сканирование завершено"),
        );
      }
      await loadDisk();
    } else {
      schedulePoll(400);
    }
  } catch (error) {
    state.busy = false;
    $("scan-btn").disabled = false;
    $("scan-btn").removeAttribute("aria-busy");
    $("progress-card").hidden = true;
    showError(error);
  }
}

function applyScan(scan) {
  state.items = new Map();
  state.selected = new Set();
  state.warnings = Array.isArray(scan.warnings) ? scan.warnings : [];
  for (const item of scan.items) {
    if (item.deleted) continue;
    state.items.set(item.id, item);
    if (item.default_selected) state.selected.add(item.id);
  }
  renderGroups();
}

function renderGroups(warnings = state.warnings) {
  const items = [...state.items.values()].sort(
    (left, right) => right.size_bytes - left.size_bytes,
  );
  const total = items.reduce((sum, item) => sum + item.size_bytes, 0);
  const summary = $("results-summary");
  if (!items.length) {
    summary.textContent = "Мусор не найден.";
  } else {
    const warningText = warnings.length ? ` · ${warnings.join(" · ")}` : "";
    summary.textContent = `Пунктов: ${items.length} · всего ${formatBytes(total)}${warningText}`;
  }

  const container = $("groups");
  container.replaceChildren();
  state.groupIds = new Map();

  for (const rule of state.rules) {
    const groupItems = items.filter((item) => item.rule_id === rule.id);
    if (!groupItems.length) continue;

    const ids = groupItems.map((item) => item.id);
    state.groupIds.set(rule.id, ids);

    const section = el("section", { class: "group", "aria-labelledby": `group-${rule.id}` });
    const head = el("div", { class: "group-head" });
    head.append(el("h3", { id: `group-${rule.id}` }, rule.title));
    head.append(
      el("span", { class: `risk risk-${rule.risk}` }, t(`risk.${rule.risk}`, rule.risk)),
    );
    head.append(
      el("span", { class: "muted" }, `${groupItems.length} · ${formatBytes(
        groupItems.reduce((sum, item) => sum + item.size_bytes, 0),
      )}`),
    );
    section.append(head);

    const wrap = el("div", { class: "table-wrap" });
    const table = el("table");
    table.append(el("caption", {}, rule.description));
    const thead = el("thead");
    const headRow = el("tr");
    const checkHead = el("th", { scope: "col", class: "check" });
    const selectAll = el("input", { type: "checkbox", dataset: { selectAll: rule.id } });
    selectAll.setAttribute("aria-label", `Выбрать все: ${rule.title}`);
    const selectedCount = ids.filter((id) => state.selected.has(id)).length;
    selectAll.checked = selectedCount === ids.length;
    selectAll.indeterminate = selectedCount > 0 && selectedCount < ids.length;
    selectAll.addEventListener("change", () => {
      for (const id of ids) {
        if (selectAll.checked) state.selected.add(id);
        else state.selected.delete(id);
      }
      syncIndividualChecks();
      syncGroupChecks();
      updateSelection();
    });
    checkHead.append(selectAll);
    headRow.append(checkHead);
    for (const label of ["Путь", "Размер", "Изменён", "Риск"]) {
      headRow.append(el("th", { scope: "col" }, label));
    }
    thead.append(headRow);
    table.append(thead);

    const tbody = el("tbody");
    for (const item of groupItems) {
      const row = el("tr");

      const checkCell = el("td", { class: "check" });
      const checkbox = el("input", {
        type: "checkbox",
        checked: state.selected.has(item.id),
        dataset: { itemId: item.id },
      });
      checkbox.setAttribute(
        "aria-label",
        `Выбрать ${item.path}, ${formatBytes(item.size_bytes)}`,
      );
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.selected.add(item.id);
        else state.selected.delete(item.id);
        syncGroupChecks();
        updateSelection();
      });
      checkCell.append(checkbox);
      row.append(checkCell);

      const pathCell = el("td", { class: "path" });
      pathCell.append(el("code", {}, item.path));
      if (item.warnings.length) {
        const warning = el("span", { class: "warn", title: item.warnings.join("\n") }, " ⚠");
        warning.setAttribute("aria-label", `Предупреждения: ${item.warnings.join("; ")}`);
        pathCell.append(warning);
      }
      row.append(pathCell);

      row.append(el("td", { class: "size" }, formatBytes(item.size_bytes)));
      row.append(el("td", {}, formatDate(item.mtime)));

      const riskCell = el("td");
      riskCell.append(
        el("span", { class: `risk risk-${item.risk}` }, t(`risk.${item.risk}`, item.risk)),
      );
      row.append(riskCell);
      tbody.append(row);
    }
    table.append(tbody);
    wrap.append(table);
    section.append(wrap);
    container.append(section);
  }

  updateSelection();
}

function syncIndividualChecks() {
  document.querySelectorAll("input[data-item-id]").forEach((checkbox) => {
    checkbox.checked = state.selected.has(checkbox.dataset.itemId);
  });
}

function syncGroupChecks() {
  document.querySelectorAll("input[data-select-all]").forEach((checkbox) => {
    const ids = state.groupIds.get(checkbox.dataset.selectAll) || [];
    const selected = ids.filter((id) => state.selected.has(id)).length;
    checkbox.checked = ids.length > 0 && selected === ids.length;
    checkbox.indeterminate = selected > 0 && selected < ids.length;
  });
}

function selectedItems() {
  return [...state.selected]
    .map((id) => state.items.get(id))
    .filter(Boolean);
}

function updateSelection() {
  const items = selectedItems();
  const total = items.reduce((sum, item) => sum + item.size_bytes, 0);
  $("selection-text").textContent = items.length
    ? `Выбрано: ${items.length} · ${formatBytes(total)}`
    : t("delete.none", "Ничего не выбрано");
  $("selection-bar").hidden = items.length === 0;
  $("trash-btn").disabled = items.length === 0;
  $("permanent-btn").disabled = items.length === 0;
}

function openConfirm(mode) {
  const dialog = $("confirm-dialog");
  if (dialog.open) return;
  const items = selectedItems();
  if (!items.length) return;

  const list = $("confirm-list");
  list.replaceChildren();
  for (const item of items.slice(0, 20)) {
    list.append(el("li", {}, `${item.path} — ${formatBytes(item.size_bytes)}`));
  }
  if (items.length > 20) {
    list.append(el("li", {}, `и ещё ${items.length - 20}`));
  }

  const total = items.reduce((sum, item) => sum + item.size_bytes, 0);
  const allInTrash = items.every((item) => item.in_trash);
  const someInTrash = items.some((item) => item.in_trash);
  // Пункты корзины при «перемещении в корзину» очищаются безвозвратно,
  // поэтому чекбокс подтверждения нужен и для смешанной выборки.
  const irreversible = mode === "permanent" || someInTrash;
  let question;
  if (mode === "permanent") {
    question = t("delete.confirm_permanent", "Удалить выбранные пункты безвозвратно?");
  } else if (allInTrash) {
    question = t(
      "delete.confirm_trash_items",
      "Очистить выбранные пункты корзины безвозвратно?",
    );
  } else if (someInTrash) {
    question = t(
      "delete.confirm_mixed",
      "Некоторые пункты уже в корзине и будут удалены безвозвратно. Продолжить?",
    );
  } else {
    question = t("delete.confirm_trash", "Переместить выбранные пункты в корзину?");
  }
  $("confirm-text").textContent =
    `${question} Всего: ${items.length} · ${formatBytes(total)}`;

  $("permanent-wrap").hidden = !irreversible;
  $("permanent-check").checked = false;
  const ok = $("confirm-ok");
  ok.disabled = irreversible;
  ok.textContent =
    mode === "permanent"
      ? "Удалить навсегда"
      : allInTrash
        ? "Очистить"
        : someInTrash
          ? "Продолжить"
          : "В корзину";

  dialog.returnValue = "";
  dialog.addEventListener(
    "close",
    () => {
      if (dialog.returnValue !== "ok") return;
      if (irreversible && !$("permanent-check").checked) return;
      void performDelete(mode, items.map((item) => item.id));
    },
    { once: true },
  );
  dialog.showModal();
}

async function performDelete(mode, ids) {
  if (!state.scanId) {
    showError(new Error("Сессия сканирования не найдена, запустите сканирование заново"));
    return;
  }
  setStatus("");
  try {
    const response = await api(`/api/scans/${state.scanId}/delete`, {
      method: "POST",
      body: JSON.stringify({ item_ids: ids, mode, confirm: true }),
    });

    let ok = 0;
    for (const result of response.results) {
      if (result.status === "trashed" || result.status === "deleted") {
        ok += 1;
        state.items.delete(result.item_id);
        state.selected.delete(result.item_id);
      } else if (result.status === "simulated") {
        ok += 1;
      } else {
        showError(
          new Error(`${result.item_id}: ${result.message || result.status}`),
        );
      }
    }

    let message;
    if (state.config?.dry_run) {
      message = t("delete.dry_run", "Dry-run: изменения не применены");
    } else if (response.freed_bytes > 0) {
      message = t(
        "delete.result",
        "Готово: {ok} из {total}, освобождено {size}",
        { ok, total: ids.length, size: formatBytes(response.freed_bytes) },
      );
    } else {
      message = t(
        "delete.moved",
        "Готово: {ok} из {total}, перемещено в корзину",
        { ok, total: ids.length },
      );
    }
    setStatus(message);
    renderGroups();
    await loadDisk();
    if (state.historyLoaded) await loadHistory();
  } catch (error) {
    showError(error);
  }
}

async function cancelScan() {
  if (!state.scanId) return;
  try {
    await api(`/api/scans/${state.scanId}/cancel`, { method: "POST" });
    setStatus("Отмена…");
  } catch (error) {
    showError(error);
  }
}

async function loadHistory() {
  const { entries } = await api("/api/history?limit=20");
  const list = $("history-list");
  list.replaceChildren();
  if (!entries.length) {
    list.append(el("li", { class: "muted" }, "Пока пусто"));
    return;
  }
  for (const entry of entries.slice().reverse()) {
    const status = t(`status.${entry.status}`, entry.status);
    const parts = [
      new Date(entry.ts).toLocaleString("ru-RU"),
      entry.path || "",
      status,
    ];
    if (entry.error) parts.push(entry.error);
    list.append(el("li", {}, parts.filter(Boolean).join(" · ")));
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("scan-btn").addEventListener("click", startScan);
  $("cancel-btn").addEventListener("click", cancelScan);
  $("trash-btn").addEventListener("click", () => openConfirm("trash"));
  $("permanent-btn").addEventListener("click", () => openConfirm("permanent"));
  $("history-btn").addEventListener("click", async () => {
    state.historyLoaded = true;
    try {
      await loadHistory();
    } catch (error) {
      showError(error);
    }
  });
  $("permanent-check").addEventListener("change", (event) => {
    if (!$("permanent-wrap").hidden) {
      $("confirm-ok").disabled = !event.target.checked;
    }
  });
  void loadInitial();
});
