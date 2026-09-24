<script setup>
import { JEV_CHANNELS } from '@/utils/jevChannels'

defineEmits(['usePreset'])
</script>

<template>
  <div class="jev-guide">
    <div class="jev-guide-intro">
      <span class="jev-guide-eyebrow">JEV · 新手配置</span>
      <h4>先拿密钥，再填三项</h4>
      <p>选一个平台即可。接口地址、模型和密钥必须来自同一平台；两种平台的密钥不能混用。</p>
    </div>

    <div class="jev-guide-options">
      <article v-for="channel in JEV_CHANNELS" :key="channel.id" class="jev-guide-card">
        <div class="jev-guide-card-head">
          <strong>{{ channel.name }}</strong>
          <span>{{ channel.id === 'typesafe' ? '官方直连' : '第三方接入' }}</span>
        </div>
        <p>{{ channel.intro }}</p>
        <ol>
          <li>
            <a :href="channel.keyUrl" target="_blank" rel="noopener noreferrer">
              {{ channel.keyLabel }} ↗
            </a>
            <span>{{ channel.keyStep }}</span>
          </li>
          <li>
            <span>点击下方「前往配置并填入地址和模型」，再把刚复制的密钥粘贴到 API Key。</span>
          </li>
          <li><span>点击分类层的「测试」。显示「已连通」后，再点击「保存」。</span></li>
        </ol>
        <dl>
          <div><dt>接口地址</dt><dd><code>{{ channel.baseUrl }}</code></dd></div>
          <div><dt>模型</dt><dd><code>{{ channel.model }}</code></dd></div>
          <div><dt>API Key</dt><dd>填入从{{ channel.name }}复制的密钥</dd></div>
        </dl>
        <button type="button" class="jev-guide-use" @click="$emit('usePreset', channel.id)">
          前往配置并填入地址和模型 →
        </button>
      </article>
    </div>
    <p class="jev-guide-help">密钥只在首次设置或更换时填写；已配置后留空不会清除旧密钥。Jev 用于意图和情绪分析；潜台词与回复建议还需要单独配置「生成层」。</p>
  </div>
</template>
