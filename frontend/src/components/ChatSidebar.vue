<script setup>
import { storeToRefs } from 'pinia'
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { useSessionSwitch } from '@/composables/useSessionSwitch'
import { useSessionsStore } from '@/stores/sessions'
import { formatSessionTime } from '@/utils/time'

// 列表状态全在 sessions store 里；侧栏只负责展示 + 把点击转成 action。
// 原来它从 App 接 6 个 props、往回抛 5 个事件，等于把 store 的字段抄了一遍。
const sessionsStore = useSessionsStore()
const { sessions, currentId, selectMode, selected } = storeToRefs(sessionsStore)
const { openSession, startImport, removeSessions } = useSessionSwitch()

function onItemClick(session) {
  if (selectMode.value) sessionsStore.toggleSelect(session.id)
  else openSession(session)
}

// 搜索只在已加载的列表里前端过滤：列表本就只取最近 100 条，不必再为搜索加一个端点。
// 命中范围覆盖标题 / 预览 / 关系，方便用「同事」这类场景词快速缩小。
const keyword = ref('')
// 搜索框会被浏览器当成「账号 / 密码」输入框：Chrome 的密码管理器会在这里弹「保存的密码」。
// autocomplete="off" 对密码管理器无效，必须用 new-password 才不会被当成可填充的凭据字段；
// 再配一个每次加载都不同的随机 name，避免浏览器按字段名把历史记录关联进来。
const searchName = 'q' + Math.random().toString(36).slice(2, 8)
// 光靠 autocomplete 还不够：浏览器会把这行普通输入框当成可填充字段，把上次输过的东西
// （比如模型名）直接填进来，看起来就像「会话记录不见了」。readonly 是唯一稳定的拦截手段——
// 挂载后保持只读，用户真的来点（pointerdown / focus）才解锁，解锁后浏览器不会再改它。
const touched = ref(false)

let fillTimer = null
onMounted(() => {
  // 兜底：个别浏览器在挂载之后才异步填充，用户没碰过就清掉（碰过的绝不覆盖）
  fillTimer = window.setTimeout(() => {
    fillTimer = null
    if (!touched.value) keyword.value = ''
  }, 200)
})
onUnmounted(() => {
  if (fillTimer) clearTimeout(fillTimer)
})
const filteredSessions = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  if (!query) return sessions.value
  return sessions.value.filter((session) =>
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
      <button type="button" class="session-new" @click="startImport()">＋ 导入聊天</button>
      <button
        v-if="sessions.length"
        type="button"
        class="session-manage"
        :class="{ on: selectMode }"
        @click="sessionsStore.setSelectMode(!selectMode)"
      >{{ selectMode ? '完成' : '管理' }}</button>
    </div>
    <input
      v-model="keyword"
      class="wx-search"
      type="text"
      placeholder="搜索会话（标题 / 内容 / 场景）"
      aria-label="搜索会话"
      autocomplete="new-password"
      spellcheck="false"
      :name="searchName"
      :readonly="!touched"
      @pointerdown="touched = true"
      @focus="touched = true"
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
          @click.stop="sessionsStore.toggleSelect(session.id)"
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
          <button type="button" class="session-act danger" @click.stop="removeSessions([session.id])">删除</button>
        </div>
      </div>
      <div v-if="!sessions.length" class="session-empty">
        {{ sessionsStore.loading ? '正在读取会话…' : '还没有会话。点上面「导入聊天」开始，记录会自动存下来。' }}
      </div>
      <div v-else-if="!filteredSessions.length" class="session-empty">
        <p>没有匹配「{{ keyword.trim() }}」的会话。</p>
        <button type="button" class="session-clear" @click="keyword = ''">清空搜索，看全部会话</button>
      </div>
      <!-- 服务端一次只回最近 100 条（见 SESSIONS_LIST_LIMIT）。与其做一套分页，不如
           把「这里显示的不是全部」直说，用户至少知道该去清理旧会话了。 -->
      <p v-if="sessionsStore.total > sessions.length && filteredSessions.length" class="session-more">
        共 {{ sessionsStore.total }} 条，这里显示最近 {{ sessions.length }} 条（删掉旧会话后会显示更多）。
      </p>
    </div>
    <div v-if="selectMode" class="session-bar">
      <span>已选 {{ selected.size }} 项</span>
      <button
        type="button"
        class="session-act danger"
        :disabled="!selected.size"
        @click="removeSessions([...selected])"
      >删除选中</button>
    </div>
  </aside>
</template>
