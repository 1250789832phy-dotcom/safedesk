const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require(path.join(process.env.RUNTIME_NODE_MODULES, "playwright"));

(async () => {
  const browser = await chromium.launch({ headless: true, channel: "msedge" });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const checks = [];
  const check = (name) => checks.push(name);
  try {
    await page.goto("http://127.0.0.1:8000/", { waitUntil: "networkidle" });
    assert.equal(await page.locator("html").getAttribute("lang"), "zh-CN");
    assert.equal(await page.locator("#status").innerText(), "空闲");
    assert.deepEqual(
      await page.locator("#output option").evaluateAll((options) =>
        options.map((option) => [option.value, option.textContent])),
      [["text", "文字"], ["voice", "语音"], ["both", "文字 + 语音"]]
    );
    check("Chinese initial UI and unchanged output option values");
    await page.locator("#submit").click();
    assert.equal(await page.locator("#notice").innerText(), "请先输入任务内容，或使用语音输入。");
    check("Empty submission message");

    await page.locator("#output").selectOption("text");
    await page.locator("#task").fill("读取 example.txt");
    await page.locator("#submit").click();
    await page.waitForFunction(() => document.getElementById("status").textContent === "已完成");
    assert.match(await page.locator("#report").innerText(), /这是练习文件/);
    assert.match(await page.locator("#taskList").innerText(), /已完成/);
    assert.match(await page.locator("#plan").innerText(), /tool_calls/);
    assert.match(await page.locator("#trace").innerText(), /retrieval_mode/);
    check("Real local mock-planner task submission; original JSON fields preserved");

    const labels = await page.evaluate(() => [
      "idle", "received", "clarifying", "planned", "waiting_confirmation",
      "running", "verifying", "succeeded", "failed", "blocked"
    ].map((status) => {
      renderTask({
        task_id: `fixture-${status}`, status, request: "界面测试",
        output_preference: "text", plan: null, trace: [], report: "",
        context_sources: [], context_truncated: false, retrieval_mode: "disabled"
      });
      return [
        document.getElementById("status").textContent,
        document.getElementById("status").className,
        document.getElementById("plan").textContent,
        document.getElementById("report").textContent
      ];
    }));
    assert.deepEqual(labels.map((row) => row[0]), [
      "空闲", "已接收", "待补充信息", "已规划", "待确认",
      "执行中", "验证中", "已完成", "失败", "已阻止"
    ]);
    assert.equal(labels.at(-1)[1], "blocked");
    assert.equal(labels.at(-1)[2], "暂无执行计划。");
    assert.equal(labels.at(-1)[3], "暂无任务报告。");
    check("All status labels, raw CSS codes, dynamic empty states (UI fixtures)");

    await page.route("**/tasks", (route) => route.fulfill({
      status: 503, contentType: "application/json",
      body: JSON.stringify({ detail: [{ msg: "diagnostic-detail" }] })
    }));
    await page.locator("#refresh").click();
    await page.waitForFunction(() =>
      document.getElementById("notice").textContent.includes("无法加载任务历史"));
    await page.locator("#submit").click();
    await page.waitForFunction(() =>
      document.getElementById("notice").textContent.includes("任务提交失败"));
    assert.match(await page.locator("#notice").innerText(), /diagnostic-detail/);
    await page.unroute("**/tasks");
    check("Chinese HTTP error headings and preserved structured diagnostics (HTTP fixtures)");

    await page.evaluate(() => {
      window.SpeechRecognition = undefined;
      window.webkitSpeechRecognition = undefined;
    });
    await page.locator("#record").click();
    assert.equal(await page.locator("#notice").innerText(), "当前浏览器不支持语音识别，请使用文字输入。");
    check("Speech unsupported fallback (UI fixture; no real microphone)");

    await page.evaluate(() => {
      window.SpeechRecognition = class {
        start() {}
        stop() { this.onend(); }
        abort() { this.onend(); }
      };
    });
    await page.locator("#record").click();
    assert.match(await page.locator("#notice").innerText(), /正在录音/);
    await page.evaluate(() => recognition.onerror({ error: "not-allowed" }));
    assert.match(await page.locator("#notice").innerText(), /未获得麦克风或语音识别权限/);
    await page.locator("#cancel-record").click();
    assert.equal(await page.locator("#notice").innerText(), "已取消录音。");
    assert.equal(await page.locator("#task").inputValue(), "");
    check("Speech recording, permission error and cancel labels (UI fixture only)");

    await page.reload({ waitUntil: "networkidle" });
    for (const [name, width, height] of [["desktop", 1280, 900], ["mobile", 375, 812], ["narrow", 320, 740]]) {
      await page.setViewportSize({ width, height });
      assert.equal(await page.evaluate(() =>
        document.documentElement.scrollWidth <= window.innerWidth), true);
      for (const id of ["record", "stop-record", "cancel-record", "clear", "submit"]) {
        assert.equal(await page.locator(`#${id}`).evaluate((el) =>
          el.scrollWidth <= el.clientWidth), true, `${name}: ${id} overflow`);
        assert.equal(await page.locator(`#${id}`).evaluate((el) => {
          const range = document.createRange();
          range.selectNodeContents(el);
          return range.getClientRects().length;
        }), 1, `${name}: ${id} text wraps`);
      }
      await page.screenshot({ path: path.join(__dirname, `chinese-ui-${name}.png`), fullPage: true });
      check(`${name} layout: no horizontal or button text overflow`);
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: checks.length, checks, browserErrors: errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
