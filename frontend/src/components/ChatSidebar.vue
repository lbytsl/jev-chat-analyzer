<script setup>
import { computed, ref } from 'vue'
import { formatSessionTime } from '@/utils/time'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  currentId: { type: String, default: null },
  selectMode: { type: Boolean, default: false },
  selected: { type: Object, default: () => new Set() },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['import', 'open', 'delete', 'toggle-select', 'toggle-mode'])

function onItemClick(session) {
  if (props.selectMode) emit('toggle-select', session.id)
  else emit('open', session)
}

// 搜索只在已加载的列表里前端过滤：列表本就只取最近 100 条，不必再为搜索加一个端点。
// 命中范围覆盖标题 / 预览 / 关系，方便用「同事」这类场景词快速缩小。
const keyword = ref('')
// 搜索框会被浏览器当成「账号 / 密码」输入框：Chrome 的密码管理器会在这里弹「保存的密码」。
// autocomplete="off" 对密码管理器无效，必须用 new-password 才不会被当成可填充的凭据字段；
// 再配一个每次加载都不同的随机 name，避免浏览器按字段名把历史记录关联进来。
const searchName = 'q' + Math.random().toString(36).slice(2, 8)
const filteredSessions = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  if (!query) return props.sessions
  return props.sessions.filter((session) =>
    [session.title, session.preview, session.relationship]
      .filter(Boolean)
      .some((text) => String(text).toLowerCase().includes(query)),
  )
})
</script>

<template>
  <aside class="wx-side">
    <div class="brand">微信</div>
    <div class="session-tools">
      <button type="button" class="session-new" @click="emit('import')">＋ 导入聊天</button>
      <button
        v-if="sessions.length"
        type="button"
        class="session-manage"
        :class="{ on: selectMode }"
        @click="emit('toggle-mode', !selectMode)"
      >{{ selectMode ? '完成' : '管理' }}</button>
    </div>
    <input
      v-model="keyword"
      class="wx-search"
      type="text"
      placeholder="搜索会话"
      aria-label="搜索会话"
      autocomplete="new-password"
      spellcheck="false"
      :name="searchName"
    >
    <div class="session-list">
      <div
        v-for="session in filteredSessions"
        :key="session.id"
        class="wx-item session-item"
        :class="{ on: session.id === currentId, selecting: selectMode }"
        role="button"
        :aria-current="session.id === currentId ? 'true' : 'false'"
        @click="onItemClick(session)"
      >
        <input
          v-if="selectMode"
          class="session-check"
          type="checkbox"
          :checked="selected.has(session.id)"
          :aria-label="'选择会话 ' + session.title"
          @click.stop="emit('toggle-select', session.id)"
        >
        <div class="session-body">
          <div class="session-row">
            <b class="session-title">{{ session.title || '未命名会话' }}</b>
            <span class="session-time">{{ formatSessionTime(session.updated_at) }}</span>
          </div>
          <small class="session-preview">{{ session.preview || '（还没有消息）' }}</small>
          <div class="session-meta">
            <span class="session-tag">{{ session.relationship }}</span>
            <span>{{ session.message_count }} 条</span>
            <span v-if="session.failed_count" class="session-warn">{{ session.failed_count }} 条未取到</span>
          </div>
        </div>
        <div v-if="!selectMode" class="session-actions">
          <button type="button" class="session-act danger" @click.stop="emit('delete', [session.id])">删除</button>
        </div>
      </div>
      <div v-if="!sessions.length" class="session-empty">
        {{ loading ? '正在读取会话…' : '还没有会话。点上面「导入聊天」开始，记录会自动存下来。' }}
      </div>
      <div v-else-if="!filteredSessions.length" class="session-empty">
        没有匹配「{{ keyword.trim() }}」的会话。
      </div>
    </div>
    <div v-if="selectMode" class="session-bar">
      <span>已选 {{ selected.size }} 项</span>
      <button
        type="button"
        class="session-act danger"
        :disabled="!selected.size"
        @click="emit('delete', [...selected])"
      >删除选中</button>
    </div>
  </aside>
</template>
