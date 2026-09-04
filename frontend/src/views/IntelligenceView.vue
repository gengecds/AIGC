<template>
  <div class="intel-view">
    <header class="header">
      <h1>情报看板</h1>
      <p class="subtitle">情报站爆款拆解 → 选题库 → 一键推入漫剧管线</p>
    </header>

    <main class="main">
      <!-- 情报能力提示 -->
      <div v-if="!loading && topics.length === 0 && cases.length === 0" class="empty-tip">
        <p>暂无情报案例。请先开启 <code>INTEL_ENABLED</code> 并运行管线，由 <code>IntelligenceAgent</code> 采集/拆解情报源，再回到本看板查看选题。</p>
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
            <div class="topic-source">来源：{{ t.source_title }}（{{ t.platform }}）</div>
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

async function pushTopic(t) {
  const input = t.input || `创作一个「${t.title}」主题的漫剧视频`
  router.push({ name: 'pipeline', query: { t: input, auto: '1' } })
}

onMounted(loadData)
</script>

<style scoped>
.intel-view { max-width: 960px; margin: 0 auto; padding: 2rem 1rem; }
.header { text-align: center; margin-bottom: 2rem; }
.header h1 { font-size: 1.8rem; margin: 0 0 0.3rem; }
.subtitle { color: #666; margin: 0; }
.empty-tip { padding: 2rem 1.5rem; background: #fff; border: 1px dashed #ccc; border-radius: 8px; text-align: center; color: #888; }
.empty-tip code { background: #f0f0f0; padding: 0.1rem 0.4rem; border-radius: 4px; }
.section { margin-top: 2rem; }
.section h2 { font-size: 1.2rem; margin: 0 0 1rem; }
.topic-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1rem; }
.topic-card { background: #fff; border-radius: 10px; padding: 1rem 1.2rem; box-shadow: 0 1px 4px rgba(0,0,0,0.06); display: flex; flex-direction: column; gap: 0.5rem; }
.topic-head { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
.topic-title { font-weight: 700; font-size: 1.05rem; }
.topic-mode { background: #eef4ff; color: #4a90d9; padding: 0.15rem 0.55rem; border-radius: 999px; font-size: 0.75rem; white-space: nowrap; }
.topic-source { font-size: 0.82rem; color: #888; }
.topic-points { margin: 0; padding-left: 1.1rem; font-size: 0.85rem; color: #555; display: flex; flex-direction: column; gap: 0.3rem; }
.btn { margin-top: auto; padding: 0.5rem 1rem; border: none; border-radius: 6px; cursor: pointer; font-size: 0.9rem; }
.btn-push { background: #4a90d9; color: #fff; }
.btn-push:hover { background: #357abd; }
.case-list { display: flex; flex-direction: column; gap: 1rem; }
.case-card { background: #fff; border-radius: 10px; padding: 1.2rem 1.4rem; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.case-head { display: flex; align-items: baseline; justify-content: space-between; gap: 0.8rem; flex-wrap: wrap; }
.case-title { font-weight: 700; font-size: 1.1rem; }
.case-tag { font-size: 0.8rem; color: #888; white-space: nowrap; }
.case-summary { color: #444; margin: 0.6rem 0 0; }
.case-block { margin-top: 0.8rem; }
.case-label { font-size: 0.8rem; font-weight: 700; color: #999; text-transform: uppercase; margin-bottom: 0.3rem; }
.case-block ul { margin: 0; padding-left: 1.1rem; color: #555; font-size: 0.88rem; display: flex; flex-direction: column; gap: 0.3rem; }
</style>
