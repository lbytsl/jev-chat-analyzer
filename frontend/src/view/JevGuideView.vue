<script setup>
import { useRouter } from 'vue-router'

import JevSetupGuide from '@/components/JevSetupGuide.vue'

const router = useRouter()

function usePreset(id) {
  router.push({ path: '/', query: { jevPreset: id } })
}
</script>

<template>
  <div class="guide-page">
    <header class="guide-topbar">
      <RouterLink to="/" class="guide-brand">恋爱·职场聊天神器</RouterLink>
      <RouterLink to="/" class="guide-back">返回应用 ↗</RouterLink>
    </header>
    <main>
      <JevSetupGuide @use-preset="usePreset" />
    </main>
  </div>
</template>
