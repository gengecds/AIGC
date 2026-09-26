<!--
  Pipeline 5 步向导式 Review UI（域 D 独立组件）
  布局结构：
    1. 顶部 5 节点进度条（Script → Storyboard → Image → Video → Final）
    2. 中间「当前步骤审查卡片」，每步内容不同
    3. 底部左右按钮：上一步 / 继续
  数据来源：SSE EventSource 订阅 /pipeline/events?run_id=<真实管线 run_id>
    事件类型：progress / checkpoint / done / error / end
-->
<template>
  <div class="review-page">
    <!-- ========== 页面标题 ========== -->
    <header class="page-header">
      <h1>Pipeline 审查向导</h1>
      <p class="subtitle">
        共 5 步，实时接收后端 SSE 进度 · 每步可编辑确认
        <span v-if="done" class="tag-done">✅ 已全部完成</span>
      </p>
    </header>

    <!-- ========== 真实创作运行栏：一句话启动真实管线 / 恢复进行中 ========== -->
    <div class="runbar">
      <input
        v-model="newIdea"
        class="runbar-input"
        type="text"
        placeholder="输入一句话/题材，例如：末世废土下少年独自寻找妹妹…"
        @keyup.enter="startRealRun"
      />
      <select v-model="runStyle" class="runbar-select" :disabled="!styles.length">
        <option v-for="s in styles" :key="s" :value="s">{{ s }}</option>
      </select>
      <button class="runbar-btn" @click="startRealRun">▶ 开始真实创作</button>
      <button class="runbar-ghost" @click="loadActive">↻ 恢复进行中</button>
      <span class="real-tag" :title="runId">真实运行中</span>
    </div>

    <!-- ========== 5 节点进度条 ========== -->
    <div class="stepper">
      <div
        v-for="(node, idx) in stepNodes"
        :key="idx"
        class="step-node"
        :class="stepClass(idx)"
        @click="jumpToStep(idx)"
      >
        <div class="node-circle">{{ stepIcon(idx) }}</div>
        <div class="node-label">{{ node.label }}</div>
        <!-- 连接线：除了最后一个节点都画 -->
        <div v-if="idx < stepNodes.length - 1" class="node-connector">
          <div
            class="connector-fill"
            :style="{ width: step > idx ? '100%' : '0%' }"
          />
        </div>
      </div>
      <!-- 整体百分比进度条（细线） -->
      <div class="overall-bar">
        <div class="overall-bar-fill" :style="{ width: overallPercent + '%' }" />
      </div>
    </div>

    <!-- ========== 当前步骤审查卡片 ========== -->
    <section class="card">
      <!-- 步骤标题栏 -->
      <div class="card-header">
        <div>
          <span class="step-badge">Step {{ step + 1 }} / 5</span>
          <h2 class="card-title">{{ stepNodes[step].label }}</h2>
          <p class="card-subtitle" v-if="currentMessage">{{ currentMessage }}</p>
        </div>
        <div v-if="isLoading(step)" class="loading-inline">
          <div class="spinner" />
          <span>生成中…</span>
        </div>
      </div>

      <!-- ========== 空状态 / Loading ========== -->
      <div v-if="isLoading(step)" class="empty-state">
        <div class="empty-icon">⏳</div>
        <div class="empty-text">生成中，等待 LLM/模型返回…</div>
        <div class="thin-loading">
          <div class="thin-loading-bar" />
        </div>
      </div>

      <!-- ========== Step 0：剧本审查 ========== -->
      <div v-else-if="step === 0" class="step-content">
        <div class="field-row">
          <label class="field-label">剧本标题</label>
          <input
            v-model="scriptTitle"
            type="text"
            class="field-input"
            placeholder="剧本标题"
          />
        </div>
        <div class="field-row">
          <label class="field-label">剧本正文（可编辑修改）</label>
          <textarea
            v-model="scriptText"
            rows="14"
            class="field-textarea"
            placeholder="剧本内容将在这里显示…"
          />
        </div>
        <div class="hint">
          💡 提示：修改后点击右下角「继续」，修改稿会作为下一阶段（分镜）的输入。
        </div>
      </div>

      <!-- ========== Step 1：分镜审查 ========== -->
      <div v-else-if="step === 1" class="step-content">
        <div class="shots-grid">
          <div
            v-for="(shot, idx) in storyboardShots"
            :key="idx"
            class="shot-card"
          >
            <div class="shot-header">
              <span class="shot-id">{{ shot.shot_id }}</span>
              <span class="shot-duration">{{ shot.duration_sec || '?' }}s</span>
            </div>
            <div class="shot-field">
              <span class="shot-key">景别</span>
              <span class="shot-val">{{ shot.景别 || '—' }}</span>
            </div>
            <div class="shot-field">
              <span class="shot-key">对白</span>
              <span class="shot-val shot-dialogue">{{ shot.对白 || '（无）' }}</span>
            </div>
            <div class="shot-field">
              <span class="shot-key">画面 Prompt</span>
              <textarea
                v-model="shot.prompt"
                rows="5"
                class="shot-prompt"
                placeholder="AI 出图 Prompt"
              />
            </div>
          </div>
        </div>
        <div class="hint">
          💡 左右两列分别对应两个关键分镜，可直接修改 Prompt 后点击「继续」。
        </div>
      </div>

      <!-- ========== Step 2：角色图审查 ========== -->
      <div v-else-if="step === 2" class="step-content">
        <div class="chars-grid">
          <div
            v-for="(char, idx) in characterList"
            :key="idx"
            class="char-card"
          >
            <div class="char-image-wrap">
              <!-- 真实定妆照；旁白/声线类角色本就不出镜，直接说明而不是摆随机占位图 -->
              <img
                v-if="char.url"
                :src="mediaUrl(char.url)"
                :alt="char.suggested_name || `角色${idx + 1}`"
                class="char-image"
                loading="lazy"
              />
              <div v-else class="char-image char-image-empty">该角色不出现在画面中</div>
            </div>
            <div class="char-footer">
              <label class="char-label">角色名</label>
              <select v-model="char.assigned_name" class="char-select">
                <option value="">请选择角色…</option>
                <option v-for="name in characterNameOptions" :key="name" :value="name">
                  {{ name }}
                </option>
              </select>
              <div class="char-suggest" v-if="char.suggested_name">
                建议：<em>{{ char.suggested_name }}</em>
              </div>
            </div>
          </div>
        </div>
        <div class="hint">
          💡 共 {{ characterList.length }} 张角色定妆照，每张下方选择对应的角色名（可覆盖建议名）。
        </div>

        <!-- 分镜出图（image_agent 断点带来）：逐镜展示，点图可看原图 -->
        <div v-if="shotImages.length" class="shot-images-block">
          <div class="shot-images-title">分镜出图 · {{ shotImages.length }} 张</div>
          <div class="shot-images-grid">
            <figure v-for="it in shotImages" :key="it.shot_id" class="shot-image-card">
              <a :href="mediaUrl(it.url)" target="_blank" rel="noopener">
                <img :src="mediaUrl(it.url)" :alt="`镜头 ${it.shot_id}`" loading="lazy" />
              </a>
              <figcaption>镜头 {{ it.shot_id }}</figcaption>
            </figure>
          </div>
        </div>
      </div>

      <!-- ========== Step 3：视频预览 ========== -->
      <div v-else-if="step === 3" class="step-content">
        <div class="video-wrap">
          <!-- 占位视频 URL（W3Schools 公共示例 mp4） -->
          <video
            v-if="videoUrl"
            controls
            class="preview-video"
            :poster="videoPoster"
            preload="metadata"
          >
            <source :src="mediaUrl(videoUrl)" type="video/mp4" />
            您的浏览器不支持 HTML5 video 标签。
          </video>
        </div>
        <div class="video-meta">
          <div class="meta-item">
            <span class="meta-key">时长</span>
            <span class="meta-val">{{ videoDuration }} 秒</span>
          </div>
          <div class="meta-item">
            <span class="meta-key">分辨率</span>
            <span class="meta-val">{{ videoResolution }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-key">字幕</span>
            <span class="meta-val">{{ videoHasSubtitle ? '已内嵌' : '无' }}</span>
          </div>
        </div>
        <div class="hint">
          💡 预览成片效果，确认无误后进入「最终发布」步骤。
        </div>
      </div>

      <!-- ========== Step 4：最终发布 ========== -->
      <div v-else-if="step === 4" class="step-content">
        <div class="final-meta">
          <div class="final-meta-row">
            <span class="final-key">成片标题</span>
            <span class="final-val">{{ finalPayload.title || '—' }}</span>
          </div>
          <div class="final-meta-row">
            <span class="final-key">总时长</span>
            <span class="final-val">{{ finalPayload.total_duration || '—' }}</span>
          </div>
          <div class="final-meta-row">
            <span class="final-key">场景数</span>
            <span class="final-val">{{ finalPayload.scenes || 0 }} 个</span>
          </div>
          <div class="final-meta-row">
            <span class="final-key">输出规格</span>
            <span class="final-val">{{ finalPayload.output_size || '—' }}</span>
          </div>
          <div class="final-meta-row">
            <span class="final-key">文件大小</span>
            <span class="final-val">{{ finalPayload.file_size || '—' }}</span>
          </div>
          <div class="final-meta-row">
            <span class="final-key">生成时间</span>
            <span class="final-val">{{ finalPayload.created_at || '—' }}</span>
          </div>
        </div>

        <div class="final-buttons">
          <button class="big-btn big-btn-ghost" @click="handleExportObsidian">
            <div class="big-btn-icon">📝</div>
            <div class="big-btn-text">
              <div class="big-btn-title">导出到 Obsidian</div>
              <div class="big-btn-sub">写入本地知识库，沉淀创作素材</div>
            </div>
          </button>
          <button class="big-btn big-btn-primary" @click="handleDownload">
            <div class="big-btn-icon">⬇️</div>
            <div class="big-btn-text">
              <div class="big-btn-title">下载成片</div>
              <div class="big-btn-sub">获取 MP4 文件到本地</div>
            </div>
          </button>
        </div>
      </div>
    </section>

    <!-- ========== 底部操作按钮 ========== -->
    <footer class="action-bar">
      <button
        class="action-btn action-prev"
        :disabled="step === 0"
        @click="handlePrev"
      >
        ← 上一步
      </button>
      <div class="action-center">
        <span v-if="sseError" class="error-text">
          ⚠️ 连接中断：{{ sseError }}
        </span>
        <span v-else-if="awaitingReview" class="review-tag">
          ⏸️ 管线等待你确认：查看内容后点「确认继续」放行，或「拒绝跳过」
        </span>
        <span v-else class="status-text">
          后端 SSE：{{ eventCount }} 条事件已接收
        </span>
      </div>
      <div class="action-right">
        <button
          v-if="awaitingReview"
          class="action-btn action-reject"
          @click="handleReject"
        >
          拒绝并跳过
        </button>
        <button
          class="action-btn action-next"
          :disabled="isLoading(step) || (step === 4 && !done && !awaitingReview)"
          @click="handleNext"
        >
          {{ step === 4 && !awaitingReview ? '完成 🎉' : '确认继续 ⏩' }}
        </button>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
/**
 * Pipeline 5 步向导 - 脚本区
 * 关键职责：
 *   1. mounted 时建立 EventSource 连接到 SSE 端点
 *   2. 监听 progress / done / error / end 事件，自动推进进度条、填充卡片
 *   3. 手动上一步 / 继续按钮（进度到了才能点下一步，避免跳步）
 *   4. beforeUnmount 关闭 EventSource，防止内存泄漏
 */
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'

// ── 真实运行来源：URL 参数 ?run_id=xxx 订阅真实管线事件；缺省尝试恢复最近真实运行 ──
const API_BASE = 'http://localhost:8888'
function sseUrl(run_id: string): string {
  return `${API_BASE}/pipeline/events?run_id=${encodeURIComponent(run_id)}`
}
// 后端返回的图片/视频地址是站内相对路径（/storage/output/… 、/comfyui-output/…），
// 但页面可能跑在 Vite dev server(5173) 上——那里没有这些静态挂载，直接用会全部 404。
// 统一补上后端 origin；已是绝对地址（http/https）的原样返回。
function mediaUrl(u?: string): string {
  const s = (u || '').trim()
  if (!s) return ''
  return s.startsWith('/') ? `${API_BASE}${s}` : s
}
const runParam = new URLSearchParams(location.search).get('run_id') || ''
const runId = ref<string>(runParam.trim() ? runParam.trim() : '')

// ── 真实创作运行栏：输入一句话 + 风格 → POST /pipeline/run ──
const newIdea = ref<string>('')
const runStyle = ref<string>('')
const styles = ref<string[]>([])

// ── 情报站「推入管线」参数：t=选题输入，auto=1 进入页面后自动启动 ──
{
  const qs = new URLSearchParams(location.search)
  const topic = (qs.get('t') || '').trim()
  if (topic) newIdea.value = topic
}
const autoStart = new URLSearchParams(location.search).get('auto') === '1'

// ── 5 步定义（对应后端 step 0-4）──
const stepNodes = [
  { label: '剧本审查', key: 'script' },
  { label: '分镜审查', key: 'storyboard' },
  { label: '角色图审查', key: 'image' },
  { label: '视频预览', key: 'video' },
  { label: '最终发布', key: 'final' },
]

// ── 响应式状态 ────────────────────────────────────────────────
const step = ref<number>(0)          // 当前步骤 0-4
const overallPercent = ref<number>(0) // 总进度百分比 0-100
const eventCount = ref<number>(0)     // 已接收事件数量（调试信息）
const done = ref<boolean>(false)      // 是否收到 done 事件
const sseError = ref<string>('')      // SSE 错误信息
// 真实模式：管线正停在某个「审核断点」等待人工确认（点「继续」= 批准放行）
const awaitingReview = ref<boolean>(false)

// 记录哪些 step 已收到 payload（用于判断 loading vs 内容）
const stepReady = reactive<Record<number, boolean>>({
  0: false, 1: false, 2: false, 3: false, 4: false,
})

// 当前步骤的 message（显示在卡片副标题）
const stepMessages = reactive<Record<number, string>>({
  0: '', 1: '', 2: '', 3: '', 4: '',
})
const currentMessage = computed(() => stepMessages[step.value] || '')

// ── Step 0：剧本 ──────────────────────────────────────────────
const scriptTitle = ref<string>('')
const scriptText = ref<string>('')

// ── Step 1：分镜 ──────────────────────────────────────────────
interface Shot {
  shot_id: string
  景别?: string
  对白?: string
  prompt: string
  duration_sec?: number
}
const storyboardShots = reactive<Shot[]>([])

// ── Step 2：角色图 ────────────────────────────────────────────
interface CharacterItem {
  image_seed: string
  // 真实定妆照的浏览器可访问 URL（后端 _wiz_media_url 已转好）；空则回退占位图
  url?: string
  suggested_name?: string
  assigned_name: string
}
const characterList = reactive<CharacterItem[]>([])
// 下拉框角色名选项：由后端返回的真实角色卡填充（勿写死演示名，否则界面会显示假角色）
const characterNameOptions = reactive<string[]>([])

// ── Step 2：分镜出图（image_agent 断点附带，一镜一行）──────────
interface ShotImageItem {
  shot_id: string
  url: string
  image_path?: string
}
const shotImages = reactive<ShotImageItem[]>([])

// ── Step 3：视频预览 ──────────────────────────────────────────
const videoUrl = ref<string>('')
const videoPoster = ref<string>('https://picsum.photos/seed/videoposter/1280/720')
const videoDuration = ref<number | string>('—')
const videoResolution = ref<string>('—')
const videoHasSubtitle = ref<boolean>(false)

// ── Step 4：最终发布 ──────────────────────────────────────────
const finalPayload = reactive<Record<string, any>>({})

// ── EventSource 实例引用（供卸载时 close） ─────────────────────
let eventSource: EventSource | null = null

// ── 辅助函数：判断某步是否还在 loading ────────────────────────
function isLoading(idx: number): boolean {
  // done 事件到来前，最后一个到达的 step 的下一步是 loading
  return !stepReady[idx]
}

// ── 辅助函数：节点样式类 ───────────────────────────────────────
function stepClass(idx: number) {
  if (stepReady[idx] || (done.value && idx <= 4)) {
    return { 'is-done': true, 'is-current': idx === step.value }
  }
  if (idx === step.value) {
    return { 'is-current': true }
  }
  return { 'is-pending': true }
}

// ── 辅助函数：节点图标 ─────────────────────────────────────────
function stepIcon(idx: number): string | number {
  if (stepReady[idx]) return '✓'
  return idx + 1
}

// ── 手动跳步（允许回到已就绪的上一步查看） ────────────────────
function jumpToStep(idx: number) {
  // 只允许跳到「已就绪」或「当前」或「当前 - 1（已就绪）」的步骤
  if (stepReady[idx] || idx === step.value) {
    step.value = idx
  }
}

// ── 按钮：上一步 ───────────────────────────────────────────────
function handlePrev() {
  if (step.value > 0) {
    step.value -= 1
  }
}

// ── 按钮：继续 / 完成 ──────────────────────────────────────────
// 真实模式：「确认继续」= 调用 /approve 放行审核断点，让管线继续（不毁数据）
function handleNext() {
  // 停在审核断点时，点「确认继续」= 批准放行
  if (awaitingReview.value) {
    approveCurrent()
    return
  }
  if (step.value < 4 && stepReady[step.value + 1]) {
    step.value += 1
  }
}

// ── 真实模式：批准当前审核断点（放行管线，不修改 checkpoints 数据） ──
async function approveCurrent() {
  const rid = runId.value
  try {
    const resp = await fetch(`${API_BASE}/api/v1/pipeline/approve/${encodeURIComponent(rid)}`, {
      method: 'POST',
    })
    const j = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      sseError.value = `批准失败：${j.detail || resp.status}`
      return
    }
    awaitingReview.value = false
    stepMessages[step.value] = '✅ 已确认，管线继续推进中…'
  } catch (err) {
    sseError.value = `批准失败：${(err as Error).message}`
  }
}

