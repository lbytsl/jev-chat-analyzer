<script setup>
import { computed, ref, watch } from 'vue'

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
  suggestions_count: 3,
})
// 生成层可以存多套「地址 + 模型 + 密钥」：整表在 generation.profiles 里，
// activeIndex 是「当前启用」的下标（用下标而不是名字，改名 / 增删都不会指错）。
const generation = ref({ profiles: [], activeIndex: 0, error: '' })
const config = ref(null)
const test = ref({ classification: null, generation: null, profileIndex: -1 })
const busy = ref(false)
const statusText = ref('')
const statusErr = ref(false)

// 上限由服务端给（避免两边各写一份常量，改一边忘一边）
const maxProfiles = computed(() => config.value?.generation?.max_profiles || 20)
const maxProfileName = computed(() => config.value?.generation?.name_max || 24)

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

function profileHint(profile) {
  if (!profile) return ''
  if (!profile.configured) return '还没有配置，请填写'
  return '已配置 ' + profile.masked + '（留空 = 不改动）'
}

// 服务端回的是「打码后的样子」，密钥框一律留空：留空 = 不改动，不会被打码值覆盖掉。
// source 记住这套在服务端原来的名字，改名后服务端靠它把原来的密钥接上。
function toProfileForm(item) {
  const key = item.api_key || {}
  return {
    name: item.name || '',
    source: item.name || '',
    base_url: item.base_url || '',
    model: item.model || '',
    api_key: '',
    masked: key.masked || '',
    configured: !!key.configured,
  }
}

async function load() {
  setStatus('')
  test.value = { classification: null, generation: null, profileIndex: -1 }
  try {
    const info = await getConfig()
    config.value = info
    form.value = {
      typesafe_base_url: info.classification.base_url || '',
      typesafe_default_model: info.classification.model || '',
      typesafe_api_key: '',
      suggestions_count: info.output ? info.output.suggestions_count : 3,
    }
    const layer = info.generation || {}
    const profiles = (layer.profiles || []).map(toProfileForm)
    generation.value = {
      profiles,
      activeIndex: Math.max(0, profiles.findIndex((item) => item.active)),
      error: layer.profiles_error || '',
    }
  } catch (err) {
    setStatus(err.message, true)
  }
}

function currentProfile() {
  return generation.value.profiles[generation.value.activeIndex] || null
}

function payload() {
  const body = {
    classification: {
      base_url: form.value.typesafe_base_url,
      model: form.value.typesafe_default_model,
      api_key: form.value.typesafe_api_key,
    },
    generation: {
      // 整表提交：增 / 删 / 改名 / 切换启用都在这一份里
      profiles: generation.value.profiles.map((profile) => ({
        name: profile.name,
        source: profile.source,
        base_url: profile.base_url,
        model: profile.model,
        api_key: profile.api_key,
      })),
      active: currentProfile() ? currentProfile().name : '',
    },
  }
  const count = Number(form.value.suggestions_count)
  if (Number.isFinite(count) && count > 0) body.output = { suggestions_count: count }
  return body
}

function addProfile() {
  generation.value.profiles.push(
    { name: '', source: '', base_url: '', model: '', api_key: '', masked: '', configured: false })
  generation.value.activeIndex = generation.value.profiles.length - 1
}

function removeProfile(index) {
  if (generation.value.profiles.length <= 1) {
    setStatus('至少要留一套配置。', true)
    return
  }
  generation.value.profiles.splice(index, 1)
  if (index < generation.value.activeIndex) generation.value.activeIndex -= 1
  else if (index === generation.value.activeIndex) generation.value.activeIndex = 0
  test.value = { ...test.value, generation: null, profileIndex: -1 }
}

async function onTest() {
  busy.value = true
  setStatus('正在探测两个接口…')
  try {
    const result = await testConfig(payload())
    test.value = { classification: result.classification, generation: result.generation,
                   profileIndex: generation.value.activeIndex }
    const failed = ['classification', 'generation'].filter((key) => !result[key].ok)
    if (!failed.length) setStatus('两个接口都连通。')
    else setStatus('有接口没连通，请看对应 Tab 里的提示。', true)
  } catch (err) {
    setStatus(err.message, true)
  } finally {
    busy.value = false
  }
}

