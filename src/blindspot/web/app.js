const byId = (id) => document.getElementById(id);
const form = byId("upload");
const inputs = [...form.querySelectorAll("input[type=file]")];
const studies = byId("studies");
const detail = byId("detail");
const activity = byId("activity");
const issues = byId("issues");
const submit = form.querySelector("button[type=submit]");
let currentStudy = null;
let uploadKey = null;
const commandKeys = new Map();
let busy = false;
let baselineTimer;

function setBusy(value) {
  busy = value;
  byId("start").disabled = value;
  byId("prepare").disabled = value;
  form.querySelector("fieldset").disabled = value;
  byId("refresh").disabled = value;
  byId("new").disabled = value;
  byId("validate").disabled = value;
  for (const button of studies.querySelectorAll("button")) button.disabled = value;
}

function clearErrors() {
  issues.replaceChildren();
  for (const input of inputs) {
    input.removeAttribute("aria-invalid");
    byId(input.name + "-error").textContent = "";
  }
}

function showErrors(errors) {
  for (const { field, message } of errors) {
    const root = field?.split(/[.[]/, 1)[0];
    const input = inputs.find((item) => item.name === root);
    const fieldName = input?.labels[0].textContent;
    const path = fieldName ? fieldName + field.slice(root.length) : field;
    const text = path ? path + " — " + message : message;
    const row = document.createElement("li");
    row.textContent = text;
    issues.append(row);
    if (!form.hidden && input) {
      input.setAttribute("aria-invalid", "true");
      const hint = byId(input.name + "-error");
      hint.textContent += (hint.textContent ? "\n" : "") + text;
    }
  }
}

function clearSelection() {
  clearTimeout(baselineTimer);
  currentStudy = null;
  uploadKey = null;
  detail.hidden = true;
  byId("identity").textContent = "";
  byId("outcome").textContent = "";
  byId("validate").hidden = true;
  clearErrors();
  submit.hidden = false;
  activity.textContent = "";
}

function showStudy(study) {
  currentStudy = study;
  byId("prepare").hidden = study.stage !== "проверена";
  byId("split").textContent = study.preparation
    ? "Строк для поиска: " + study.preparation.f1_rows + ". Для подтверждения: " + study.preparation.f2_rows + "."
    : "";
  clearErrors();
  byId("identity").textContent = [study.id, study.stage, "ревизия " + study.revision].join(" · ");
  const report = study.validation;
  const outcome = byId("outcome");
  delete outcome.dataset.valid;
  if (report) {
    outcome.dataset.valid = String(report.valid);
    outcome.textContent = report.valid
      ? "Входные материалы прошли проверку."
      : "Проверка выявила ошибки. Запуск заблокирован.";
    showErrors(report.errors || []);
  } else {
    outcome.textContent = "Материалы ещё не проверены.";
  }
  byId("validate").hidden = study.stage !== "черновик";
  submit.hidden = !form.hidden;
  detail.hidden = false;
  showBaseline(study);
}

const metricFormat = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 4, maximumFractionDigits: 6 });
const pendingBaseline = new Set(["очередь", "выполняется"]);

function metricValue(measurement) {
  return measurement.value === null ? "Не определено. " + measurement.reason : metricFormat.format(measurement.value);
}

function makeTable(captionText, headers, values) {
  const table = document.createElement("table");
  table.createCaption().textContent = captionText;
  const heading = table.createTHead().insertRow();
  for (const label of headers) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    heading.append(cell);
  }
  const body = table.createTBody();
  for (const [name, ...numbers] of values) {
    const row = body.insertRow();
    const cell = document.createElement("th");
    cell.scope = "row";
    cell.textContent = name;
    row.append(cell);
    for (const number of numbers) row.insertCell().textContent = number;
  }
  return table;
}

