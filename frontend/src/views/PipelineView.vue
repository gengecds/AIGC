<!--
  Pipeline 5 步向导式 Review UI（域 D 独立组件）
  布局结构：
    1. 顶部 5 节点进度条（Script → Storyboard → Image → Video → Final）
    2. 中间「当前步骤审查卡片」，每步内容不同
    3. 底部左右按钮：上一步 / 继续
  数据来源：SSE EventSource 订阅 /pipeline/events?run_id=__demo__
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
              <!-- picsum.photos 占位图，seed 不同保证每张图不一样 -->
              <img
                :src="`https://picsum.photos/seed/${char.image_seed || 'char' + idx}/420/560`"
                :alt="char.suggested_name || `角色${idx + 1}`"
                class="char-image"
                loading="lazy"
              />
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
          💡 2×2 共 4 张角色定妆照，每张下方选择对应的角色名（可覆盖建议名）。
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
            <source :src="videoUrl" type="video/mp4" />
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
        <span v-else class="status-text">
          后端 SSE：{{ eventCount }} 条事件已接收
        </span>
      </div>
      <button
        class="action-btn action-next"
        :disabled="isLoading(step) || (step === 4 && !done)"
        @click="handleNext"
      >
        {{ step === 4 ? '完成 🎉' : '继续 →' }}
      </button>
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

// ── SSE 端点地址：和后端 /pipeline/events 对齐（与 FastAPI 同端口 8888）
const SSE_URL = 'http://localhost:8888/pipeline/events?run_id=__demo__'

// ── 5 步定义（对应后端 step 0-4）
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
  suggested_name?: string
  assigned_name: string
}
const characterList = reactive<CharacterItem[]>([])
// 下拉框角色名选项（演示数据）
const characterNameOptions: string[] = [
  '林峰（舰长）',
  '苏晴（副官）',
  '外星指挥官',
  'AI 机器人小七',
  '神秘女子',
  '舰长父亲（回忆）',
]

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
function handleNext() {
  if (step.value === 4 && done.value) {
    // 最后一步：演示 toast，不做真实调用
    alert('🎉 5 步审查全部完成！（演示模式，无真实发布）')
    return
  }
  if (step.value < 4 && stepReady[step.value + 1]) {
    step.value += 1
  }
}