// ── 真实模式：拒绝当前审核断点（跳过该步骤继续） ───────────────
async function handleReject() {
  const rid = runId.value
  try {
    const resp = await fetch(`${API_BASE}/api/v1/pipeline/reject/${encodeURIComponent(rid)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    const j = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      sseError.value = `拒绝失败：${j.detail || resp.status}`
      return
    }
    awaitingReview.value = false
    stepMessages[step.value] = '⏭️ 已拒绝，管线跳过此步骤继续…'
  } catch (err) {
    sseError.value = `拒绝失败：${(err as Error).message}`
  }
}

// ── 最终页大按钮 ───────────────────────────────────────────────
function handleExportObsidian() {
  // 创作笔记已由管线收尾自动生成（pipeline/notes），此处仅为提示，不重复触发
  alert('📝 已生成创作笔记并保存到项目 storage；写入 Obsidian 知识库为后续能力')
}
function handleDownload() {
  if (!videoUrl.value) {
    alert('成片尚未生成，无法下载')
    return
  }
  const a = document.createElement('a')
  a.href = mediaUrl(videoUrl.value)
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// ── SSE 事件处理：progress ─────────────────────────────────────
function handleProgressEvent(raw: any) {
  const stepIdx = Number(raw.step)
  if (Number.isNaN(stepIdx) || stepIdx < 0 || stepIdx > 4) return

  // 更新总进度百分比
  if (typeof raw.percent === 'number') {
    overallPercent.value = Math.min(100, Math.max(0, raw.percent))
  }

  // 记录当前步骤的提示文字
  if (typeof raw.message === 'string') {
    stepMessages[stepIdx] = raw.message
  }

  // 后端 on_review 会把断点译为「⏸️ 等待审核：…」的 progress 事件，
  // 前端据此置 awaitingReview，让「确认继续」去调 /approve（此事件通过 /pipeline/events 可达）
  if (typeof raw.message === 'string' && raw.message.includes('等待审核')) {
    awaitingReview.value = true
  }

  // 填充对应 step 的 payload
  const payload = raw.payload || {}

  // 只有「带内容」的 payload 才点亮步骤：真实运行中模型执行期间会收到
  // 无 payload 的「进行中」事件（仅刷新提示文案），不能提前点亮成空白卡片。
  const payloadHasKeys = !!payload && typeof payload === 'object'
    && !Array.isArray(payload) && Object.keys(payload).length > 0

  // —— Step 0：剧本 ——
  if (stepIdx === 0 && payloadHasKeys) {
    scriptTitle.value = payload.title || ''
    scriptText.value = payload.script || ''
    stepReady[0] = true
  }

  // —— Step 1：分镜 ——
  if (stepIdx === 1 && payload && Array.isArray(payload.shots)) {
    storyboardShots.splice(0, storyboardShots.length, ...payload.shots.map((s: any) => ({
      shot_id: s.shot_id || '',
      景别: s.景别 || s.scene_type || '',
      对白: s.对白 || s.dialogue || '',
      prompt: s.prompt || '',
      duration_sec: s.duration_sec,
    })))
    stepReady[1] = true
  }

  // —— Step 2：角色图 / 分镜出图 ——
  if (stepIdx === 2 && payload && Array.isArray(payload.characters)) {
    characterList.splice(0, characterList.length, ...payload.characters.map((c: any) => ({
      image_seed: c.image_seed || ('char_' + Math.random().toString(36).slice(2, 7)),
      url: c.url || '',
      suggested_name: c.suggested_name || '',
      assigned_name: '',
    })))
    // 下拉选项同步为真实角色名，避免出现写死的演示名
    characterNameOptions.splice(0, characterNameOptions.length,
      ...payload.characters
        .map((c: any) => c.assigned_name || c.suggested_name || '')
        .filter((n: string) => !!n))
    stepReady[2] = true
  }
  // 分镜出图：image_agent 断点附带的 images（一镜一行，带已转好的 url）
  if (stepIdx === 2 && payload && Array.isArray(payload.images)) {
    shotImages.splice(0, shotImages.length, ...payload.images
      .filter((it: any) => it && it.url)
      .map((it: any) => ({
        shot_id: String(it.shot_id ?? ''),
        url: it.url,
        image_path: it.image_path || '',
      })))
    if (shotImages.length) stepReady[2] = true
  }

  // —— Step 3：视频预览 ——
  if (stepIdx === 3 && payloadHasKeys) {
    videoUrl.value = payload.video_url || ''
    videoDuration.value = payload.duration_sec ?? '—'
    videoResolution.value = payload.resolution || '—'
    videoHasSubtitle.value = !!payload.has_subtitle
    stepReady[3] = true
  }

  // —— Step 4：最终发布 ——
  if (stepIdx === 4 && payloadHasKeys) {
    Object.keys(payload).forEach(k => { finalPayload[k] = payload[k] })
    stepReady[4] = true
  }

  // 自动推进：把当前 step 推到「最新已就绪且未 done 的那个」
  // 策略：找到最大的 ready 的 step，如果它 > 当前 step，就前进
  let maxReady = -1
  for (let i = 0; i < 5; i++) {
    if (stepReady[i]) maxReady = i
  }
  if (maxReady >= 0 && maxReady > step.value) {
    step.value = maxReady
  }
}

// ── SSE 事件处理：done ─────────────────────────────────────────
function handleDoneEvent(raw: any) {
  done.value = true
  awaitingReview.value = false
  overallPercent.value = 100
  if (typeof raw.message === 'string') {
    stepMessages[4] = raw.message
  }
  // 如果 Step 4 还没被标记 ready（done payload 可能为 null），确保最后一步可点
  stepReady[4] = true
  step.value = 4
}

// ── 建立 SSE 订阅 ──────────────────────────────────────────────
function startSSE() {
  try {
    // 真实运行与演示共用同一个处理器：仅把订阅的 run_id 换掉
    eventSource = new EventSource(sseUrl(runId.value), { withCredentials: false })

    // —— progress 事件（主事件，每个步骤完成推一次）——
    eventSource.addEventListener('progress', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        eventCount.value += 1
        handleProgressEvent(data)
      } catch (err) {
        console.warn('[SSE] progress 解析失败:', err)
      }
    })

    // —— checkpoint 事件（和 progress 相同处理方式，兼容未来扩展）——
    eventSource.addEventListener('checkpoint', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        eventCount.value += 1
        handleProgressEvent(data)
      } catch (err) {
        console.warn('[SSE] checkpoint 解析失败:', err)
      }
    })

    // —— done 事件（管线完成）——
    eventSource.addEventListener('done', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        eventCount.value += 1
        handleDoneEvent(data)
        // done 之后关闭连接，避免浏览器自动重连
        eventSource?.close()
      } catch (err) {
        console.warn('[SSE] done 解析失败:', err)
      }
    })

    // —— error 事件（后端错误）——
    eventSource.addEventListener('error', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        eventCount.value += 1
        sseError.value = data.message || '未知错误'
        done.value = true
        eventSource?.close()
      } catch {
        // 非 JSON 的 error 是网络级错误，单独处理
        sseError.value = 'EventSource 网络错误，已断开'
        eventSource?.close()
      }
    })

    // —— end 事件（后端主动结束：demo_completed / done / error）——
    eventSource.addEventListener('end', () => {
      eventSource?.close()
    })

    // —— 兜底 onmessage（如果后端发的是默认 unnamed 事件也能接住）——
    eventSource.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data)
        eventCount.value += 1
        const type = data.type
        if (type === 'progress' || type === 'checkpoint') {
          handleProgressEvent(data)
        } else if (type === 'done') {
          handleDoneEvent(data)
        } else if (type === 'error') {
          sseError.value = data.message || '未知错误'
        }
      } catch (err) {
        console.warn('[SSE] onmessage 解析失败:', err)
      }
    }

    // —— 网络级错误（浏览器 EventSource 通用 error handler）——
    eventSource.onerror = () => {
      if (!sseError.value) {
        sseError.value = '网络连接异常，请检查后端是否启动'
      }
    }
  } catch (err) {
    sseError.value = `EventSource 创建失败：${(err as Error).message}`
  }
}

// ── 真实创作：POST /run 启动真实管线 ───────────────────────────
function resetRunState() {
  // 清空上一轮展示的内容，防止串场（真实运行/演示切换时调用）
  step.value = 0
  done.value = false
  awaitingReview.value = false
  sseError.value = ''
  eventCount.value = 0
  overallPercent.value = 0
  for (const i of [0, 1, 2, 3, 4]) stepReady[i] = false
  scriptTitle.value = ''
  scriptText.value = ''
  storyboardShots.splice(0, storyboardShots.length)
  characterList.splice(0, characterList.length)
  shotImages.splice(0, shotImages.length)
  videoUrl.value = ''
}

async function startRealRun() {
  const text = newIdea.value.trim()
  if (text.length < 2) {
    alert('请先输入创作想法（一句话/题材，至少 2 个字）')
    return
  }
  const style = runStyle.value || '写实风格'
  try {
    const resp = await fetch(`${API_BASE}/api/v1/pipeline/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: text, style: [style], resume: false }),
    })
    if (!resp.ok) {
      const j = await resp.json().catch(() => ({}))
      sseError.value = `启动失败：${j.detail || resp.status}`
      return
    }
    const j = await resp.json()
    // 切换到真实 run_id 并重连 SSE（后端事件总线会先回放历史，再增量推送）
    runId.value = j.pipeline_id
    history.replaceState(null, '', `/pipeline?run_id=${encodeURIComponent(runId.value)}`)
    resetRunState()
    eventSource?.close()
    startSSE()
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } catch (err) {
    sseError.value = `启动失败：${(err as Error).message}`
  }
}