function showMeasurements(measurements) {
  const metrics = byId("metrics");
  metrics.replaceChildren();
  if (!measurements.length) return;
  const quality = [];
  const matrix = new Map();
  const predicted = new Set();
  for (const item of measurements) {
    if (item.metric === "confusion_matrix") {
      if (!matrix.has(item.actual)) matrix.set(item.actual, new Map());
      matrix.get(item.actual).set(item.predicted, metricValue(item));
      predicted.add(item.predicted);
    } else {
      const names = { accuracy: "Доля верных ответов (accuracy)", macro_f1: "Macro-F1" };
      const label = item.metric === "recall" ? "Полнота класса " + JSON.parse(item.actual) : names[item.metric];
      quality.push([label, metricValue(item)]);
    }
  }
  metrics.append(makeTable("Метрики классификации", ["Показатель", "Значение"], quality));
  if (matrix.size) {
    const columns = [...predicted];
    const rows = Array.from(matrix, ([label, cells]) => [
      JSON.parse(label), ...columns.map((col) => cells.get(col) ?? "Нет измерения"),
    ]);
    metrics.append(makeTable("Матрица ошибок, строка — истинный класс, столбец — прогноз",
      ["Истинный класс", ...columns.map((key) => JSON.parse(key))], rows));
  }
}

function showBaseline(study) {
  clearTimeout(baselineTimer);
  const result = study.baseline;
  byId("start").hidden = study.stage !== "подготовлена" || Boolean(result);
  byId("baseline").hidden = !result;
  if (!result) return;
  const saved = result.status === "сохранён";
  byId("baseline-state").textContent = saved ? "Расчёт сохранён. Строк с прогнозами — " + result.rows + "."
    : result.status === "очередь" ? "Базовый расчёт ожидает свободного исполнителя."
    : result.status === "выполняется" ? "Идёт базовый расчёт."
    : "Базовый расчёт завершился с ошибкой. " + result.reason;
  byId("baseline-budget").textContent = "Использовано попыток в поиске — " + result.search_used
    + ", всего — " + result.total_used + ".";
  showMeasurements(result.measurements || []);
  byId("predictions").hidden = !saved;
  if (saved) byId("predictions").href = "/studies/" + encodeURIComponent(study.id) + "/predictions";
  if (!pendingBaseline.has(result.status)) return;
  const update = async () => {
    try {
      const latest = await readStudy(study.id);
      if (currentStudy !== study) return;
      showStudy(latest);
      if (!pendingBaseline.has(latest.baseline?.status)) await loadStudies();
    } catch {
      if (currentStudy !== study) return;
      byId("baseline-state").textContent = "Не удалось обновить состояние расчёта. Повторяем запрос…";
      baselineTimer = setTimeout(update, 3000);
    }
  };
  baselineTimer = setTimeout(update, 1000);
}

async function readResponse(response) {
  if (response.ok) return response.json();
  const body = await response.json().catch(() => ({}));
  const error = new Error(body.message || "Не удалось выполнить запрос.");
  error.status = response.status;
  error.field = body.field;
  throw error;
}

async function readStudy(id) {
  const response = await fetch("/studies/" + encodeURIComponent(id), { cache: "no-store" });
  return readResponse(response);
}

async function openStudy(id) {
  setBusy(true);
  clearSelection();
  form.hidden = true;
  byId("workspace").hidden = false;
  byId("workspace-title").textContent = "Сохранённое исследование";
  byId("workspace-title").focus();
  activity.textContent = "Загрузка исследования…";
  try {
    showStudy(await readStudy(id));
    activity.textContent = "";
  } catch (error) {
    activity.textContent = error.status ? error.message : "Не удалось загрузить исследование.";
  } finally {
    setBusy(false);
  }
}

function listEntry(study) {
  const entry = document.createElement("li");
  const summary = document.createElement("div");
  const id = document.createElement("strong");
  id.textContent = study.id;
  const meta = document.createElement("span");
  meta.className = "metadata";
  const date = new Date(study.created_at).toLocaleString("ru-RU");
  meta.textContent = [study.stage, "ревизия " + study.revision, date].join(" · ");
  summary.append(id, meta);
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Открыть";
  button.setAttribute("aria-label", "Открыть исследование " + study.id);
  button.disabled = busy;
  button.addEventListener("click", () => openStudy(study.id));
  entry.append(summary, button);
  return entry;
}

