const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(path.join(process.env.RUNTIME_NODE_MODULES, "playwright"));

const phase = process.argv[2] || "prepare";
const key = "browser-test-key-not-a-real-credential";
const settingsPath = path.join(__dirname, "model-settings.json");

(async () => {
  const browser = await chromium.launch({ headless: true, channel: "msedge" });
  const page = await browser.newPage({
    baseURL: "http://127.0.0.1:8000", viewport: { width: 1280, height: 900 }
  });
  const errors = [];
  const checks = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("dialog", (dialog) => dialog.accept());
  await page.route("**/*", (route) => {
    if (new URL(route.request().url()).hostname !== "127.0.0.1") {
      errors.push("unexpected external browser request");
      return route.abort();
    }
    return route.continue();
  });
  const notice = (part) => page.waitForFunction(
    (text) => document.getElementById("model-notice").textContent.includes(text), part
  );
  try {
    await page.goto("http://127.0.0.1:8000/", { waitUntil: "networkidle" });
    if (phase === "prepare") {
      assert.equal((await page.request.get("/settings/model")).ok(), true);
      assert.equal(await page.locator("#model-mode").innerText(), "模拟模式");
      await page.locator("#api-url").fill("https://example.com/v1");
      await page.locator("#model-name").fill("browser-test-model");
      await page.locator("#api-key").fill(key);
      await page.locator("#show-key").check();
      assert.equal(await page.locator("#api-key").getAttribute("type"), "text");
      await page.locator("#show-key").uncheck();
      assert.equal(await page.locator("#api-key").getAttribute("type"), "password");
      checks.push("Chinese form and explicit key visibility toggle");
      await page.locator("#save-model").click();
      await notice("配置已加密保存并启用");
      assert.equal(await page.locator("#api-key").inputValue(), "");
      assert.equal(await page.locator("#model-mode").innerText(), "API 模式");
      const saved = fs.readFileSync(settingsPath, "utf8");
      assert.equal(saved.includes(key), false);
      assert.ok(JSON.parse(saved).encrypted_api_key);
      const status = await (await page.request.get("/settings/model")).text();
      assert.equal(status.includes(key), false);
      checks.push("Real local save writes encrypted file and redacted status");
      await page.reload({ waitUntil: "networkidle" });
      assert.equal(await page.locator("#api-url").inputValue(), "https://example.com/v1");
      assert.equal(await page.locator("#model-name").inputValue(), "browser-test-model");
      assert.equal(await page.locator("#api-key").inputValue(), "");
      assert.match(await page.locator("#api-key").getAttribute("placeholder"), /已加密保存/);
      assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
      checks.push("Page reload restores URL/model without key or browser storage");
      await page.route("**/settings/model/test", (route) => route.fulfill({
        status: 502, contentType: "application/json",
        body: JSON.stringify({ detail: "连接测试失败（页面模拟，不是真实模型请求）。" })
      }));
      await page.locator("#test-model").click();
      await notice("连接测试失败");
      assert.equal(await page.locator("#model-notice").getAttribute("class"), "error");
      assert.equal(await page.locator("#test-model").isEnabled(), true);
      await page.unroute("**/settings/model/test");
      checks.push("Connection error UI fixture only; no real provider request");
      await page.locator("#model-name").fill("browser-restart-model");
      await page.locator("#api-protocol").selectOption("responses");
      await page.locator("#save-model").click();
      await notice("配置已加密保存并启用");
      checks.push("Blank-key update retains encrypted key and changes protocol");
      for (const [name, width, height] of [
        ["desktop", 1280, 900], ["mobile", 375, 812], ["narrow", 320, 740]
      ]) {
        await page.setViewportSize({ width, height });
        assert.equal(await page.evaluate(() =>
          document.documentElement.scrollWidth <= innerWidth), true);
        for (const id of ["api-url", "model-name", "api-protocol", "api-key",
                          "save-model", "test-model", "mock-model", "forget-model"]) {
          assert.equal(await page.locator(`#${id}`).evaluate((el) =>
            el.scrollWidth <= el.clientWidth), true, `${name} ${id} overflow`);
        }
        await page.screenshot({
          path: path.join(__dirname, `model-settings-${name}.png`), fullPage: true
        });
        checks.push(`${name} layout has no horizontal/control overflow`);
      }
    } else {
      assert.equal(await page.locator("#model-mode").innerText(), "API 模式");
      assert.equal(await page.locator("#model-name").inputValue(), "browser-restart-model");
      assert.equal(await page.locator("#api-protocol").inputValue(), "responses");
      assert.equal(await page.locator("#api-key").inputValue(), "");
      assert.equal((await (await page.request.get("/settings/model")).json()).has_api_key, true);
      checks.push("Actual server restart restores encrypted configuration");
      await page.locator("#mock-model").click();
      await notice("已切回模拟模式");
      assert.equal(await page.locator("#model-mode").innerText(), "模拟模式");
      assert.equal((await (await page.request.get("/settings/model")).json()).has_api_key, true);
      checks.push("Mock switch retains encrypted configuration");
      await page.locator("#forget-model").click();
      await notice("已删除保存的配置");
      const status = await (await page.request.get("/settings/model")).json();
      assert.equal(status.mode, "mock");
      assert.equal(status.has_api_key, false);
      assert.equal(status.base_url, "");
      assert.equal(JSON.parse(fs.readFileSync(settingsPath, "utf8")).encrypted_api_key, "");
      checks.push("Confirmed clear removes dummy configuration and leaves mock active");
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ phase, passed: checks.length, checks, browserErrors: errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
