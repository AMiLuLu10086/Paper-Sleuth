# PaperSleuth · 安装、运行与开发指南

本页保留安装、配置、使用与排障说明。产品定位、用户场景和设计取舍见 [产品介绍](../README.md)。

Windows 本机运行的文献 RAG 系统。PDF 解析、清洗、BGE-M3 向量化、Qdrant 向量库和 BGE 重排序在本机完成；问答与中英文检索式改写使用你配置的 DeepSeek 服务。

## 启动

新电脑请先运行 `.\install.ps1` 安装依赖并下载模型，再将 `.env.example` 复制为 `.env`；已安装的环境可直接启动。编辑项目根目录的 `.env`：

```dotenv
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=your-key
DEEPSEEK_MODEL=deepseek-flash
```

保存后，在项目目录的 PowerShell 中启动：

```powershell
.\start.ps1
```

浏览器访问 http://127.0.0.1:8000。保持终端打开，按 Ctrl+C 停止服务。未填写密钥仍可导入、解析和查看文献；问答会显示配置提示。`.env` 已加入 Git 忽略规则，密钥不会返回给网页或写入文献数据库。修改 `.env` 后需要停止并重新启动服务。

如果再次运行 `start.ps1` 时提示 `PaperSleuth is already running`，说明已有服务正在运行，直接使用浏览器即可。需要重启时，先在原来运行服务的终端按 Ctrl+C，再运行 `start.ps1`。若端口被其他程序占用或已有服务仍在启动，脚本会提示检查原终端，不再启动第二个后端。

如果出现 `Storage folder ... is already accessed by another instance of Qdrant client`，说明另一个进程占用了本地向量库。浏览器仍能打开时，可访问 http://127.0.0.1:8000/api/health；返回 `"status":"ok"` 表示已有后端正常响应。即使使用不同端口，同一 `data/` 目录也只能运行一个服务。

仍支持系统或终端环境变量。优先级为：已有环境变量 > `.env` > `config.yaml` 中的 URL 和模型默认值。若曾在终端设置同名变量，先清除该变量或换一个终端，文件中的配置才能生效。`.env.example` 是不含真实密钥的参考模板。

新电脑安装需 Python 3.12 和 Node.js 22 以上：

```powershell
.\install.ps1
```

首次需要下载约数 GB 的 PyTorch 与模型；失败后重跑安装脚本可复用缓存。RTX 5060 使用 CUDA 12.8 构建；没有 NVIDIA GPU 时用 `./install.ps1 -CpuOnly`，也可以在 config.yaml 中将 embedding.device 和 reranker.device 设置为 cpu。GPU 的启用以实际张量运算和模型推理为准，异常时回退 CPU。

## 磁盘空间占用

以 2026-10-12 的 Windows 本机环境为例，完成 CUDA 版 PyTorch、项目依赖和本地模型下载后，整个项目目录的文件大小合计约 **16.34 GB（15.22 GiB）**，包含安装缓存和当前少量文献数据。

| 内容 | 目录 | 文件大小合计 |
|---|---|---:|
| 本地模型与解析资源 | `.cache/models/` | 5.37 GB |
| Python 虚拟环境及依赖 | `.venv/` | 5.32 GB |
| Python 依赖安装缓存 | `.cache/uv/` | 5.23 GB |
| npm 下载缓存 | `.cache/npm/` | 223.95 MB |
| 前端开发依赖 | `frontend/node_modules/` | 171.70 MB |
| 已导入文献、数据库和向量索引 | `data/` | 15.91 MB |
| 源码、Git 记录、构建产物、示例 PDF 等 | 其他文件 | 7.43 MB |
| **项目目录合计** | | **16.34 GB** |

上述 GB、MB 按十进制计量，统计方式为逐文件大小求和。本机部分依赖通过硬链接共享文件，相同文件只计一次时约为 11.14 GB（10.38 GiB）；实际磁盘分配量还受文件系统影响。

按本次测量值，建议为项目预留 **至少 25 GB 可用磁盘空间**，给安装临时文件、缓存和后续导入留出余量。大量论文、页面图片和多次重新索引会继续增加占用；CPU 版依赖、不同版本和缓存状态下的大小也会不同。这里统计的是磁盘文件大小，运行时的 RAM 和显存占用需在解析、向量化和问答时另行测量。

## 使用

