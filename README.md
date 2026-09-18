# Qwen PDF 保版翻译器

一个面向 Windows 的轻量 GUI：使用 Qwen OCR 识别扫描版或图片型 PDF，使用
Qwen 翻译文本，再通过 RetainPDF 的排版与渲染能力重建 PDF，尽量保持原书的
图片、文字位置和图文对应关系。

本项目是独立维护的 RetainPDF 衍生项目，并非 RetainPDF 官方发行版。

## 最快开始（推荐）

1. 从 GitHub Releases 下载 `QwenPDF-Layout-Translator-Windows-x64-Portable.zip`。
2. 将 ZIP **完整解压**到普通文件夹；不要直接在压缩包内运行。
3. 双击 `Start.cmd`。
4. 选择 PDF 和输出目录。
5. 输入自己的阿里云百炼 DashScope API Key。
6. 保持默认模型，点击“开始翻译”。

便携版已经包含 Electron、Python、Typst、字体和所需 Python 包，无需安装
Node.js、Python 或 RetainPDF。

## 默认配置

| 用途 | 默认值 |
| --- | --- |
| API Base URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| OCR 模型 | `qwen3.5-ocr` |
| 翻译模型 | `qwen-plus` |
| OCR 并发 | `3` |
| 翻译并发 / 批大小 | `4 / 4` |
| 排版模式 | `auto` |
| 字号系数 | `0.95` |

API Key 只会传给本机启动的子进程，不会写入项目目录或 GUI 设置文件。输出
目录、模型名称和并发等非敏感设置会保存在 Electron 的用户配置目录中。

## 输出在哪里

每次任务会在所选输出目录中创建一个带时间和原文件名的任务文件夹。最终
PDF 位于该任务文件夹的 `translated` 子目录中。完成后可直接点击 GUI 中的
“打开结果”。OCR 中间文件位于所选输出目录的 `_qwen_ocr` 子目录。

## 常见问题

### OCR 出现 `SSLEOFError` 或连接被中断

这通常是并发连接遇到的临时网络问题。程序会保留成功页面，并将失败页面
降级为串行重试。如果仍失败，请把“OCR 并发页数”改为 `1` 后重新运行，并
检查代理、VPN、防火墙或安全软件。

### API 测试失败

确认 Base URL 完整填写为：

```text
https://dashscope.aliyuncs.com/compatible-mode/v1
```

同时检查 API Key 是否有效、账户是否开通相应模型，以及当前网络能否连接
`dashscope.aliyuncs.com`。

### 图片很多时应该选择什么排版模式

优先使用 `auto`。它会根据页面结构在背景保留和覆盖式渲染之间选择。只有在
排查具体页面问题时再手动尝试 `overlay` 或 `typst`。

### 字太大或太小

调整“字号系数”。小于 `1.0` 会缩小译文字号，大于 `1.0` 会放大。建议每次以
`0.05` 为步长调整。

## 源码目录

```text
backend/scripts/    RetainPDF 文档流程、翻译和排版代码
gui/                Electron 图形界面
tools/              Qwen OCR 到 RetainPDF 文档结构的适配器
runtime/            便携运行环境（不提交 Git）
scripts/            构建便携版的脚本
dist/               构建出的 ZIP（作为 GitHub Release 附件发布）
Start.cmd             Windows 主启动器
```

源码仓库不提交 `runtime/` 和 `dist/`，因为完整便携版超过 GitHub 普通仓库的
单文件大小限制。发布时运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-portable.ps1
```

然后将 `dist` 中的 ZIP 上传到 GitHub Releases。

## 安全提示

- 不要把 API Key 写进代码、截图、Issue 或提交记录。
- 如果 Key 曾经公开，请立即在阿里云控制台撤销并重新生成。
- 翻译受版权保护的书籍时，请确认自己拥有处理和传播相应内容的权利。

## 上游与许可证

本项目使用 RetainPDF 的 PDF 布局分析、翻译工作流和渲染代码：

- 上游项目：https://github.com/newnol/retain-pdf-en
- Copyright (c) 2026 RetainPDF contributors
- RetainPDF 根据 MIT License 发布

本项目代码同样以 MIT License 发布。第三方运行时、字体和依赖仍适用各自的
许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
