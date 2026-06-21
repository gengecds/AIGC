/**
 * AI 漫剧制作平台 — 前端控制器
 * 支持 GPU 状态轮询、管线进度展示、分镜/角色/视频预览
 */
(function() {
  'use strict';

  // ===== 配置 =====
  const CONFIG = {
    apiBase: '',  // 后续可对接后端
    pollInterval: 3000,  // GPU 状态轮询间隔 (ms)
  };

  // ===== 状态 =====
  const state = {
    story: '',
    pipelineId: null,
    currentStep: 5,  // 当前在步骤5（视频生成）
    totalSteps: 7,
    characters: [
      { id: 1, name: '主角·程序', role: '程序员（穿越者）', image: null },
      { id: 2, name: '小白', role: '猫娘向导', image: null },
      { id: 3, name: '橘长老', role: '猫娘长老', image: null },
      { id: 4, name: '黑爪', role: '反派猫娘', image: null },
    ],
    shots: [
      { id: 1, desc: '程序深夜写代码，屏幕蓝光映照疲惫的脸', image: null },
      { id: 2, desc: '键盘突然发光，程序被吸入屏幕', image: null },
      { id: 3, desc: '程序醒来，发现身处猫娘世界', image: null },
      { id: 4, desc: '小白发现程序是人类，惊讶', image: null },
      { id: 5, desc: '小白带程序去见橘长老', image: null },
      { id: 6, desc: '橘长老解释穿越原因，需要程序拯救世界', image: null },
    ],
    videos: [],
    gpuInfo: { model: 'RTX 4090', memoryUsed: '3.7', memoryTotal: '24.6' },
    queueRunning: 1,
    queuePending: 5,
    pipelineStartTime: null,
  };

  // ===== DOM 引用 =====
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  const DOM = {};
  const initDom = () => {
    DOM.storyInput = $('#storyInput');
    DOM.generateBtn = $('#generateBtn');
    DOM.stopBtn = $('#stopBtn');
    DOM.progressCard = $('#progressCard');
    DOM.progressFill = $('#progressFill');
    DOM.progressDetail = $('#progressDetail');
    DOM.progressTitle = $('#progressTitle');
    DOM.progressTime = $('#progressTime');
    DOM.charactersPanel = $('#charactersPanel');
    DOM.characterGrid = $('#characterGrid');
    DOM.charCount = $('#charCount');
    DOM.shotsPanel = $('#shotsPanel');
    DOM.shotGrid = $('#shotGrid');
    DOM.shotCount = $('#shotCount');
    DOM.videoPanel = $('#videoPanel');
    DOM.videoGrid = $('#videoGrid');
    DOM.videoCount = $('#videoCount');
    DOM.emptyState = $('#emptyState');
    DOM.stepBadge = $('#stepBadge');
    DOM.gpuInfo = $('#gpuInfo');
    DOM.queueInfo = $('#queueInfo');
    DOM.serverStatus = $('#serverStatus');
    DOM.exportBtn = $('#exportBtn');
  };

  // ===== 工具函数 =====
  function formatTime(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // ===== 渲染函数 =====

  /** 渲染角色卡片 */
  function renderCharacters(characters) {
    if (!characters || characters.length === 0) return;
    DOM.charactersPanel.style.display = '';
    DOM.charCount.textContent = `${characters.length} 个角色`;

    DOM.characterGrid.innerHTML = characters.map(c => `
      <div class="char-card">
        <div class="char-avatar">
          ${c.image ? `<img src="${c.image}" alt="${escapeHtml(c.name)}">` : '👤'}
        </div>
        <div class="char-name">${escapeHtml(c.name)}</div>
        <div class="char-role">${escapeHtml(c.role)}</div>
      </div>
    `).join('');
  }

  /** 渲染分镜网格 */
  function renderShots(shots) {
    if (!shots || shots.length === 0) return;
    DOM.shotsPanel.style.display = '';
    DOM.shotCount.textContent = `${shots.length} 镜头`;

    DOM.shotGrid.innerHTML = shots.map(s => `
      <div class="shot-card">
        <div class="shot-image">
          ${s.image ? `<img src="${s.image}" alt="镜头 ${s.id}">` : `🎬`}
        </div>
        <div class="shot-info">
          <div class="shot-num">镜头 #${s.id}</div>
          <div class="shot-desc">${escapeHtml(s.desc)}</div>
        </div>
      </div>
    `).join('');
  }

  /** 渲染视频列表 */
  function renderVideos(videos) {
    if (!videos || videos.length === 0) {
      if (state.currentStep >= 5) {
        DOM.videoPanel.style.display = '';
        DOM.videoGrid.innerHTML = `
          <div style="grid-column:1/-1;padding:40px;text-align:center;color:var(--text-muted)">
            <div style="font-size:32px;margin-bottom:8px">🎥</div>
            <div>视频生成中（GPU 排队…）</div>
          </div>`;
        DOM.videoCount.textContent = '生成中';
      }
      return;
    }
    DOM.videoPanel.style.display = '';
    DOM.videoCount.textContent = `${videos.length} 段`;

    DOM.videoGrid.innerHTML = videos.map(v => `
      <div class="video-card">
        <div class="video-player">
          ${v.src ? `<video controls preload="metadata"><source src="${v.src}" type="video/mp4"></video>` : '⏳'}
        </div>
        <div class="video-info">
          <div class="video-num">镜头 #${v.shotId || '?'}</div>
        </div>
      </div>
    `).join('');
  }

  /** 更新进度 */
  function updateProgress(step, total, detail) {
    DOM.progressCard.style.display = '';
    const pct = Math.round((step / total) * 100);
    DOM.progressFill.style.width = `${pct}%`;
    DOM.stepBadge.textContent = `${step}/${total}`;

    const labels = ['', '剧本生成', '分镜拆解', '角色定妆', '场景出图', '图生视频', '字幕生成', '合成输出'];
    DOM.progressTitle.textContent = `步骤 ${step}: ${labels[step] || '处理中…'}`;
    DOM.progressDetail.textContent = detail || '';

    // 更新工作流侧栏状态
    $$('.step').forEach(el => {
      const s = parseInt(el.dataset.step);
      el.className = 'step' +
        (s < step ? ' completed' : '') +
        (s === step ? ' active' : '') +
        (s > step ? ' pending' : '');
    });
  }

  /** 更新 GPU 信息 */
  function updateGPUInfo() {
    const { gpuInfo, queueRunning, queuePending } = state;
    DOM.gpuInfo.textContent = `${gpuInfo.model} · ${gpuInfo.memoryUsed}/${gpuInfo.memoryTotal} GB`;
    DOM.queueInfo.textContent = `队列: ${queueRunning} 运行 · ${queuePending} 等待`;

    if (state.pipelineStartTime) {
      const elapsed = (Date.now() - state.pipelineStartTime) / 1000;
      DOM.progressTime.textContent = formatTime(elapsed);
    }
  }

  /** 显示空状态或隐藏 */
  function updateEmptyState(hasContent) {
    DOM.emptyState.style.display = hasContent ? 'none' : '';
  }

  // ===== 核心操作 =====

  /** 开始生成管线 */
  function startPipeline() {
    const story = DOM.storyInput.value.trim();
    if (!story) {
      DOM.storyInput.focus();
      return;
    }
    state.story = story;
    state.pipelineStartTime = Date.now();
    state.currentStep = 1;

    // 清空旧结果
    DOM.charactersPanel.style.display = 'none';
    DOM.shotsPanel.style.display = 'none';
    DOM.videoPanel.style.display = 'none';
    updateEmptyState(false);

    DOM.generateBtn.disabled = true;
    DOM.generateBtn.textContent = '⏳ 生成中…';
    DOM.stopBtn.style.display = '';

    // 启动进度模拟（实际应用会对接WS）
    simulatePipeline();
  }

  /** 模拟管线进度（展示用，后续对接真实后端） */
  function simulatePipeline() {
    const steps = [
      { step: 1, delay: 2000, detail: 'DeepSeek 创作剧本…' },
      { step: 2, delay: 2500, detail: '分解镜头结构…' },
      { step: 3, delay: 8000, detail: 'ComfyUI 生成角色定妆照…' },
      { step: 4, delay: 12000, detail: 'ComfyUI SD 批量出图中…' },
      { step: 5, delay: 3000, detail: 'HunyuanVideo 图生视频（排队中）' },
      { step: 6, delay: 2000, detail: 'ASR 语音识别 + 字幕打轴…' },
      { step: 7, delay: 4000, detail: 'FFmpeg 合成最终输出…' },
    ];

    let i = 0;
    function nextStep() {
      if (i >= steps.length) {
        finishPipeline();
        return;
      }
      const s = steps[i];
      state.currentStep = s.step;
      updateProgress(s.step, 7, s.detail);

      // 渲染对应数据
      if (s.step === 3) renderCharacters(state.characters);
      if (s.step === 4) renderShots(state.shots);

      setTimeout(nextStep, s.delay);
      i++;
    }

    nextStep();
  }

  /** 管线完成 */
  function finishPipeline() {
    state.currentStep = 7;
    updateProgress(7, 7, '✅ 已完成');

    DOM.generateBtn.disabled = false;
    DOM.generateBtn.textContent = '▶ 开始生成';
    DOM.stopBtn.style.display = 'none';

    // 显示假视频
    state.videos = state.shots.map(s => ({
      shotId: s.id,
      src: null, // 生产环境填真实路径
    }));
    renderVideos(state.videos);
  }

  /** 停止生成 */
  function stopPipeline() {
    // TODO: 真实环境中断队列
    DOM.generateBtn.disabled = false;
    DOM.generateBtn.textContent = '▶ 开始生成';
    DOM.stopBtn.style.display = 'none';
  }

  /** 导出全部视频 */
  function exportAll() {
    // TODO: 真实环境打包下载
    alert('导出功能：将打包所有视频为 ZIP（开发中）');
  }

  // ===== 事件绑定 =====
  function bindEvents() {
    DOM.generateBtn.addEventListener('click', startPipeline);
    DOM.stopBtn.addEventListener('click', stopPipeline);
    DOM.exportBtn.addEventListener('click', exportAll);

    // 快捷键 Ctrl+Enter
    DOM.storyInput.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        startPipeline();
      }
    });
  }

  // ===== 初始化 =====
  function init() {
    initDom();
    bindEvents();
    renderCharacters(state.characters);
    renderShots(state.shots);
    updateEmptyState(true);
    updateProgress(5, 7, 'HunyuanVideo · GPU 排队中');
    updateGPUInfo();

    // GPU 状态轮询（模拟）
    setInterval(() => {
      updateGPUInfo();
    }, CONFIG.pollInterval);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
