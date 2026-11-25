const assert = require("node:assert/strict");
const { chromium } = require("playwright");

async function check(url) {
  const browser = await chromium.launch({ channel: "chrome" });
  try {
    const page = await browser.newPage();
    const list = await (await page.request.get(url + "/studies")).json();
    const study = list.items.find(item => item.stage === "проверена" && (!process.argv[3] || item.id === process.argv[3]));
    assert.ok(study, "Сначала загрузите и проверьте независимый пример");
    await page.goto(url);
    await page.getByRole("button", { name: "Открыть исследование " + study.id }).click();
    await page.getByRole("button", { name: "Подготовить выборки" }).click();
    await page.getByText("Строк для поиска: 600. Для подтверждения: 400.").waitFor();
    console.log("OK — подготовка выборок через браузер");
  } finally {
    await browser.close();
  }
}

check(process.argv[2]).catch(error => { console.error(error); process.exitCode = 1; });
