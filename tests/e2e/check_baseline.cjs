const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

async function check(address, directory, screenshot) {
  if (!address || !directory) throw new Error("Укажите адрес приложения и папку примера.");
  const browser = await chromium.launch({ channel: "chrome" });
  try {
    const page = await browser.newPage({ locale: "ru-RU", viewport: { width: 1152, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(address);
    await page.getByRole("button", { name: "Новое исследование" }).click();
    for (const file of fs.readdirSync(directory).filter((name) => /^(model\.onnx|data\.(csv|parquet)|(passport|policy|methods)\.json)$/.test(name))) {
      await page.locator("#" + path.parse(file).name).setInputFiles(path.resolve(directory, file));
    }
    await page.getByRole("button", { name: "Загрузить и проверить" }).click();
    const start = page.getByRole("button", { name: "Запустить базовый расчёт" });
    const prepare = page.getByRole("button", { name: "Подготовить выборки" });
    await prepare.waitFor({ timeout: 310000 });
    assert.equal(await start.isVisible(), false);
    await prepare.click();
    const ack = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/start"));
    await start.click({ timeout: 130000 });
    const response = await ack;
    assert.equal(response.status(), 202);
    const { id, operation_id } = await response.json();
    assert.ok(id && operation_id);
    await page.getByText("Расчёт сохранён.", { exact: false }).waitFor({ timeout: 320000 });
    await page.reload();
    await page.getByRole("button", { name: "Открыть исследование " + id }).click();
    const predictions = page.getByRole("link", { name: "Скачать прогнозы по строкам (Parquet)" });
    await predictions.waitFor();
    assert.equal(await predictions.getAttribute("href"), "/studies/" + id + "/predictions");
    const study = await (await page.request.get(new URL("/studies/" + id, address).href)).json();
    const { baseline } = study;
    assert.equal(study.stage, "поиск");
    assert.equal(baseline.status, "сохранён");
    assert.equal(await start.isVisible(), false);
    assert.ok((await page.locator("#baseline-state").innerText()).includes(String(baseline.rows)));
    assert.equal(await page.locator("#baseline-budget").innerText(), "Использовано попыток в поиске — 1, всего — 1.");
    const format = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 4, maximumFractionDigits: 6 });
    const quality = page.getByRole("table", { name: "Метрики классификации" });
    const matrix = page.getByRole("table", { name: /Матрица ошибок/ });
    const headings = (await matrix.getByRole("columnheader").allTextContents()).slice(1);
    for (const m of baseline.measurements) {
      const expected = m.value === null ? "Не определено. " + m.reason : format.format(m.value);
      if (m.metric === "confusion_matrix") {
        const actual = String(JSON.parse(m.actual));
        const col = headings.indexOf(String(JSON.parse(m.predicted)));
        assert.ok(col >= 0);
        const row = matrix.getByRole("row").filter({ has: page.getByRole("rowheader", { name: actual, exact: true }) });
        assert.equal(await row.getByRole("cell").nth(col).textContent(), expected);
      } else {
        const label = m.metric === "accuracy" ? "Доля верных ответов (accuracy)"
          : m.metric === "macro_f1" ? "Macro-F1" : "Полнота класса " + JSON.parse(m.actual);
        const row = quality.getByRole("row").filter({ has: page.getByRole("rowheader", { name: label, exact: true }) });
        assert.equal(await row.getByRole("cell").textContent(), expected);
      }
    }
    if (screenshot) await page.locator("#workspace").screenshot({ path: screenshot });
    assert.deepEqual(errors, []);
    console.log("OK — запуск из формы, сохранённые метрики, матрица ошибок и ссылка на прогнозы", id);
  } finally {
    await browser.close();
  }
}

check(...process.argv.slice(2)).catch((error) => { console.error(error); process.exitCode = 1; });
