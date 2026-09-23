export const API_PORT = '8767'

// 同源（页面就是从 8767 打开的）走相对路径；其他来源——vite dev（5173）、IDE 内置预览、
// file:// ——一律显式打到本机 8767：后端 CORS 已允许 127.0.0.1 / localhost 任意端口与 null。
export const API = location.protocol === 'http:' && location.port === API_PORT
  ? ''
  : 'http://127.0.0.1:' + API_PORT

export const OFFLINE = '连不上本地服务 http://127.0.0.1:' + API_PORT
  + '。请在项目目录运行 python3 -m app serve（或双击 启动.command），再从 http://127.0.0.1:'
  + API_PORT + ' 打开本页。'

/** 业务错误（后端返回了 error 字段）与网络错误要分开处理：前者显示在状态栏，后者提示在顶部。 */
export class ApiError extends Error {
  constructor(message, kind, status) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind // 'app' | 'offline'
    this.status = status
  }
}

/**
 * 主动取消（AbortController 触发）。必须与「连不上本地服务」分开：取消是调用方
 * 自己的决定（切会话 / 重新导入 / 离开页面），不该弹「请重启服务」那种提示。
 */
export class CancelledError extends Error {
  constructor() {
    super('已取消')
    this.name = 'CancelledError'
    this.kind = 'cancelled'
  }
}

export function isCancelled(err) {
  return !!err && err.kind === 'cancelled'
}

/**
 * 流式读取的空闲超时：这么久没有任何事件就认为服务卡住了。
 * 取 120s 是因为 Jev 单条实测 13-15s，但整批里最慢的一条可能远超平均。
 */
export const STREAM_IDLE_TIMEOUT_MS = 120000

export async function callAPI(path, options) {
  let response
  try {
    response = await fetch(API + path, options)
  } catch (err) {
    if (err && err.name === 'AbortError') throw new CancelledError()
    throw new ApiError(OFFLINE, 'offline')
  }
  const text = await response.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch (err) {
      data = null
    }
  }
  if (!response.ok) {
    throw new ApiError((data && data.error) || '服务返回错误 HTTP ' + response.status, 'app', response.status)
  }
  if (!data) {
    const peek = (text || '(空响应)').replace(/\s+/g, ' ').slice(0, 80)
    throw new ApiError(
      '服务返回了读不懂的响应（HTTP ' + response.status + '，对方回了：' + peek + '）。请重启 python3 -m app serve 再试。',
      'app',
      response.status,
    )
  }
  return data
}

