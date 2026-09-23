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

export async function callAPI(path, options) {
  let response
  try {
    response = await fetch(API + path, options)
  } catch (err) {
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
 */
export async function streamAPI(path, payload, onEvent) {
  let response
  try {
    response = await fetch(API + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    })
  } catch (err) {
    throw new ApiError(OFFLINE, 'offline')
  }
  if (!response.ok || !response.body) {
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
  while (true) {
    let chunk
    try {
      chunk = await reader.read()
    } catch (err) {
      throw new ApiError(OFFLINE, 'offline')
    }
    if (chunk.done) break
    buffer += decoder.decode(chunk.value, { stream: true })
    let cut = buffer.indexOf('\n\n')
    while (cut >= 0) {
      const event = parseSSE(buffer.slice(0, cut))
      buffer = buffer.slice(cut + 2)
      cut = buffer.indexOf('\n\n')
      if (!event) continue
      if (event.type === 'error') {
        throw new ApiError(event.message || '这次没有取得结果，请稍后重试。', 'app', event.status)
      }
      onEvent(event)
    }
  }
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

export function analyzeChat(payload) {
  return postJSON('/analyze-chat', payload)
}

// 潜台词与推荐回复是两个独立端点：各自一套提示词、各自一次模型调用、各自失败。
export function interpretChat(payload) {
  return postJSON('/interpret-chat', payload)
}

export function suggestReplies(payload) {
  return postJSON('/suggest-chat', payload)
}

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
