// 说话人识别：与后端 app/domain/transcript.py 同一套规则（TS_RE / is_name_line），
// 前端后端必须一致，否则「我是谁」下拉会和真实解析结果对不上。
export const ME_WORDS = ['我', '我方', '自己', '本人']
export const OTHER_WORDS = ['她', '他', '对方', 'TA', 'ta', 'Ta', 'tA', '对方昵称']

export const TS_RE = /^(?:(?:\d{4}年)?\d{1,2}月\d{1,2}日|\d{4}[-\/]\d{1,2}[-\/]\d{1,2}|星期[一二三四五六日天]|昨天|今天|前天)?[ 　]*(?:上午|下午|凌晨|中午|晚上)?[ 　]*\d{1,2}:\d{2}(?::\d{2})?$/
// 微信昵称可以包含英文句号，例如「Mr.Wang」；它后面必须跟时间行。
const NAME_PUNCT = /[。！？，、；：,!?;]/

export function isNameLine(line) {
  return !!line && line.length <= 24 && line.indexOf(':') < 0 && line.indexOf('：') < 0
    && !TS_RE.test(line) && !NAME_PUNCT.test(line)
}

export function detectPeople(text) {
  const lines = String(text || '').split('\n').map((s) => s.trim())
  const order = []
  const counts = {}
  const samples = {}
  const selfHits = {}
  const bump = (label, body) => {
    if (!order.includes(label)) order.push(label)
    counts[label] = (counts[label] || 0) + 1
    if (!samples[label] && body) samples[label] = body.slice(0, 14)
    selfHits[label] = (selfHits[label] || 0) + (String(body).match(/我/g) || []).length
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (!line) continue
    const next = lines[i + 1] || ''
    if (next && TS_RE.test(next) && isNameLine(line)) {
      bump(line, lines[i + 2] || '')
      i++
      continue
    }
    const m = /^\s*([^：:\n]{1,12}?)\s*[:：]\s*(.+)$/.exec(line)
    if (m && !TS_RE.test(line)) bump(m[1].trim(), m[2].trim())
  }
  const people = order.map((label) => ({
    label,
    count: counts[label] || 0,
    sample: samples[label] || '',
    self: selfHits[label] || 0,
  }))
  const hasKnown = order.some((l) => ME_WORDS.includes(l) || OTHER_WORDS.includes(l))
  return hasKnown || people.length >= 2 ? people : []
}

// 猜「我是谁」：优先明示代称，其次谁的话里自称「我」最多（最像在说自己）。
export function guessMe(people) {
  if (!people.length) return null
  const explicit = people.find((p) => ME_WORDS.includes(p.label))
  if (explicit) return explicit.label
  const scored = [...people].filter((p) => !OTHER_WORDS.includes(p.label)).sort((a, b) => b.self - a.self)
  if (scored.length && scored[0].self > 0) return scored[0].label
  const neutral = people.find((p) => !OTHER_WORDS.includes(p.label))
  return (neutral || people[0]).label
}