function postJSON(path, payload) {
  return callAPI(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/**
 * 读一条 SSE 流并逐事件回调。
 *
 * 不用 EventSource：它只能 GET，而我们的请求体是 JSON。所以 fetch + ReadableStream 手写解析
 * （`data: {...}\n\n` 分帧）。错误语义与 callAPI 对齐：连不上 = offline（顶部提示条），
 * 服务端发来的 error 事件 = app（抽屉状态栏），这样两种调用方式的提示位置一致。
 *
 * options:
 * - `signal`        AbortController 的 signal。取消时抛 CancelledError（**不是** offline），
 *                   调用方据此静默收尾，不弹提示。
 * - `idleTimeoutMs` 空闲超时，默认 STREAM_IDLE_TIMEOUT_MS。
 *
 * 三条容易踩的边界（都已处理，别「优化」掉）：
 * 1) 无论正常结束、业务 error、超时还是取消，都必须 `reader.cancel()`——否则连接悬挂，
 *    后端也会一直算到跑完（整段分析要几十秒到几分钟，是真金白银的上游额度）；
 * 2) 分帧要兼容 `\n\n` 与 `\r\n\r\n`（SSE 规范三种换行都合法）；
 * 3) 流结束时必须冲刷解码器并处理 buffer 里最后一帧，否则 `done` 事件会被静默丢掉。
 */
export async function streamAPI(path, payload, onEvent, options = {}) {
  const { signal, idleTimeoutMs = STREAM_IDLE_TIMEOUT_MS } = options
  let response
  try {
    response = await fetch(API + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(payload || {}),
      signal,
    })
  } catch (err) {
    if (signal?.aborted || (err && err.name === 'AbortError')) throw new CancelledError()
    throw new ApiError(OFFLINE, 'offline')
  }
  if (!response.ok || !response.body) {
    if (signal?.aborted) throw new CancelledError()
    const text = await response.text()
    let message = '服务返回错误 HTTP ' + response.status
    if (text) {
      try {
        message = JSON.parse(text).error || message
      } catch (err) {
        // 保留默认文案
      }
    }
    throw new ApiError(message, 'app', response.status)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const emit = (raw) => {
    const event = parseSSE(raw)
    if (!event) return
    if (event.type === 'error') {
      throw new ApiError(event.message || '这次没有取得结果，请稍后重试。', 'app', event.status)
    }
    onEvent(event)
  }

  try {
    while (true) {
      let chunk
      try {
        chunk = await readWithIdleTimeout(reader, idleTimeoutMs)
      } catch (err) {
        if (err instanceof ApiError) throw err // 空闲超时：文案已经写好了
        if (signal?.aborted) throw new CancelledError()
        throw new ApiError(OFFLINE, 'offline')
      }
      if (chunk.done) {
        buffer += decoder.decode() // 冲刷解码器里可能残留的半个多字节字符
        break
      }
      buffer += decoder.decode(chunk.value, { stream: true })
      // 归一化换行：\r\n → \n。JSON 里的 CR 会被转义成字面量，不会出现裸 CR，所以整段替换安全；
      // 跨分片的 \r\n 也照顾到了（下一片补齐后整段再替换一次）。
      buffer = buffer.replace(/\r\n/g, '\n')
      let cut = buffer.indexOf('\n\n')
      while (cut >= 0) {
        emit(buffer.slice(0, cut))
        buffer = buffer.slice(cut + 2)
        cut = buffer.indexOf('\n\n')
      }
    }
    // 最后一帧没有以空行收尾时也要处理，否则会丢掉 done（前端就永远停在「生成中」）。
    if (buffer.trim()) emit(buffer)
  } finally {
    try {
      await reader.cancel()
    } catch (err) {
      // 流已正常结束 / 已被取消：忽略
    }
  }
}

/** 给 reader.read() 套一个空闲超时：超时抛 ApiError（文案与业务错误一样走状态栏）。 */
function readWithIdleTimeout(reader, ms) {
  let timer = null
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      reject(new ApiError(
        '本地服务已 ' + Math.round(ms / 1000) + ' 秒没有返回新进度，可能卡住了。请重试；'
        + '若一直如此，请重启 python3 -m app serve。',
        'app',
      ))
    }, ms)
  })
  return Promise.race([reader.read(), timeout]).finally(() => clearTimeout(timer))
}

function parseSSE(raw) {
  for (const line of raw.split('\n')) {
    if (!line.startsWith('data:')) continue
    try {
      return JSON.parse(line.slice(5).trim())
    } catch (err) {
      return null
    }
  }
  return null
}

export function fetchHealth() {
  return callAPI('/health')
}

// 注意：这里只有 `/append-chat` 走一次性 POST。整段分析、潜台词、推荐回复前端一律走
// `streamAPI('/…/stream')`（一次性版本仍在后端保留，供脚本与夹具用），所以**不要**为了
// 「对称」再补 `analyzeChat` / `interpretChat` / `suggestReplies` —— 曾经有过的三个
// 包装函数全仓无人引用，只会让人误以为界面上有非流式路径。
export function appendChat(payload) {
  return postJSON('/append-chat', payload)
}

// ---------- 会话（持久记忆） ----------
export function listSessions() {
  return callAPI('/sessions')
}

export function getSession(sessionId) {
  return callAPI('/sessions/' + encodeURIComponent(sessionId))
}

export function deleteSessions(ids) {
  return postJSON('/sessions/delete', { ids })
}

// ---------- 可视化配置（Jev / LLM 模型） ----------
export function getConfig() {
  return callAPI('/config')
}

export function updateConfig(payload) {
  return callAPI('/config', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function testConfig(payload) {
  return postJSON('/config/test', payload || {})
}
