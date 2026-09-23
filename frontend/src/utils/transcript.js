// 说话人识别：与后端 app/domain/transcript.py 同一套规则（TS_RE / is_name_line / 默认「我是谁」），
// 前端后端必须一致，否则「我是谁」下拉会和真实解析结果对不上。
//
// 这里是刻意保留的双份实现（前端要实时显示 chips，不能每敲一个字就往后端跑一趟），
// 靠 data/transcript_cases.json 这组共享用例兜底：后端 tests/test_transcript_consistency.py、
// 前端 `pnpm run check:transcript` 各跑一遍同一份用例。**改这里的规则请同时改 Python 那侧与用例**，
// 否则总有一边会红。
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
  const bump = (label, body) => {
    if (!label) return
    if (!order.includes(label)) order.push(label)
    counts[label] = (counts[label] || 0) + 1
    if (!samples[label] && body) samples[label] = body.slice(0, 14)
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (!line) continue
    // 文档标题行（`# 聊天记录`）：后端 _scan_blocks 会跳过，这里必须同样跳过。
    // 少了这一步，标题后面紧跟时间行时会被当成一个说话人，用户勾中它就会拿到 400
    // 「识别不到这个人」——而他在界面上明明勾得出来。
    if (line.startsWith('#')) continue
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
  }))
  const hasKnown = order.some((l) => ME_WORDS.includes(l) || OTHER_WORDS.includes(l))
  return hasKnown || people.length >= 2 ? people : []
}

/**
 * 猜「我是谁」：与后端 parse_transcript 的默认口径逐条一致——先取明示代称（我 / 本人…），
 * 否则取第一个「不是对方代称」的名字，都没有则 null。
 *
 * 刻意不再用「谁的话里自称『我』最多」那种启发式：两套规则必须给出同一个答案，
 * 否则界面上预选的人和落库时判定的人会是两个（前端把「她」当成了我，后端却按对方处理）。
 */
export function guessMe(people) {
  if (!people.length) return null
  const explicit = people.find((p) => ME_WORDS.includes(p.label))
  if (explicit) return explicit.label
  const neutral = people.find((p) => !OTHER_WORDS.includes(p.label))
  return neutral ? neutral.label : null
}
