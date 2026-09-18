const $ = (id) => document.getElementById(id);
let lastResult = "";

function append(message, isError = false) {
  const line = document.createElement("span");
  line.textContent = message;
  if (isError) line.className = "error";
  $("log").append(line);
  $("log").scrollTop = $("log").scrollHeight;
}

function setRunning(running) {
  $("start").disabled = running;
  $("cancel").disabled = !running;
}

function options() {
  return {
    inputPdf: $("inputPdf").value,
    outputDir: $("outputDir").value,
    ocrKey: $("ocrKey").value.trim(),
    ocrBaseUrl: $("ocrBaseUrl").value.trim(),
    ocrModel: $("ocrModel").value.trim(),
    ocrWorkers: Number($("ocrWorkers").value),
    translationKey: $("sameKey").checked ? $("ocrKey").value.trim() : $("translationKey").value.trim(),
    translationBaseUrl: $("translationBaseUrl").value.trim(),
    translationModel: $("translationModel").value.trim(),
    translationWorkers: Number($("translationWorkers").value),
    batchSize: Number($("batchSize").value),
    renderMode: $("renderMode").value,
    fontSizeFactor: Number($("fontSizeFactor").value),
  };
}

$("pickPdf").addEventListener("click", async () => { const value = await window.qwenPdf.choosePdf(); if (value) $("inputPdf").value = value; });
$("pickOutput").addEventListener("click", async () => { const value = await window.qwenPdf.chooseOutput(); if (value) $("outputDir").value = value; });
$("sameKey").addEventListener("change", () => { $("translationKey").disabled = $("sameKey").checked; });
$("start").addEventListener("click", async () => {
  $("log").textContent = "";
  lastResult = "";
  $("openResult").disabled = true;
  setRunning(true);
  $("status").textContent = "运行中";
  const result = await window.qwenPdf.start(options());
  setRunning(false);
  if (!result.ok) $("status").textContent = "失败";
});
$("cancel").addEventListener("click", async () => { await window.qwenPdf.cancel(); $("status").textContent = "正在停止"; });
$("openResult").addEventListener("click", () => window.qwenPdf.openPath(lastResult));

window.qwenPdf.onEvent((event) => {
  if (event.type === "stage") { $("status").textContent = event.message; append(`\n=== ${event.message} ===\n`); }
  if (event.type === "log") append(event.message, event.stream === "stderr");
  if (event.type === "error") { $("status").textContent = `失败：${event.message}`; append(`\n错误：${event.message}\n`, true); setRunning(false); }
  if (event.type === "done") { lastResult = event.result; $("status").textContent = "完成"; $("openResult").disabled = false; append(`\n完成：${event.result}\n`); setRunning(false); }
});

window.qwenPdf.loadSettings().then((saved) => {
  for (const id of ["outputDir", "ocrBaseUrl", "ocrModel", "ocrWorkers", "translationBaseUrl", "translationModel", "translationWorkers", "batchSize", "renderMode", "fontSizeFactor"]) {
    if (saved[id] !== undefined && $(id)) $(id).value = saved[id];
  }
});