async function loadLatestWizard(): Promise<boolean> {
  // 回填最近一次落盘的创作结果：管线跑完后该 run 已从 /active 移除、事件总线也在
  // 后端重启时清空，刷新页面会拿不到任何数据（角色定妆照与分镜出图因此看不到）。
  // 后端 /wizard/latest 直接把各 step 的 payload 从 checkpoint 转好返回。
  try {
    const resp = await fetch(`${API_BASE}/api/v1/pipeline/wizard/latest`)
    if (!resp.ok) return false
    const j = await resp.json()
    const steps = Object.keys(j || {})
      .map(Number)
      .filter(n => Number.isInteger(n) && n >= 0 && n <= 4)
      .sort((a, b) => a - b)
    if (!steps.length) return false
    // 复用 SSE 的填充逻辑：构造同构事件逐个回填，再补一个 done 收尾
    for (const idx of steps) {
      handleProgressEvent({
        type: 'progress',
        step: idx,
        payload: j[String(idx)],
        message: '已回填最近一次创作结果',
        percent: 100,
      })
    }
    handleDoneEvent({ type: 'done', message: '已加载最近一次创作结果' })
    return true
  } catch {
    return false
  }
}

async function loadActive(silent = false) {
  // 从后端 /active 找回最近一条进行中/等待审核的管线并订阅（页面刷新后恢复审核）
  try {
    const resp = await fetch(`${API_BASE}/api/v1/pipeline/active`)
    if (!resp.ok) return
    const j = await resp.json()
    const entry = (j.active || []).find(
      (a: any) => a.status === 'review' || a.status === 'running' || a.status === 'queued'
    )
    if (!entry) {
      // 没有进行中的管线：回填最近一次结果，否则页面一片空白
      const ok = await loadLatestWizard()
      if (!ok && !silent) alert('当前没有进行中或等待审核的管线')
      return
    }
    runId.value = entry.pipeline_id
    history.replaceState(null, '', `/pipeline?run_id=${encodeURIComponent(runId.value)}`)
    resetRunState()
    eventSource?.close()
    startSSE()
  } catch (err) {
    sseError.value = `恢复失败：${(err as Error).message}`
  }
}

