# AGENTS.md This file provides guidance to agents when working with code in this repository.

恋爱·职场聊天神器：本地运行的聊天记录意图 / 情绪分析工具。FastAPI 分层后端 + Vite/Vue 3 前端，分析逻辑全部在后端，前端只做展示与编排。

## 常用命令

### 后端依赖（uv 管理）
```bash
uv sync --no-dev          # 仅装运行依赖并建 .venv（启动用不到测试包）
uv sync                   # 含 dev 组：pytest、ruff
cp .env.example .env      # 填入 API 配置（见「配置」一节）
```

### 启动后端（127.0.0.1:8767）
```bash
.venv/bin/python -m app serve                # macOS / Linux
.venv\Scripts\python.exe -m app serve        # Windows
```
`serve` 默认开热重载；`--no-reload` 关闭。`--host/--port` 可覆盖。接口文档在 `/docs`。

### 启动前端
```bash
cd frontend && pnpm install && pnpm run dev  # 开发模式 http://127.0.0.1:5173（直连 8767）
cd frontend && pnpm run build                # 构建到 frontend/dist，由后端在 8767 托管
```
一键脚本：macOS 双击 `启动.command`，Windows 双击 `start.bat`（找不到 pnpm 时退回「构建产物 + 后端托管」单端口 8767）。

### 测试（离线，假上游，不消耗额度）
```bash
.venv/bin/python -m pytest                                        # 全量离线套件
.venv/bin/python -m pytest tests/services/test_pipeline.py::test_analyze  # 跑单条测试
```

### 静态检查（ruff，dev 依赖）
```bash
.venv/bin/ruff check .          # 或 uv run ruff check .
```

### 夹具回归 / 回流池（会真实调用模型）
```bash
.venv/bin/python -m app check   # 跑 data/cases.json，结果写 var/regression/test-results.json
.venv/bin/python -m app pool    # 查看低置信度回流池待审样本
```

## 架构

### 双模型、两条链路
系统刻意做成「双模型架构」：
- **分类层（Jev）**：意图 / 情绪两级判定，请求体是 Jev 私有协议（`state` + `questions`），由 `app/clients/jev.py` 拼包。换别家分类模型需改写该文件的拼包与解析。
- **生成层（OpenAI 兼容）**：潜台词与回复建议走标准 `/chat/completions`，由 `app/clients/general_llm.py` 调用，可换任意 OpenAI 兼容端点（仅改 `.env` 三个变量即可）。

**潜台词与推荐回复是两条独立链路**（`/interpret-chat`、`/suggest-chat`），不是一次调用里顺带做。原因：两者的长度 / 人称约束完全不同，混在一起时一侧失败会丢掉另一侧结果，且「只想要潜台词」不该顺带生成建议。现在各点各的、各自整形、各自标记失败，合并回会话时按「字段是否存在」判断，跑一类不会清掉另一类已生成内容。

### 后端分层与依赖方向
依赖方向自上而下，**禁止反向依赖**：

```
main.py → api/ → services/ → { domain/, clients/, repositories/ } → core/
```

- `api/`：接口层，只做协议转换与来源校验（`guards.py`）、依赖注入（`deps.py`）、统一响应（`responses.py`）。`routes/` 下只放端点定义。
- `services/`：用例层，业务编排（`classifier` 单条分类、`pipeline` 整段 / 追加 / 补跑、`sessions` 落库与合并、`generation` 两条生成链路、`review_pool` 低置信度回流）。
- `domain/`：纯逻辑、无 IO（`labels` 标签库、`prompts` 全部提示词、`transcript` 聊天解析）。
- `clients/`：外部依赖（Jev 网关、OpenAI 兼容生成层）。
- `repositories/`：持久化层，只存取不写业务规则（SQLite 会话库 `var/sessions.db`）。
- `schemas/`：只描述协议；**校验不放这里**（见下）。
- `core/`：配置（`config`）、异常（`exceptions`）、日志（`logging`）。

