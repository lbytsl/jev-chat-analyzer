# 优化方案（Optimization Plan）

> 文档性质：**评审稿**。本文只描述问题与方案，不包含任何代码改动。
> 生成日期：2026-09-23　|　适用版本：`v1.0.0`（`app/core/config.py:16`）
> 审查范围：`app/`、`frontend/`、`tests/`、根级工程配置。

---

## 目录

- [一、总体判断](#一总体判断)
- [二、P0 正确性与稳定性](#二p0--正确性与稳定性会真实出错)
- [三、P1 性能](#三p1--性能)
- [四、P2 可维护性](#四p2--可维护性)
- [五、P3 工程化](#五p3--工程化)
- [六、P4 安全与隐私](#六p4--安全与隐私)
- [七、P5 产品体验（可选）](#七p5--产品体验可选)
- [八、实施路线图](#八实施路线图)
- [九、刻意的设计，不建议改](#九刻意的设计不建议改)
- [十、验收与回归方式](#十验收与回归方式)

---

## 实施状态（2026-09-23）

**P0 / P1 / P2 / P3 全部落地**；P4 完成了「该做的部分」，P5 完成体验项，两处**刻意不做**的理由见下。

| 条目 | 状态 | 落地要点 |
|------|------|----------|
| P0-1 前端 SSE 取消 / 超时 / 分帧 | ✅ | `api/client.js` 新增 `CancelledError` 与可选的 `signal` / `idleTimeoutMs`；`reader.cancel()` 放进 `finally`；分帧兼容 `\r\n\r\n`；流结束时冲刷解码器并处理最后一帧。`useAnalysis` 用 `Set` 管在途 controller，切会话 / 重进导入 / 卸载时全部 abort，取消不算失败也不弹提示 |
| P0-2 transcript 一致性 | ✅ | 前端补上 `#` 跳过、`guessMe` 改成与后端同口径；新增共享用例 `data/transcript_cases.json` + `tests/test_transcript_consistency.py` + `frontend/scripts/check-transcript.mjs`（`pnpm run check:transcript`），两侧跑同一份用例 |
| P0-3 存储事务与锁 | ✅ | `_connect` 改 contextmanager（提交/回滚后**一定关闭**）；`upsert` 的「取戳 → 读 created_at → 写」合并进单个 `BEGIN IMMEDIATE`；`save_analysis` / `update_analysis` 也加会话锁（`apply_augmentations` 靠 RLock 重入） |
| P0-4 锁表有界化 | ✅ | 改成 64 个固定分片（`session_id` 走 crc32），不再按访问过的会话累积 |
| P1-1 共享 HTTP 客户端 | ✅ | 新增 `app/clients/http.py`：进程级单例 + 连接池 + 分项超时 |
| P1-2 共享线程池 + 请求级取消 | ✅ | 新增 `app/core/executors.py`；`drain` / `_run_generation_jobs` 改滑动窗口提交；`sse_response` 在事件之间检查 `request.is_disconnected()`，断开即置位 cancel —— 流水线不再提交剩余消息、不落库、不发 `done` |
| P1-3 分项超时 + 总预算 | ✅ | `httpx.Timeout(connect/read/write/pool)`；Jev 单次 `decide()` 有 `TOTAL_BUDGET_SECONDS = 60` 硬上限，读超时按剩余预算收窄 |
| P1-4 前端响应式 | ✅ | `withItem` / `applyPreview` / `clearPreviews` 改原地操作（消掉 O(n²) 与「每个 token 拷一遍全表」）；`usePeople` 的输入推断加 250ms 防抖 + `dispose()` |
| P1-5 静态资源缓存 | ⛔ 有意不做 | 收益小，且会牵动「重新构建后刷新即生效」的语义（`/assets/*` 现在也走 `no-store`）。真要做得先把 `index.html` 与带 hash 的资源分开设缓存，属于独立议题 |
| P2-1 流式 / 非流式去重 | ✅ | `_run_targets` 改为**消费** `_run_targets_streaming`（删掉第二套并发 + 补跑实现）；抽出 `_analyze_setup` / `_finish_analyze` 给两条路径共用；`analysis.py` 的补跑准备与落库收成 `_prepare_augment` / `_persist_augment`。**顺带修掉一个真 bug**（见下） |
| P2-2 生成失败标记收敛 | ✅ | `GenerationService` 新增 `run_interpretation` / `run_suggestions`（不抛版本，自带日志），四处重复的 `try/except + logger.warning` 收进一处；失败**字段名**仍由各自边界定义（一条消息的结果 vs 一次补跑的增量，契约不同，注释写明了）|
| P2-3 `App.vue` 上帝组件 | ✅ | 两步走：先新增 `composables/useSessionSwitch.js` 把「打开 / 清空会话」的编排从 App 搬走，`ImportDrawer` 的三个按钮状态移回组件内部自算；随后**整体迁到 Pinia**（见下） |
| P2-4 死代码 + 静默失败 | ✅ | 删掉 `client.js` 里无人引用的 `analyzeChat` / `interpretChat` / `suggestReplies`、`usePeople.totalTargets`、`useAnalysis` 的三个内部导出；**修掉真 bug**：`useSessions.error` 现在经 `noticeText` 显示在顶部提示条（以前会话列表加载失败完全静默） |
| P2-5 日志与可观测性 | ✅ | 新增 `core/retry.py` 自带重试日志；`clients/jev.py` 补重试 / 超预算 warning；`classifier.py` 日志标签由 `gen` 改为 `classifier`；`main.py` 的兜底异常改走 `logger.exception`（去掉 `traceback.print_exc()`，不再绕过 logging 配置）|
| P2-6 类型注解与魔法数 | ✅ | 新增 `core/retry.py` 统一退避口径（`BASE_DELAY_SECONDS` + `MAX_DELAY_SECONDS` + 抖动，Jev 与生成层共用，`Retry-After` 解析也收在一处）；`pipeline.py` / `classifier.py` 的方法补齐参数与返回注解；「我 / 对方」与 `me / other` 的双表示收进 `speaker_label()` / `speaker_code()` |
| P3 工程化 | ✅ | ①`pyproject.toml` 里**显式钉住 ruff 规则集**（`E4,E7,E9,F,W,B,I`）——以前结果取决于本机隐式配置，同一份代码在两台机器上报的数字都不一样，没法当门禁；现在 `ruff check .` **全绿**。②新增 `.github/workflows/ci.yml`（后端 ruff + pytest + 覆盖率；前端 install + 一致性 + lint + 单测 + 构建，全程离线不烧额度）。③新增 `.pre-commit-config.yaml`。④前端补 eslint（扁平配置）+ prettier + vitest + jsdom，18 项单测。⑤补测试：Jev 重试矩阵（403 / `Retry-After` / 5xx / 400 / 坏 JSON / 缺 key）、体积上限与「无 Content-Length」、回流池判据与上限、`cli.py` 参数与夹具判定、`core/retry.py` |
| P4 安全与隐私 | 🟡 部分（有意） | 做了：把体积上限的**实际行为**钉成测试（超长 → 400、「没有 Content-Length」→ 400）。**没做**（附理由）：①计划里「谎报 Content-Length 可塞超大 body」经核实**不成立** —— HTTP/1.1 里 Content-Length 是成帧依据，uvicorn 的 h11 只按它读 body，多出来的字节会被当成下一个请求；真正能进来的是「根本没有长度」，而那条路径已经被拒。②没加 Host 守卫：浏览器攻击面已被「只绑 127.0.0.1 + 写接口查 Origin」覆盖，加了反而会破坏 `serve --host 0.0.0.0` 的用法。③没动 `Origin: null`（file:// 页面是明确支持的用法）与 localhost 任意端口（IDE 内置预览要用）——这两条在 `core/config.py` 的注释里本就写明了代价 |
| P5 产品体验 | ✅ | ①`SettingsDrawer` 的配置套不再用下标作 key（改名/删除会错位），改用界面内稳定 key。②新增 `composables/useFocusTrap.js`，配置面板与追加弹窗把键盘焦点关在卡片内、关闭后归还。③右键菜单支持键盘：焦点在消息行上按 **Shift+F10 / 菜单键**打开，↑↓ 选、Enter 确认、Esc 关闭（并归还焦点），提示条同步说明。④`prefers-reduced-motion` 下关掉装饰动画。⑤新增**导出为 Markdown**（`utils/export.js`，纯前端，含单测）。⑥侧栏在「库里多于已加载」时明说「共 N 条，这里显示最近 100 条」，而不是默默截断。⑦三处 `setTimeout` 补上卸载清理 |

### 实施中发现的三个真 bug（都不是计划里写的）

1. **流式整段分析遇「部分失败」会整条崩掉**（P2-1 顺带修）：`analyze_stream` 把失败事件的
   `index`（int）交给 `_envelope`，而后者按 `item['index']` 取序号 → `TypeError`。触发条件是
   「一部分消息成功、一部分两轮都失败」，用户看到的是「分析没有正常结束」且**不会落库**。
   已补复现测试（`tests/test_pipeline.py::TestStreamingFailureAccounting`）。
2. **会话列表加载失败完全静默**（P2-4）：`useSessions` 把错误记进 `error` 却没人读，用户的观感
   就是「历史会话全没了」。现在接到顶部提示条。
3. **`ImportDrawer` 里 `readLabels` 没走 `props`**（P5 引入后由新加的 eslint 当场抓到）：
   `<script setup>` 里直接写 `readLabels.value` 会 `ReferenceError`。这条恰好说明「把 lint 变成
   门禁」的价值 —— 它是新写的代码在第一轮 `pnpm run lint` 里就被挡下的。

### 与计划不同的四处判断

1. **`lastData` 没有改成 `shallowRef`**（P1-4 原文如此建议）。`lastData` 会作为 prop 传给
   `ChatFlow → MessageRow`，子组件靠「渲染时读到的深层属性」建立依赖。换成 `shallowRef`
   后原地改内容不再触发子组件更新（prop 引用没变），只能反过来每次造新对象——正好把
   O(n²) 请回来。所以改为「保留深层响应式 + 原地操作 + `triggerRef` 显式通知」。
2. **配置保存时不关闭共享客户端**（P1-1 原文如此建议）。客户端里只有连接池，不含
   base_url / 密钥（那是每次请求带上的），换配置没有陈旧状态可言；而配置保存可能与一次
   跑了半分钟的整段分析并发，这时关掉客户端会直接把它打断。空闲连接由 httpx 的
   `keepalive_expiry` 自行回收，进程退出时统一关闭。
3. **P4 的体积上限维持「只看 Content-Length」**：见上表 P4 一栏，原判断有技术错误。
4. **P3 的类型检查器（mypy / pyright）没有引入**：现在 ruff 刚全绿，再挂一个「一装上就有几百条
   报错」的检查器只会两头都不绿。注解已经补齐（P2-6），等哪天真要做，先按模块收窄范围再上。

### 追加：前端状态管理迁到 Pinia（2026-09-24）

原方案只到「把会话切换抽出去」为止（P2-3 的第一步）。后来把整块状态迁进了 Pinia，五个 store：

| store | 接管 | 说明 |
|------|------|------|
| `form` | 正文 / 关系 / 两个 AI 勾选 | 原来被导入抽屉、脏检测、会话切换同时读写，靠 8 次 props/emits 来回传 |
| `people` | 说话人推断、我是谁 / 解读谁 | 正文改从 `form` 取，不再由 App 注入 |
| `sessions` | 列表、当前会话、多选删除 | 原样迁入 |
| `analysis` | 结果 + 流生命周期（整段 / 补跑 / 单条 / 追加 / 导出） | 最大的一块 |
| `ui` | 浮层开关、追加草稿、复制态 | 原来是散在 App 里的 7 个 ref |

**迁移原则是机械搬家，不顺手改行为**：`analysis` / `people` / `sessions` 的实现体几乎逐字未动
（只在 store 顶部用 `storeToRefs` 把旧参数名还原成同名变量），所以风险集中在「接线」而不是逻辑。

**边界**：`App.vue` 只剩布局与页面级行为（顶栏徽标、Esc 收浮层、卸载时取消在途请求、复制按钮）；
`ImportDrawer` / `ChatSidebar` / `ChatFlow` / `AugmentBar` / `AppendModal` / `SettingsDrawer` 直连 store，
props/emits 基本清零（`ImportDrawer` 从 11 props + 8 emits 变成不接参数的组件）；
**展示型叶子组件 `MessageRow` / `AnalysisNote` / `ReplyDock` 继续收 props**——它们只负责画东西，
直连 store 反而更难复用。跨 store 的编排（打开 / 清空 / 新建导入 / 删除会话）集中在
`composables/useSessionSwitch.js`，仓库里只剩两个「不是状态」的 composable（另一个是 `useFocusTrap`）。

**验证**：新增 `App.smoke.test.js`，把整页真的挂到 jsdom 上跑一次，专接「构建看不到、挂载才炸」
的接线问题（store 写在 setup 之外、模板引用了已经搬走的 prop…）。前端单测 18 → **31 项**。

### 回归结果

- `pytest`：**288 项通过**（基线 209 + 新增 79）。
- `pytest --cov`：**91.01%**，`fail_under = 85` 通过（最低两块是 `clients/general_llm.py` 60% 与
  `cli.py` 60%——都是「只有真连上游 / 真起服务才走得到」的路径，没有为了凑数字去写测试）。
- `ruff check .`：**0 项**（新增 `[tool.ruff.lint]` 显式规则集后，从 80 条收敛到 0，且不再依赖本机隐式配置）。
- 前端：`pnpm run lint` **0 问题**、`pnpm run test` **31 项通过**（含整页挂载冒烟）、
  `pnpm run check:transcript` **14/14**、`pnpm run build` 通过
  （`index-*.js` 132.51 kB / gzip 50.43 kB，含 Pinia）。
- 后端托管端到端：`GET /` 200、`GET /assets/*` 200。
- **未改任何判定口径**：`data/cases.json` 的回归结果不受影响（`app check` 会真实调用 Jev，需要时再跑）。
- **本机环境坑（与代码无关）**，已在 `frontend/pnpm-workspace.yaml` 里解决：① 全局 pnpm 12.5.1 与
  `frontend/package.json` pin 的 `pnpm@11.21.0` 冲突（引导安装失败）→ `pmOnFail: ignore`；
  ② 本机**无法遍历 pnpm 创建的符号链接 / 联接**（`os error 448` 不受信任的装入点），默认 isolated
  布局「装得下、用不了」→ `nodeLinker: hoisted`。两者落地后，原生的
  `pnpm install --frozen-lockfile` / `pnpm run build` / `pnpm run dev` 都能直接跑（已从零安装验证）。
  > 都能直接跑**（已从零安装验证）。

---

## 一、总体判断

> 从这一节到第十章是**评审当时的快照**（文件行数、缺口清单都停在动手之前）。
> 「现在做到哪了」以顶部【实施状态】为准，这里保留原样是为了让每条结论都能对回当时的依据。

项目的**架构底子很好**：分层清晰、依赖方向单向、提示词集中、流式与非流式共用一套 pipeline、测试全部用假上游不烧额度。文档（`README.md` + `AGENTS.md`）质量远超同类个人项目。

问题集中在三个层面：

| 层面 | 现状 | 主要缺口 |
|------|------|----------|
| **健壮性** | 主流程稳，边界未覆盖 | 前端 SSE 无取消/超时；前后端双份 transcript 规则已分叉；存储读-改-写非原子 |
| **性能** | 逻辑正确但有系统性浪费 | HTTP 客户端与线程池「每请求新建」，连接复用为零 |
| **工程化** | 几乎为零 | 无 CI、无覆盖率、无前端 lint/测试、无 pre-commit |

### 规模速览

| 区域 | 规模 | 最大文件 |
|------|------|----------|
| 后端 `app/` | 42 个 `.py` | `app/services/pipeline.py`（579 行） |
| 前端 `frontend/src` | 10 个 `.vue` + 9 个 `.js` + 2 个 `.css` | `composables/useAnalysis.js`（552 行）、`App.vue`（345 行） |
| 测试 `tests/` | 9 个测试文件（全部假上游） | `test_config_api.py`（450 行） |
| 工程化 | 无 CI / 无覆盖率 / 无前端测试 / 无 pre-commit | — |

---

## 二、P0 — 正确性与稳定性（会真实出错）

### P0-1 前端 SSE 读取缺少取消、超时与异常清理

**证据**：`frontend/src/api/client.js:68-115`

```68:115:frontend/src/api/client.js
export async function streamAPI(path, payload, onEvent) {
  ...
  const reader = response.body.getReader()
  ...
  while (true) {
    let chunk
    try {
      chunk = await reader.read()
    } ...
    if (chunk.done) break
```

四个具体缺陷：

1. **无 `AbortController`**：fetch 未传 `signal`（`:71-75`），全仓 0 处 `AbortController`。用户切会话、点「＋ 导入聊天」、或重试时，旧流仍在跑。
2. **异常路径不释放 reader**：`:110` 抛出 `ApiError` 时未 `reader.cancel()`，连接悬挂到 GC 回收。
3. **无超时**：后端假死时 `reader.read()` 永久 pending，`busy`（`useAnalysis.js:186`）永远停在 `true`，UI 无出路——只能刷新页面。
4. **分帧只认 `\n\n`**（`:103`）且 `chunk.done` 时不 flush decoder（`:101`）。当前后端固定发 `\n\n`（`app/api/streaming.py:42`），所以**暂时不会丢事件**；但这是隐式契约，后端一旦改用 `\r\n\r\n` 就会「假死」（buffer 无限增长、永不触发事件）。

**影响**：后端出问题时用户看到的是「点了没反应」，且后台仍在消耗 API 额度直到跑完（整段分析可能几分钟）。

**方案**：

- `streamAPI` 增加可选 `signal` 参数，返回 `cancel` 句柄；`useAnalysis` 在会话切换、重新导入、组件卸载时调用。
- `try/finally` 中保证 `reader.cancel()` + `reader.releaseLock()`。
- 加「读空闲超时」（如 120s 无事件则 abort 并提示「服务似乎没有响应」）。
- 分帧改为兼容 `\n\n` / `\r\n\r\n` / `\r\n`，并在 `done` 时 flush 一次 decoder + 处理残留完整帧。
- `parseSSE` 返回 `null` 的分支（`:117-127`）目前被静默吞掉（`:108`），改为可配置的调试日志。

**验收**：分析中途切会话，Network 面板确认旧请求变为 `canceled`，后端不再有对应的在途工作线程。

---

### P0-2 前后端 transcript 规则已实际分叉

**证据**：`frontend/src/utils/transcript.js` 与 `app/domain/transcript.py`。两处注释都写着「必须同步改」，但**没有共享源、也没有一致性测试**，且已经分叉：

| 规则 | 前端 | 后端 | 后果 |
|------|------|------|------|
| `#` 标题行 | 不跳过（`transcript.js:27-38`） | 跳过（`transcript.py:41-43`） | 前端多认出一个「说话人」 |
| 默认「我是谁」 | 按正文自称「我」计数取最大（`transcript.js:50-58`） | 取首个非对方代称候选（`transcript.py:89-93`） | 默认选中的人可能不同 |
| 正文聚合 | 只扫描标签计数 | 完整分块、聚合多行、过滤夹层时间行 | 解析粒度不同 |

**具体触发链**：用户粘贴带 Markdown 标题的记录（如 `## 聊天记录` ⏎ `09:30` ⏎ 正文）→ 前端 `detectPeople` 多识别一个标签 → 用户在「解读谁」里勾选它 → `read_labels` 送到后端 → `_assert_labels_exist` 抛 `InvalidRequest` → 用户看到 400「识别不到这个人」，但他明明在界面上勾了。

**方案**（按性价比排序，三选一）：

1. **最小改动**：后端 `_scan_blocks` 的 `#` 跳过逻辑，前端同步补上（约 1 行）；`guessMe` 与后端 fallback 口径对齐。
2. **推荐**：把「提取候选说话人」抽成一组**前后端共享的规则测试用例**（输入 → 期望标签列表），前端 JS 与后端 Python 各跑一遍同一份 fixture；任何一侧改动都会立刻红。新增 `tests/fixtures/transcript_cases.json` + 前端最小测试。
3. **彻底**：前端实时 chips 不再自己解析，改为 300ms 防抖调一个轻量 `/parse-transcript` 端点。代价是输入时有一次网络往返，但消除全部双份逻辑。

**验收**：同一份含 `#` 标题、多说话人、微信格式混合的文本，前后端产出的 `speakers` 列表完全一致。

---

### P0-3 SQLite：连接未显式关闭 + 读-改-写非原子

**证据**：`app/repositories/session_store.py`

```61:70:app/repositories/session_store.py
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        return conn
```

三个问题：

1. **连接未关闭**：全文 7 处用 `with self._connect() as conn:`（`:61/74/79/88/109/135/158`）。Python 的 `sqlite3.Connection` 上下文管理器**只 commit/rollback，不 close**。CPython 下靠引用计数侥幸回收，属依赖实现的隐患（PyPy 或未来改动可能累积 fd）。
2. **`upsert` 是三步非原子**（`:113-151`）：`_next_stamp()`（一次连接）→ `self.get()`（一次连接）→ 写入（一次连接）。**单次保存开 3-4 个连接**，且 `created_at` 判定存在 TOCTOU 竞态。
3. **写锁不完整**：`SessionService._lock_for` 只包住 `apply_augmentations`（`app/services/sessions.py:149`）；`save_analysis`（`:55-71`）与 `update_analysis`（`:73-81`）**无锁**。并发 `append` + 补跑生成写同一会话时，`updated_at` 可能撞值（破坏「按最后活跃倒序」）。

**方案**：

- `_connect` 改为 `contextlib.closing` 显式关闭；或引入模块级连接 + `threading.local()`。
- `upsert` 三步合并进**单个事务**（`BEGIN IMMEDIATE`，`_latest_stamp` 与 INSERT 同事务），消除竞态与连接放大。
- 把会话级写锁从「只有 augment 加」提升为「所有写路径都加」，统一在 `SessionService` 入口获取。

**验收**：并发 20 个 write 到同一会话，`updated_at` 严格递增且无丢失；`SessionStore` 单次保存的 `sqlite3.connect` 调用数从 3-4 降到 1。

---

### P0-4 `SessionService._locks` 无界增长

**证据**：`app/services/sessions.py:28`

```28:29:app/services/sessions.py
    _locks: dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()
```

每访问过一个会话就永久留一把锁，从不清理。长期运行（或测试反复建会话）下内存缓慢增长。

**方案**：
- 改用「带引用计数的锁表」（`WeakValueDictionary` 不适用于 `Lock`）+ 使用计数归零即删；
- 或退化为按 `session_id` 哈希分片的固定大小锁数组（如 64 把），彻底消除增长。

---

## 三、P1 — 性能

### P1-1 HTTP 客户端零复用（收益最大、成本最低）

**证据**：

```83:83:app/clients/jev.py
        with httpx.Client(timeout=self._timeout) as client:
```

`app/clients/general_llm.py:167`（`complete_json`）与 `:234`（`stream_json`）同样每次调用新建。

**影响**：每条消息 2 次 Jev 调用，50 条约 **100 次 TCP + TLS 握手**；生成层同理。

- 生成层：100 次调用 × ~200ms 握手 / 6 并发 ≈ 3.3s，占该阶段总时长（≈27s）的 **约 12%**；
- Jev 阶段占比约 3%；
- 更重要的是**无法享受 keep-alive**，且本地代理 / 弱网下握手抖动会被放大 100 倍。

**方案**：`JevClient` / `GeneralLLMClient` 持有**进程级共享 `httpx.Client`**（`limits=httpx.Limits(max_connections=..., max_keepalive_connections=...)`），由 `app/api/deps.py` 注入，并在 `reset_service_caches()`（`deps.py:39-47`）里一并关闭重建。

> **注意**：此改动会让「保存配置立即生效」依赖 `reset_service_caches` 正确关闭旧 client——**必须同时改 `deps.py`**，否则是连接泄漏（旧 base_url 的连接池残留）。

---

### P1-2 线程池每请求新建

**证据**：`app/services/pipeline.py:139`（主批）、`:148`（补跑批）、`:337`（流式 drain）、`:513`（生成批）——4 处 `ThreadPoolExecutor(...)` 都是请求内 `with` 现场创建。

**影响**：每次请求创建/销毁线程；流式大批量分析（50 条）会创建 2 个池共约 8 个线程。线程创建本身成本不高（~0.1ms），但**槽位不可控**：Starlette 默认线程池（40）+ 每请求内层池，高并发下会线程放大。

**方案**：引入进程级共享执行器（`JEV_MAX_WORKERS` / `GEN_MAX_WORKERS` 两个），由 `PipelineService` 持有，`shutdown` 挂在应用生命周期上。

> **风险提示**：共享池若被某个超长请求占满，会拖累其它请求。建议保留「每请求上限 = `min(workers, len(targets))`」的信号量语义，不要简单换成全局无界池。

---

### P1-3 超时粒度过粗，单条最坏 3 分钟

**证据**：`app/clients/jev.py:29` `TIMEOUT_SECONDS = 45.0`；`app/clients/general_llm.py:26` `40.0`；两者都是 `httpx.Client(timeout=单值)`，connect/read/write/pool 共用。

`MAX_ATTEMPTS = 4`（`jev.py:30`）+ 退避 sleep ⇒ 单条消息最坏耗时 ≈ `4 × 45s + 退避` ≈ **3 分钟**，且**没有整体请求超时上限**。用户看到的就是「卡住了」。

**方案**：
- 换成 `httpx.Timeout(connect=5, read=..., write=..., pool=...)` 分项配置：读超时该长（模型推理慢），连接超时该短（TCP 建连失败不必等 45s）；
- 增加「单条消息总预算」（如 90s），超预算即放弃该条并标 `failed`，让它走已有的补跑 / 失败标记路径——比整批挂死好得多。

---

### P1-4 前端响应式与渲染开销

| # | 问题 | 证据 | 方案 |
|---|------|------|------|
| 1 | **O(n²) 数组拼接** | `useAnalysis.js:281` `withItem` 每次 `[...(data.analyses||[]), item]`，50 条累计 1275 次元素拷贝，且每次整块替换 `lastData.value`，触发全量 diff | 改为 `analyses.push(item)` + `triggerRef(lastData)`，或维护独立 `shallowRef` 数组 |
| 2 | **每次按键全量重解析** | `usePeople.js:89` `watch(transcript, sync)` → `detectPeople` 对整段 `split('\n')` 逐行正则；输入框 `maxlength="50000"`（`ImportDrawer.vue:82`）⇒ **每个输入事件对 5 万字符跑一次全量扫描** | 加 200-300ms 防抖；或只在 `blur` / 粘贴完成时解析 |
| 3 | **未用 `shallowRef` / `markRaw`** | `useAnalysis.js:23` `lastData` 承载完整会话（全部 analyses/messages/context），走深层代理；全仓 0 处 `shallowRef` | `lastData` / `previews` 改 `shallowRef`，改动处显式赋值；`transcript` 原始文本 `markRaw` |
| 4 | **`previews` 每 token 整表浅拷贝** | `useAnalysis.js:309/325` | 改为按 index 的对象 + `triggerRef` |

---

### P1-5 静态资源每请求读盘

**证据**：`app/api/routes/pages.py:31/42` 每次请求 `read_bytes()`。

**影响**：本地单用户影响可忽略；但 `dist/assets/*` 是带 hash 的不可变文件，本可长缓存 + 内存缓存。

**方案**：对 `/assets/*` 设置 `Cache-Control: public, max-age=31536000, immutable`，并加进程内 `lru_cache` 文件缓存，配合 `mtime` 失效。

> **注意**：当前被 `ResponseHeadersMiddleware` 统一改成 `no-store`（`app/main.py:54`），**这正是「重新构建后刷新即生效」的实现方式**。改缓存必须保留该语义。
> **结论**：收益小、牵扯热更新行为，**建议放最后或不做**。

---

## 四、P2 — 可维护性

### P2-1 流式与非流式存在成片重复

**证据**：

- `app/api/routes/analysis.py`：`_stream_analyze`（`:110-123`）vs `analyze_transcript`（`:67-74`）；`_stream_generation`（`:126-141`）vs `_run_generation`（`:144-164`）——几乎逐行重复的「读会话 → 置 prev → 运行 → 合并落库」。
- `app/services/pipeline.py`：`_run_targets`（`:117-155`）vs `_run_targets_streaming`（`:298-361`）两套独立的并发 + 补跑；`append` 里又手写第三遍（`:622-641`）。
- `analyze`（`:192-235`）vs `analyze_stream`（`:237-296`）：校验块与 `gen_flags` 近乎逐字重复。

**方案**：

- `_run_targets` 改为**消费** `_run_targets_streaming`（与已有 `_augment` 消费 `_augment_stream` 同一手法——项目里已有正确范式，只是没推广到整段分析）。
- 校验块抽成 `_validate_analyze_request(data)`，两个入口共用。
- `analysis.py` 的两个流式包装函数抽公共 `_persist_on_done(payload, sessions, runner, transcript_key, kind)`。

**收益**：`pipeline.py` 预计减 120-150 行，且消灭「流式改了非流式没改」这类分叉风险。

---

### P2-2 生成失败标记逻辑散落三处

**证据**：`app/services/classifier.py:160-188`（`_run_generation`）、`app/services/pipeline.py:558-586`、`app/services/generation.py:139-208`。`GeneralLLMError → {interpretation_failed / gen_failed / gen_error}` 的映射规则写了三遍。

**方案**：在 `services/generation.py` 暴露两个统一的「带失败标记的执行器」，返回固定结果字典；三处调用方只做赋值。

---

### P2-3 `App.vue` 是上帝组件

**证据**：`frontend/src/App.vue` 345 行，自有状态 7 个 + computed 9 个 + handler 14 个 + 跨 3 个 composable 写入。`ImportDrawer` 单组件收 **11 个 props + 8 个 emits**；props 链最深 3-4 层；全仓无 `provide/inject`、无 Pinia。

**方案**（渐进式，不必一次上 Pinia）：

1. 把「会话切换」抽成 `useSessionSwitch`：`openSession` / `clearCurrentSession` 目前直接跨改 3 个 composable 的字段（`App.vue:143-170`），是主要耦合点。
2. `ImportDrawer` 的 11 个 props 合并为 1 个 `form` 对象 + `update:form`，emits 从 8 个降到 2-3 个。
3. 视情况引入 Pinia 存「会话 / 分析结果」，composable 只管流程。

> **注意**：这是一次有回归风险的重构（前端零测试），建议先补上 P3 的前端测试再动。

---

### P2-4 死代码与未消费的导出

**证据**：

| 项 | 位置 | 说明 |
|----|------|------|
| `analyzeChat` / `interpretChat` / `suggestReplies` | `frontend/src/api/client.js:133/138/142` | 全仓**无任何引用**（前端已全走 `streamAPI`） |
| `totalTargets` | `frontend/src/composables/usePeople.js:93` | 被 return 但 `App.vue:22` 未解构 |
| `error` | `frontend/src/composables/useSessions.js:16` | 记录了列表加载失败，但 `App.vue:26-29` 未读取 ⇒ **会话列表加载失败对用户完全静默（真 bug）** |
| `analyzedText` / `augBusy` / `isPending` | `frontend/src/composables/useAnalysis.js:24/30/96` | 未被外部使用 |

**方案**：删 3 个死导出；`useSessions.error` 接到顶部 notice（真修 bug）；其余按需保留或加注释说明「供调试」。

---

### P2-5 日志与可观测性

**证据**：

- `app/clients/jev.py:27` 定义了 `logger` 但**全文 0 次使用**——Jev 客户端完全没有日志（无耗时、无重试记录、无端点）。
- `app/services/classifier.py:41` 与 `app/services/pipeline.py:44` 都用 `get_logger('gen')`，**标签混用**（分类器记日志标 `gen`）。
- `app/main.py:94` `traceback.print_exc()` 直接写 stderr，绕过 logging 配置，且与 `:91` 的 `logger.error` 重复记录。

**方案**：

- Jev 客户端补 `logger.warning/error`（重试、最终失败、状态码、耗时 ms）；
- 标签改为 `classifier` / `pipeline` / `jev` / `llm`；
- `traceback.print_exc()` → `logger.exception()`；
- `PipelineService` 在每条消息完成时记一行「index / 耗时 / 是否低置信度」。

---

### P2-6 类型注解与魔法数字

**证据**：`app/services/pipeline.py:76/94/117/158/298/428/558/575`、`app/services/classifier.py:160`、`app/services/settings.py:363` 等多处缺返回 / 参数注解；`job` 靠元组位置约定传递，不易静态检查。

魔法数：`app/clients/jev.py:90/105/122/125`（`1.2 / 1.5 / 8`）、`app/clients/general_llm.py:176/186/249/266`（内联 `1.5`，同文件重复 4 次）、`general_llm.py:181/244`（`[:1500]`）、`pipeline.py:48/53`。

**并发隐患**：`pipeline.py:114/555` 用中英混合的 `'我'/'对方'` ↔ `'me'/'other'` 互转（`'我' if speaker == 'me' else '对方'`），双表示容易出 bug。

**方案**：把重试退避抽成两个客户端共用的小工具（`retry_delay(attempt, retry_after=None)`），统一 `1.2` vs `1.5` 口径并加 jitter；补注解到公开方法；引入 `pyright` 或 `mypy`（`ruff` 已配，可在 dev 组补 `[tool.pyright]` / `mypy`）。

---

## 五、P3 — 工程化

当前几乎为零：

| 缺口 | 现状 | 方案 |
|------|------|------|
| **CI** | 全仓无 `.github/`，无任何 `.yml` | GitHub Actions：`uv sync` → `ruff check` → `pytest` → 前端 `pnpm build`，PR 必过 |
| **覆盖率** | 无 `pytest-cov`、无 `.coveragerc` | `uv add --group dev pytest-cov`，CI 输出报告（`clients/` 目前接近 0 覆盖） |
| **前端质量** | 无 ESLint / Prettier、无 TS、无 test 脚本 | 加 `eslint` + `prettier`（成本低）；测试用 `vitest`，至少覆盖 `utils/transcript.js`（P0-2 的一致性测试正好落在这里） |
| **pre-commit** | 无 | `pre-commit`：ruff + prettier + 大文件检查 |
| **编辑器配置** | 无 `.editorconfig`、无 `.python-version` | 各加一个（跨平台协作必需） |
| **文档一致性** | `AGENTS.md:31` 引用 `tests/services/test_pipeline.py`，**该路径不存在**（实际是 `tests/test_pipeline.py`） | 修正；并收敛 `AGENTS.md` 与 `README.md` 的重复架构描述（两处都在讲架构，容易各改一半） |

### 高优先补测试清单

当前明显未覆盖，且都是高风险路径：

1. **`app/clients/jev.py` 的重试 / 403 / `Retry-After` / 超时 / 非法 JSON / 缺 key**。目前测试全走 `FakeJev`（`tests/conftest.py:53`）**完全绕过** `JevClient`；可测的只有 `resolve_endpoint`（`tests/test_config_api.py:245-259`）。建议用 `httpx.MockTransport`，零成本。
2. **`app/clients/general_llm.py` 的 `complete_json`**：状态码映射（`_status_error` 的 401/403/404/429/5xx）、`max_tokens` 递增、`parse_json_content`。`stream_json` 有测（`tests/test_streaming.py:221-242`），`complete_json` 没有。
3. **`app/api/guards.py` 的体积上限分支**（`:44`）——目前无超长 body 用例。
4. **`app/cli.py` 全部命令**（0 测试）：`check_case` / `run_check` / `show_pool` / `build_parser` / `main`。
5. **`app/services/review_pool.py`**：`low_confidence` / `generic_label` / `emotion_default` 三个触发分支、`POOL_LIMIT` 满池告警（`:121-123`）、写盘失败（`:134-135`）。
6. **`app/services/settings.py` 的真实 `reload()` 路径**：`get_settings.cache_clear()`（`:286-293`）目前被 monkeypatch 假替（`tests/test_config_api.py:214-219`）。

---

## 六、P4 — 安全与隐私

| 项 | 证据 | 风险 | 方案 |
|----|------|------|------|
| **`MAX_BODY_BYTES` 可绕过** | `app/api/guards.py:40-45` 只看 `Content-Length` 头，不实际限制读取字节 | 伪造小 Content-Length 即可让 FastAPI 读完整 body，本地内存 DoS | 改为流式读取 + 累计字节上限，超限即断 |
| **GET 无来源守卫** | `app/api/routes/sessions.py:28/34`、`config.py:22` 的 GET 无 `guard_origin` | 跨站 GET 可触发（虽读不到响应） | GET 也校验 `Host` / `Origin`；至少校验 `Host` 必须为 `127.0.0.1:8767`，以挡 DNS rebinding |
| **CORS 放行 `null`** | `app/core/config.py:50-51` 放行 `null`（file://）+ localhost 任意端口 | 本机任意页面都能调用（消耗 API 额度）；`main.py:108-109` 注释已承认 | 移除 `null`（`README.md:67` 已说明 file:// 双击不可用）；`localhost` 任意端口可收窄为 `5173` + `8767` |
| **密钥未泄漏** | `app/services/settings.py:57-64` `mask_key`；`general_llm.py:110-129` `_status_error` 只回 body 片段 | 无需修改 | 保持 |
| **原文明文落盘**（可选） | `var/sessions.db` 存真实聊天原文与全部分析结果 | 已 gitignore、本机单用户，属预期设计 | 可选：加「一键导出 / 清空」入口，或对 `payload` 本地加密（收益有限，优先级低） |

---

## 七、P5 — 产品体验（可选）

1. **右键菜单无键盘入口**：单条「生成潜台词 / 推荐回复」只能 `@contextmenu`（`ChatFlow.vue:100`），键盘用户完全用不了。→ 气泡上加常驻小按钮（移动端顺带解决），或支持 `Shift+F10` + 方向键导航 + 焦点归还。
2. **`SettingsDrawer` profile 用 `index` 作 key**（`frontend/src/components/SettingsDrawer.vue:276-277`），而 profiles 支持增 / 删 / 改序（`:125 / :131`）——Vue 官方反模式，会导致删除后输入框与数据错位。**建议优先修**（改用 `profile.name`）。
3. **模态无焦点陷阱**：`AppendModal` / `SettingsDrawer` 打开后不锁定背景滚动、关闭不归还焦点。→ 抽一个 `useFocusTrap`。
4. **无 `prefers-reduced-motion`**：`frontend/src/styles/main.css:177`（`noteIn`）、`:262`（`streamPulse`）等动画。→ 加一行媒体查询。
5. **列表无分页**：`/sessions` 固定返回最近 100 条（`app/core/config.py:43`）。→ 加游标分页或真正的服务端搜索（`ChatSidebar` 已有搜索框，但只是本地过滤）。
6. **无导出**：会话不支持导出 markdown / JSON。→ 「导出本次分析」按钮，成本低、实用。
7. **未清理的定时器**：`App.vue:137`、`ReplyDock.vue:56`、`ChatSidebar.vue:34` 的 `setTimeout` 无 `clearTimeout` / `onUnmounted`。风险低，但不符合规范。

---

## 八、实施路线图

### 迭代 1：稳定性（3-4 人日）——风险最低、体感最明显

| 项 | 工作量 |
|----|--------|
| P0-1 SSE 取消 / 超时 / 分帧加固 | 0.5d |
| P0-2 transcript 补 `#` 一致性 + 加一致性 fixture 测试 | 0.5d |
| P0-3 存储单事务 + 全写路径加锁 + 显式 close | 1d |
| P0-4 锁表有界化 | 0.2d |
| P1-1 共享 `httpx.Client` + `deps.py` 重建 | 0.5d |
| P1-3 分项超时 + 单条总预算 | 0.3d |
| P2-4 死代码清理 + 修 `useSessions.error` 静默 bug | 0.3d |

**回归**：全量 `pytest`（离线）→ 手动跑四种场景 × 各 10 条 → 不应改变任何判定输出。

### 迭代 2：性能与去重（4-6 人日）

P1-2 共享线程池 + 请求级取消 → P1-4 前端响应式优化 → P2-1 pipeline 去重 → P2-2 失败映射收敛 → P2-5 日志 → P1-5（可选）

### 迭代 3：工程化（3-4 人日）

CI + 覆盖率 → 补齐 P3 的 6 项高风险测试 → 前端 ESLint / vitest → pre-commit → 前端 lint / format

### 迭代 4：架构重构（按需，5+ 人日）

P2-3 `App.vue` 拆分 / 引入 Pinia → P2-6 类型注解 + mypy → P5 体验项

---

## 九、刻意的设计，**不建议改**

以下几处在 `AGENTS.md` 里被明确标为「刻意取舍」，本方案不应触碰：

1. **路由用 `def` 而非 `async def`**（`app/api/routes/analysis.py:21-22`）——分类与生成都是阻塞 IO，交给 Starlette 线程池。改成 `async def` 会卡死事件循环。
2. **业务校验不放 pydantic**（字符串默认空串、模型 `extra='allow'`，校验统一在 services 抛 `InvalidRequest`）——这是「前端拿一句中文 + 400」契约的实现基础。
3. **提示词全部集中在 `app/domain/prompts.py`**——改动等于改判定口径，不要为了「就近」把提示词挪进 service。
4. **潜台词与推荐回复是两条独立链路**（两个端点、各自失败标记）——`README.md` 已详述理由，不要合并成一次调用。
5. **`httpx[socks]` 里的 `socksio` 不能删**（`pyproject.toml:10-12`）——本机设了 `all_proxy=socks5://` 时缺它直接 `ImportError`。
6. **生成层不是「DeepSeek 专用」**：报错文案必须带「模型名 + 实际请求地址」，端点规则只在 `GeneralLLMClient.endpoint` 判定一处——不要在别处再拼一次 `/chat/completions`。
7. **多套 LLM 配置只存在于配置层**（`Settings.model_post_init` 落到三个老字段）——不要让 `clients/general_llm.py` 知道多套配置的存在，切换只动 `core/config.py` 一处。
8. **`/assets/*` 逐请求读盘**（`app/main.py:54` 统一 `no-store`）——这是「重新构建后刷新即生效」的实现方式，P1-5 若要改必须保留该语义。
9. **流式与非流式共用同一套 pipeline 实现**（`pipeline.py`）——优化去重时只能「收敛成一份」，不能为流式另写一份。

---

## 十、验收与回归方式

### 1. 离线套件（每次改动的硬门槛）

```bash
.venv\Scripts\python.exe -m pytest      # Windows
.venv/bin/python -m pytest              # macOS / Linux
```

必须全绿；每次迭代后覆盖率不下降。

### 2. 真实回归（改判定口径后必跑）

```bash
.venv\Scripts\python.exe -m app check   # 跑 data/cases.json，结果写 var/regression/test-results.json
```

本方案中的改动**都不涉及判定口径**，因此 `data/cases.json` 的结果应与改动前逐字段一致——这是回归的硬标准。（`app check` 会真实调用 Jev，每条消息 2 次。）

### 3. 性能基准

建议先记录基线（50 条消息、两个生成勾选全开）：总耗时与上游调用数。

- 当前每次请求的上游调用数：`50 × 2（Jev）+ 50（潜台词）+ 1（建议）= 151`；
- 优化目标：**请求数不变**、**连接数从 151 降到 2**、**生成层阶段耗时下降约 10%**。

### 4. 前端手测清单

- 分析中途「切换会话」/「关闭页面」/「kill 掉后端」→ 三种情况下 UI 都不能卡在「生成中」；
- 粘贴含 `#` 标题的记录 → 说话人列表前后端一致；
- 50 条消息 + 两个生成全开 → 逐条上屏不掉帧，输入 5 万字符不卡顿。

### 5. 工程化验收

- CI 在 PR 上自动跑 `ruff` + `pytest` + `pnpm build`，全绿方可合并；
- `pre-commit` 在本地拦截未格式化代码；
- 覆盖率报告可见，`app/clients/` 覆盖率不再为 0。

---

## 附：优先级索引速查

| 优先级 | 条目 | 主要影响 |
|--------|------|----------|
| P0-1 | 前端 SSE 取消 / 超时 / 清理 | 用户可见的「卡死无出路」 |
| P0-2 | transcript 双份规则分叉 | 特定输入下 400 报错 |
| P0-3 | SQLite 事务与锁 | 并发写丢更新 |
| P0-4 | `_locks` 无界增长 | 内存缓慢泄漏 |
| P1-1 | HTTP 客户端零复用 | 连接开销 / 无法 keep-alive |
| P1-2 | 线程池每请求新建 | 线程放大 |
| P1-3 | 超时粒度过粗 | 单条最坏 3 分钟 |
| P1-4 | 前端响应式开销 | 长记录卡顿 |
| P1-5 | 静态资源读盘 | 收益小，建议不做 |
| P2-1 | 流式 / 非流式重复 | 分叉风险，可减 120-150 行 |
| P2-2 | 失败标记散落三处 | 映射规则漂移 |
| P2-3 | `App.vue` 上帝组件 | 维护成本 |
| P2-4 | 死代码 + 静默失败 | 1 个真 bug |
| P2-5 | 日志缺失 / 标签混用 | 可观测性 |
| P2-6 | 类型注解 / 魔法数 | 静态检查覆盖 |
| P3 | CI / 覆盖率 / 前端 lint / 补测试 | 长期可维护性 |
| P4 | 体积上限 / GET 守卫 / CORS | 本地安全面 |
| P5 | 键盘可用性 / key 反模式 / 导出 | 体验与无障碍 |