// ── 生命周期 ───────────────────────────────────────────────────
onMounted(() => {
  // 预载风格列表（供「开始真实创作」下拉选择）
  fetch(`${API_BASE}/api/v1/pipeline/styles`)
    .then(r => (r.ok ? r.json() : {}))
    .then((j: any) => {
      const names = ((j && j.styles) || []).map((s: any) => (typeof s === 'string' ? s : s?.name)).filter(Boolean)
      styles.value = names
      if (!runStyle.value && names.length) runStyle.value = names[0]
    })
    .catch(() => {})
  // 默认真实链路：有 run_id 则订阅该运行；否则尝试恢复最近进行中的真实管线。
  // 不再有 __demo__ 演示流，无运行时不展示任何假数据。
  if (runId.value) {
    startSSE()
  } else {
    loadActive(true)
  }
  // 情报站「推入管线」自动启动：t 已填入 newIdea，auto=1 时直接开始真实创作
  if (autoStart && newIdea.value.trim().length >= 2) {
    setTimeout(() => startRealRun(), 300)
  }
})

onBeforeUnmount(() => {
  // 组件卸载前必须关闭，否则浏览器会一直保持连接并自动重连
  eventSource?.close()
  eventSource = null
})
</script>

<style scoped>
/* ==================== 全局页面容器 ==================== */
.review-page {
  width: 920px;
  max-width: 100%;
  margin: 0 auto;
  padding: 48px 60px 80px;
  box-sizing: border-box;
  font-family: var(--font-body), "PingFang SC", "Microsoft YaHei", sans-serif;
  color: var(--text-0);
  background:
    radial-gradient(1200px 500px at 50% -10%, rgba(217,169,78,0.05), transparent 60%),
    var(--bg-0);
  min-height: 100vh;
}