// ── 最终页大按钮占位 ───────────────────────────────────────────
function handleExportObsidian() {
  alert('📝 已触发「导出到 Obsidian」（演示模式，占位动作）')
}
function handleDownload() {
  alert('⬇️ 已触发「下载成片」（演示模式，占位动作）')
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

  // 填充对应 step 的 payload
  const payload = raw.payload || {}

  // —— Step 0：剧本 ——
  if (stepIdx === 0 && payload) {
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

  // —— Step 2：角色图 ——
  if (stepIdx === 2 && payload && Array.isArray(payload.characters)) {
    characterList.splice(0, characterList.length, ...payload.characters.map((c: any) => ({
      image_seed: c.image_seed || ('char_' + Math.random().toString(36).slice(2, 7)),
      suggested_name: c.suggested_name || '',
      assigned_name: '',
    })))
    stepReady[2] = true
  }

  // —— Step 3：视频预览 ——
  if (stepIdx === 3 && payload) {
    videoUrl.value = payload.video_url || ''
    videoDuration.value = payload.duration_sec ?? '—'
    videoResolution.value = payload.resolution || '—'
    videoHasSubtitle.value = !!payload.has_subtitle
    stepReady[3] = true
  }

  // —— Step 4：最终发布 ——
  if (stepIdx === 4 && payload) {
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
    eventSource = new EventSource(SSE_URL, { withCredentials: false })

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

// ── 生命周期 ───────────────────────────────────────────────────
onMounted(() => {
  startSSE()
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
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei",
               "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #1f2937;
  background: linear-gradient(180deg, #fafbff 0%, #f5f6fb 100%);
  min-height: 100vh;
}

.page-header { margin-bottom: 32px; }
.page-header h1 {
  margin: 0 0 6px;
  font-size: 28px;
  font-weight: 700;
  letter-spacing: 0.3px;
}
.subtitle {
  margin: 0;
  color: #6b7280;
  font-size: 14px;
}
.tag-done {
  display: inline-block;
  margin-left: 10px;
  padding: 2px 10px;
  border-radius: 999px;
  background: #ecfdf5;
  color: #047857;
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
  background: #e5e7eb;
  color: #9ca3af;
  font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px;
  border: 2px solid transparent;
  transition: all 0.3s ease;
  flex-shrink: 0;
}
.node-label {
  margin-left: 10px;
  font-size: 13px;
  color: #9ca3af;
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
  background: #e5e7eb;
  z-index: 0;
}
.connector-fill {
  height: 100%;
  background: #4f46e5;
  transition: width 0.5s ease;
}
/* 已完成节点 */
.step-node.is-done .node-circle {
  background: #10b981;
  color: #fff;
  border-color: #10b981;
}
.step-node.is-done .node-label { color: #065f46; }
/* 当前节点 */
.step-node.is-current:not(.is-done) .node-circle {
  background: #fff;
  color: #4f46e5;
  border-color: #4f46e5;
  box-shadow: 0 0 0 4px rgba(79, 70, 229, 0.12);
  transform: scale(1.05);
}
.step-node.is-current:not(.is-done) .node-label {
  color: #4f46e5;
  font-weight: 600;
}
/* 整体细线进度 */
.overall-bar {
  margin-top: 20px;
  height: 4px;
  background: #e5e7eb;
  border-radius: 999px;
  overflow: hidden;
}
.overall-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #6366f1, #4f46e5);
  border-radius: 999px;
  transition: width 0.5s ease;
}

/* ==================== 审查卡片 ==================== */
.card {
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 6px 20px rgba(0, 0, 0, 0.03);
  padding: 28px 32px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
  padding-bottom: 18px;
  border-bottom: 1px solid #f0f0f3;
}
.step-badge {
  display: inline-block;
  padding: 3px 10px;
  background: #eef2ff;
  color: #4f46e5;
  font-size: 12px;
  font-weight: 600;
  border-radius: 6px;
  margin-bottom: 8px;
}
.card-title {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 700;
}
.card-subtitle {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}
.loading-inline {
  display: flex; align-items: center; gap: 8px;
  color: #6366f1;
  font-size: 13px;
  font-weight: 500;
}
.spinner {
  width: 14px; height: 14px;
  border-radius: 50%;
  border: 2px solid #c7d2fe;
  border-top-color: #4f46e5;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ==================== 空状态 ==================== */
.empty-state {
  padding: 48px 16px;
  text-align: center;
  color: #6b7280;
}
.empty-icon { font-size: 40px; margin-bottom: 12px; opacity: 0.7; }
.empty-text { font-size: 15px; margin-bottom: 20px; }
.thin-loading {
  width: 260px;
  margin: 0 auto;
  height: 3px;
  background: #e5e7eb;
  border-radius: 999px;
  overflow: hidden;
}
.thin-loading-bar {
  height: 100%;
  width: 30%;
  background: linear-gradient(90deg, #a5b4fc, #4f46e5, #a5b4fc);
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
  color: #374151;
}
.field-input {
  width: 100%;
  padding: 10px 14px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  box-sizing: border-box;
  transition: border-color 0.15s;
}
.field-input:focus {
  outline: none;
  border-color: #6366f1;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
}
.field-textarea {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.7;
  box-sizing: border-box;
  resize: vertical;
  font-family: inherit;
  transition: border-color 0.15s;
}
.field-textarea:focus {
  outline: none;
  border-color: #6366f1;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
}
.hint {
  margin-top: 18px;
  padding: 10px 14px;
  background: #fef3c7;
  border-left: 3px solid #f59e0b;
  color: #92400e;
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
  background: #fafafa;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 16px;
}
.shot-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 12px;
}
.shot-id {
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-weight: 700;
  font-size: 13px;
  color: #4f46e5;
  background: #eef2ff;
  padding: 2px 8px;
  border-radius: 4px;
}
.shot-duration {
  font-size: 12px;
  color: #6b7280;
  background: #f3f4f6;
  padding: 2px 8px;
  border-radius: 4px;
}
.shot-field { margin-bottom: 12px; }
.shot-key {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #6b7280;
  margin-bottom: 4px;
}
.shot-val {
  font-size: 14px;
  color: #1f2937;
}
.shot-dialogue {
  font-style: italic;
  color: #374151;
  background: #fff;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px dashed #d1d5db;
  display: inline-block;
}
.shot-prompt {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 12.5px;
  font-family: "SF Mono", Menlo, Consolas, monospace;
  line-height: 1.6;
  box-sizing: border-box;
  resize: vertical;
}
.shot-prompt:focus {
  outline: none;
  border-color: #6366f1;
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.12);
}

/* ==================== Step 2：角色图 2×2 网格 ==================== */
.chars-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
.char-card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  overflow: hidden;
  transition: transform 0.2s, box-shadow 0.2s;
}
.char-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.06);
}
.char-image-wrap {
  width: 100%;
  aspect-ratio: 3 / 4;
  background: #f3f4f6;
  overflow: hidden;
}
.char-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.char-footer { padding: 12px 14px 16px; }
.char-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 6px;
}
.char-select {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 14px;
  background: #fff;
  cursor: pointer;
}
.char-select:focus {
  outline: none;
  border-color: #6366f1;
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.12);
}
.char-suggest {
  margin-top: 8px;
  font-size: 12px;
  color: #6b7280;
}
.char-suggest em { color: #4f46e5; font-style: normal; font-weight: 500; }

/* ==================== Step 3：视频预览 ==================== */
.video-wrap {
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
  border-radius: 10px;
  overflow: hidden;
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
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 14px;
}
.meta-key {
  display: block;
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}
.meta-val {
  font-size: 15px;
  font-weight: 600;
  color: #111827;
}

/* ==================== Step 4：最终发布 ==================== */
.final-meta {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 18px 22px;
  margin-bottom: 22px;
}
.final-meta-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 9px 0;
  border-bottom: 1px dashed #e5e7eb;
}
.final-meta-row:last-child { border-bottom: none; }
.final-key {
  font-size: 13px;
  color: #6b7280;
  font-weight: 500;
}
.final-val {
  font-size: 14px;
  color: #111827;
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
  background: #fff;
  color: #4f46e5;
  border: 2px solid #c7d2fe;
  box-shadow: 0 1px 3px rgba(79, 70, 229, 0.06);
}
.big-btn-ghost:hover {
  border-color: #4f46e5;
  box-shadow: 0 4px 14px rgba(79, 70, 229, 0.15);
}
.big-btn-primary {
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: #fff;
  box-shadow: 0 4px 14px rgba(79, 70, 229, 0.3);
}
.big-btn-primary:hover {
  box-shadow: 0 6px 20px rgba(79, 70, 229, 0.4);
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
  border: none;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, opacity 0.15s;
}
.action-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.action-prev {
  background: #fff;
  color: #4b5563;
  border: 1px solid #d1d5db;
}
.action-prev:hover:not(:disabled) {
  background: #f3f4f6;
}
.action-next {
  background: #4f46e5;
  color: #fff;
}
.action-next:hover:not(:disabled) {
  background: #4338ca;
}
.action-center {
  font-size: 13px;
  color: #6b7280;
}
.status-text { color: #6b7280; }
.error-text { color: #dc2626; font-weight: 500; }

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
