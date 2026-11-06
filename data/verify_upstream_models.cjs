const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require(path.join(process.env.RUNTIME_NODE_MODULES, "playwright"));

(async () => {
  const browser = await chromium.launch({ headless: true, channel: "msedge" });
  const page = await browser.newPage({
    baseURL: "http://127.0.0.1:8011", viewport: { width: 1280, height: 900 }
  });
  const errors = [];
  const checks = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("dialog", (dialog) => dialog.accept());
  const notice = (text) => page.waitForFunction(
    (value) => document.getElementById("model-notice").textContent.includes(value), text
  );
  const observed = async () => (await page.request.get("/fixture/observed")).json();
  try {
    await page.request.delete("/settings/model", { headers: { "X-SafeDesk-Settings": "1" } });
    const initialCount = (await observed()).length;
    await page.goto("/", { waitUntil: "networkidle" });
    assert.equal((await observed()).length, initialCount);
    checks.push("Opening the page does not contact the fake upstream");
    await page.locator("#api-url").fill("http://127.0.0.1:8011/fixture/v1");
    await page.locator("#api-key").fill("browser-discovery-dummy-key");
    assert.equal(await page.locator("#model-name").inputValue(), "");
    await page.locator("#fetch-models").click();
    await notice("模型列表已获取");
    assert.deepEqual(await page.locator("#upstream-model option").evaluateAll(
      (options) => options.map((option) => option.value)
    ), ["", "model-alpha", "model-beta"]);
    assert.equal(await page.locator("#model-name").inputValue(), "");
    assert.equal((await (await page.request.get("/settings/model")).json()).mode, "mock");
    assert.deepEqual((await observed()).slice(initialCount), [{
      method: "GET", path: "/fixture/v1/models", has_expected_dummy_key: true
    }]);
    checks.push("Real SDK calls local fake /models with dummy key; model is not required");
    await page.locator("#upstream-model").selectOption("model-beta");
    assert.equal(await page.locator("#model-name").inputValue(), "model-beta");
    await page.locator("#save-model").click();
    await notice("配置已加密保存并启用");
    assert.equal((await (await page.request.get("/settings/model")).json()).model, "model-beta");
    assert.equal(await page.locator("#api-key").inputValue(), "");
    checks.push("Select updates model input; explicit save persists chosen model");
    await page.reload({ waitUntil: "networkidle" });
    assert.equal((await observed()).length, initialCount + 1);
    assert.equal(await page.locator("#model-name").inputValue(), "model-beta");
    await page.locator("#fetch-models").click();
    await notice("模型列表已获取");
    assert.equal(await page.locator("#upstream-model").inputValue(), "model-beta");
    assert.equal((await observed()).length, initialCount + 2);
    checks.push("Reload preserves chosen model; explicit fetch reuses encrypted key");

    for (const [name, width, height] of [
      ["desktop", 1280, 900], ["mobile", 375, 812], ["narrow", 320, 740]
    ]) {
      await page.setViewportSize({ width, height });
      assert.equal(await page.evaluate(() =>
        document.documentElement.scrollWidth <= innerWidth), true);
      for (const id of ["upstream-model", "fetch-models", "save-model"]) {
        assert.equal(await page.locator(`#${id}`).evaluate((el) =>
          el.scrollWidth <= el.clientWidth), true, `${name}: ${id} overflow`);
      }
      await page.screenshot({
        path: path.join(__dirname, `upstream-models-${name}.png`), fullPage: true
      });
      checks.push(`${name} native dropdown and buttons do not overflow`);
    }

    await page.locator("#api-key").fill("alternate-dummy-key");
    await page.locator("#fetch-models").click();
    await notice("模型列表已获取");
    assert.equal(await page.locator("#upstream-model option").last().getAttribute("value"), "alternate-key-only-model");
    await page.locator("#mock-model").click();
    await notice("已切回模拟模式");
    assert.equal(await page.locator("#api-key").inputValue(), "");
    assert.equal(await page.locator("#upstream-model-field").isVisible(), false,
      "Restoring the saved key must discard the unsaved key's catalog");
    await page.locator("#fetch-models").click();
    await notice("模型列表已获取");
    assert.deepEqual(await page.locator("#upstream-model option").evaluateAll(
      (options) => options.map((option) => option.value)
    ), ["", "model-alpha", "model-beta"]);
    checks.push("Restoring saved credentials discards the unsaved key's model catalog");
    await page.locator("#api-key").fill("empty-dummy-key");
    assert.equal(await page.locator("#upstream-model-field").isVisible(), false);
    await page.locator("#fetch-models").click();
    await notice("未返回可选择的模型");
    assert.equal(await page.locator("#upstream-model").isDisabled(), true);
    assert.equal(await page.locator("#model-name").inputValue(), "model-beta");
    await page.locator("#model-name").fill("manual-model");
    checks.push("Key edits clear stale choices; empty catalog preserves manual entry");
    await page.locator("#api-key").fill("denied-dummy-key");
    await page.locator("#fetch-models").click();
    await notice("密钥无效或已过期");
    assert.equal(await page.locator("#model-notice").getAttribute("class"), "error");
    assert.equal(await page.locator("#model-name").inputValue(), "manual-model");
    assert.equal(await page.locator("#model-notice").innerText().then(
      (value) => value.includes("denied-dummy-key")), false);
    assert.equal(await page.locator("#fetch-models").isEnabled(), true);
    checks.push("Real local fake 401 is sanitized and manual entry remains available");
    await page.locator("#api-key").fill("browser-discovery-dummy-key");
    await page.route("**/settings/model/models", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 400));
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ models: ["<img src=x onerror=alert(1)>"], truncated: false, message: "测试模型" })
      });
    });
    await page.locator("#fetch-models").click();
    assert.equal(await page.locator("#fetch-models").isDisabled(), true);
    await notice("模型列表已获取");
    assert.equal(await page.locator("#upstream-model option").last().textContent(), "<img src=x onerror=alert(1)>");
    assert.equal(await page.locator("#upstream-model-field img").count(), 0);
    await page.unroute("**/settings/model/models");
    checks.push("Loading controls disable; model IDs render as text (UI fixture)");
    await page.locator("#api-url").fill("http://127.0.0.1:8011/other/v1");
    assert.equal(await page.locator("#upstream-model-field").isVisible(), false);
    await page.locator("#api-key").fill("");
    const before = (await observed()).length;
    await page.locator("#fetch-models").click();
    await notice("重新填写 API Key");
    assert.equal((await observed()).length, before);
    checks.push("Changed URL cannot reuse saved key or show stale catalog");
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({
      passed: checks.length, checks, browserErrors: errors,
      verification: "local fake upstream only; no real provider"
    }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