// 单独测某一套（可以用还没保存的地址 / 模型先验证再保存）
async function onTestProfile(index) {
  const profile = generation.value.profiles[index]
  if (!profile) return
  busy.value = true
  setStatus('正在探测「' + (profile.name || '未命名') + '」…')
  try {
    const result = await testConfig({
      generation: { base_url: profile.base_url, model: profile.model, api_key: profile.api_key },
    })
    test.value = { ...test.value, generation: result.generation, profileIndex: index }
    setStatus(result.generation.ok ? '这套配置已连通。' : '这套没连通，请看下面的提示。',
              !result.generation.ok)
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
    // 保存后服务端已清缓存：这里重新读一次，顺带把打码值与启用状态刷新
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
              <input v-model="form.typesafe_default_model" placeholder="jev-1.13.0">
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
            <p class="muted cfg-note">
              可以保存多套「接口地址 + 模型 + 密钥」，选中哪套就用哪套（潜台词与回复建议都走它）。
              改完点右下「保存」生效，不用重启服务。
            </p>
            <p v-if="generation.error" class="cfg-test">{{ generation.error }}</p>

            <div class="llm-profiles">
              <div
                v-for="(profile, index) in generation.profiles"
                :key="index"
                class="llm-profile"
                :class="{ on: index === generation.activeIndex }"
              >
                <div class="llm-profile-head">
                  <label class="llm-pick">
                    <input
                      type="radio"
                      name="llm-active"
                      :value="index"
                      :checked="index === generation.activeIndex"
                      :aria-label="'启用第 ' + (index + 1) + ' 套配置'"
                      @change="generation.activeIndex = index"
                    >
                    <input
                      v-model="profile.name"
                      class="llm-name"
                      :maxlength="maxProfileName"
                      placeholder="配置名字（如 DeepSeek 官方）"
                      :aria-label="'第 ' + (index + 1) + ' 套配置的名字'"
                    >
                  </label>
                  <div class="llm-actions">
                    <button type="button" class="llm-btn" :disabled="busy"
                            @click="onTestProfile(index)">测试</button>
                    <button type="button" class="llm-btn danger"
                            :disabled="busy || generation.profiles.length <= 1"
                            @click="removeProfile(index)">删除</button>
                  </div>
                </div>
                <label class="cfg-field">
                  <span>接口地址</span>
                  <input v-model="profile.base_url" placeholder="https://api.deepseek.com（填到 /v1 那一层）">
                </label>
                <label class="cfg-field">
                  <span>模型</span>
                  <input v-model="profile.model" placeholder="模型名，如 deepseek-chat">
                </label>
                <label class="cfg-field">
                  <span>API Key</span>
                  <input
                    v-model="profile.api_key"
                    type="password"
                    autocomplete="off"
                    :placeholder="profileHint(profile)"
                  >
                </label>
                <p v-if="test.profileIndex === index && test.generation"
                   class="cfg-test" :class="{ ok: test.generation.ok }">
                  {{ test.generation.detail }}
                </p>
              </div>
            </div>

            <div class="llm-foot">
              <button type="button" class="aug-btn ghost"
                      :disabled="busy || generation.profiles.length >= maxProfiles"
                      @click="addProfile">＋ 新增一套配置</button>
              <span class="muted">最多 {{ maxProfiles }} 套</span>
            </div>

            <p class="muted cfg-note">
              只要是 OpenAI 兼容的 /chat/completions 都能用（DeepSeek / 通义千问 / 智谱 GLM / Kimi /
              阶跃 StepFun / 本地 vLLM…），前提是上游支持 JSON 输出模式。
              接口地址填到 /v1 那一层就行（如 https://api.stepfun.com/v1），直接粘完整的
              /chat/completions 端点也认，两种写法都不会拼错。密钥留空 = 不改动，界面只显示打码值。
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
