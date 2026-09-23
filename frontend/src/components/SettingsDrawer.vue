<script setup>
import { ref, watch } from 'vue'

import { getConfig, testConfig, updateConfig } from '@/api/client'

const props = defineProps({
  open: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'saved'])

// 左侧 Tab：分类层 / 生成层 / 输出设置，各自管一摊配置。
const TABS = [
  { key: 'classification', label: '分类层' },
  { key: 'generation', label: '生成层' },
  { key: 'output', label: '输出设置' },
]
const active = ref('classification')

// 表单里只放「会改动的值」；密钥框默认留空 = 保持服务端已有的那把，避免把打码值写回去。
const form = ref({
  typesafe_base_url: '',
  typesafe_default_model: '',
  typesafe_api_key: '',
  deepseek_base_url: '',
  deepseek_model: '',
  deepseek_api_key: '',
  suggestions_count: 3,
})
const config = ref(null)
const test = ref({ classification: null, generation: null })
const busy = ref(false)
const statusText = ref('')
const statusErr = ref(false)

function setStatus(text, isErr = false) {
  statusText.value = text
  statusErr.value = isErr
}

function maskHint(layer) {
  if (!layer) return '加载中…'
  const key = layer.api_key || {}
  if (!key.configured) return '还没有配置，请填写'
  return '已配置 ' + key.masked + '（留空 = 不改动）'
}

async function load() {
  setStatus('')
  test.value = { classification: null, generation: null }
  try {
    const info = await getConfig()
    config.value = info
    form.value = {
      typesafe_base_url: info.classification.base_url || '',
      typesafe_default_model: info.classification.model || '',
      typesafe_api_key: '',
      deepseek_base_url: info.generation.base_url || '',
      deepseek_model: info.generation.model || '',
      deepseek_api_key: '',
      suggestions_count: info.output ? info.output.suggestions_count : 3,
    }
  } catch (err) {
    setStatus(err.message, true)
  }
}

function payload() {
  const body = {
    classification: {
      base_url: form.value.typesafe_base_url,
      model: form.value.typesafe_default_model,
      api_key: form.value.typesafe_api_key,
    },
    generation: {
      base_url: form.value.deepseek_base_url,
      model: form.value.deepseek_model,
      api_key: form.value.deepseek_api_key,
    },
  }
  const count = Number(form.value.suggestions_count)
  if (Number.isFinite(count) && count > 0) body.output = { suggestions_count: count }
  return body
}

async function onTest() {
  busy.value = true
  setStatus('正在探测两个接口…')
  try {
    const result = await testConfig(payload())
    test.value = result
    const failed = ['classification', 'generation'].filter((key) => !result[key].ok)
    if (!failed.length) setStatus('两个接口都连通。')
    else setStatus('有接口没连通，请看对应 Tab 里的提示。', true)
  } catch (err) {
    setStatus(err.message, true)
  } finally {
    busy.value = false
  }
}

async function onSave() {
  busy.value = true
  setStatus('正在保存…')
  try {
    const result = await updateConfig(payload())
    config.value = result
    form.value.typesafe_api_key = ''
    form.value.deepseek_api_key = ''
    // 保存后服务端已清缓存：这里重新读一次，顺带把打码值刷新
    await load()
    setStatus('已保存，立即生效（不用重启服务）。')
    emit('saved')
  } catch (err) {
    setStatus(err.message, true)
  } finally {
    busy.value = false
  }
}

// 点遮罩空白处关闭（点在卡片上不关）。
function onMaskClick(event) {
  if (event.target === event.currentTarget) emit('close')
}

watch(() => props.open, (open) => { if (open) load() })
</script>

