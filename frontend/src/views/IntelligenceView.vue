<template>
  <div class="intel-view">
    <header class="header">
      <h1>情报看板</h1>
      <p class="subtitle">情报站爆款拆解 → 选题库 → 一键推入漫剧管线</p>
    </header>

    <main class="main">
      <!-- 自动采集控制台：一键全平台采集 → 落盘 source → 重跑拆解 → 刷新看板 -->
      <section class="collect-panel">
        <div class="collect-row">
          <label class="collect-label">关键词</label>
          <input v-model="form.keyword" class="collect-input" type="text" placeholder="如：美食" @keyup.enter="collect()" />
          <label class="collect-label">每平台</label>
          <input v-model.number="form.limit" class="collect-input collect-input-num" type="number" min="1" max="30" />
          <label class="collect-check"><input type="checkbox" v-model="form.with_audio" /> 下载音频</label>
          <label class="collect-check"><input type="checkbox" v-model="form.transcribe" /> ASR转写</label>
        </div>
        <div class="collect-row">
          <div class="collect-platforms">
            <label class="collect-check" v-for="p in platformList" :key="p.value">
              <input type="checkbox" :value="p.value" v-model="platforms" /> {{ p.label }}
            </label>
          </div>
          <div class="collect-actions">
            <button class="btn btn-collect" :disabled="busy" @click="collect()">
              {{ busy ? '采集中…' : '一键全平台采集' }}
            </button>
            <button class="btn btn-ghost" :disabled="busy" @click="rebuild()">仅刷新拆解</button>
          </div>
        </div>
        <div v-if="collectMsg || collectLogs.length" class="collect-msg" :class="{ err: collectErr }">
          <div v-if="collectMsg" class="collect-status">{{ collectMsg }}</div>
          <div v-for="(l, i) in collectLogs" :key="i" class="collect-log">{{ l }}</div>
        </div>
      </section>

      <!-- 情报能力提示 -->
      <div v-if="!loading && topics.length === 0 && cases.length === 0" class="empty-tip">
        <p>暂无情报案例。点击上方「一键全平台采集」抓取爆款素材，再由 <code>IntelligenceAgent</code> 自动拆解入库，回到本看板查看选题。</p>
      </div>

      <!-- 选题库 -->
      <section v-if="topics.length" class="section">
        <h2>选题库（可一键推入管线）</h2>
        <div class="topic-grid">
          <div v-for="t in topics" :key="t.id" class="topic-card">
            <div class="topic-head">
              <span class="topic-title">{{ t.title }}</span>
              <span class="topic-mode">{{ t.mode }}</span>
            </div>
            <div class="topic-source">框架：{{ t.template }} · 平台：{{ t.platform }}</div>
            <ul class="topic-points" v-if="t.takeaways && t.takeaways.length">
              <li v-for="(tk, i) in t.takeaways" :key="i">{{ tk }}</li>
            </ul>
            <button class="btn btn-push" @click="pushTopic(t)">推入管线</button>
          </div>
        </div>
      </section>

      <!-- 拆解案例 -->
      <section v-if="cases.length" class="section">
        <h2>情报案例拆解</h2>
        <div class="case-list">
          <div v-for="c in cases" :key="c.title" class="case-card">
            <div class="case-head">
              <span class="case-title">{{ c.title }}</span>
              <span class="case-tag">{{ c.platform }} · {{ c.creator }}</span>
            </div>
            <p class="case-summary">{{ c.summary }}</p>
            <div v-if="c.highlights && c.highlights.length" class="case-block">
              <div class="case-label">亮点可借鉴</div>
              <ul>
                <li v-for="(h, i) in c.highlights" :key="i">{{ h }}</li>
              </ul>
            </div>
            <div v-if="c.borrow && c.borrow.length" class="case-block">
              <div class="case-label">可复用机制</div>
              <ul>
                <li v-for="(b, i) in c.borrow" :key="i">{{ b }}</li>
              </ul>
            </div>
            <div v-if="c.avoid && c.avoid.length" class="case-block">
              <div class="case-label">避坑清单</div>
              <ul>
                <li v-for="(a, i) in c.avoid" :key="i">{{ a }}</li>
              </ul>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'

const API_BASE = 'http://localhost:8888/api/v1'
const router = useRouter()

const cases = ref([])
const topics = ref([])
const loading = ref(true)

// ── 自动采集控制台状态 ─────────────────────────
const platformList = [
  { value: 'douyin', label: '抖音' },
  { value: 'xiaohongshu', label: '小红书' },
  { value: 'youtube', label: 'YouTube' },
]
const form = ref({ keyword: '美食', limit: 8, with_audio: false, transcribe: false })
const platforms = ref(['douyin', 'xiaohongshu', 'youtube'])
const busy = ref(false)
const collectMsg = ref('')
const collectErr = ref(false)
const collectLogs = ref([])
let collectDone = false

