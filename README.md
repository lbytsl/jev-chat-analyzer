# 恋爱·职场聊天神器

一个本地运行的聊天记录分析小工具：把一段对话粘进来，自动识别每句话的**意图**和**情绪**，并（可选）生成**潜台词**和**若干条回复建议**。覆盖暧昧、恋爱、同事、上下级四种关系场景。

> 纯本地运行，对话文本只发往你自己的模型 API，不会上传到任何第三方服务。

## 功能

- **意图 / 情绪两级分类**：先判大类，再在类内判细分，给出带百分比的标签，识别不出时诚实显示「看不出情绪」。
- **关系场景**：暧昧 / 恋爱 / 同事 / 上下级，标签体系按场景使用不同叫法（同一情绪在不同关系下叫法不同）。
- **可选 AI 增强**（勾选后调用，默认关闭）：
  - **生成潜台词**：把一句话背后的真实意图翻成人话（6–14 字）。
  - **生成推荐回复**：针对最后一条消息，给出几条可直接发送的回复方向（条数在「配置 → 输出设置」里改）。
  - 两者是**两个独立端点**（`/interpret-chat`、`/suggest-chat`）：点哪个只付哪个的模型调用，一边失败不影响另一边。
- **单条生成**：气泡右侧一个按钮就能只给这条生成潜台词；**右键气泡**还有「生成潜台词 / 生成推荐回复 / 一键生成」三项，给中间某条单独生成推荐回复时，建议就贴在那条消息下面（自带复制）。
- **可视化配置**：顶栏「配置」分三个 Tab —— 分类层（Jev）/ 生成层（LLM）/ 输出设置（推荐回复给几条）；「测试连接」可以先验证再保存，保存写入 `.env` 并立即生效，不用重启服务。
- **工具栏在标题栏**：生成潜台词 / 生成推荐回复 / 继续记录 常驻在「关系/场景」右侧，不再占对话区下方的一整条。
- **流式体验**：整段分析卡片一条条出现（每条算完就推），潜台词逐字写出来、回复建议逐条冒出来；正在生成的那几条在卡片上显示「生成中…」。
- **会话持久记忆**：每次导入的聊天记录自动存进本地会话库，左侧可切换历史会话、多选删除；刷新页面会回到最近一条。
- **继续记录**：分析完可追加新消息，只对新增部分重新分析，旧结果保留。
- **低置信度回流**：分类拿不准或命中泛化标签的样本写入本地池，供人工审阅补标签。

## 快速开始

### 1. 安装依赖（macOS / Windows 都要）