.page-header { margin-bottom: 32px; }
.page-header h1 {
  margin: 0 0 6px;
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 600;
  letter-spacing: 2px;
  color: var(--text-0);
}
.subtitle {
  margin: 0;
  color: var(--text-2);
  font-size: 14px;
}
.tag-done {
  display: inline-block;
  margin-left: 10px;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--gold-dim);
  color: var(--gold-bright);
  font-size: 12px;
  font-weight: 600;
}

/* ==================== 5 节点 Stepper ==================== */
.stepper {
  position: relative;
  margin-bottom: 32px;
}
.step-node {
  display: inline-flex;
  align-items: center;
  vertical-align: top;
  cursor: default;
  width: calc(100% / 5 - 2px);  /* 5 个节点均分宽度 */
  position: relative;
}
.node-circle {
  width: 38px; height: 38px;
  border-radius: 50%;
  background: var(--bg-3);
  color: var(--text-2);
  font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
  border: 2px solid var(--line-strong);
  transition: all 0.3s ease;
  flex-shrink: 0;
}
.node-label {
  margin-left: 10px;
  font-size: 13px;
  color: var(--text-2);
  font-weight: 500;
  white-space: nowrap;
}
/* 连接线（节点之间）*/
.node-connector {
  position: absolute;
  top: 18px;
  left: 38px;
  width: calc(100% - 38px);
  height: 2px;
  background: var(--bg-3);
  z-index: 0;
}
.connector-fill {
  height: 100%;
  background: var(--gold);
  transition: width 0.5s ease;
}
/* 已完成节点 */
.step-node.is-done .node-circle {
  background: var(--green);
  color: #08130e;
  border-color: var(--green);
  box-shadow: 0 0 10px rgba(76,191,138,0.35);
}
.step-node.is-done .node-label { color: var(--green); }
/* 当前节点 */
.step-node.is-current:not(.is-done) .node-circle {
  background: var(--bg-2);
  color: var(--gold-bright);
  border-color: var(--gold);
  box-shadow: 0 0 0 4px var(--gold-dim);
  transform: scale(1.05);
}
.step-node.is-current:not(.is-done) .node-label {
  color: var(--gold-bright);
  font-weight: 600;
}
/* 整体细线进度 */
.overall-bar {
  margin-top: 20px;
  height: 4px;
  background: var(--bg-3);
  border-radius: 999px;
  overflow: hidden;
}
.overall-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #b9842f, var(--gold-bright));
  border-radius: 999px;
  box-shadow: 0 0 12px rgba(217,169,78,0.5);
  transition: width 0.5s ease;
}