<template>
  <div id="settingsModal" v-show="open" class="modal-mask" @click="onMaskClick">
    <div class="modal settings-modal" role="dialog" aria-modal="true" aria-label="配置">
      <div class="settings-head">
        <h3>配置</h3>
        <button type="button" class="close" aria-label="关闭配置" @click="emit('close')">×</button>
      </div>
      <p class="muted">
        分类层（Jev）判定意图与情绪，生成层（OpenAI 兼容）写潜台词与回复建议。
        保存后立即生效，不需要重启服务。
      </p>

      <div class="settings-body">
        <nav class="settings-tabs" role="tablist" aria-label="配置分类">
          <button
            v-for="tab in TABS"
            :key="tab.key"
            type="button"
            role="tab"
            class="settings-tab"
            :class="{ on: active === tab.key }"
            :aria-selected="active === tab.key ? 'true' : 'false'"
            @click="active = tab.key"
          >{{ tab.label }}</button>
        </nav>

        <div class="settings-pane">
          <section v-show="active === 'classification'" role="tabpanel">
            <div class="cfg-head">
              <strong>分类层 · Jev</strong>
              <span class="cfg-endpoint">{{ config ? config.classification.endpoint : '' }}</span>
            </div>
            <label class="cfg-field">
              <span>接口地址</span>
              <input v-model="form.typesafe_base_url" placeholder="https://api.typesafe.ai">
            </label>
            <label class="cfg-field">
              <span>模型</span>
              <input v-model="form.typesafe_default_model" placeholder="jev-latest">
            </label>
            <label class="cfg-field">
              <span>API Key</span>
              <input
                v-model="form.typesafe_api_key"
                type="password"
                autocomplete="off"
                :placeholder="maskHint(config && config.classification)"
              >
            </label>
            <p v-if="test.classification" class="cfg-test" :class="{ ok: test.classification.ok }">
              {{ test.classification.detail }}
            </p>
            <p class="muted cfg-note">
              只写主机名（如 https://api.typesafe.ai）会自动补 /v1/systemone；
              写完整端点（如 OpenRouter 的 /api/alpha/decisions）则原样使用。
            </p>
          </section>

          <section v-show="active === 'generation'" role="tabpanel">
            <div class="cfg-head">
              <strong>生成层 · LLM（OpenAI 兼容）</strong>
              <span class="cfg-endpoint">{{ config ? config.generation.endpoint : '' }}</span>
            </div>
            <label class="cfg-field">
              <span>接口地址</span>
              <input v-model="form.deepseek_base_url" placeholder="https://api.deepseek.com">
            </label>
            <label class="cfg-field">
              <span>模型</span>
              <input v-model="form.deepseek_model" placeholder="deepseek-chat">
            </label>
            <label class="cfg-field">
              <span>API Key</span>
              <input
                v-model="form.deepseek_api_key"
                type="password"
                autocomplete="off"
                :placeholder="maskHint(config && config.generation)"
              >
            </label>
            <p v-if="test.generation" class="cfg-test" :class="{ ok: test.generation.ok }">
              {{ test.generation.detail }}
            </p>
            <p class="muted cfg-note">
              只要是 OpenAI 兼容的 /chat/completions 都能用（DeepSeek / 通义 / GLM / Kimi / 本地 vLLM…），
              前提是支持 JSON 输出模式。
            </p>
          </section>

          <section v-show="active === 'output'" role="tabpanel">
            <div class="cfg-head">
              <strong>输出设置</strong>
            </div>
            <label class="cfg-field">
              <span>推荐回复条数</span>
              <input
                v-model.number="form.suggestions_count"
                type="number"
                :min="config && config.output ? config.output.min : 1"
                :max="config && config.output ? config.output.max : 6"
                step="1"
              >
            </label>
            <p class="muted cfg-note">
              点「生成推荐回复」时让模型给几个方向（{{ config && config.output ? config.output.min : 1 }}–{{ config && config.output ? config.output.max : 6 }} 条）。
              这个条数同时写进提示词与结果整形：模型被要求给这么多条，多给的也会被截掉。
            </p>
          </section>
        </div>
      </div>

      <p class="muted cfg-path">
        写入文件：{{ config ? config.env_path : '…' }}（密钥只存本地，界面上永远不回显明文）
      </p>

      <div class="modal-actions">
        <p id="settingsStatus" class="muted" :class="{ err: statusErr }" v-show="statusText">{{ statusText }}</p>
        <button type="button" class="aug-btn ghost" :disabled="busy" @click="onTest">测试连接</button>
        <button type="button" class="aug-btn" :disabled="busy" @click="onSave">保存</button>
      </div>
    </div>
  </div>
</template>
