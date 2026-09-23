/**
 * 跑一遍 data/transcript_cases.json：确认前端的说话人识别与后端解析结果一致。
 *
 * 前端（src/utils/transcript.js）与后端（app/domain/transcript.py）各有一份说话人识别规则，
 * 前端那份是为了实时显示 chips。两边靠这份共享用例兜底——后端侧是
 * tests/test_transcript_consistency.py，前端侧就是这个脚本（`pnpm run check:transcript`）。
 *
 * 不引任何测试框架：Node 直接跑，装完依赖就能用，改规则时顺手跑一下。
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { detectPeople, guessMe } from '../src/utils/transcript.js'

const here = dirname(fileURLToPath(import.meta.url))
const casesPath = resolve(here, '../../data/transcript_cases.json')
const { cases } = JSON.parse(readFileSync(casesPath, 'utf8'))

let failed = 0
for (const item of cases) {
  const people = detectPeople(item.transcript)
  const labels = people.map((person) => person.label)
  // 与后端同一条口径：没有说话人时「我是谁」无意义，一律 null。
  const me = people.length ? guessMe(people) : null

  const problems = []
  if (JSON.stringify(labels) !== JSON.stringify(item.labels)) {
    problems.push(`labels 期望 ${JSON.stringify(item.labels)}，实得 ${JSON.stringify(labels)}`)
  }
  if (me !== item.me_label) {
    problems.push(`me_label 期望 ${JSON.stringify(item.me_label)}，实得 ${JSON.stringify(me)}`)
  }
  if (problems.length) {
    failed += 1
    console.error(`✗ ${item.name}`)
    for (const problem of problems) console.error(`  ${problem}`)
  }
}

console.log(`transcript 一致性用例：${cases.length - failed}/${cases.length} 通过`)
if (failed) process.exit(1)