/* ==================== 审查卡片 ==================== */
.card {
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
  padding: 28px 32px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line);
}
.step-badge {
  display: inline-block;
  padding: 3px 10px;
  background: var(--gold-dim);
  color: var(--gold-bright);
  font-size: 12px;
  font-weight: 600;
  border-radius: 6px;
  margin-bottom: 8px;
}
.card-title {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 700;
  color: var(--text-0);
}
.card-subtitle {
  margin: 0;
  color: var(--text-2);
  font-size: 13px;
}
.loading-inline {
  display: flex; align-items: center; gap: 8px;
  color: var(--gold-bright);
  font-size: 13px;
  font-weight: 500;
}
.spinner {
  width: 14px; height: 14px;
  border-radius: 50%;
  border: 2px solid var(--gold-dim);
  border-top-color: var(--gold-bright);
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ==================== 空状态 ==================== */
.empty-state {
  padding: 48px 16px;
  text-align: center;
  color: var(--text-2);
}
.empty-icon { font-size: 40px; margin-bottom: 12px; opacity: 0.7; }
.empty-text { font-size: 15px; margin-bottom: 20px; }
.thin-loading {
  width: 260px;
  margin: 0 auto;
  height: 3px;
  background: var(--bg-3);
  border-radius: 999px;
  overflow: hidden;
}
.thin-loading-bar {
  height: 100%;
  width: 30%;
  background: linear-gradient(90deg, #b9842f, var(--gold-bright), #b9842f);
  background-size: 200% 100%;
  border-radius: 999px;
  animation: thin-shine 1.5s ease-in-out infinite;
}
@keyframes thin-shine {
  0%   { transform: translateX(-100%); }
  100% { transform: translateX(400%); }
}

/* ==================== Step 通用表单样式 ==================== */
.step-content { }
.field-row { margin-bottom: 18px; }
.field-label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}
.field-input {
  width: 100%;
  padding: 10px 14px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  font-size: 14px;
  background: var(--bg-1);
  color: var(--text-0);
  box-sizing: border-box;
  transition: border-color 0.15s;
}
.field-input::placeholder { color: var(--text-2); }
.field-input:focus {
  outline: none;
  border-color: var(--gold);
  box-shadow: 0 0 0 3px var(--gold-dim);
}
.field-textarea {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.7;
  background: var(--bg-1);
  color: var(--text-0);
  box-sizing: border-box;
  resize: vertical;
  font-family: inherit;
  transition: border-color 0.15s;
}
.field-textarea::placeholder { color: var(--text-2); }
.field-textarea:focus {
  outline: none;
  border-color: var(--gold);
  box-shadow: 0 0 0 3px var(--gold-dim);
}
.hint {
  margin-top: 18px;
  padding: 10px 14px;
  background: var(--gold-dim);
  border-left: 3px solid var(--gold);
  color: var(--gold-bright);
  font-size: 13px;
  border-radius: 4px;
}

/* ==================== Step 1：分镜左右两列 ==================== */
.shots-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
.shot-card {
  background: var(--bg-3);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 16px;
}
.shot-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 12px;
}
.shot-id {
  font-family: var(--font-mono), Menlo, Consolas, monospace;
  font-weight: 700;
  font-size: 13px;
  color: var(--gold-bright);
  background: var(--gold-dim);
  padding: 2px 8px;
  border-radius: 4px;
}
.shot-duration {
  font-size: 12px;
  color: var(--text-2);
  background: var(--bg-2);
  padding: 2px 8px;
  border-radius: 4px;
}
.shot-field { margin-bottom: 12px; }
.shot-key {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  margin-bottom: 4px;
}
.shot-val {
  font-size: 14px;
  color: var(--text-0);
}
.shot-dialogue {
  font-style: italic;
  color: var(--text-1);
  background: var(--bg-1);
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px dashed var(--line-strong);
  display: inline-block;
}
.shot-prompt {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  font-size: 12.5px;
  font-family: var(--font-mono), Menlo, Consolas, monospace;
  line-height: 1.6;
  background: var(--bg-1);
  color: var(--text-0);
  box-sizing: border-box;
  resize: vertical;
}
.shot-prompt::placeholder { color: var(--text-2); }
.shot-prompt:focus {
  outline: none;
  border-color: var(--gold);
  box-shadow: 0 0 0 2px var(--gold-dim);
}

