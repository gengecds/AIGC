<template>
  <div class="pipeline-view">
    <header class="header">
      <h1>AI 漫剧创作平台</h1>
      <p class="subtitle">输入一句话/大纲/小说，自动生成完整漫剧视频</p>
    </header>

    <main class="main">
      <div class="input-section">
        <label class="label">创作输入</label>
        <textarea v-model="userInput" rows="4" class="input-area"
          placeholder="例如：末世题材，主角林峰在丧尸爆发后独自生存..."
        />
        <button class="btn btn-primary" :disabled="running || !userInput.trim()" @click="startPipeline">
          {{ running ? '生成中...' : '开始创作' }}
        </button>
      </div>

      <div v-if="running || results.length > 0" class="progress-section">
        <h2>执行进度</h2>
        <div class="agent-steps">
          <div v-for="(step, i) in agentSteps" :key="i" class="step"
            :class="{
              'step-done': step.status === 'done',
              'step-running': step.status === 'running',
              'step-pending': step.status === 'pending',
              'step-error': step.status === 'error'
            }">
            <span class="step-icon">{{ icons[step.status] }}</span>
            <div class="step-content">
              <div class="step-title">{{ step.title }}</div>
              <div class="step-detail">{{ step.detail }}</div>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import axios from 'axios'

const API_BASE = 'http://localhost:8000/api/v1'
const userInput = ref('')
const running = ref(false)

const agentSteps = reactive([
  { title: '剧本生成', detail: '等待中', status: 'pending' },
  { title: '分镜设计', detail: '等待中', status: 'pending' },
  { title: '角色定妆照', detail: '等待中', status: 'pending' },
  { title: '批量出图', detail: '等待中', status: 'pending' },
  { title: '图生视频', detail: '等待中', status: 'pending' },
  { title: '字幕生成', detail: '等待中', status: 'pending' },
  { title: '视频合成', detail: '等待中', status: 'pending' },
])

const icons = { pending: '⏳', running: '🔄', done: '✅', error: '❌' }

async function startPipeline() {
  running.value = true
  agentSteps.forEach(s => { s.status = 'pending'; s.detail = '等待中' })
  agentSteps[0].status = 'running'
  agentSteps[0].detail = '正在生成剧本...'

  try {
    const resp = await axios.post(`${API_BASE}/pipeline/run`, { input: userInput.value })
    const data = resp.data
    const results = data.results || {}
    const stepMap = ['script_agent', 'storyboard_agent', 'character_agent',
      'image_agent', 'video_agent', 'subtitle_agent', 'compose_agent']

    stepMap.forEach((name, idx) => {
      const r = results[name]
      if (r) {
        agentSteps[idx].status = r.success ? 'done' : 'error'
        agentSteps[idx].detail = r.success ? '完成' : `失败: ${r.error}`
      }
    })
  } catch (err) {
    agentSteps[0].status = 'error'
    agentSteps[0].detail = `请求失败: ${err.message}`
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.pipeline-view { max-width: 800px; margin: 0 auto; padding: 2rem 1rem; }
.header { text-align: center; margin-bottom: 2rem; }
.header h1 { font-size: 1.8rem; margin: 0 0 0.3rem; }
.subtitle { color: #666; margin: 0; }
.input-area { width: 100%; padding: 0.8rem; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 1rem; resize: vertical; box-sizing: border-box; }
.input-area:focus { outline: none; border-color: #4a90d9; }
.btn { margin-top: 0.8rem; padding: 0.7rem 1.5rem; border: none; border-radius: 6px; font-size: 1rem; cursor: pointer; }
.btn-primary { background: #4a90d9; color: white; }
.btn-primary:hover:not(:disabled) { background: #357abd; }
.btn-primary:disabled { background: #b0c4de; cursor: not-allowed; }
.progress-section { margin-top: 2rem; }
.agent-steps { display: flex; flex-direction: column; gap: 0.5rem; }
.step { display: flex; align-items: center; gap: 0.8rem; padding: 0.7rem 1rem; background: #f8f9fa; border-radius: 8px; border: 2px solid transparent; }
.step-done { border-color: #28a745; background: #f0fff0; }
.step-running { border-color: #ffc107; background: #fffef0; }
.step-error { border-color: #dc3545; background: #fff0f0; }
.step-icon { font-size: 1.3rem; }
.step-content { flex: 1; }
.step-title { font-weight: 600; }
.step-detail { font-size: 0.85rem; color: #888; }
</style>