async function loadData() {
  loading.value = true
  try {
    const [cResp, tResp] = await Promise.all([
      axios.get(`${API_BASE}/intel/cases`),
      axios.get(`${API_BASE}/intel/topics`),
    ])
    cases.value = cResp.data.cases || []
    topics.value = tResp.data.topics || []
  } catch (err) {
    console.error('情报看板加载失败:', err)
    cases.value = []
    topics.value = []
  } finally {
    loading.value = false
  }
}

function summarizeSteps(steps) {
  const parts = []
  const dl = steps?.download
  if (dl) parts.push(`采集${dl.ok ? '✓' : '✗'}`)
  const tr = steps?.transcribe
  if (tr) parts.push(`转写${tr.ok ? '✓' : '✗'}`)
  const rc = steps?.rebuild_cases
  if (rc) parts.push(`拆解${rc.ok ? `✓(${rc.count}条)` : '✗'}`)
  return parts.join(' · ')
}

async function applyCollectResult(data) {
  cases.value = data.cases || []
  topics.value = data.topics || []
  const s = data.steps || {}
  const ok = !!data.ok
  collectErr.value = !ok
  collectMsg.value = `完成：${summarizeSteps(s)}${ok ? '' : '（部分步骤失败，详见前端控制台/后端日志）'}`
}

function pushLog(line) {
  // 只保留最近若干行，避免进度刷屏
  collectLogs.value.push(line)
  if (collectLogs.value.length > 8) collectLogs.value.shift()
}

function handleStreamEvent(data) {
  if (!data || !data.type) return
  switch (data.type) {
    case 'status':
      collectMsg.value = data.msg || ''
      break
    case 'log':
      if (data.msg) pushLog(data.msg)
      break
    case 'chrome':
      if (data.ok === false) {
        collectErr.value = true
        collectMsg.value = '浏览器未就绪。若平台未登录，请先在持久浏览器里登录一遍（见操作手册）。'
      }
      break
    case 'done':
      collectDone = true
      busy.value = false
      cases.value = data.cases || []
      topics.value = data.topics || []
      collectErr.value = !data.ok
      if (data.ok) {
        collectMsg.value = `采集完成：落盘 ${data.count ?? 0} 条案例。${data.download_code === 0 ? '' : '（下载步骤有异常，看上方日志）'}`
      } else {
        collectMsg.value = '采集/拆解完成，但存在失败步骤：' + summarizeNext({ ok: data.ok, count: data.count })
      }
      break
  }
}

function summarizeNext(d) {
  const parts = []
  parts.push(`拆解${d.ok ? `✓(${d.count ?? 0}条)` : '✗'}`)
  return parts.join(' · ')
}

async function collect() {
  busy.value = true
  collectErr.value = false
  collectMsg.value = '正在启动全平台采集…'
  collectLogs.value = []
  collectDone = false
  const params = new URLSearchParams({
    keyword: form.value.keyword,
    limit: String(form.value.limit || 8),
    with_audio: form.value.with_audio ? 'true' : 'false',
    transcribe: form.value.transcribe ? 'true' : 'false',
    platforms: platforms.value.join(','),
  })
  try {
    const resp = await fetch(`${API_BASE}/intel/collect/stream?${params.toString()}`)
    if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)
    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        const line = block.split('\n').find((l) => l.startsWith('data:'))
        if (!line) continue
        let data
        try {
          data = JSON.parse(line.slice(5).trim())
        } catch (e) {
          continue
        }
        handleStreamEvent(data)
      }
    }
    if (!collectDone) {
      collectErr.value = true
      collectMsg.value = '连接中断，未收到完成信号（可能后端已重启），请重试。'
    }
  } catch (err) {
    collectErr.value = true
    collectMsg.value = '触发采集失败：' + (err?.message || err)
  } finally {
    busy.value = false
  }
}

async function rebuild() {
  busy.value = true
  collectErr.value = false
  collectMsg.value = '正在重新拆解情报源…'
  try {
    const resp = await axios.post(`${API_BASE}/intel/rebuild`)
    await applyCollectResult(resp.data)
  } catch (err) {
    collectErr.value = true
    collectMsg.value = '刷新拆解失败：' + (err?.response?.data?.detail || err.message)
  } finally {
    busy.value = false
  }
}

async function pushTopic(t) {
  const input = t.input || `创作一个「${t.title}」主题的漫剧视频`
  router.push({ name: 'pipeline', query: { t: input, auto: '1' } })
}

onMounted(loadData)
</script>

