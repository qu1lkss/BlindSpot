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
const validationKeys = new Map();
let busy = false;

function setBusy(value) {
  busy = value;
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

async function checkMaterials() {
  const { id, revision } = currentStudy;
  const version = id + "/" + revision;
  if (!validationKeys.has(version)) validationKeys.set(version, crypto.randomUUID());
  activity.textContent = "Проверяем входные материалы…";
  const report = await command("/studies/" + encodeURIComponent(id) + "/validate", revision, validationKeys.get(version));
  validationKeys.delete(version);
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