### 三条刻意约定（改代码前必读）
1. **提示词集中在 `domain/prompts.py`**：这是产品的真正逻辑，改动等于改判定口径，不应散落在服务代码里。
2. **业务校验不在 pydantic 模型里**：字符串字段默认空串、模型 `extra='allow'`，校验统一在 services 抛 `InvalidRequest`；前端拿到的是「一句中文提示 + 400」，不要去模型里加校验。
3. **路由用 `def` 而非 `async def`**：分类与生成都是阻塞 IO，交给 Starlette 线程池，避免整段分析（几十秒）卡死事件循环。

### 流式（SSE）
三个 `/…/stream` 端点用 `text/event-stream` 推事件。前端用 `fetch` + `ReadableStream` 手写分帧读取（`api/client.js` 的 `streamAPI`），**不用 `EventSource`**：后者只能 GET，而请求体是 JSON。流式与非流式共用同一套 pipeline 实现（`pipeline.py`），不要为流式单独写一份逻辑。错误只能靠事件传达（`error` 事件），HTTP 已是 200，文案与一次性端点必须完全一致。

### 会话持久化规则
- 导入即存档；**正文与当前会话完全相同 = 原地重跑**（不产生重复条目），**正文不同 = 另起一条**（旧会话保留）。
- `session_id` 可选：带上就「以库里的会话为准」（补跑端点读库、合并、写回）；不带则保持老路径（只带 `prev`，不落库），脚本与夹具继续可用。
- 两类生成并发写同一会话时是加锁的，不会互相覆盖。

### 配置优先级
真实环境变量（shell 里 `export` 的）**优先于** `.env`；界面保存是「按行合并」写回 `.env`（只替换目标键那一行，注释 / 顺序 / 其它键不动、密钥留空 = 不改动），保存后清空配置与各服务实例缓存，**下一次请求即用新配置，不用重启**。

### 生成层的多套配置（v009）
生成层可以保存多套「地址 + 模型 + 密钥」并切换启用哪套：整表存 `.env` 的 `LLM_PROFILES`（单行 JSON），
启用的名字存 `LLM_ACTIVE`。**多套只存在于配置层**：`Settings.model_post_init` 会把「当前启用」那套
落到 `deepseek_base_url / deepseek_model / deepseek_api_key` 三个老字段上，所以 `clients/general_llm.py`
与生成链路不知道多套配置的存在，改切换只动 `core/config.py` 一处。两个键都没写过（老 `.env` / 脚本 / 夹具）
时回退 `DEEPSEEK_*`，行为不变；`LLM_PROFILES` 解析失败也只回退 + 提示，不抛异常。
界面提交 `profiles` = 整表替换（增删改都在里面），提交 `active` = 只切换；密钥留空按**配置名字**沿用旧值
（界面只拿得到打码值，这条不能少）。`.env` 存 JSON 因此 `core/env_file.py` 的读写转义必须对称。

生成层**不是「DeepSeek 专用」**：它可以是任意 OpenAI 兼容端点，所以
1) 端点规则与 Jev 一致（`GeneralLLMClient.endpoint` 是唯一判定处）：填前缀自动补 `/chat/completions`，
填完整端点原样使用——**别在别处再拼一次**（`services/settings.py` 也是调它算给界面看的 endpoint）；
2) 报错文案不许写死上游名，一律「模型名 + 实际请求地址 + 上游返回」，404 另外点明「接口地址或模型名不对」，
否则用户换了模型根本不知道在说谁。

### 前端（只展示与编排）
前端只做展示与状态编排，分类 / 提示词 / 标签库等业务规则全在后端，**不要在 `frontend/` 里补一份实现**。**唯一刻意外置的双份逻辑是说话人识别**：`utils/transcript.js` 与后端 `domain/transcript.py` 必须同步改（两处注释已标注），否则实时 chips 与落库结果会不一致。

## 测试约定
`tests/` 用假上游覆盖领域层 / 服务层 / 仓储层 / 接口契约，**不烧额度**；新增依赖外部模型 / API 的代码时，务必注入假 client 而非真实调用。`app check` 是唯一的真实调用入口（每条消息两次 Jev），改提示词后跑一遍做回归。