1. 点击「导入 PDF 文献」，或把多份文件拖入左侧文献库。每批最多 100 个文件，单文件默认不超过 100 MB、500 页。
2. 入库在后台逐篇执行，显示解析、模型加载和向量化进度。重复文件按内容哈希跳过，失败后可重试；重启会恢复未完成任务。
3. 点开文献查看原 PDF、按阅读顺序排列的文本、图表以及清洗前后记录。
4. 默认在全部已入库文献中提问；勾选文献可以限制问答范围。输入中文问题，点击引用检查英文证据和 PDF 高亮区域。
5. 可继续追问、停止生成，在左下角打开对话记录。

数据保存在 `data/`，模型保存在 `.cache/models/`。Git 仓库不包含密钥、论文 PDF、运行数据、模型缓存或构建产物；示例论文验证需自行准备 PDF。原始 PDF 保留；索引成功后才切换版本，旧版本仍能打开历史证据。运行时仅使用一个后端进程；不要为 uvicorn 添加多个 workers，也不要同时启动两个访问同一 data 目录的服务。

## 参数调整

编辑项目根目录的 `config.yaml`。网页「设置」可查看脱敏配置和模型状态。

- `chunking.max_tokens / overlap_tokens`：默认 700 / 100，按 BGE-M3 tokenizer 计数，标题也计入预算。
- `embedding.batch_size / reranker.batch_size`：默认 4；显存紧张时减到 1 或 2。
- `pdf.device / threads / ocr / ocr_language`：默认 CPU、4 线程、自动 OCR、英语。中英混合扫描件可选 `ch`；更改语言后首次可能需要下载识别权重。
- `cleaning`：默认页边上下各 8%，重复内容至少出现 3 页且覆盖 60%；自定义规则仅作用于页边。
- `retrieval`：每路召回 20 条，RRF 融合最多 40 条，重排取 8 条；可关闭重排序以减少问答时的本机计算。
- 问答连接在 `.env` 中填写 `DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / DEEPSEEK_API_KEY`；URL 填服务前缀，不含 `/chat/completions`。`config.yaml` 的 `llm.base_url / model` 用作回退默认值，`api_key_env` 默认 DEEPSEEK_API_KEY。

普通检索和问答配置在下一次请求生效。清洗、PDF、切片、向量模型参数修改后，点击原文区右上角「重新索引」。改变模型或编码参数不能混用旧向量，即使维度相同也需要重新索引。存储及模型缓存路径修改后需重启。

## PDF 与问答边界

- Docling 分析版面与阅读顺序，使用 PDFium 读取 PDF，兼容 Windows 中文项目路径。优先使用文本层，缺少文本的区域由 RapidOCR 识别。
- 页眉页脚和重复页边信息清洗后不会入库，原始文字及清洗记录保留。数字、浓度、单位和正文公式不会用全局正则粗暴删除。
- 表格保存单元格结构、HTML 和截图，长表分行组并重复表头；复杂跨页表格仍需人工核对。
- 图区域通过页面渲染裁切，覆盖矢量图和嵌入图片。图注参与检索，图像本身不做视觉推理；无图注的图片仅供查看。
- 只被正文提及、实际未导入的补充材料不能作为已读取证据。系统显式阻止编造缺失的 Table S 编号表格数据。
- 引用 ID 可检查是否对应提供的证据，但这不等于自动证明每个结论正确。使用研究数据前需要核对原文。
- 问题、相关对话上下文和检索片段会发送到配置的问答服务。完整 PDF 不上传给问答服务。

## 验证

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts/create_fixtures.py
.venv\Scripts\python.exe scripts/verify_parsing.py
.venv\Scripts\python.exe scripts/verify_retrieval.py
```

`scripts/ingest_sample.py` 在服务启动后通过真实上传接口导入项目中的论文；重复执行会识别已有文献。`scripts/browser_check.py` 使用无界面的本机 Edge 检查网页和 PDF 渲染，截图保存在 test-results。

真实论文问答基准与验证报告见 [验收记录](acceptance.md)。DeepSeek 真实生成需要你填写密钥；没有密钥时用可控流式响应验证接口，不冒充真实模型测试。

## 开发

后端：`.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`

前端：在 frontend 目录运行 `npm run dev`，访问 http://127.0.0.1:5173。构建使用 `npm run build`；正常使用只需启动后端，它会同时提供构建后的网页。