<style scoped>
.intel-view { max-width: 960px; margin: 0 auto; padding: 2rem 1rem; min-height: 100%; }
.header { text-align: center; margin-bottom: 2rem; }
.header h1 { font-size: 1.8rem; margin: 0 0 0.3rem; font-family: var(--font-display); letter-spacing: 2px; color: var(--text-0); }
.subtitle { color: var(--text-2); margin: 0; }
.empty-tip { padding: 2rem 1.5rem; background: var(--bg-2); border: 1px dashed var(--line-strong); border-radius: 8px; text-align: center; color: var(--text-2); }
.empty-tip code { background: var(--bg-3); padding: 0.1rem 0.4rem; border-radius: 4px; color: var(--gold-bright); }
.section { margin-top: 2rem; }
.section h2 { font-size: 1.2rem; margin: 0 0 1rem; color: var(--text-1); letter-spacing: 1px; }
.topic-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }
.topic-card { background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--radius); padding: 1rem 1.2rem; display: flex; flex-direction: column; gap: 0.5rem; transition: border-color .15s ease, transform .15s ease; }
.topic-card:hover { border-color: var(--gold); transform: translateY(-2px); }
.topic-head { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
.topic-title { font-weight: 700; font-size: 1.05rem; color: var(--text-0); }
.topic-mode { background: var(--gold-dim); color: var(--gold-bright); padding: 0.15rem 0.55rem; border-radius: 999px; font-size: 0.75rem; white-space: nowrap; }
.topic-source { font-size: 0.82rem; color: var(--text-2); }
.topic-points { margin: 0; padding-left: 1.1rem; font-size: 0.85rem; color: var(--text-1); display: flex; flex-direction: column; gap: 0.3rem; }
.btn { margin-top: auto; padding: 0.5rem 1rem; border: 1px solid var(--line-strong); border-radius: 6px; cursor: pointer; font-size: 0.9rem; background: var(--bg-3); color: var(--text-0); transition: all .18s ease; letter-spacing: 1px; font-family: var(--font-body); }
.btn-push { background: linear-gradient(135deg, #d9a94e, #b9842f); border-color: transparent; color: #1a1408; font-weight: 600; box-shadow: 0 4px 18px rgba(217, 169, 78, 0.22); }
.btn-push:hover { filter: brightness(1.08); color: #1a1408; }
.case-list { display: flex; flex-direction: column; gap: 1rem; }
.case-card { background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--radius); padding: 1.2rem 1.4rem; }
.case-head { display: flex; align-items: baseline; justify-content: space-between; gap: 0.8rem; flex-wrap: wrap; }
.case-title { font-weight: 700; font-size: 1.1rem; color: var(--text-0); }
.case-tag { font-size: 0.8rem; color: var(--gold-bright); white-space: nowrap; }
.case-summary { color: var(--text-1); margin: 0.6rem 0 0; }
.case-block { margin-top: 0.8rem; }
.case-label { font-size: 0.8rem; font-weight: 700; color: var(--text-2); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 0.3rem; }
.case-block ul { margin: 0; padding-left: 1.1rem; color: var(--text-1); font-size: 0.88rem; display: flex; flex-direction: column; gap: 0.3rem; }

/* 自动采集控制台 */
.collect-panel { margin-top: 2rem; padding: 1.1rem 1.3rem; background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--radius); display: flex; flex-direction: column; gap: 0.75rem; }
.collect-row { display: flex; align-items: center; gap: 0.7rem; flex-wrap: wrap; }
.collect-label { font-size: 0.82rem; color: var(--text-2); white-space: nowrap; }
.collect-input { flex: 0 1 180px; min-width: 120px; padding: 0.45rem 0.6rem; background: var(--bg-3); border: 1px solid var(--line-strong); border-radius: 6px; color: var(--text-0); font-size: 0.88rem; font-family: var(--font-body); }
.collect-input:focus { border-color: var(--gold); outline: none; }
.collect-input-num { flex: 0 1 74px; min-width: 0; }
.collect-check { display: inline-flex; align-items: center; gap: 0.32rem; font-size: 0.82rem; color: var(--text-1); cursor: pointer; white-space: nowrap; }
.collect-check input { accent-color: var(--gold); }
.collect-platforms { display: flex; align-items: center; gap: 0.85rem; }
.collect-actions { display: flex; align-items: center; gap: 0.6rem; margin-left: auto; }
.btn-collect { background: linear-gradient(135deg, #d9a94e, #b9842f); border-color: transparent; color: #1a1408; font-weight: 600; box-shadow: 0 4px 18px rgba(217, 169, 78, 0.22); }
.btn-collect:hover { filter: brightness(1.08); color: #1a1408; }
.btn-ghost { background: transparent; color: var(--text-1); }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.collect-msg { margin: 0; font-size: 0.84rem; color: var(--gold-bright); display: flex; flex-direction: column; gap: 0.2rem; }
.collect-msg.err { color: #e06a6a; }
.collect-status { font-weight: 600; }
.collect-log { font-family: var(--font-body); font-size: 0.78rem; color: var(--text-2); white-space: pre-wrap; word-break: break-all; max-height: 9.5rem; overflow-y: auto; }
</style>