async function loadStudies() {
  const statusLine = byId("status");
  statusLine.textContent = "Загрузка исследований…";
  try {
    const response = await fetch("/studies", { cache: "no-store" });
    const { items } = await readResponse(response);
    studies.replaceChildren(...items.map(listEntry));
    statusLine.textContent = items.length ? "" : "Сохранённых исследований пока нет.";
  } catch {
    studies.replaceChildren();
    statusLine.textContent = "Не удалось загрузить исследования. Попробуйте обновить список.";
  }
}

async function refreshStudies() {
  setBusy(true);
  try {
    await loadStudies();
  } finally {
    setBusy(false);
  }
}

async function command(path, revision, key, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Idempotency-Key": key, "If-Match": String(revision) },
    body,
  });
  return readResponse(response);
}

async function studyCommand(action) {
  const { id, revision } = currentStudy;
  const path = "/studies/" + encodeURIComponent(id) + "/" + action;
  const version = path + "/" + revision;
  if (!commandKeys.has(version)) commandKeys.set(version, crypto.randomUUID());
  const result = await command(path, revision, commandKeys.get(version));
  commandKeys.delete(version);
  return result;
}

async function checkMaterials() {
  activity.textContent = "Проверяем входные материалы…";
  const report = await studyCommand("validate");
  showStudy({ ...report, validation: report });
  activity.textContent = report.valid ? "Проверка завершена." : "Исправьте указанные поля и загрузите новый комплект.";
}

async function submitMaterials(event) {
  event.preventDefault();
  clearErrors();
  const missing = inputs.filter((input) => !input.files.length);
  if (missing.length) {
    showErrors(missing.map((input) => ({ field: input.name, message: "Выберите файл." })));
    activity.textContent = "Выберите все пять входных файлов.";
    missing[0].focus();
    return;
  }
  const body = new FormData(form);
  uploadKey ??= crypto.randomUUID();
  await runCommand(async () => {
    activity.textContent = "Сохраняем входные материалы…";
    const study = await command("/studies", 0, uploadKey, body);
    showStudy(study);
    await checkMaterials();
  });
}

async function runCommand(action) {
  setBusy(true);
  try {
    await action();
  } catch (error) {
    if (error.status === 409 && currentStudy) {
      const id = currentStudy.id;
      clearSelection();
      form.hidden = true;
      byId("workspace-title").textContent = "Сохранённое исследование";
      try {
        showStudy(await readStudy(id));
        activity.textContent = "Команда отклонена. Показана текущая ревизия исследования.";
      } catch {
        activity.textContent = "Не удалось получить текущую ревизию. Откройте исследование из списка.";
      }
    } else {
      activity.textContent = error.status ? error.message : "Ошибка соединения. Повторите команду.";
      if (error.field) showErrors([{ field: error.field, message: error.message }]);
    }
  } finally {
    await loadStudies();
    setBusy(false);
  }
}

byId("new").addEventListener("click", () => {
  clearSelection();
  form.reset();
  form.hidden = false;
  byId("workspace").hidden = false;
  byId("workspace-title").textContent = "Новое исследование";
  byId("workspace-title").focus();
});
form.addEventListener("change", () => {
  const hadStudy = Boolean(currentStudy);
  clearSelection();
  if (hadStudy) activity.textContent = "Материалы изменены. Для нового комплекта требуется проверка.";
});
form.addEventListener("submit", submitMaterials);
byId("validate").addEventListener("click", () => runCommand(checkMaterials));
byId("refresh").addEventListener("click", refreshStudies);
refreshStudies();

byId("prepare").addEventListener("click", () => runCommand(async () => {
  const { id } = currentStudy;
  activity.textContent = "Готовим выборки…";
  await studyCommand("prepare");
  showStudy(await readStudy(id));
  activity.textContent = "Выборки сохранены.";
}));

byId("start").addEventListener("click", () => runCommand(async () => {
  const { id } = currentStudy;
  activity.textContent = "Передаём команду запуска…";
  await studyCommand("start");
  showStudy(await readStudy(id));
  activity.textContent = "";
}));