/* ==================== Step 2：角色图 2×2 网格 ==================== */
.chars-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
.char-card {
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
  transition: transform 0.2s, box-shadow 0.2s, border-color 0.2s;
}
.char-card:hover {
  transform: translateY(-2px);
  border-color: var(--gold);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.3);
}
.char-image-wrap {
  width: 100%;
  aspect-ratio: 3 / 4;
  background: var(--bg-3);
  overflow: hidden;
}
.char-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
/* 无形象角色（旁白/声线）的占位：说明不出镜，而不是摆一张随机图 */
.char-image-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 14px;
  font-size: 12px;
  color: var(--text-2, #999);
  text-align: center;
}

/* 分镜出图：逐镜网格（比角色卡小，一屏尽量多显示几张） */
.shot-images-block {
  margin-top: 26px;
  padding-top: 20px;
  border-top: 1px solid var(--line);
}
.shot-images-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
  margin-bottom: 12px;
}
.shot-images-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
}
.shot-image-card {
  margin: 0;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  transition: transform 0.2s, border-color 0.2s;
}
.shot-image-card:hover {
  transform: translateY(-2px);
  border-color: var(--gold);
}
.shot-image-card img {
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
  display: block;
}
.shot-image-card figcaption {
  padding: 6px 8px;
  font-size: 12px;
  color: var(--text-2, #999);
  text-align: center;
}
.char-footer { padding: 12px 14px 16px; }
.char-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-1);
  margin-bottom: 6px;
}
.char-select {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  font-size: 14px;
  background: var(--bg-1);
  color: var(--text-0);
  cursor: pointer;
}
.char-select:focus {
  outline: none;
  border-color: var(--gold);
  box-shadow: 0 0 0 2px var(--gold-dim);
}
.char-suggest {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-2);
}
.char-suggest em { color: var(--gold-bright); font-style: normal; font-weight: 500; }

