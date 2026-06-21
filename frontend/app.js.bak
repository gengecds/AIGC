/**
 * AI 漫剧制作平台 — 前端控制器
 * 完全贴合 前端展示方案.md 的设计
 * 状态机：空闲 → 排队 → 执行⏳ → ⏸️待审核 / ❌失败 / ✅完成
 */
(function(){
  'use strict';

  const API = 'http://127.0.0.1:8000';
  const AGENTS = ['script','storyboard','character','image','video','subtitle','compose'];
  const AGENT_LABELS = { script:'剧本', storyboard:'分镜', character:'定妆照', image:'出图', video:'视频', subtitle:'字幕', compose:'合成' };
  const AGENT_ICONS = { script:'📝', storyboard:'📋', character:'🎭', image:'🖼️', video:'🎬', subtitle:'📄', compose:'📦' };

  const $ = s => document.querySelector(s);
  const $$ = s => document.querySelectorAll(s);

  let state = {
    pipelineId: null, running: false, startTime: null,
    cards: {},   // agent -> {status,meta}
    detailAgent: null,
    ws: null, logCount: 0,
  };

  const els = {};
  function el() {
    els.storyInput = $('#storyInput');
    els.generateBtn = $('#generateBtn');
    els.stopBtn = $('#stopBtn');
    els.gpBar = $('#globalProgress');
    els.gpFill = $('#gpFill');
    els.gpText = $('#gpText');
    els.serverStatus = $('#serverStatus');
    els.logList = $('#logList');
    els.logCount = $('#logCount');
    els.logHandle = $('#logHandle');
    els.logbar = $('#logbar');
    els.detailSection = $('#detailSection');
    els.detailTitle = $('#detailTitle');
    els.detailBody = $('#detailBody');
    els.detailActions = $('#detailActions');
    els.detailClose = $('#detailClose');
    els.drawerOverlay = $('#drawerOverlay');
    els.historyDrawer = $('#historyDrawer');
    els.drawerBody = $('#drawerBody');
    els.drawerClose = $('#drawerClose');
    els.historyBtn = $('#historyBtn');
    els.assetChars = $('#assetChars');
    els.assetScenes = $('#assetScenes');
    els.stylePicker = $('#stylePicker');
    els.ovStatus = $('#ovStatus');
    els.ovEpisodes = $('#ovEpisodes');
    els.ovShots = $('#ovShots');
    els.ovTime = $('#ovTime');
    els.ovRemain = $('#ovRemain');
    els.detailPanel = $('#detailPanel');
    // Tab
    els.tabBtns = $$('.tab');
    els.tabContents = [document.getElementById('tab-assets'), document.getElementById('tab-overview')];
  }

  function esc(t) { const d=document.createElement('div'); d.textContent=t; return d.innerHTML; }
  function fmt(t) { const m=Math.floor(t/60); const s=Math.floor(t%60); return `${m}:${s.toString().padStart(2,'0')}`; }

  // ═══ Card State Machine ═══
  function setCardState(agent, status, meta) {
    const card = document.querySelector(`.chain-card[data-agent="${agent}"]`);
    if (!card) return;
    // 状态: idle|queued|running|review|done|failed
    AGENTS.forEach(a => document.querySelector(`.chain-card[data-agent="${a}"]`).className = 'chain-card');
    card.classList.add('state-'+status);
    const stEl = document.getElementById('status-'+agent);
    const meEl = document.getElementById('meta-'+agent);
    const statusLabels = { idle:'空闲', queued:'⏳ 排队', running:'⏳ 执行中…', review:'⏸️ 待确认', done:'✅ 完成', failed:'❌ 失败' };
    stEl.textContent = statusLabels[status] || status;
    meEl.textContent = meta || '';
    state.cards[agent] = { status, meta };
  }

  function resetAllCards() {
    AGENTS.forEach(a => setCardState(a, 'idle', ''));
  }

  // ═══ Global Progress ═══
  function setProgress(pct, text) {
    els.gpBar.style.display = '';
    els.gpFill.style.width = Math.min(100, Math.max(0, pct)) + '%';
    els.gpText.textContent = text || '';
  }

  // ═══ Log ═══
  function addLog(level, agent, msg) {
    const entry = document.createElement('div');
    entry.className = 'log-entry ' + level;
    const t = new Date();
    const ts = t.toTimeString().slice(0,8);
    entry.innerHTML = `<span class="time">${ts}</span>[${AGENT_LABELS[agent]||agent}] ${esc(msg)}`;
    els.logList.prepend(entry);
    state.logCount++;
    els.logCount.textContent = state.logCount;
    // 自动扩展
    if (!els.logbar.classList.contains('expanded')) {
      els.logbar.classList.add('expanded');
    }
  }

  // ═══ Detail Panel ═══
  let _detailAgent = '';
  let _detailReviewReason = '';

  function openDetail(title, html, agent='', reviewReason='') {
    _detailAgent = agent;
    _detailReviewReason = reviewReason;
    els.detailTitle.textContent = title;
    els.detailBody.innerHTML = html;
    // 审核按钮区
    if (agent && reviewReason) {
      els.detailActions.style.display = 'flex';
      els.detailActions.innerHTML = `
        <div class="review-bar">
          <span>⏸️ ${reviewReason}</span>
          <div class="review-btns">
            <button class="btn btn-primary btn-sm" onclick="window._approveReview()">✅ 确认</button>
            <button class="btn btn-danger btn-sm" onclick="window._rejectReview()">🔄 重跑</button>
          </div>
        </div>
      `;
    } else {
      els.detailActions.style.display = 'none';
    }
    els.detailSection.style.display = '';
    state.detailAgent = title;
  }

  window._approveReview = async () => {
    const pid = state.pipelineId;
    if (!pid) return;
    try {
      await fetch(API + '/api/v1/pipeline/approve/' + pid, { method:'POST', mode:'cors' });
      addLog('INFO', 'review', '✅ 已确认，管线继续');
      els.detailActions.style.display = 'none';
    } catch(e) {
      addLog('ERROR', 'review', '确认失败: '+e.message);
    }
  };

  window._rejectReview = async () => {
    const pid = state.pipelineId;
    if (!pid) return;
    try {
      await fetch(API + '/api/v1/pipeline/reject/' + pid, { method:'POST', mode:'cors', headers:{'Content-Type':'application/json'}, body:'{}' });
      addLog('INFO', 'review', '🔄 已拒绝，等待重跑');
      els.detailActions.style.display = 'none';
    } catch(e) {
      addLog('ERROR', 'review', '拒绝失败: '+e.message);
    }
  };

  function closeDetail() {
    els.detailSection.style.display = 'none';
    els.detailActions.style.display = 'none';
    state.detailAgent = null;
    _detailAgent = '';
    _detailReviewReason = '';
  }

  // ═══ Card Click → Detail ═══
  function bindCardClicks() {
    $$('.chain-card').forEach(card => {
      card.addEventListener('click', async () => {
        const agent = card.dataset.agent;
        if (state.cards[agent]?.status === 'idle') return;
        await renderDetail(agent);
      });
    });
  }

  async function renderDetail(agent, reviewReason='') {
    try {
      const resp = await fetch(API + '/api/v1/pipeline/snapshot/latest', { mode:'cors' });
      if (!resp.ok) return;
      const snap = await resp.json();
      let title = AGENT_LABELS[agent] || agent;
      let review = reviewReason;
      // 如果卡片是 review 状态但没传 reason，自动加
      if (!review && state.cards[agent]?.status === 'review') {
        review = state.cards[agent].meta || '请确认结果';
      }
      let html = '';

      switch(agent) {
        case 'script': {
          const sc = snap.script_agent;
          if (!sc) { html = '<div class="empty-state"><div class="icon">📝</div>暂无数据</div>'; break; }
          let diaHtml = '';
          const episodes = sc.episodes || sc.data?.episodes || [];
          episodes.forEach((ep, i) => {
            const lines = ep.dialogues || ep.dialogues || [];
            diaHtml += `<div style="margin-bottom:12px"><strong>第${ep.episode_number||i+1}集</strong>`;
            (lines||[]).forEach(d => {
              diaHtml += `<div class="dialogue"><span class="char-name">[${esc(d.character||d.char_name||'')}]</span> <span class="text">${esc(d.text||d.content||'')}</span></div>`;
            });
            diaHtml += '</div>';
          });
          html = `<div class="script-detail">${diaHtml}</div>`;
          break;
        }
        case 'storyboard': {
          const sb = snap.storyboard_agent;
          if (!sb) { html = '<div class="empty-state"><div class="icon">📋</div>暂无数据</div>'; break; }
          const eps = sb.episodes || [];
          let rows = '';
          let totalShots = 0;
          eps.forEach((ep, ei) => {
            (ep.shots||[]).forEach((sh, si) => {
              totalShots++;
              rows += `<tr><td>${ei+1}.${sh.shot_id||si+1}</td><td>${esc(sh.type||sh.camera||'')}</td><td><input value="${esc(sh.description||sh.desc||'')}" /></td><td style="font-size:10px;color:var(--text-muted)">${(sh.duration||'')}</td></tr>`;
            });
          });
          html = `<table class="sb-table"><thead><tr><th style="width:50px">序号</th><th style="width:60px">景别</th><th>描述（可编辑）</th><th style="width:50px">时长</th></tr></thead><tbody>${rows}</tbody></table>`;
          break;
        }
        case 'character': {
          const ch = snap.character_agent;
          const chars = ch?.characters || [];
          html = '<div class="char-detail-grid">' + chars.map(c => {
            const imgSrc = c.image || (c.asset?.controlnet_ref_path || '');
            return `<div class="char-detail-card"><div class="char-name">${esc(c.name)}</div>${imgSrc ? `<img src="${imgSrc}" alt="${esc(c.name)}">` : '👤'}<div style="font-size:10px;color:var(--text-muted)">${esc(c.role||c.type||'')}</div></div>`;
          }).join('') + '</div>';
          break;
        }
        case 'image': {
          const im = snap.image_agent;
          const images = im?.images || {};
          let imgs = `<div class="img-detail-grid">`;
          let idx = 0;
          for (const ek in images) {
            for (const sk in images[ek]) {
              const img = images[ek][sk];
              idx++;
              const fn = img.filename || '';
              const url = fn ? `/storage/output/${fn}` : '';
              imgs += `<div class="img-detail-item"><span class="badge">#${idx}</span>${url ? `<img src="${url}" alt="ep${ek}_shot${sk}">` : '🖼️'}</div>`;
            }
          }
          imgs += '</div>';
          html = imgs;
          break;
        }
        case 'video': {
          const vd = snap.video_agent;
          const videos = vd?.videos || vd?.data?.videos || {};
          let vhtml = '<div class="video-detail-grid">';
          for (const ek in videos) {
            for (const sk in videos[ek]) {
              const v = videos[ek][sk];
              const src = v.local_path || '';
              const proxySrc = src.includes('/storage/output/') ? src : (src ? '/storage/output/' + src.split('/').pop() : '');
              vhtml += `<div class="video-detail-item">${proxySrc ? `<video controls preload="metadata"><source src="${proxySrc}" type="video/mp4"></video>` : '⏳'}</div>`;
            }
          }
          vhtml += '</div>';
          html = vhtml || '<div class="empty-state"><div class="icon">🎬</div>视频生成中…</div>';
          break;
        }
        case 'subtitle': {
          const sub = snap.subtitle_agent;
          const srt = sub?.srt_files || sub?.data?.srt_files || {};
          let lines = '';
          for (const ek in srt) {
            const content = srt[ek];
            if (typeof content === 'string') {
              content.split('\n').filter(l => l.includes('-->')).forEach(t => lines += `<div class="subtitle-line"><span class="time">${esc(t)}</span></div>`);
            }
          }
          html = lines || '<div class="empty-state"><div class="icon">📄</div>暂无字幕</div>';
          break;
        }
        case 'compose': {
          const cp = snap.compose_agent;
          const pub = cp?.published || cp?.data?.published || [];
          if (!pub.length) { html = '<div class="empty-state"><div class="icon">📦</div>合成中…</div>'; break; }
          let chtml = '';
          pub.forEach(p => {
            let src = p.final_path || '';
            if (src.includes('/storage/output/')) {
              const parts = src.split('/storage/output/');
              src = '/storage/output/' + parts[1];
            } else if (src) {
              src = '/storage/output/' + src.split('/').pop();
            }
            chtml += `<div class="compose-player"><strong style="font-size:13px">第${p.episode_number||'?'}集</strong><br>${src ? `<video controls preload="metadata"><source src="${src}" type="video/mp4"></video>` : '⏳'}</div>`;
          });
          html = chtml;
          break;
        }
        default: html = '<div class="empty-state">未知</div>';
      }
      openDetail(title, html, agent, review);
    } catch(e) {
      openDetail(AGENT_LABELS[agent]||agent, '<div class="empty-state"><div class="icon">⚠️</div>加载失败</div>', agent, review);
    }
  }

  // ═══ SSE / Backend Connection ═══
  function connectSSE() {
    // 用 Server-Sent Events
    const es = new EventSource(API + '/api/v1/pipeline/events/stream');
    es.onopen = () => {
      els.serverStatus.className = 'server-status';
      els.serverStatus.innerHTML = '<span class="status-dot"></span>GPU 在线';
    };
    es.addEventListener('log', e => {
      try {
        const d = JSON.parse(e.data);
        addLog(d.level||'INFO', d.agent, d.message);
      } catch(e) {}
    });
    es.addEventListener('status', e => {
      try {
        const d = JSON.parse(e.data);
        setCardState(d.agent, d.status||'running', d.progress_text||'');
      } catch(e) {}
    });
    es.addEventListener('agent_done', e => {
      try {
        const d = JSON.parse(e.data);
        setCardState(d.agent, d.review ? 'review' : 'done', d.summary_text||'');
      } catch(e) {}
    });
    es.addEventListener('agent_blocked', e => {
      try {
        const d = JSON.parse(e.data);
        setCardState(d.agent, 'review', d.reason||'等待审核');
        setProgress(parseInt(d.progress||'0'), `⏸️ 等待人工确认：${d.reason||'请检查'}`);
        state.pipelineId = d.pipeline_id || state.pipelineId;
        // 自动打开详情
        renderDetail(d.agent, d.reason||'等待审核');
      } catch(e) {}
    });
    es.addEventListener('review_approved', e => {
      try {
        const d = JSON.parse(e.data);
        const agent = d.agent || state.pausedAgent;
        if (agent) setCardState(agent, 'pending', '已确认，继续…');
        setProgress(parseInt(d.progress||'0'), '✅ 已确认，继续管线');
      } catch(e) {}
    });
    es.addEventListener('review_rejected', e => {
      try {
        const d = JSON.parse(e.data);
        const agent = d.agent || state.pausedAgent;
        if (agent) setCardState(agent, 'failed', '已拒绝');
        setProgress(0, '❌ 已拒绝，等待修改');
      } catch(e) {}
    });
    es.addEventListener('shot_done', e => {
      try {
        const d = JSON.parse(e.data);
        addLog('INFO', d.agent, `🖼 ${d.label||'产出'}完成 (${d.index}/${d.total})`);
      } catch(e) {}
    });
    es.addEventListener('pipeline_done', e => {
      try {
        const d = JSON.parse(e.data);
        setProgress(100, '✅ 全部完成！');
        addLog('SUCCESS', '', '管线全部完成！');
        doneUI();
        refreshSnapshot();
      } catch(e) {}
    });
    es.addEventListener('pipeline_cancelled', e => {
      doneUI();
      addLog('WARN', '', '❌ 管线已取消');
      setProgress(0, '已取消');
    });
    es.onerror = () => {
      els.serverStatus.className = 'server-status disconnected';
      els.serverStatus.innerHTML = '<span class="status-dot"></span>断线重连…';
    };
    return es;
  }

  // 降级：没有 SSE 就用 WS + 轮询
  let eventSource = null;
  function connectBackend() {
    eventSource = connectSSE();
    // fallback: 5s 轮询
    setInterval(() => {
      if (state.pipelineId) refreshSnapshot();
    }, 5000);
  }

  // ═══ Snapshot Refresh ═══
  async function refreshSnapshot() {
    try {
      const resp = await fetch(API + '/api/v1/pipeline/snapshot/latest', { mode:'cors' });
      if (!resp.ok) return;
      const snap = await resp.json();
      updateAssets(snap);
      updateOverview(snap);
      // 如果有合成结果就展示
      if (snap.compose_agent?.published) {
        setCardState('compose', 'done', `${snap.compose_agent.published.length}集`);
      }
      if (snap.image_agent?.images) {
        const imgs = snap.image_agent.images;
        let total = 0;
        for (const k in imgs) total += Object.keys(imgs[k]).length;
        setCardState('image', 'done', `${total}张`);
      }
      if (snap.character_agent?.characters) {
        setCardState('character', 'done', `${snap.character_agent.characters.length}角色`);
      }
      if (snap.storyboard_agent?.episodes) {
        const eps = snap.storyboard_agent.episodes;
        let totalShots = 0;
        eps.forEach(ep => totalShots += (ep.shots||[]).length);
        setCardState('storyboard', 'done', `${eps.length}集/${totalShots}镜头`);
      }
      if (snap.script_agent?.episodes) {
        setCardState('script', 'done', `${snap.script_agent.episodes.length}集`);
      }
      if (snap.video_agent?.videos) {
        setCardState('video', 'done', `视频就绪`);
      }
    } catch(e) {}
  }

  // ═══ Assets & Overview ═══
  function updateAssets(snap) {
    const ch = snap.character_agent?.characters || [];
    els.assetChars.innerHTML = ch.map(c => {
      const img = c.image || (c.asset?.controlnet_ref_path || '');
      return `<div class="asset-item">${img ? `<img src="${img}">` : '<div style="font-size:20px">👤</div>'}<div class="name">${esc(c.name)}</div><div class="count">${c.reuse_count||1}次</div></div>`;
    }).join('') || '<div style="font-size:11px;color:var(--text-muted)">暂无角色</div>';
  }

  function updateOverview(snap) {
    const sc = snap.script_agent;
    const sb = snap.storyboard_agent;
    const episodes = sc?.episodes || sb?.episodes || [];
    let totalShots = 0;
    episodes.forEach(ep => totalShots += (ep.shots||[]).length);
    els.ovEpisodes.textContent = episodes.length || '—';
    els.ovShots.textContent = totalShots || '—';
    els.ovStatus.textContent = state.running ? '执行中' : '空闲';
    if (state.startTime) els.ovTime.textContent = fmt((Date.now()-state.startTime)/1000);
  }

  // ═══ Pipeline Start ═══
  async function startPipeline() {
    const text = els.storyInput.value.trim();
    if (!text) return;

    state.startTime = Date.now();
    state.running = true;
    resetAllCards();
    closeDetail();
    setProgress(0, '启动中…');
    els.generateBtn.disabled = true;
    els.generateBtn.textContent = '⏳ 生成中…';
    els.stopBtn.style.display = '';

    try {
      const resp = await fetch(API + '/api/v1/pipeline/run', {
        method: 'POST', mode: 'cors',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({input: text}),
      });
      const r = await resp.json();
      if (r.success) {
        state.pipelineId = r.pipeline_id;
        setCardState('script', 'queued', '排队中');
        addLog('INFO', 'pipeline', `启动成功 ID=${r.pipeline_id}`);
      } else {
        throw new Error(r.error || '启动失败');
      }
    } catch(e) {
      doneUI();
      addLog('ERROR', 'pipeline', '启动失败: '+e.message);
    }
  }

  function doneUI() {
    els.generateBtn.disabled = false;
    els.generateBtn.textContent = '🚀 开始制作';
    els.stopBtn.style.display = 'none';
    state.running = false;
  }

  async function stopPipeline() {
    const pid = state.pipelineId;
    if (pid) {
      try {
        await fetch(API + '/api/v1/pipeline/cancel/' + pid, { method:'POST', mode:'cors' });
        addLog('WARN', 'pipeline', '🛑 停止请求已发送');
      } catch(e) {
        addLog('ERROR', 'pipeline', '停止失败: '+e.message);
      }
    }
    doneUI();
  }

  // ═══ History Drawer ═══
  async function openHistory() {
    els.drawerOverlay.classList.add('open');
    els.historyDrawer.classList.add('open');
    try {
      const resp = await fetch(API + '/api/v1/pipeline/history', { mode: 'cors' });
      const h = await resp.json();
      const items = h.history || [];
      els.drawerBody.innerHTML = items.length ? items.map(item => `
        <div class="history-item">
          <div class="title">${esc(item.input||'无标题')}</div>
          <div class="meta">状态: ${item.status||'?'} · ${item.created_at||''}</div>
          <div class="actions">
            <button class="btn btn-ghost btn-sm">查看详情</button>
            ${item.status === 'running' ? '<button class="btn btn-danger btn-sm">停止</button>' : ''}
          </div>
        </div>
      `).join('') : '<div class="empty-state"><div class="icon">📂</div>暂无历史任务</div>';
    } catch(e) {
      els.drawerBody.innerHTML = '<div class="empty-state">加载失败</div>';
    }
  }

  function closeHistory() {
    els.drawerOverlay.classList.remove('open');
    els.historyDrawer.classList.remove('open');
  }

  // ═══ Tab ═══
  function bindTabs() {
    els.tabBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        els.tabBtns.forEach(b => b.classList.remove('active'));
        els.tabContents.forEach(c => c.classList.add('hidden'));
        btn.classList.add('active');
        const tab = btn.dataset.tab;
        const content = document.getElementById('tab-'+tab);
        if (content) content.classList.remove('hidden');
      });
    });
  }

  // ═══ Init ═══
  function init() {
    el();
    resetAllCards();
    setProgress(0, '就绪');
    els.logbar.classList.remove('expanded');
    els.detailSection.style.display = 'none';

    els.generateBtn.addEventListener('click', startPipeline);
    els.stopBtn.addEventListener('click', stopPipeline);
    els.detailClose.addEventListener('click', closeDetail);
    els.drawerClose.addEventListener('click', closeHistory);
    els.drawerOverlay.addEventListener('click', closeHistory);
    els.historyBtn.addEventListener('click', openHistory);
    els.logHandle.addEventListener('click', () => {
      els.logbar.classList.toggle('expanded');
    });
    bindTabs();
    bindCardClicks();

    // 连接后端
    connectBackend();
    // 加载已有数据
    setTimeout(refreshSnapshot, 1000);
    // 心跳
    setInterval(() => {
      fetch(API+'/health', {mode:'cors'}).catch(() => {});
    }, 30000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
