<script setup>
defineEmits(['import'])
</script>

<template>
  <section class="welcome" aria-labelledby="welcomeTitle">
    <div class="welcome-copy">
      <span class="welcome-eyebrow"><span class="welcome-spark" aria-hidden="true">✦</span> 新会话 · 从这里开始</span>
      <h1 id="welcomeTitle">看见聊天里的<br><em>弦外之音。</em></h1>
      <p class="welcome-lead">导入一段聊天，先看每句话的意图与情绪；需要时，再生成潜台词和回复建议。</p>
      <button type="button" class="welcome-cta" @click="$emit('import')">
        导入聊天记录 <span aria-hidden="true">↗</span>
      </button>
      <p class="welcome-cta-note">粘贴聊天记录，选择关系或场景，即可开始分析。</p>
      <div class="welcome-capabilities" aria-label="可以分析的内容">
        <span>意图与情绪</span><i aria-hidden="true"></i><span>潜台词</span><i aria-hidden="true"></i><span>回复建议</span>
      </div>
    </div>

    <div class="welcome-scene" aria-label="聊天解读示例">
      <div class="welcome-scene-head">
        <span class="welcome-scene-mark" aria-hidden="true">J</span>
        <span>一段对话 · 示例</span>
        <span class="welcome-scene-dots" aria-hidden="true">···</span>
      </div>
      <div class="welcome-chat-line">
        <span class="welcome-speaker">对方</span>
        <div class="welcome-bubble">没事，你先忙吧。</div>
      </div>
      <div class="welcome-insight">
        <span class="welcome-insight-label"><span aria-hidden="true">✦</span> 字面之外</span>
        <p>一句“没事”，也可能是在等一句关心。</p>
      </div>
      <div class="welcome-chat-line welcome-chat-line-self">
        <span class="welcome-speaker">你</span>
        <div class="welcome-bubble">忙完我来找你，想听你说说。</div>
      </div>
      <p class="welcome-scene-note">示例仅用于说明界面；实际解读以导入的聊天为准。</p>
    </div>
  </section>
</template>
