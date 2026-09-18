const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("qwenPdf", {
  choosePdf: () => ipcRenderer.invoke("choose-pdf"),
  chooseOutput: () => ipcRenderer.invoke("choose-output"),
  start: (options) => ipcRenderer.invoke("start-pipeline", options),
  cancel: () => ipcRenderer.invoke("cancel-pipeline"),
  openPath: (target) => ipcRenderer.invoke("open-path", target),
  loadSettings: () => ipcRenderer.invoke("load-settings"),
  onEvent: (listener) => ipcRenderer.on("pipeline-event", (_event, payload) => listener(payload)),
});
