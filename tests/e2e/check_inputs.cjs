const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { chromium } = require("playwright");

async function check(address, directory, screenshot) {
  if (!address || !directory) throw new Error("Нужны адрес приложения и папка с пятью входными файлами.");
  const dataName = ["data.csv", "data.parquet"].find((name) => fs.existsSync(path.join(directory, name)));
  assert.ok(dataName, "В папке нет data.csv или data.parquet");
  const files = { model: "model.onnx", data: dataName, passport: "passport.json", policy: "policy.json", methods: "methods.json" };
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    const page = await browser.newPage({ locale: "ru-RU", viewport: { width: 1152, height: 900 } });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    const submit = page.getByRole("button", { name: "Загрузить и проверить" });
    const success = page.getByText("Входные материалы прошли проверку.", { exact: true });
    const failure = page.getByText("Проверка выявила ошибки. Запуск заблокирован.", { exact: true });
    const nextPost = (suffix) => page.waitForResponse(
      (response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith(suffix),
      { timeout: 310000 },
    );

    await page.goto(address);
    await page.getByRole("button", { name: "Новое исследование" }).click();
    await submit.click();
    assert.equal(await page.locator("input[aria-invalid=true]").count(), 5);
    for (const [field, filename] of Object.entries(files)) {
      await page.locator("#" + field).setInputFiles(path.resolve(directory, filename));
    }

    await page.locator("#data").setInputFiles({ name: "data.txt", mimeType: "text/plain", buffer: Buffer.from("ID") });
    const invalidUpload = nextPost("/studies");
    await submit.click();
    assert.equal((await invalidUpload).status(), 422);
    await page.locator('#data[aria-invalid="true"]').waitFor();
    assert.match(await page.locator("#data-error").innerText(), /CSV|Parquet/);
    await page.locator("#data").setInputFiles(path.resolve(directory, dataName));

    const created = nextPost("/studies");
    const validated = nextPost("/validate");
    await submit.click();
    const createResponse = await created;
    const validateResponse = await validated;
    assert.equal(createResponse.status(), 201);
    assert.equal(validateResponse.status(), 200);
    const draft = await createResponse.json();
    const report = await validateResponse.json();
    assert.equal(report.id, draft.id);
    assert.ok(report.valid, JSON.stringify(report.errors));
    const createHeaders = createResponse.request().headers();
    const validateHeaders = validateResponse.request().headers();
    const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
    assert.match(createHeaders["idempotency-key"], uuid);
    assert.match(validateHeaders["idempotency-key"], uuid);
    assert.notEqual(createHeaders["idempotency-key"], validateHeaders["idempotency-key"]);
    assert.equal(createHeaders["if-match"], "0");
    assert.equal(validateHeaders["if-match"], String(draft.revision));
    await success.waitFor();
    if (screenshot) await page.screenshot({ path: screenshot, fullPage: true });

    await page.locator("#passport").setInputFiles({
      name: "passport.json", mimeType: "application/json", buffer: Buffer.from("{"),
    });
    await success.waitFor({ state: "hidden" });
    await page.getByText("Материалы изменены.", { exact: false }).waitFor();
    const rejected = nextPost("/validate");
    await submit.click();
    const invalidResponse = await rejected;
    assert.equal(invalidResponse.status(), 200);
    const invalid = await invalidResponse.json();
    assert.equal(invalid.valid, false);
    assert.notEqual(invalid.id, draft.id);
    await failure.waitFor();
    assert.equal(await page.locator("#passport").getAttribute("aria-invalid"), "true");
    assert.ok((await page.locator("#passport-error").innerText()).trim());
    assert.equal(await page.getByRole("button", { name: /^Запустить/ }).count(), 0);

    await page.reload();
    await page.getByRole("button", { name: "Открыть исследование " + report.id }).click();
    await success.waitFor();
    await page.getByRole("button", { name: "Открыть исследование " + invalid.id }).click();
    await failure.waitFor();
    assert.ok((await page.locator("#issues").innerText()).trim());
    const validatePath = "/studies/" + invalid.id + "/validate";
    const competing = await page.request.post(new URL(validatePath, address).href, {
      headers: { "If-Match": String(invalid.revision), "Idempotency-Key": randomUUID() },
      timeout: 310000,
    });
    assert.equal(competing.status(), 200);
    const updated = await competing.json();
    const conflict = nextPost("/validate");
    await page.getByRole("button", { name: "Проверить материалы" }).click();
    assert.equal((await conflict).status(), 409);
    await page.getByText("Команда отклонена. Показана текущая ревизия исследования.").waitFor();
    assert.ok((await page.locator("#identity").innerText()).includes("ревизия " + updated.revision));
    await failure.waitFor();
    assert.deepEqual(pageErrors, []);
    console.log("OK — обязательные поля, отказ загрузки, проверка " + dataName
      + ", сброс допуска, сохранённые отчёты и конфликт команд");
  } finally {
    await browser.close();
  }
}

check(...process.argv.slice(2)).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