/* ==================== Step 3：视频预览 ==================== */
.video-wrap {
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid var(--line);
}
.preview-video {
  width: 100%;
  height: 100%;
  display: block;
  background: #000;
}
.video-meta {
  margin-top: 18px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}
.meta-item {
  background: var(--bg-3);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 14px;
}
.meta-key {
  display: block;
  font-size: 12px;
  color: var(--text-2);
  margin-bottom: 4px;
}
.meta-val {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-0);
}

/* ==================== Step 4：最终发布 ==================== */
.final-meta {
  background: var(--bg-3);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 18px 22px;
  margin-bottom: 22px;
}
.final-meta-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 9px 0;
  border-bottom: 1px dashed var(--line);
}
.final-meta-row:last-child { border-bottom: none; }
.final-key {
  font-size: 13px;
  color: var(--text-2);
  font-weight: 500;
}
.final-val {
  font-size: 14px;
  color: var(--text-0);
  font-weight: 600;
}
.final-buttons {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.big-btn {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 20px 22px;
  border-radius: 12px;
  border: none;
  cursor: pointer;
  text-align: left;
  transition: transform 0.15s, box-shadow 0.2s;
}
.big-btn:hover { transform: translateY(-2px); }
.big-btn-icon {
  font-size: 30px;
  line-height: 1;
  flex-shrink: 0;
}
.big-btn-title {
  font-size: 16px;
  font-weight: 700;
  margin-bottom: 4px;
}
.big-btn-sub { font-size: 12.5px; opacity: 0.85; }

.big-btn-ghost {
  background: var(--bg-2);
  color: var(--gold-bright);
  border: 2px solid var(--gold-dim);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}
.big-btn-ghost:hover {
  border-color: var(--gold);
  box-shadow: 0 4px 14px rgba(217, 169, 78, 0.15);
}
.big-btn-primary {
  background: linear-gradient(135deg, #d9a94e, #b9842f);
  color: #1a1408;
  box-shadow: 0 4px 14px rgba(217, 169, 78, 0.3);
}
.big-btn-primary:hover {
  filter: brightness(1.08);
  box-shadow: 0 6px 20px rgba(217, 169, 78, 0.4);
}

/* ==================== 底部操作栏 ==================== */
.action-bar {
  margin-top: 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 4px;
}
.action-btn {
  padding: 10px 22px;
  border-radius: 8px;
  border: 1px solid var(--line-strong);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, opacity 0.15s, border-color 0.15s;
  font-family: var(--font-body);
}
.action-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.action-prev {
  background: var(--bg-2);
  color: var(--text-1);
  border: 1px solid var(--line-strong);
}
.action-prev:hover:not(:disabled) {
  background: var(--bg-3);
  color: var(--text-0);
}
.action-next {
  background: linear-gradient(135deg, #d9a94e, #b9842f);
  border-color: transparent;
  color: #1a1408;
  box-shadow: 0 4px 14px rgba(217, 169, 78, 0.25);
}
.action-next:hover:not(:disabled) {
  filter: brightness(1.08);
  color: #1a1408;
}
.action-center {
  font-size: 13px;
  color: var(--text-2);
}
.status-text { color: var(--text-2); }
.error-text { color: var(--red); font-weight: 500; }
.review-tag {
  color: var(--gold-bright);
  background: var(--gold-dim);
  padding: 3px 10px;
  border-radius: 6px;
  font-weight: 500;
}
.action-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.action-reject {
  background: transparent;
  color: var(--red, #e05b5b);
  border: 1px solid var(--red, #e05b5b);
}
.action-reject:hover:not(:disabled) {
  background: rgba(224, 91, 91, 0.1);
  color: var(--red, #e05b5b);
}

/* ==================== 真实创作运行栏 ==================== */
.runbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 12px 14px;
  margin-bottom: 28px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
}
.runbar-input {
  flex: 1;
  min-width: 220px;
  padding: 9px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  font-size: 13.5px;
  background: var(--bg-1);
  color: var(--text-0);
  outline: none;
  font-family: var(--font-body);
}
.runbar-input::placeholder { color: var(--text-2); }
.runbar-input:focus { border-color: var(--gold); box-shadow: 0 0 0 3px var(--gold-dim); }
.runbar-select {
  padding: 9px 10px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  font-size: 13px;
  background: var(--bg-1);
  color: var(--text-0);
  max-width: 150px;
  font-family: var(--font-body);
}
.runbar-select:focus { outline: none; border-color: var(--gold); }
.runbar-btn {
  padding: 9px 16px;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, #d9a94e, #b9842f);
  color: #1a1408;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  font-family: var(--font-body);
}
.runbar-btn:hover { filter: brightness(1.08); color: #1a1408; }
.runbar-ghost {
  padding: 8px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--bg-2);
  color: var(--text-1);
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  font-family: var(--font-body);
}
.runbar-ghost:hover { background: var(--bg-3); color: var(--text-0); }
.real-tag {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--gold-dim);
  color: var(--gold-bright);
  font-size: 12px;
  font-weight: 600;
}

/* ==================== 响应式：窄屏友好 ==================== */
@media (max-width: 720px) {
  .review-page { padding: 24px 16px 40px; }
  .page-header h1 { font-size: 22px; }
  .step-node { width: 100%; margin-bottom: 14px; }
  .node-connector { display: none; }
  .card { padding: 20px 16px; }
  .shots-grid, .chars-grid, .final-buttons {
    grid-template-columns: 1fr;
  }
  .video-meta { grid-template-columns: 1fr; }
}
</style>
