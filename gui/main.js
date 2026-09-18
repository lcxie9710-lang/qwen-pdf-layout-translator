const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

for (const stream of [process.stdout, process.stderr]) {
  stream?.on?.("error", (error) => {
    if (error?.code !== "EPIPE") process.exitCode = 1;
  });
}

const projectRoot = path.resolve(__dirname, "..");
const python = path.join(projectRoot, "runtime", "python", "python.exe");
const ocrScript = path.join(projectRoot, "tools", "qwen_ocr_adapter.py");
const flowScript = path.join(projectRoot, "backend", "scripts", "entrypoints", "run_document_flow.py");
const scriptsRoot = path.join(projectRoot, "backend", "scripts");
let mainWindow = null;
let activeChild = null;
let canceled = false;

function send(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("pipeline-event", payload);
}

function settingsPath() {
  return path.join(app.getPath("userData"), "settings.json");
}

function loadSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsPath(), "utf8"));
  } catch (_error) {
    return {};
  }
}

function saveSettings(options) {
  const safe = {
    outputDir: options.outputDir,
    ocrBaseUrl: options.ocrBaseUrl,
    ocrModel: options.ocrModel,
    ocrWorkers: options.ocrWorkers,
    translationBaseUrl: options.translationBaseUrl,
    translationModel: options.translationModel,
    translationWorkers: options.translationWorkers,
    batchSize: options.batchSize,
    renderMode: options.renderMode,
    fontSizeFactor: options.fontSizeFactor,
  };
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(safe, null, 2), "utf8");
}

function validate(options) {
  if (!options || typeof options !== "object") throw new Error("缺少任务参数");
  if (!fs.existsSync(options.inputPdf || "")) throw new Error("请选择存在的 PDF 文件");
  if (!options.outputDir) throw new Error("请选择输出目录");
  if (!options.ocrKey) throw new Error("请输入 DashScope OCR API Key");
  if (!options.translationKey) throw new Error("请输入翻译 API Key");
  if (!/^https?:\/\//i.test(options.ocrBaseUrl || "")) throw new Error("OCR Base URL 无效");
  if (!/^https?:\/\//i.test(options.translationBaseUrl || "")) throw new Error("翻译 Base URL 无效");
  if (!options.ocrModel || !options.translationModel) throw new Error("模型名称不能为空");
  const ocrWorkers = Number(options.ocrWorkers);
  if (!Number.isInteger(ocrWorkers) || ocrWorkers < 1 || ocrWorkers > 8) throw new Error("OCR 并发必须为 1–8");
}

function runProcess(label, args, env) {
  return new Promise((resolve, reject) => {
    send({ type: "stage", message: label });
    const child = spawn(python, args, {
      cwd: projectRoot,
      env,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    activeChild = child;
    const onData = (stream) => (chunk) => send({ type: "log", stream, message: chunk.toString() });
    child.stdout.on("data", onData("stdout"));
    child.stderr.on("data", onData("stderr"));
    child.once("error", reject);
    child.once("exit", (code) => {
      activeChild = null;
      if (canceled) return reject(new Error("任务已取消"));
      if (code === 0) resolve();
      else reject(new Error(`${label}失败，进程退出码 ${code}`));
    });
  });
}

async function runPipeline(options) {
  validate(options);
  if (activeChild) throw new Error("已有任务正在运行");
  canceled = false;
  saveSettings(options);
  fs.mkdirSync(options.outputDir, { recursive: true });

  const stem = path.basename(options.inputPdf, path.extname(options.inputPdf));
  const workDir = path.join(options.outputDir, "_qwen_ocr");
  fs.mkdirSync(workDir, { recursive: true });
  const ocrJson = path.join(workDir, `${stem}.qwen-ocr.json`);
  const jobId = `${new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14)}-${stem}`;
  const env = {
    ...process.env,
    DASHSCOPE_API_KEY: options.ocrKey,
    DEEPSEEK_API_KEY: options.translationKey,
    PYTHONPATH: [scriptsRoot, process.env.PYTHONPATH || ""].filter(Boolean).join(path.delimiter),
    PYTHONUTF8: "1",
    PYTHONUNBUFFERED: "1",
    TYPST_BIN: path.join(projectRoot, "runtime", "typst", "bin", "typst.exe"),
    TYPST_PACKAGE_PATH: path.join(projectRoot, "runtime", "typst-packages"),
    RETAIN_PDF_FONT_PATH: path.join(projectRoot, "runtime", "fonts", "SourceHanSerifSC-Regular.otf"),
    RETAIN_PDF_TITLE_BOLD_FONT_PATH: path.join(projectRoot, "runtime", "fonts", "SourceHanSerifSC-Bold.otf"),
    RETAIN_PDF_TYPST_FONT_DIRS: path.join(projectRoot, "runtime", "fonts"),
  };

  await runProcess("第 1/2 步：Qwen OCR", [
    ocrScript, "--input", options.inputPdf, "--output", ocrJson,
    "--base-url", options.ocrBaseUrl, "--model", options.ocrModel,
    "--workers", String(options.ocrWorkers),
  ], env);

  await runProcess("第 2/2 步：翻译并按原版重建 PDF", [
    flowScript, "--source-json", ocrJson, "--source-pdf", options.inputPdf,
    "--base-url", options.translationBaseUrl, "--model", options.translationModel,
    "--workers", String(options.translationWorkers), "--batch-size", String(options.batchSize),
    "--mode", "fast", "--render-mode", options.renderMode,
    "--body-font-size-factor", String(options.fontSizeFactor),
    "--output-root", options.outputDir, "--job-id", jobId,
    "--output", `${stem}-translated.pdf`,
  ], env);

  const result = path.join(options.outputDir, jobId, "translated", `${stem}-translated.pdf`);
  send({ type: "done", message: "翻译完成", result, outputDir: options.outputDir });
  return { ok: true, result, outputDir: options.outputDir };
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 980,
    height: 820,
    minWidth: 820,
    minHeight: 680,
    title: "Qwen PDF 保版翻译",
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false },
  });
  mainWindow.loadFile(path.join(__dirname, "index.html"));
}

ipcMain.handle("choose-pdf", async () => {
  const result = await dialog.showOpenDialog(mainWindow, { properties: ["openFile"], filters: [{ name: "PDF", extensions: ["pdf"] }] });
  return result.canceled ? "" : result.filePaths[0];
});
ipcMain.handle("choose-output", async () => {
  const result = await dialog.showOpenDialog(mainWindow, { properties: ["openDirectory", "createDirectory"] });
  return result.canceled ? "" : result.filePaths[0];
});
ipcMain.handle("load-settings", () => loadSettings());
ipcMain.handle("start-pipeline", async (_event, options) => {
  try { return await runPipeline(options); }
  catch (error) { send({ type: "error", message: error?.message || String(error) }); return { ok: false, error: error?.message || String(error) }; }
});
ipcMain.handle("cancel-pipeline", () => {
  canceled = true;
  if (activeChild) spawn("taskkill", ["/PID", String(activeChild.pid), "/T", "/F"], { windowsHide: true });
  return { ok: true };
});
ipcMain.handle("open-path", async (_event, target) => {
  if (!target) return "";
  return shell.openPath(target);
});

app.setName("Qwen PDF Layout Translator");
app.whenReady().then(createWindow);
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("before-quit", () => { if (activeChild) activeChild.kill(); });