后端用 [uv](https://docs.astral.sh/uv/) 管理依赖，前端用 [pnpm](https://pnpm.io/)（需先装 Node.js）。

```bash
git clone <你的仓库地址>
cd jev-chat-analyzer

# 后端：装好 uv 后同步依赖。macOS: brew install uv；Windows: winget install astral-sh.uv 或 pip install uv
uv sync --no-dev                           # 按 pyproject.toml 同步依赖，自动建 .venv（启动用不到测试包）
cp .env.example .env                       # 填入你的 API 配置（见下方「配置」一节）

# 前端：装好 Node.js 后全局装 pnpm（npm i -g pnpm），再装前端依赖
cd frontend && pnpm install && cd ..       # 前端（Vite + Vue 3）
```

### 2. 启动（两种平台，二选一）

**一键脚本（推荐）**

- **macOS**：直接双击 `启动.command`。它会用项目里的 `.venv` 起后端（8767）、用 `pnpm run dev` 起前端 dev server（5173）、自动打开页面 —— 改完前端刷新即见，不用手动 build。若后端端口已有旧代码，会提示你重启而不是静默复用（比对版本号 + 「`app/` 下最新 `.py` 是否比进程启动时间新」）。找不到 pnpm 时退回「构建产物 + 后端托管」，页面开 8767。
- **Windows**：直接双击 `start.bat`，效果同上（后端 8767 + 前端 dev server 5173，自动开页面）。关闭弹出的「jev-backend」「jev-frontend」两个窗口即停止服务；找不到 pnpm 时同样退回「构建产物 + 后端托管」，页面开 8767。

**手动启动（跨平台）**：开两个终端，

```bash
.venv/bin/python -m app serve             # 后端 API，监听 127.0.0.1:8767（Windows 用 .venv\Scripts\python.exe）
cd frontend && pnpm run dev               # 另一个终端：前端 dev server，http://127.0.0.1:5173
```

浏览器打开 <http://127.0.0.1:5173> 即可使用（dev server 直连 8767 的后端）；接口文档在 <http://127.0.0.1:8767/docs>。

改前端时用开发模式，热更新 + 直接连本机 8767 的后端（后端 CORS / origin 白名单已放行 localhost 任意端口）：

```bash
cd frontend && pnpm run dev                # http://127.0.0.1:5173
```

> 页面只能从后端地址（http://127.0.0.1:8767，托管 `frontend/dist`）或 Vite dev server（http://127.0.0.1:5173）打开；双击 `frontend/index.html`（file://）不可用。想用单端口（8767）就先 `pnpm run build`，后端逐请求读 `frontend/dist`，重新构建后刷新即生效。

> `.env` 放在项目根目录即可；若放在项目上一级目录也能被读到（兼容旧布局）。

## 配置（.env）

项目是**双模型架构**，需要配置两个 API：

| 变量 | 用途 | 能否替换 |
|------|------|----------|
| `TYPESAFE_BASE_URL` `TYPESAFE_DEFAULT_MODEL` `TYPESAFE_API_KEY` | **分类层**：意图 / 情绪判定，请求体为 Jev 私有协议（`state` + `questions`） | 不能直接换别家模型；接入点二选一（见下），改写 `app/clients/jev.py` 的拼包逻辑才行 |
| `DEEPSEEK_BASE_URL` `DEEPSEEK_MODEL` `DEEPSEEK_API_KEY` | **生成层**：潜台词 + 回复建议，走 OpenAI 兼容 `/chat/completions` | ✅ 可换任意 OpenAI 兼容端点（OpenAI / 通义千问 / 智谱 GLM / Kimi / 本地 Ollama 等），前提是对方支持 JSON 输出模式 |

并发度可用环境变量覆盖：`JEV_MAX_WORKERS`（默认 4）、`GEN_MAX_WORKERS`（默认 6）。

> 说明：生成层是标准 OpenAI 兼容调用，换端点只改上面三个变量、无需改代码。分类层请求格式为 Jev 私有协议，若要换成别的分类模型需要改写 `app/clients/jev.py` 的拼包与解析。

### 从界面上改配置

顶栏「配置」打开面板，左侧三个 Tab：

| Tab | 内容 |
|-----|------|
| 分类层 | Jev 的接口地址 / 模型 / API Key |
| 生成层 | OpenAI 兼容端点的接口地址 / 模型 / API Key |
| 输出设置 | `GEN_SUGGESTIONS_COUNT`：点「生成推荐回复」时给几个方向（1–6，默认 3） |

面板右上角有关闭按钮，底部「测试连接」分别探测分类层与生成层，**可以先验证再保存**：

- 保存是「按行合并」写回 `.env`：只替换目标键那一行，注释、顺序、其它键都不动；
- 密钥字段留空 = 不改动，界面只显示打码值（`sk-or…5d75`），不回传也不回填明文；
- 保存后服务端清掉配置与各服务实例的缓存，**下一次请求就用新配置，不用重启**；
- 注意优先级：真实环境变量（例如 shell 里 `export` 的 `TYPESAFE_API_KEY`）优先于 `.env`，
  那种情况下界面保存会被环境变量盖住——先 `unset` 再用界面改。

### 流式（SSE）：边算边出，而不是等整批

三个 `/…/stream` 端点用 `text/event-stream` 推事件，前端用 `fetch` + `ReadableStream` 读
（不用 `EventSource`：它只能 GET，而请求体是 JSON）。一次性版本原样保留，脚本与夹具继续用。

| 端点 | 流式事件 |
|------|----------|
| `POST /analyze-chat/stream` | `start`（消息骨架）→ 每条算完一个 `message` → 生成层预览 → `done`（信封 + `session_id`） |
| `POST /interpret-chat/stream` | `start` → `delta`（潜台词写到哪）→ `done` |
| `POST /suggest-chat/stream` | `start` → `item`（建议逐条）→ `done` |

另有 `reset`（截断重试，清掉上一轮预览）、`retrying` / `failed`（断连补跑）、
`error`（中途出错；HTTP 早已是 200，错误只能靠事件传达，文案与一次性端点**完全一致**）。

两个实测结论决定了实现的侧重：

1. **Jev 分类不支持流式**，而且它是主要等待（长上下文下 ~13-15s/条 × 2 次调用）。所以
   `/analyze-chat/stream` 的重点是**逐条进度**：算完一条就推一条（按完成顺序，不按序号），
   卡片一条条出现，状态栏显示「已完成 x/y 条」。
2. **生成端点有可能攒够再吐**：本地那台 `deepseek-flash` 实测「首片 1.47s / 总 1.63s」、
   分片时间戳几乎相同——协议层是流式，但服务端一次性 flush。换成会真流的端点（如
   `api.deepseek.com`）就能看到逐字效果；功能与结果不受影响。

### 生成层：潜台词与推荐回复是两条链路

| 端点 | 提示词 | 默认范围 | 产出字段 |
|------|--------|----------|----------|
| `POST /interpret-chat` | `build_interpretation_messages` | 全部已有分析（`indexes` 可指定某几条） | 每条一个 `intent_detail`（6–14 字；只有「收到 / 好的 / 嗯」这类纯事务性应答才允许空串，界面会如实标注「模型判断这句是纯事务性应答」） |
| `POST /suggest-chat` | `build_suggestions_messages(count)` | 全局最后一条（`indexes` 可指定某几条） | 每条 `GEN_SUGGESTIONS_COUNT` 条 `suggestions` |

拆开的原因：两件事的约束完全不同（潜台词限 6–14 字且禁用第二人称；建议要几个方向明显不同），
混在一次调用里时任何一侧出问题都会把整次调用判失败、另一侧结果也一起丢掉；而且只想要潜台词时
不该顺带生成建议（反之亦然）。现在各点各的、各自整形、各自标记失败——推荐回复失败标
`gen_failed`（底部回复面板据此提示），潜台词失败标 `interpretation_failed`。合并回会话时按
「字段是否存在」判断，跑一类不会清掉另一类已经生成的内容。

### 分类层接入点（`TYPESAFE_BASE_URL` 的两种写法）

| 接入方式 | `TYPESAFE_BASE_URL` | `TYPESAFE_API_KEY` | 说明 |
|----------|---------------------|--------------------|------|
| TypeSafe 原生网关 | `https://api.typesafe.ai` | TypeSafe 的 key | 只写主机名；代码补上原生路径 `/v1/systemone` |
| OpenRouter alpha decisions | `https://openrouter.ai/api/alpha/decisions` | `sk-or-...`（OpenRouter 的 key） | 已是完整端点；代码原样使用，不再追加路径 |

判定规则见 `app/clients/jev.py` 的 `resolve_endpoint()`：base URL 带路径就按原样用，只写主机名才补 `/v1/systemone`。模型名 `typesafe/jev-1.13` 是 OpenRouter 上的写法，TypeSafe 原生网关用 `jev-latest`。

## 架构

后端 FastAPI + 分层，前端 Vite + Vue 3，两边都不放业务规则的第二份实现。

### 前端

```
frontend/
├── index.html          Vite 入口（只有 #app 与 /src/main.js）
├── vite.config.js      base='/'（产物由 FastAPI 托管）、@ → src 别名
└── src/
    ├── main.js         挂载 App
    ├── App.vue         页面骨架 + 状态编排（谁在分析、抽屉、回复面板、弹窗、会话切换）
    ├── api/client.js   /health /analyze-chat /interpret-chat /suggest-chat /append-chat /sessions*
    │                   （含 streamAPI：读 SSE，fetch + ReadableStream 手写分帧），统一错误分类
    ├── composables/
    │   ├── usePeople.js    说话人推断与「我是谁 / 解读谁」的自动勾选状态机
    │   ├── useAnalysis.js  整段分析 / 补跑潜台词 / 补跑推荐回复 / 单条生成 / 追加消息 / 会话还原
    │   └── useSessions.js  会话列表、当前会话、多选删除
    ├── components/     ChatSidebar（会话列表）· ChatFlow · MessageRow · AnalysisNote
    │                   ReplyDock · ImportDrawer · AugmentBar · AppendModal
    ├── styles/main.css 样式（旧版逐字保留 + 会话列表/单句按钮等新增段）
    └── utils/          transcript.js（与后端 domain/transcript.py 同规则）· clipboard.js · time.js
```

前端只做展示与编排：分类、提示词、标签库全在后端。唯一「两侧同规则」的地方是说话人识别——前端要实时显示 chips，所以 `utils/transcript.js` 和后端 `domain/transcript.py` 必须同步改（两边都留着这条注释）。

### 后端

依赖方向自上而下，禁止反向依赖：

```
app/
├── main.py           应用装配：中间件（CORS / 统一响应头）、异常映射、路由挂载
├── cli.py            命令行：serve / check / pool
├── api/              接口层（只做协议转换，不写业务）
│   ├── routes/       pages（页面托管）· health · analysis（4 个分析端点）
│   ├── guards.py     来源白名单 / Content-Type / 体积上限
│   ├── deps.py       依赖注入（服务单例，测试可整体替换）
│   └── responses.py  统一 UTF-8 JSON 响应类
├── schemas/          请求 / 响应模型（只描述协议，校验在 services）
├── services/         用例层
│   ├── classifier.py 单条分类：两级路由、平票让步、两层结论调和
│   ├── pipeline.py   整段分析 / 追加消息 / 补跑潜台词、推荐回复（流式与非流式共用同一实现）
│   ├── sessions.py   会话用例：落库规则、envelope ↔ 会话记录、合并补跑结果
│   ├── generation.py 潜台词与回复建议两条独立链路（各自提示词、整形与降级）
│   └── review_pool.py 低置信度回流池与置信度判据
├── repositories/     持久化层（只存取，不写业务规则）
│   └── session_store.py SQLite 会话库（var/sessions.db）
├── domain/           领域层（纯逻辑，无 IO）
│   ├── labels.py     标签库：候选过滤、大类/展示层叫法、泛化标签集合
│   ├── prompts.py    全部提示词：分类层 questions + 潜台词 / 推荐回复两套 system·user
│   └── transcript.py 聊天记录解析（标签式 / 微信复制式）
├── clients/          外部依赖
│   ├── jev.py        Jev 分类网关（端点解析、重试、错误映射）
│   └── general_llm.py OpenAI 兼容生成层（截断重试、JSON 抠取）
└── core/             配置（config）· 异常（exceptions）· 日志（logging）
```

几个刻意的取舍：

- **提示词集中在 `domain/prompts.py`**：它是这套东西真正的产品逻辑，改动等于改判定口径，不该散落在服务代码里。
- **业务校验不放 pydantic 模型**：字符串字段默认空串、模型 `extra='allow'`，校验统一在 services 抛 `InvalidRequest`，前端拿到的仍是「一句中文提示 + 400」。
- **路由用 `def` 而不是 `async def`**：分类与生成都是阻塞 IO，交给 Starlette 的线程池，避免整段分析（几十秒）把事件循环卡死。
- **服务无请求级状态**：批量并发由 `ThreadPoolExecutor`（`JEV_MAX_WORKERS`）控制，同一个服务实例可被并发请求共用。

## 目录结构

| 路径 | 说明 |
|------|------|
| `app/` | 后端服务（FastAPI，分层见上） |
| `frontend/` | 前端源码（Vite + Vue 3，构建产物 `frontend/dist` 已 gitignore） |
| `data/intents_seed.json` | 意图 / 情绪标签库（含各场景叫法） |
| `data/cases.json` | 回归测试夹具 |
| `tests/` | pytest：领域层 / 服务层 / 仓储层 / 接口契约，全部用假上游，不烧额度 |
| `scripts/measure_decisive.py` | 量分差与大类分布的实验脚本 |
| `var/` | 运行产物：`sessions.db`（会话库）· `low_confidence_pool.json` · `regression/`，已 gitignore |
| `启动.command` | macOS 一键启动脚本（后端 8767 + 前端 dev server 5173） |
| `start.bat` | Windows 一键启动脚本（后端 8767 + 前端 dev server 5173） |
| `四场景对话样本_40条.txt` | 测试语料 |
| `.env.example` | 配置模板（无密钥） |
| `pyproject.toml` `uv.lock` | 后端依赖清单（uv 管理，含 `httpx[socks]`，见下） |

> `httpx[socks]` 里的 `socksio` 不是可选项：httpx 默认读取环境变量里的代理，若本机设了 `all_proxy=socks5://…` 而缺少 socksio，出网请求会直接抛 `ImportError`。

> **隐私**：`var/sessions.db` 里存的是真实聊天原文与全部分析结果。它只在你自己机器上，且已被 `.gitignore` 排除；要清空历史直接删掉这个文件即可。

## 接口

| 方法 | 路径 | 用途 | 是否调生成层 |
|------|------|------|--------------|
| POST | `/analyze` | 单条消息分类（脚本 / 夹具用） | 默认不调 |
| POST | `/analyze-chat` | 整段聊天记录分析（并存入会话） | 由 `gen_interpretation` / `gen_suggestions` 控制 |
| POST | `/analyze-chat/stream` | 同上，SSE 流式：每条算完就推 | 同上 |
| POST | `/interpret-chat/stream` | 同上，SSE 流式：潜台词逐字推 | 只跑潜台词 |
| POST | `/suggest-chat/stream` | 同上，SSE 流式：建议逐条推 | 只跑推荐回复 |
| POST | `/interpret-chat` | 补跑**潜台词**；带 `indexes` 时只跑指定几条（单条生成） | 只跑潜台词 |
| POST | `/suggest-chat` | 补跑**推荐回复**；默认只跑最后一条，带 `indexes` 可指定任意条 | 只跑推荐回复 |
| POST | `/append-chat` | 追加新消息，只跑分类 | 不调 |
| GET | `/sessions` | 会话列表（最近 100 条，按最后活跃倒序） | — |
| GET | `/sessions/{id}` | 会话详情：切换会话时整屏还原，**零模型成本** | — |
| DELETE | `/sessions/{id}` | 删除单个会话 | — |
| POST | `/sessions/delete` | 批量删除（多选删除） | — |
| GET | `/config` | 当前 Jev / LLM 配置（密钥打码，不回传明文） | — |
| PATCH | `/config` | 保存配置到 `.env`（留空 = 不改动），清缓存后立即生效 | — |
| POST | `/config/test` | 连通性自检（可带未保存的值先验证） | 各 1 次探测调用 |
| GET | `/health` | 版本与配置状态（启动脚本用它比对版本） | — |
| GET | `/` | 托管 Vue 前端（`frontend/dist/index.html`，未构建时返回 503 并给出构建命令） | — |
| GET | `/assets/*` | 前端静态资源（逐请求读盘，重新构建后刷新即可生效，不用重启后端） | — |

### 会话规则

- **导入即存档**：点「仅Jev分析」就会落一条会话；**正文与当前会话完全相同 = 原地重跑**（不产生重复条目），**正文不同 = 另起一条**（旧会话保留）。
- **点「＋ 导入聊天」= 新起一段**：先把界面收回空态（清空结果显示、聊天记录框、关系选择、两个 AI 勾选、会话绑定），再打开导入抽屉。旧会话不会被删，仍在左侧列表里，点一下就回去；因为会话绑定也清空了，新记录会存成新会话，不会覆盖上一条。
- **追加 / 补跑生成 / 单条生成**：都更新当前会话，生成内容不会丢；两类生成并发写同一会话时是加锁的，不会互相覆盖。
- `session_id` 是可选参数：带上它就「以库里的会话为准」（补跑端点会读库、合并、写回）；不带则保持老路径（只带 `prev`，不落库），脚本与夹具继续可用。
- 两个补跑端点的响应**只带本次这一类产物**（`augmentations`），不回带整份会话：会话语境里另一类的字段是存量数据，夹在响应里会被误读成「这个端点也生成了那一类」。前端按同一套「字段是否存在」规则把增量合并进本地结果。
- 删除只由你显式触发，不自动清理；`GET /sessions` 默认返回最近 100 条。

错误响应统一是 `{"error": "中文提示", "code": "可选错误码"}`：入参问题 400、找不到会话 404、本地缺 key 500、上游报错 / 结构不兼容 502、上游连不上 503。

## 本地验证

```bash
.venv/bin/python -m pytest          # 离线测试套件（假上游，不消耗额度）
.venv/bin/python -m app check       # 跑 data/cases.json 夹具回归，结果写 var/regression/test-results.json
.venv/bin/python -m app pool        # 查看低置信度回流池里待人工审的样本
.venv/bin/python scripts/measure_decisive.py   # 量各层分差与大类分布（会真实调用模型）

cd frontend && pnpm run dev          # 开发模式（http://127.0.0.1:5173，改完刷新即见）
cd frontend && pnpm run build        # 单端口模式：构建到 frontend/dist，由后端在 8767 托管
```

`app check` 会真实调用 Jev（每条消息两次），改提示词后跑一遍再用。

## 许可证

本项目采用 [MIT 许可证](./LICENSE)，详见 LICENSE 文件。

## 致谢

本项目的雏形与核心思路来自抖音博主 **废话会开花（GracieZGC）** 的开源项目 [jev-chat-analyzer](https://github.com/GracieZGC/jev-chat-analyzer)，感谢原作者的开源分享与启发。
