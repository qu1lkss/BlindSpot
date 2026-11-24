const assert = require("node:assert/strict");
const { chromium } = require("playwright");

async function check(address, screenshot) {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1100, height: 650 } });
    await page.goto(address);
    const empty = page.getByText("Сохранённых исследований пока нет.", { exact: true });
    await empty.waitFor();
    if (screenshot) await page.screenshot({ path: screenshot });
    const refresh = page.getByRole("button", { name: "Обновить" });
    await page.route("**/studies", (route) => route.abort());
    await refresh.click();
    await page.getByText("Не удалось загрузить исследования.", { exact: false }).waitFor();
    await page.unroute("**/studies");
    await refresh.click();
    await empty.waitFor();
    assert.equal(await page.title(), "Исследования · BlindSpot");
    console.log("OK — пустой список, ошибка чтения, повторная загрузка");
  } finally {
    await browser.close();
  }
}

check(process.argv[2], process.argv[3]).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
