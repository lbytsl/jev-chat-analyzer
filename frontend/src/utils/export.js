/**
 * 把一次分析导出成 Markdown。
 *
 * 只导出界面上真实展示过的东西（说话人、正文、意图 / 情绪标签、潜台词、回复建议），
 * 不导出 prompt、usage、置信度这些调试字段——用户要的是这次的结论，不是模型的体检报告。
 */

function pad(value) {
  return String(value).padStart(2, '0')
}

/** 导出用的时间戳：YYYY-MM-DD HH:mm。 */
export function nowStamp(date = new Date()) {
  return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
    + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes())
}

/** 文件名里不能出现这些字符（Windows 更严），一律换成下划线。 */
export function exportFilename(names = {}, date = new Date()) {
  const label = String(names.other || '对话').replace(/[\\/:*?"<>|\s]+/g, '_')
  const stamp = date.getFullYear() + pad(date.getMonth() + 1) + pad(date.getDate())
    + '-' + pad(date.getHours()) + pad(date.getMinutes())
  return '聊天分析-' + label + '-' + stamp + '.md'
}

export function toMarkdown(data, names = {}, date = new Date()) {
  if (!data) return ''
  const byIndex = {}
  for (const item of data.analyses || []) byIndex[item.index] = item

  const lines = []
  lines.push('# ' + (data.other_label || names.other || '对话') + ' · ' + (data.relationship || '未选场景'))
  lines.push('')

  const meta = ['导出时间：' + nowStamp(date),
                '消息 ' + (data.messages || []).length + ' 条',
                '已解读 ' + (data.analyses || []).length + ' 条']
  if (data.failed_count) meta.push('未取得结果 ' + data.failed_count + ' 条')
  lines.push('> ' + meta.join(' · '))
  lines.push('')

  for (const message of data.messages || []) {
    const who = message.label
      || (message.speaker === 'me' ? (names.me || '我') : (names.other || '对方'))
    const stamp = message.timestamp ? '（' + message.timestamp + '）' : ''
    lines.push('**' + who + '**' + stamp + '：' + (message.text || ''))

    const result = byIndex[message.index]?.result
    if (result) {
      const tags = []
      if (result.primary_intent?.label) tags.push('意图：' + result.primary_intent.label)
      if (result.emotion?.label) tags.push('情绪：' + result.emotion.label)
      if (tags.length) lines.push('- ' + tags.join(' · '))
      const dimensions = []
      if (result.relation_direction?.label) {
        dimensions.push('关系信号：' + (result.relation_direction.display || result.relation_direction.label))
      }
      if (result.response_need?.label) {
        dimensions.push('期待回应：' + (result.response_need.display || result.response_need.label))
      }
      if (result.communication_style?.label) {
        dimensions.push('表达：' + (result.communication_style.display || result.communication_style.label))
      }
      if (dimensions.length) lines.push('- ' + dimensions.join(' · '))
      if (result.intent_detail) lines.push('- 潜台词：' + result.intent_detail)
      else if (result.interpretation_failed) lines.push('- 潜台词生成失败')
      const suggestions = Array.isArray(result.suggestions) ? result.suggestions : []
      if (suggestions.length) {
        lines.push('- 回复建议：')
        for (const item of suggestions) {
          lines.push('  - ' + (item.label ? '【' + item.label + '】' : '') + (item.text || ''))
        }
      } else if (result.gen_failed) {
        lines.push('- 回复建议生成失败')
      }
    }
    lines.push('')
  }
  return lines.join('\n')
}

/** 触发浏览器下载。导出是纯前端的字符串拼接，不经过后端。 */
export function downloadText(filename, text, mime = 'text/markdown;charset=utf-8') {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // 立刻 revoke 会让部分浏览器拿到空文件：放到下一轮事件循环再收回。
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
