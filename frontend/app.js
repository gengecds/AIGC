/**
 * AI 漫剧工坊 — 前端控制器（本地模式）
 * 状态机：空闲 → 排队 → 执行⏳ → ⏸️待审核 / ❌失败 / ✅完成
 * 通过 SSE 实时接收管线进度，7 步卡片联动更新
 */
(function () {
  'use strict';

  const API = 'http://127.0.0.1:8888';
  const AGENTS = ['research', 'script', 'storyboard', 'character', 'image', 'video', 'subtitle', 'compose', 'audio', 'publish'];
  const AGENT_LABELS = { research: '方案', script: '剧本', storyboard: '分镜', character: '定妆照', image: '出图', video: '视频', subtitle: '字幕', compose: '合成', audio: '音频', publish: '发布' };
  // 前端卡片短名 → 后端 Agent 名（checkpoint / snapshot / submit 的键名）
  const BACKEND_AGENT = {
    research: 'research_agent', script: 'script_agent', storyboard: 'storyboard_agent',
    character: 'character_agent', image: 'image_agent', video: 'video_agent',
    subtitle: 'subtitle_agent', compose: 'video_compose_agent', audio: 'audio_agent',
    publish: 'publish_agent',
  };
  // 反向映射：后端完整 Agent 名 → 前端卡片短名（SSE 事件用的是完整名）
  const SHORT_AGENT = {};
  Object.keys(BACKEND_AGENT).forEach((s) => { SHORT_AGENT[BACKEND_AGENT[s]] = s; });
  function normAgent(a) { return SHORT_AGENT[a] || a; }

  // ═══ 认证工具 ═══
  function getToken() { return localStorage.getItem('aigc_token') || ''; }
  function setToken(t) { localStorage.setItem('aigc_token', t || ''); }
  // 统一请求封装：自动带 Authorization 头 + JSON
  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (opts.body && typeof opts.body !== 'string') {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const token = getToken();
    if (token) headers['Authorization'] = 'Bearer ' + token;
    const resp = await fetch(API + path, Object.assign({}, opts, { headers, mode: 'cors' }));
    if (resp.status === 401) {
      // token 失效 → 回到登录页
      setToken('');
      showLogin();
      throw new Error('登录已失效');
    }
    return resp;
  }

  // ═══ 登录控制 ═══
  function showLogin() {
    const ov = document.getElementById('loginOverlay');
    if (ov) ov.style.display = 'flex';
  }
  function hideLogin() {
    const ov = document.getElementById('loginOverlay');
    if (ov) ov.style.display = 'none';
    const ua = document.getElementById('userArea');
    if (ua) ua.style.display = '';
  }
  async function checkLogin() {
    const token = getToken();
    if (!token) { showLogin(); return; }
    try {
      const resp = await api('/api/auth/me');
      if (resp.ok) {
        const d = await resp.json();
        const un = document.getElementById('userName');
        if (un) un.textContent = d.user.username;
        hideLogin();
      } else {
        setToken('');
        showLogin();
      }
    } catch (_) {
      // 后端未启动时先展示登录页，等待连接
      showLogin();
    }
  }

  async function doAuth(mode, username, password) {
    const path = mode === 'register' ? '/api/auth/register' : '/api/auth/login';
    const resp = await fetch(API + path, {
      method: 'POST', mode: 'cors',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const d = await resp.json();
    if (!resp.ok || !d.success) throw new Error(d.detail || d.error || '认证失败');
    setToken(d.token);
    const un = document.getElementById('userName');
    if (un) un.textContent = d.user.username;
    hideLogin();
  }

  function bindLogin() {
    const overlay = document.getElementById('loginOverlay');
    const form = document.getElementById('loginForm');
    if (!overlay || !form) return;
    let mode = 'login';
    // 登录/注册 tab 切换
    overlay.querySelectorAll('.login-tab').forEach((btn) => {
      btn.addEventListener('click', () => {
        mode = btn.dataset.mode;
        overlay.querySelectorAll('.login-tab').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const submit = document.getElementById('loginSubmit');
        if (submit) submit.textContent = mode === 'register' ? '注册并进入' : '进入工作台';
      });
    });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const username = document.getElementById('loginUsername').value.trim();
      const password = document.getElementById('loginPassword').value;
      const errEl = document.getElementById('loginError');
      if (errEl) errEl.textContent = '';
      try {
        await doAuth(mode, username, password);
      } catch (err) {
        if (errEl) errEl.textContent = err.message;
      }
    });
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
      logoutBtn.addEventListener('click', async () => {
        try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) {}
        setToken('');
        showLogin();
      });
    }
  }

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  let state = {
    pipelineId: null,
    running: false,
    startTime: null,
    cards: {},
    logCount: 0,
    selectedStyles: ['写实风格'],
    hasCheckpoint: false,
  };

  const els = {};
  function collectEls() {
    els.storyInput = $('#storyInput');
    els.generateBtn = $('#generateBtn');
    els.resumeBtn = $('#resumeBtn');
    els.stopBtn = $('#stopBtn');
    els.progressText = $('#progressText');
    els.progressPct = $('#progressPct');
    els.progressFill = $('#progressFill');
    els.serverStatus = $('#serverStatus');
    els.logList = $('#logList');
    els.logCount = $('#logCount');
    els.logbar = $('#logbar');
    els.logHandle = $('#logHandle');
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
    els.modelLibBtn = $('#modelLibBtn');
    els.modelLibDrawer = $('#modelLibDrawer');
    els.modelLibBody = $('#modelLibBody');
    els.modelLibClose = $('#modelLibClose');
    els.skillsBtn = $('#skillsBtn');
    els.skillsDrawer = $('#skillsDrawer');
    els.skillsBody = $('#skillsBody');
    els.skillsClose = $('#skillsClose');
    els.assetChars = $('#assetChars');
    els.assetScenes = $('#assetScenes');
    els.imgModalOverlay = $('#imgModalOverlay');
    els.imgModalClose = $('#imgModalClose');
    els.imgModalImg = $('#imgModalImg');
    els.imgModalName = $('#imgModalName');
    els.imgModalMeta = $('#imgModalMeta');
    els.styleChips = $('#styleChips');
    els.styleInfo = $('#styleInfo');
    els.ovStatus = $('#ovStatus');
    els.ovEpisodes = $('#ovEpisodes');
    els.ovShots = $('#ovShots');
    els.ovTime = $('#ovTime');
    els.ovTitle = $('#ovTitle');
    els.ovRemain = $('#ovRemain');
    els.checkpointList = $('#checkpointList');
  }

  function esc(t) {
    if (!t) return '';
    const d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML;
  }
  function fmt(t) {
    const m = Math.floor(t / 60);
    const s = Math.floor(t % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }
  // 兼容字段可能为字符串或数组：数组用分隔符连接，字符串原样返回
  function list2str(v, sep = ' / ') {
    if (v == null) return '';
    if (Array.isArray(v)) return v.join(sep);
    return String(v);
  }
  // 多风格：当前选中的风格数组（顺序即优先级，第一个为主风格）
  function selStyles() {
    return (state.selectedStyles && state.selectedStyles.length) ? state.selectedStyles : ['写实风格'];
  }
  // 主风格（决定出图/视频底模等模型）
  function primaryStyle() {
    return selStyles()[0] || '写实风格';
  }
  function primaryStyleEntry() {
    return STYLES[primaryStyle()] || {};
  }
  // 图片路径解析：SD/LTX 生成的图在 ComfyUI 输出目录，映射为 /comfyui-output/
  // 必须带上后端 origin —— 本页常跑在 Vite dev server(5173) 上，那里没有
  // /comfyui-output、/storage/output 这些静态挂载，用站内相对路径会全部 404，
  // 资产库与出图详情因此只能看到占位符（这是「人物图看不到」的根因）。
  function imgUrl(name) {
    if (!name) return '';
    if (name.startsWith('http')) return name;
    if (name.startsWith('/')) return API + name;
    // filename 可能是相对路径（如 storage/output/shot_1.png），补前缀即可访问
    if (name.startsWith('storage/output/')) return API + '/' + name;
    // 裸文件名（character_agent 的 portrait_path 只存文件名，无任何目录信息）
    return API + '/comfyui-output/' + name.split('/').pop();
  }

  // ═══ 卡片状态机 ═══
  function setCardState(agent, status, meta) {
    const card = document.querySelector(`.pipe-card[data-agent="${agent}"]`);
    if (!card) return;
    const all = ['idle', 'queued', 'running', 'review', 'done', 'failed'];
    all.forEach((s) => card.classList.remove('state-' + s));
    card.classList.add('state-' + status);

    const statusLabels = {
      idle: '空闲', queued: '排队中', running: '执行中…',
      review: '待确认', done: '完成', failed: '失败',
    };
    const stEl = document.getElementById('status-' + agent);
    if (stEl) stEl.textContent = meta ? `${statusLabels[status] || status} · ${meta}` : (statusLabels[status] || status);
    state.cards[agent] = { status, meta };
  }

  function resetAllCards() {
    AGENTS.forEach((a) => setCardState(a, 'idle', ''));
  }

  // ═══ 进度 ═══
  function setProgress(pct, text) {
    if (els.progressFill) els.progressFill.style.width = Math.min(100, Math.max(0, pct)) + '%';
    if (els.progressText) els.progressText.textContent = text || (pct ? pct + '%' : '空闲');
    if (els.progressPct) {
      if (pct === 100) {
        els.progressPct.textContent = '✅ 全部完成';
      } else {
        els.progressPct.textContent = `${pct}%`;
      }
    }
  }

  // ═══ 日志 ═══
  function addLog(level, agent, msg) {
    if (!els.logList) return;
    const entry = document.createElement('div');
    entry.className = 'log-entry ' + level;
    const ts = new Date().toTimeString().slice(0, 8);
    entry.innerHTML = `<span class="time">${ts}</span>[${AGENT_LABELS[agent] || agent}] ${esc(msg)}`;
    els.logList.prepend(entry);
    state.logCount++;
    if (els.logCount) els.logCount.textContent = state.logCount;
    if (els.logbar && !els.logbar.classList.contains('expanded')) {
      els.logbar.classList.add('expanded');
    }
  }

  // ═══ 详情面板 ═══
  let _detailAgent = '';
  let _detailReviewReason = '';

  function openDetail(title, html, agent, reviewReason) {
    _detailAgent = agent || '';
    _detailReviewReason = reviewReason || '';
    if (els.detailTitle) els.detailTitle.textContent = title;
    if (els.detailBody) els.detailBody.innerHTML = html;
    if (agent && reviewReason && els.detailActions) {
      els.detailActions.style.display = 'flex';
      if (isApprovalReview(agent, reviewReason)) {
        els.detailActions.innerHTML = `
        <div class="review-bar">
          <span>⏸️ 正在等待你完善「${esc(AGENT_LABELS[agent] || agent)}」</span>
          <div class="review-btns">
            <button class="btn btn-primary btn-sm" onclick="window._submitEdit()">💾 保存修改并继续</button>
            <button class="btn btn-ghost btn-sm" onclick="window._approveReview()">✅ 不修改，直接继续</button>
            <button class="btn btn-danger btn-sm" onclick="window._rejectReview()">🔄 拒绝重跑</button>
          </div>
        </div>`;
      } else {
        els.detailActions.innerHTML = `
        <div class="review-bar">
          <span>⏸️ 等待人工确认：${esc(reviewReason)}</span>
          <div class="review-btns">
            <button class="btn btn-primary btn-sm" onclick="window._approveReview()">✅ 确认继续</button>
            <button class="btn btn-danger btn-sm" onclick="window._rejectReview()">🔄 拒绝重跑</button>
          </div>
        </div>`;
      }
    } else if (els.detailActions) {
      els.detailActions.style.display = 'none';
    }
    if (els.detailSection) els.detailSection.style.display = '';
  }

  window._approveReview = async () => {
    const pid = state.pipelineId;
    if (!pid) return;
    try {
      await api('/api/v1/pipeline/approve/' + pid, { method: 'POST' });
      addLog('SUCCESS', 'review', '已确认，管线继续');
      if (els.detailActions) els.detailActions.style.display = 'none';
    } catch (e) {
      addLog('ERROR', 'review', '确认失败: ' + e.message);
    }
  };

  window._rejectReview = async () => {
    const pid = state.pipelineId;
    if (!pid) return;
    try {
      await api('/api/v1/pipeline/reject/' + pid, { method: 'POST', body: {} });
      addLog('WARN', 'review', '已拒绝，等待重跑');
      if (els.detailActions) els.detailActions.style.display = 'none';
    } catch (e) {
      addLog('ERROR', 'review', '拒绝失败: ' + e.message);
    }
  };

  window._submitEdit = async () => {
    const pid = state.pipelineId;
    if (!pid) { addLog('ERROR', 'pipeline', '没有进行中的管线'); return; }
    const roots = document.querySelectorAll('#detailBody [data-edit-root]');
    if (!roots.length) { addLog('ERROR', 'pipeline', '没有可提交的内容'); return; }
    const body = {};
    let hasContent = false;
    roots.forEach((r) => {
      const name = r.getAttribute('data-edit-root');
      const value = _edit[name];
      if (value && typeof value === 'object' && Object.keys(value).length) {
        body[BACKEND_AGENT[name] || name] = value;
        hasContent = true;
      }
    });
    if (!hasContent) { addLog('ERROR', 'pipeline', '没有可提交的内容'); return; }
    try {
      await api('/api/v1/pipeline/submit/' + pid, { method: 'POST', body });
      addLog('SUCCESS', 'pipeline', '💾 修改稿已提交，管线继续');
      if (els.detailActions) els.detailActions.style.display = 'none';
    } catch (e) {
      addLog('ERROR', 'pipeline', '提交失败: ' + e.message);
    }
  };

  function closeDetail() {
    if (els.detailSection) els.detailSection.style.display = 'none';
    if (els.detailActions) els.detailActions.style.display = 'none';
    _detailAgent = '';
    _detailReviewReason = '';
    _edit = {};
  }

  // ═══ 结构化就地编辑（所有步骤通用）═══
  // _edit[步骤短名] = 编辑中的内容对象（方案/剧本/分镜/角色/出图/视频/字幕/合成/音频）
  let _edit = {};

  const OBJ_ARRAY_KEYS = {
    characters: 1, episodes: 1, dialogues: 1, candidate_words: 1,
    shots: 1, subtitles: 1, published: 1, voices: 1,
  };
  const EDIT_LABELS = {
    title: '标题', target_audience: '目标人群', style_direction: '风格方向',
    narrative_structure: '叙事结构', bgm_mood: 'BGM 情绪', key_words: '关键词',
    render_engine_words: '渲染引擎', lighting_words: '光影强化', camera_words: '镜头语言',
    copy_points: '文案要点', scene_sounds: '场景声效', reference_cases: '参考案例',
    candidate_words: '候选词', zh: '中文', en: '英文', note: '备注',
    genre: '题材', summary: '剧情梗概', characters: '角色', episodes: '分集',
    episode_number: '集数', plot: '剧情', dialogues: '对白',
    scene: '场景', character: '角色名', line: '台词', name: '名称', gender: '性别',
    age: '年龄', appearance: '外貌', personality: '性格', role: '定位',
    // 分镜
    shots: '分镜', shot_id: '镜头号', shot_type: '景别', duration: '时长(s)',
    camera_movement: '运镜', action: '动作', background: '背景', lighting: '光影',
    dialogue: '对白', sd_prompt: '出图提示词', sd_negative: '负向提示词',
    transition: '转场', desc: '画面描述',
    // 定妆照 / 角色
    status: '状态', asset: '资产', portrait_path: '定妆照', controlnet_ref_path: '参考图',
    // 字幕
    subtitles: '字幕', srt_path: '字幕文件', text: '文本', start: '开始', end: '结束',
    // 音频
    voices: '配音', final_video: '成片', audio_track: '音频轨', bgm: 'BGM',
    offset: '偏移', files: '文件', manifest_path: '清单',
  };
  const EDIT_TEXTAREA_KEYS = {
    summary: 1, plot: 1, style_direction: 1, target_audience: 1, narrative_structure: 1,
    appearance: 1, personality: 1, line: 1, note: 1, scene: 1,
    action: 1, background: 1, lighting: 1, dialogue: 1, sd_prompt: 1, sd_negative: 1, desc: 1, text: 1,
  };
  const OBJ_TEMPLATES = {
    characters: { name: '', gender: '', age: '', appearance: '', personality: '', role: '' },
    episodes: { episode_number: 1, title: '', plot: '', dialogues: [{ scene: '', character: '', line: '' }] },
    dialogues: { scene: '', character: '', line: '' },
    candidate_words: { zh: '', en: '', note: '' },
    shots: { shot_id: '', shot_type: '', duration: '', camera_movement: '', desc: '', sd_prompt: '', sd_negative: '' },
    subtitles: { episode_number: 1, srt_path: '' },
    published: { episode_number: 1, file_path: '' },
    voices: { text: '', offset: 0, duration: '' },
  };

  function setByPath(root, path, value) {
    const parts = path.split('.');
    let o = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts[i];
      if (o[p] == null) o[p] = /^\d+$/.test(parts[i + 1]) ? [] : {};
      o = o[p];
    }
    o[parts[parts.length - 1]] = value;
  }
  function getByPath(root, path) {
    return path.split('.').reduce((o, p) => (o == null ? undefined : o[p]), root);
  }
  function emptyClone(v) {
    if (Array.isArray(v)) return v.map(emptyClone);
    if (v && typeof v === 'object') {
      const o = {};
      for (const k of Object.keys(v)) o[k] = emptyClone(v[k]);
      return o;
    }
    return typeof v === 'number' ? v : '';
  }

  // 递归渲染可编辑字段表
  function editHtml(value, path) {
    const key = path ? path.split('.').pop() : '';
    if (typeof value === 'string') {
      const long = value.length > 40 || value.includes('\n') || EDIT_TEXTAREA_KEYS[key];
      return long
        ? `<textarea class="efi efta" data-path="${path}">${esc(value)}</textarea>`
        : `<input class="efi" data-path="${path}" value="${esc(value)}">`;
    }
    if (typeof value === 'number') {
      return `<input class="efi" type="number" data-path="${path}" value="${value}">`;
    }
    if (Array.isArray(value)) {
      const isStringArr = !OBJ_ARRAY_KEYS[key] && value.every((v) => typeof v === 'string');
      let inner = '';
      if (isStringArr) {
        inner = value.map((v, i) =>
          `<div class="ef-row"><input class="efi" data-path="${path}.${i}" value="${esc(v)}">` +
          `<button type="button" class="ef-del" data-del-path="${path}.${i}" title="删除">×</button></div>`
        ).join('');
      } else {
        inner = value.map((v, i) =>
          `<div class="ef-card" data-card="${path}.${i}">` + editHtml(v, path + '.' + i) +
          `<button type="button" class="ef-del" data-del-path="${path}.${i}" title="删除">×</button></div>`
        ).join('');
      }
      return `<div class="ef-list" data-list="${path}">${inner}` +
        `<button type="button" class="ef-add" data-add-list="${path}">＋ 添加</button></div>`;
    }
    if (value && typeof value === 'object') {
      const rows = Object.keys(value).map((k) => {
        const sub = value[k];
        const subPath = path ? path + '.' + k : k;
        const label = EDIT_LABELS[k] || k;
        return `<div class="ef-field"><div class="ef-label">${esc(label)}</div>` + editHtml(sub, subPath) + '</div>';
      }).join('');
      return `<div class="ef-obj">${rows}</div>`;
    }
    return '';
  }

  function rerenderEditRoot(rootEl) {
    const name = rootEl.getAttribute('data-edit-root');
    const model = _edit[name];
    const label = rootEl.getAttribute('data-edit-label') || (AGENT_LABELS[name] || name) + '（可编辑）';
    rootEl.innerHTML = `<div class="edit-label">${esc(label)}</div>` + editHtml(model, '');
  }

  function onEditAdd(btn) {
    const root = btn.closest('[data-edit-root]');
    if (!root) return;
    const model = _edit[root.getAttribute('data-edit-root')];
    const listPath = btn.getAttribute('data-add-list');
    const key = listPath.split('.').pop();
    const arr = getByPath(model, listPath);
    if (!Array.isArray(arr)) return;
    const tpl = arr.length ? emptyClone(arr[0]) : (OBJ_TEMPLATES[key] || {});
    if (key === 'episodes') tpl.episode_number = (Number(tpl.episode_number) || arr.length) + 1;
    arr.push(tpl);
    rerenderEditRoot(root);
  }

  function onEditDel(btn) {
    const root = btn.closest('[data-edit-root]');
    if (!root) return;
    const model = _edit[root.getAttribute('data-edit-root')];
    const delPath = btn.getAttribute('data-del-path');
    const segs = delPath.split('.');
    const idxStr = segs.pop();
    const idx = Number(idxStr);
    const parentPath = segs.join('.');
    const parent = getByPath(model, parentPath);
    if (Array.isArray(parent)) parent.splice(idx, 1);
    else delete parent[idxStr];
    rerenderEditRoot(root);
  }

  // ═══ 通用编辑助手（所有步骤）═══
  // 该步骤当前处于「可编辑确认断点」？
  function isApprovalReview(agent, review) {
    if (!review) return false;
    if (review === 'research_approval' || review === 'script_approval') return true;
    return review === (BACKEND_AGENT[agent] + '_approval');
  }
  // 渲染一个「可编辑根」：把 data 克隆进 _edit[name]，递归渲染可编辑字段
  function editableRoot(name, data, label) {
    _edit[name] = JSON.parse(JSON.stringify(data));
    return `<div data-edit-root="${name}" data-edit-label="${esc(label)}" class="ef-root">` +
      `<div class="edit-label">${esc(label)}</div>` + editHtml(_edit[name], '') + `</div>`;
  }
  // 已完成步骤右下角的「编辑」入口
  function editToggleBtn(agent) {
    return `<div class="edit-fab-wrap"><button class="btn btn-ghost btn-sm" onclick="window._startDirectEdit('${agent}')">✏️ 编辑此步骤</button></div>`;
  }

  // 直接编辑某个已完成步骤（不阻断管线）：走 /api/v1/pipeline/edit/{backend_agent}
  window._startDirectEdit = async (agent) => {
    try {
      const resp = await api('/api/v1/pipeline/snapshot/latest');
      if (!resp.ok) { addLog('ERROR', agent, '读取快照失败'); return; }
      const snap = await resp.json();
      const data = snap[BACKEND_AGENT[agent]] || snap[agent];
      if (!data) { addLog('WARN', agent, '该步骤暂无数据可编辑'); return; }
      _edit[agent] = JSON.parse(JSON.stringify(data));
      const label = `✏️ ${AGENT_LABELS[agent] || agent} 编辑（保存后写回该步骤结果，不阻断管线）`;
      if (els.detailBody) {
        els.detailBody.innerHTML = `<div class="edit-hint">💡 直接修改下方内容，保存后写回「${esc(AGENT_LABELS[agent] || agent)}」的当前结果（不会重跑管线）。</div>` +
          `<div data-edit-root="${agent}" data-edit-label="${esc(label)}" class="ef-root">` +
          `<div class="edit-label">${esc(label)}</div>` + editHtml(_edit[agent], '') + `</div>`;
      }
      if (els.detailActions) {
        els.detailActions.style.display = 'flex';
        els.detailActions.innerHTML = `
          <div class="review-bar">
            <span>✏️ 编辑「${esc(AGENT_LABELS[agent] || agent)}」</span>
            <div class="review-btns">
              <button class="btn btn-primary btn-sm" onclick="window._saveDirectEdit('${agent}')">💾 保存修改</button>
              <button class="btn btn-ghost btn-sm" onclick="window._closeEditDirect()">取消</button>
            </div>
          </div>`;
      }
    } catch (e) { addLog('ERROR', agent, '进入编辑失败: ' + e.message); }
  };
  window._saveDirectEdit = async (agent) => {
    const data = _edit[agent];
    if (!data) { addLog('ERROR', agent, '没有可保存的内容'); return; }
    try {
      const resp = await api('/api/v1/pipeline/edit/' + BACKEND_AGENT[agent], { method: 'PUT', body: data });
      if (!resp.ok) { addLog('ERROR', agent, '保存失败: ' + (await resp.text()).slice(0, 200)); return; }
      addLog('SUCCESS', agent, '💾 ' + (AGENT_LABELS[agent] || agent) + ' 已更新');
      if (els.detailActions) els.detailActions.style.display = 'none';
      renderDetail(agent, '');
    } catch (e) { addLog('ERROR', agent, '保存失败: ' + e.message); }
  };
  window._closeEditDirect = () => {
    if (els.detailActions) els.detailActions.style.display = 'none';
  };

  // ═══ 每个步骤的模型选择器（按步骤类别区分，只列本地已有模型）═══
  // 步骤 → 该步骤实际使用的模型类别（避免把出图模型配到视频步骤上）
  function modelCatsFor(agent) {
    switch (agent) {
      case 'research': case 'script': case 'storyboard':
        return [
          { cat: 'llm', label: '🧠 文字模型（LLM）', note: '用于 方案 / 剧本 / 分镜 生成' },
        ];
      case 'character': case 'image':
        return [
          { cat: 'image', label: '🎬 出图模型（checkpoint）', note: '用于 角色定妆 / 分镜出图' },
          { cat: 'image_type', label: '🔧 出图引擎（image_model_type）', note: '' },
        ];
      case 'video':
        return [
          { cat: 'video', label: '🎥 视频模型', note: '用于 图生视频' },
          { cat: 'video_type', label: '🎛️ 视频引擎（video_model_type）', note: '' },
        ];
      case 'subtitle':
        return [
          { cat: 'subtitle_mode', label: '📝 字幕生成方式', note: 'rule=本地规则引擎；llm=用大模型润色/翻译' },
          { cat: 'subtitle_lang', label: '🌐 字幕语言', note: 'llm 模式下生效（zh/en/中英双语）' },
        ];
      case 'audio':
        return [
          { cat: 'tts_engine', label: '🎧 配音引擎（TTS）', note: 'piper / voxcpm' },
          { cat: 'voice_prompt', label: '🗣 音色提示词（voice_prompt）', note: '描述配音音色' },
        ];
      default: return null; // 无需选择模型的步骤
    }
  }
  // 筛选一个分类的下拉 option 列表（保证当前已选值始终可选）
  function _opts(list, current) {
    return (_opt(list.slice(), current))
      .map((m) => `<option value="${esc(m)}"${m === current ? ' selected' : ''}>${esc(m)}</option>`)
      .join('');
  }
  // 渲染「本步骤模型」选择器面板；无可选配置的步骤用注释说明
  function stepModelPanel(agent) {
    const cats = modelCatsFor(agent);
    if (!cats) {
      if (agent === 'compose') return '<div class="sp-note">🎞️ 合成：FFmpeg 拼接 + 字幕烧录 + 音频混轨，无须选择模型</div>';
      return '';
    }
    const cur = primaryStyle();
    const st = primaryStyleEntry();
    let fields = '';
    for (const c of cats) {
      if (c.cat === 'llm') {
        const curv = st.llm_model || '';
        fields += `<div class="sp-field"><label>${c.label}</label>` +
          `<select class="sp-select sp-llm">${_opts(LOCAL_MODELS.llm_models, curv) || '<option>—</option>'}</select></div>`;
      } else if (c.cat === 'image') {
        const curv = st.image_ckpt || '';
        fields += `<div class="sp-field"><label>${c.label}</label>` +
          `<select class="sp-select sp-ckpt">${_opts(LOCAL_MODELS.image_ckpts, curv) || '<option>—</option>'}</select></div>`;
      } else if (c.cat === 'image_type') {
        const curv = st.image_model_type || 'sd15';
        const opts = ['sd15', 'flux'].map((m) => `<option value="${m}"${m === curv ? ' selected' : ''}>${m}</option>`).join('');
        fields += `<div class="sp-field"><label>${c.label}</label><select class="sp-select sp-ctype">${opts}</select></div>`;
      } else if (c.cat === 'video') {
        const curv = st.video_model || '';
        fields += `<div class="sp-field"><label>${c.label}</label>` +
          `<select class="sp-select sp-vmodel">${_opts(LOCAL_MODELS.video_models, curv) || '<option>—</option>'}</select></div>`;
      } else if (c.cat === 'video_type') {
        const curv = st.video_model_type || 'ltx';
        const opts = ['ltx', 'wan', 'svd', 'hunyuan'].map((m) => `<option value="${m}"${m === curv ? ' selected' : ''}>${m}</option>`).join('');
        fields += `<div class="sp-field"><label>${c.label}</label><select class="sp-select sp-vtype">${opts}</select></div>`;
      } else if (c.cat === 'subtitle_mode') {
        const curv = st.subtitle_mode || 'rule';
        const opts = [['rule', '规则引擎（本地，默认）'], ['llm', '大模型润色/翻译']]
          .map(([m, lab]) => `<option value="${m}"${m === curv ? ' selected' : ''}>${lab}</option>`).join('');
        fields += `<div class="sp-field"><label>${c.label}</label><select class="sp-select sp-submode">${opts}</select></div>`;
      } else if (c.cat === 'subtitle_lang') {
        const curv = st.subtitle_lang || 'zh';
        const opts = [['zh', '中文（zh）'], ['en', '英文（en）'], ['biling', '中英双语（biling）']]
          .map(([m, lab]) => `<option value="${m}"${m === curv ? ' selected' : ''}>${lab}</option>`).join('');
        fields += `<div class="sp-field"><label>${c.label}</label><select class="sp-select sp-sublang">${opts}</select></div>`;
      } else if (c.cat === 'tts_engine') {
        const curv = st.tts_engine || 'voxcpm';
        const opts = ['piper', 'voxcpm'].map((m) => `<option value="${m}"${m === curv ? ' selected' : ''}>${m}</option>`).join('');
        fields += `<div class="sp-field"><label>${c.label}</label><select class="sp-select sp-tts">${opts}</select></div>`;
      } else if (c.cat === 'voice_prompt') {
        const curv = st.voice_prompt || '温柔甜美年轻女声，柔和有亲和力';
        const voices = [
          '温柔甜美年轻女声，柔和有亲和力',
          '沉稳磁性成熟男声，低沉有故事感',
          '清亮少年音，活泼有朝气',
          '知性优雅女声，从容有温度',
          '低沉大叔音，稳重可信',
          '活泼元气少女声，轻快明亮',
        ];
        const opts = voices.map((m) => `<option value="${esc(m)}"${m === curv ? ' selected' : ''}>${esc(m)}</option>`).join('');
        fields += `<div class="sp-field"><label>${c.label}</label><select class="sp-select sp-voice">${opts}</select></div>`;
      }
    }
    const labelMap = {
      llm: '文字', image: '出图', video: '视频',
      subtitle_mode: '字幕', subtitle_lang: '', tts_engine: '音频', voice_prompt: '',
    };
    const applyTags = Array.from(new Set(
      cats.map((c) => labelMap[c.cat]).filter(Boolean)
    )).join('/');
    return `<div class="sp-panel" data-sp="${esc(agent)}">
      <div class="sp-title">🛠 本步骤模型${cur ? '（风格：' + esc(cur) + '）' : ''}</div>
      <div class="sp-grid">${fields}</div>
      <button class="sp-apply" onclick="window.applyStepModel('${agent}', this)">✅ 应用本步骤模型</button>
      <div class="sp-hint">💡 ${applyTags ? '只列出你本机已安装的同类别模型（' + esc(applyTags) + '），' : ''}切换后点「应用」写入当前风格，后续该步骤即使用它。</div>
    </div>`;
  }
  // 应用步骤模型 → 写入当前风格配置
  window.applyStepModel = async (agent, btn) => {
    const panel = btn.closest('.sp-panel') || document.querySelector(`[data-sp="${agent}"]`);
    const cur = primaryStyle();
    if (!cur) { addLog('ERROR', agent, '请先在上方选择风格'); return; }
    const body = {};
    const pick = (sel, key) => { const el = panel && panel.querySelector(sel); if (el) body[key] = el.value; };
    pick('.sp-llm', 'llm_model');
    pick('.sp-ckpt', 'image_ckpt');
    pick('.sp-ctype', 'image_model_type');
    pick('.sp-vmodel', 'video_model');
    pick('.sp-vtype', 'video_model_type');
    pick('.sp-submode', 'subtitle_mode');
    pick('.sp-sublang', 'subtitle_lang');
    pick('.sp-tts', 'tts_engine');
    pick('.sp-voice', 'voice_prompt');
    if (!Object.keys(body).length) { addLog('WARN', agent, '该步骤没有可选的模型'); return; }
    try {
      const resp = await api(`/api/v1/pipeline/styles/${encodeURIComponent(cur)}`, { method: 'PUT', body });
      if (!resp.ok) { addLog('ERROR', agent, '应用模型失败: ' + (await resp.text()).slice(0, 200)); return; }
      const d = await resp.json();
      if (d.styles) STYLES = d.styles;
      const label = AGENT_LABELS[agent] || agent;
      addLog('SUCCESS', agent, `✅ 已为「${label}」应用模型（${cur}）: ${Object.values(body).join(', ')}`);
      if (cur && STYLES[cur]) renderStyleInfo(cur);
      const old = btn.textContent;
      btn.textContent = '✓ 已应用'; btn.classList.add('saved');
      setTimeout(() => { btn.textContent = old; btn.classList.remove('saved'); }, 1500);
    } catch (e) { addLog('ERROR', agent, '应用模型失败: ' + e.message); }
  };

  // ═══ 详情渲染 ═══
  async function renderDetail(agent, reviewReason) {
    agent = normAgent(agent);
    try {
      const resp = await api('/api/v1/pipeline/snapshot/latest').catch(() => null);
      if (!resp || !resp.ok) {
        openDetail(AGENT_LABELS[agent] || agent, '<div class="empty-state">后端离线，请稍后重试</div>', agent, reviewReason);
        return;
      }
      const snap = await resp.json();
      const title = AGENT_LABELS[agent] || agent;
      let review = reviewReason;
      if (!review && state.cards[agent]?.status === 'review') {
        review = state.cards[agent].meta || '请确认结果';
      }
      const approval = isApprovalReview(agent, review);
      const hintEdit = (t) => `<div class="edit-hint">💡 ${esc(t)}，改完点「💾 保存修改并继续」，确认后才进入下一步。</div>`;
      let html = '';

      switch (agent) {
        case 'research': {
          const rs = snap.research_agent;
          if (approval && rs) {
            html = hintEdit('方案已生成，可直接在下方修改方案内容（风格方向 / 叙事结构 / 渲染引擎 / 文案要点等）') +
              editableRoot('research', rs, '📋 方案（制作方案，可编辑）');
            break;
          }
          if (!rs) { html = '<div class="empty-state">暂无方案数据</div>'; break; }
          const rows = [
            ['方案标题', rs.title], ['目标人群', rs.target_audience],
            ['风格方向', rs.style_direction], ['叙事结构', rs.narrative_structure],
            ['BGM 情绪', rs.bgm_mood],
            ['渲染引擎', list2str(rs.render_engine_words)],
            ['光影强化', list2str(rs.lighting_words)],
            ['镜头语言', list2str(rs.camera_words)],
          ];
          html = rows.filter(([, v]) => v).map(([k, v]) =>
            `<div class="plan-row"><span class="plan-key">${esc(k)}</span><span class="plan-val">${esc(v)}</span></div>`
          ).join('') +
            ((rs.copy_points || []).length
              ? `<div class="side-label" style="margin-top:10px">📝 文案要点</div>` + rs.copy_points.map((p) => `<div class="dialogue">· ${esc(p)}</div>`).join('')
              : '') +
            ((rs.scene_sounds || []).length
              ? `<div class="side-label" style="margin-top:10px">🔊 场景声效</div>` + rs.scene_sounds.map((s) => `<div class="dialogue">· ${esc(s)}</div>`).join('')
              : '') +
            ((rs.reference_cases && rs.reference_cases.length)
              ? `<div class="side-label" style="margin-top:10px">🧠 参考案例（情报站/素材注入）</div>` +
                rs.reference_cases.map((c) =>
                  `<div class="dialogue">· ${esc(typeof c === 'string' ? c : (c && (c.summary || c.title || c.highlights)) || JSON.stringify(c))}</div>`
                ).join('')
              : '');
          html += editToggleBtn('research');
          break;
        }
        case 'script': {
          const sc = snap.script_agent;
          if (approval && sc) {
            const rs = snap.research_agent || {};
            let h = hintEdit('剧本已生成，可直接修改下方内容（标题 / 角色 / 对白 / 剧情等）');
            if (rs && Object.keys(rs).length) h += editableRoot('research', rs, '① 方案（制作方案，可编辑）');
            h += editableRoot('script', sc, '② 剧本（对白 / 角色 / 剧情，可编辑）');
            html = h;
            break;
          }
          if (!sc) { html = '<div class="empty-state">暂无剧本数据</div>'; break; }
          const episodes = sc.episodes || sc.data?.episodes || [];
          html = episodes.map((ep, i) => {
            const lines = ep.dialogues || [];
            // scene 显示：对白场景具体描述
            const sceneName = (ep.scene || ep.scene_desc || '').trim();
            return `<div style="margin-bottom:12px"><strong style="color:var(--gold)">第${ep.episode_number || i + 1}集 · ${esc(ep.title || '')}</strong>` +
              (ep.plot ? `<div class="dialogue" style="color:var(--text-2)">📖 ${esc(ep.plot)}</div>` : '') +
              (sceneName ? `<div class="dialogue" style="color:var(--text-2)">📍 ${esc(sceneName)}</div>` : '') +
              lines.map((d) => {
                // 兼容字段：line 是剧本/分镜输出的标准对白字段，text/content 是历史兼容
                const line = d.line || d.text || d.content || d.speech || '';
                const chr = d.character || d.char_name || '';
                return `<div class="dialogue"><span class="char-name">[${esc(chr)}]</span> <span class="text">${esc(line)}</span></div>`;
              }).join('') + '</div>';
          }).join('');
          html += editToggleBtn('script');
          break;
        }
        case 'storyboard': {
          const sb = snap.storyboard_agent;
          const eps = sb?.episodes || [];
          if (approval && sb) {
            html = hintEdit('分镜已生成，可直接修改镜头描述 / 出图提示词等') +
              editableRoot('storyboard', sb, '📽️ 分镜（可编辑）');
            break;
          }
          if (!eps.length) { html = '<div class="empty-state">暂无分镜数据</div>'; break; }
          let rows = '';
          eps.forEach((ep, ei) => {
            (ep.shots || []).forEach((sh, si) => {
              // 兼容取字段：type/shot_type；描述优先 desc 摘要，其次 action/background/dialogue
              const st = sh.shot_type || sh.type || '';
              const desc = sh.desc || sh.description || '';
              const action = sh.action || '';
              const bg = sh.background || '';
              const camera = sh.camera_movement || '';
              const dlg = sh.dialogue || '';
              rows += `<tr>
                <td>${ei + 1}.${sh.shot_id || si + 1}</td>
                <td>${esc(st)}</td>
                <td class="sb-desc">
                  ${desc ? `<div>${esc(desc)}</div>` : ''}
                  ${camera ? `<div class="sb-meta">🎥 ${esc(camera)}</div>` : ''}
                  ${bg && bg !== desc ? `<div class="sb-meta">🌄 ${esc(bg)}</div>` : ''}
                  ${dlg ? `<div class="sb-dlg">💬 ${esc(dlg)}</div>` : ''}
                </td>
                <td>${sh.duration || ''}s</td>
              </tr>`;
            });
          });
          html = `<table class="sb-table"><thead><tr><th style="width:50px">序号</th><th style="width:60px">景别</th><th>画面描述</th><th style="width:50px">时长</th></tr></thead><tbody>${rows}</tbody></table>`;
          html += editToggleBtn('storyboard');
          break;
        }
        case 'character': {
          const ch = snap.character_agent;
          const chars = ch?.characters || [];
          if (approval && ch) {
            html = hintEdit('定妆照已生成，可直接修改角色设定（年龄 / 性格 / 外貌等）') +
              editableRoot('character', ch, '👤 定妆照（可编辑）');
            break;
          }
          if (!chars.length) { html = '<div class="empty-state">暂无定妆照数据</div>'; break; }
          html = '<div class="char-detail-grid">' + chars.map((c) => {
            const imgSrc = c.image || c.asset?.controlnet_ref_path || c.portrait_path || '';
            return `<div class="char-detail-card"><div class="char-name">${esc(c.name)}</div>` +
              (imgSrc ? `<img src="${imgUrl(imgSrc)}" alt="${esc(c.name)}" onerror="this.style.display='none'">` : '<div class="empty-state">👤</div>') +
              `<div style="font-size:11px;color:var(--text-2);margin-top:5px">${esc(c.role || c.type || '')}</div></div>`;
          }).join('') + '</div>';
          html += editToggleBtn('character');
          break;
        }
        case 'image': {
          const im = snap.image_agent;
          const images = im?.images || {};
          if (approval && im) {
            html = hintEdit('出图已生成，可直接修改图片清单 / 提示词') +
              editableRoot('image', im, '🖼️ 出图（可编辑）');
            break;
          }
          let imgs = '<div class="img-detail-grid">';
          let idx = 0;
          for (const ek in images) {
            for (const sk in images[ek]) {
              const img = images[ek][sk];
              idx++;
              const fn = img.filename || '';
              const url = fn ? imgUrl(fn) : '';
              imgs += `<div class="img-detail-item"><span class="badge">#${idx}</span>${url ? `<img src="${url}" alt="shot${sk}">` : '<div class="empty-state">🖼️</div>'}</div>`;
            }
          }
          imgs += '</div>';
          html = imgs;
          html += editToggleBtn('image');
          break;
        }
        case 'video': {
          const vd = snap.video_agent;
          const videos = vd?.videos || vd?.data?.videos || {};
          if (approval && vd) {
            html = hintEdit('视频已生成，可直接修改视频清单 / 参数') +
              editableRoot('video', vd, '🎬 视频（可编辑）');
            break;
          }
          let vhtml = '<div class="video-detail-grid">';
          let cnt = 0;
          for (const ek in videos) {
            for (const sk in videos[ek]) {
              const v = videos[ek][sk];
              cnt++;
              const src = v.local_path || '';
              const proxySrc = src.includes('/storage/output/') ? src : (src ? '/storage/output/' + src.split('/').pop() : '');
              vhtml += `<div class="video-detail-item"><span class="badge">镜头 ${sk}</span>` +
                (proxySrc ? `<video controls preload="metadata" src="${proxySrc}"></video>` : '<div class="empty-state">⏳</div>') + '</div>';
            }
          }
          vhtml += '</div>';
          html = cnt ? vhtml : '<div class="empty-state">视频生成中…</div>';
          if (cnt) html += editToggleBtn('video');
          break;
        }
        case 'subtitle': {
          const sub = snap.subtitle_agent;
          const srt = sub?.srt_files || sub?.data?.srt_files || {};
          if (approval && sub) {
            html = hintEdit('字幕已生成，可直接修改字幕内容') +
              editableRoot('subtitle', sub, '📝 字幕（可编辑）');
            break;
          }
          let lines = '';
          for (const ek in srt) {
            const content = srt[ek];
            if (typeof content === 'string') {
              content.split('\n').filter((l) => l.includes('-->')).forEach((t) => {
                lines += `<div class="subtitle-line"><span class="time">${esc(t.split(' --> ')[0])}</span> → ${esc(t.split(' --> ')[1])}</div>`;
              });
            }
          }
          html = lines || '<div class="empty-state">暂无字幕</div>';
          if (lines) html += editToggleBtn('subtitle');
          break;
        }
        case 'compose': {
          const cp = snap.compose_agent || snap.video_compose_agent;
          const pub = cp?.published || cp?.data?.published || [];
          if (approval && cp) {
            html = hintEdit('成片已合成，可直接修改发布清单') +
              editableRoot('compose', cp, '🎞️ 合成（可编辑）');
            break;
          }
          if (!pub.length) { html = '<div class="empty-state">合成中…</div>'; break; }
          html = pub.map((p) => {
            let src = p.final_path || '';
            if (src.includes('/storage/output/')) src = '/storage/output/' + src.split('/storage/output/')[1];
            else if (src) src = '/storage/output/' + src.split('/').pop();
            const fname = (src || '').split('/').pop() || 'final.mp4';
            return `<div class="compose-player"><strong style="color:var(--gold)">第${p.episode_number || '?'}集 成片</strong><br>` +
              (src ? `<video controls preload="metadata" src="${src}"></video>` : '<div class="empty-state">⏳</div>') +
              (src ? `<a class="btn btn-primary btn-sm" style="margin-top:6px" href="${src}" download="${esc(fname)}">⬇️ 下载成片</a>` : '') + '</div>';
          }).join('');
          html += editToggleBtn('compose');
          break;
        }
        case 'audio': {
          const ad = snap.audio_agent;
          if (approval && ad) {
            html = hintEdit('音频合成已完成，可直接修改配音 / BGM 参数') +
              editableRoot('audio', ad, '🎧 音频（可编辑）');
            break;
          }
          if (!ad) { html = '<div class="empty-state">音频合成中…</div>'; break; }
          const voices = ad.voices || [];
          let vlist = voices.length
            ? '<div class="side-label" style="margin-top:0">🎙️ 配音（Piper 本地女声）</div>' + voices.map((v) =>
                `<div class="dialogue"><span class="char-name">${esc(v.text)}</span> <span style="color:var(--text-2);font-size:11px">@${v.offset}s · ${v.duration}s</span></div>`
              ).join('')
            : '';
          let video = '';
          let final = ad.final_video || '';
          if (final.includes('/storage/output/')) final = '/storage/output/' + final.split('/storage/output/')[1];
          else if (final) final = '/storage/output/' + final.split('/').pop();
          if (final) {
            const fname = final.split('/').pop() || 'final_audio.mp4';
            video = `<div class="compose-player"><strong style="color:var(--gold)">🎬 带音频成片（配音 + BGM + 音效）</strong><br>` +
              `<video controls preload="metadata" src="${final}"></video>` +
              `<a class="btn btn-primary btn-sm" style="margin-top:6px" href="${final}" download="${esc(fname)}">⬇️ 下载成片</a></div>`;
          }
          html = video + vlist + (ad.bgm ? `<div class="side-label">🎵 BGM：柔和钢琴伴奏 · ${ad.duration}s</div>` : '');
          html += editToggleBtn('audio');
          break;
        }
        case 'publish': {
          // 发布卡：汇总「发布清单 + manifest」，并复用带音频成片的预览/下载入口
          const pub = snap.publish_agent?.published || snap.publish_agent?.data?.published || [];
          const manifest = snap.publish_agent?.manifest_path || '';
          if (approval) {
            html = hintEdit('发布清单已就绪，可直接修改（发布条目 / manifest 路径）') +
              editableRoot('publish', snap.publish_agent || {}, '📦 发布（可编辑）');
            break;
          }
          if (!pub.length) { html = '<div class="empty-state">发布中…</div>'; break; }
          html = pub.map((p) => {
            let src = p.file_path || '';
            if (src.includes('/storage/output/')) src = '/storage/output/' + src.split('/storage/output/')[1];
            else if (src) src = '/storage/output/' + src.split('/').pop();
            const fname = (src || '').split('/').pop() || 'final.mp4';
            const size = p.file_size_kb ? `（${(p.file_size_kb / 1024).toFixed(2)} MB）` : '';
            return `<div class="compose-player"><strong style="color:var(--gold)">📦 第${p.episode_number || '?'}集 已发布 ${size}</strong><br>` +
              (src ? `<video controls preload="metadata" src="${src}"></video>` : '<div class="empty-state">⏳</div>') +
              (src ? `<a class="btn btn-primary btn-sm" style="margin-top:6px" href="${src}" download="${esc(fname)}">⬇️ 下载成片</a>` : '') +
              (manifest ? `<div style="margin-top:6px;font-size:11px;color:var(--text-2)">manifest: ${esc(manifest.split('/').pop())}</div>` : '') + '</div>';
          }).join('');
          html += editToggleBtn('publish');
          break;
        }
        default: html = '<div class="empty-state">未知</div>';
      }
      // 每个步骤顶部追加「本步骤模型」选择器（按类别区分，只列本地模型）
      html = stepModelPanel(agent) + html;
      openDetail(title, html, agent, review);
    } catch (e) {
      openDetail(AGENT_LABELS[agent] || agent, '<div class="empty-state">加载失败</div>', agent, reviewReason);
    }
  }

  // ═══ SSE 连接 ═══
  function connectSSE() {
    try {
      const es = new EventSource(API + '/api/v1/pipeline/events/stream');
      es.onopen = () => {
        if (els.serverStatus) {
          els.serverStatus.className = 'server-status';
          els.serverStatus.innerHTML = '<span class="dot"></span>本地引擎在线';
        }
      };
      es.addEventListener('log', (e) => {
        try {
          const d = JSON.parse(e.data);
          addLog(d.level || 'INFO', d.agent, d.message);
        } catch (_) { /* ignore */ }
      });
      es.addEventListener('pipeline_start', (e) => {
        try {
          const d = JSON.parse(e.data);
          state.pipelineId = d.pipeline_id;
          setProgress(1, '方案研究中…');
          addLog('INFO', 'pipeline', '🚀 管线已启动，开始制作');
        } catch (_) { /* ignore */ }
      });
      es.addEventListener('agent_start', (e) => {
        try {
          const d = JSON.parse(e.data);
          d.agent = normAgent(d.agent);
          setCardState(d.agent, 'running', '');
          const label = AGENT_LABELS[d.agent] || d.agent;
          setProgress(d.progress || 0, `${label} 执行中… (${d.step}/${d.total})`);
          addLog('INFO', d.agent, `▶ 开始执行（第 ${d.step}/${d.total} 步）`);
        } catch (_) { /* ignore */ }
      });
      es.addEventListener('agent_done', (e) => {
        try {
          const d = JSON.parse(e.data);
          d.agent = normAgent(d.agent);
          const m = d.meta || {};
          const brief = m.total_shots ? `${m.total_shots}镜` : (m.total_images ? `${m.total_images}张` : (m.characters ? `${m.characters}角色` : (m.episodes ? `${m.episodes}集` : '')));
          setCardState(d.agent, 'done', brief);
          setProgress(d.progress || 0, `${AGENT_LABELS[d.agent] || d.agent} 完成`);
          addLog('SUCCESS', d.agent, `✓ 完成（第 ${d.step}/${d.total} 步）${brief ? ' · ' + brief : ''}`);
        } catch (_) { /* ignore */ }
      });
      es.addEventListener('voice_plan_progress', (e) => {
        try {
          const d = JSON.parse(e.data);
          const done = d.done || 0, total = d.total || 0;
          if (!done) {
            addLog('INFO', 'audio', `🎙️ 配音预测量开始：共 ${total} 句（首次需加载模型，请稍候）`);
          } else {
            addLog('INFO', 'audio', `🎙️ 配音预测量 ${done}/${total} (${d.percent || 0}%) · ${d.character || '旁白'}：${d.text || ''}`);
          }
        } catch (_) { /* ignore */ }
      });
      es.addEventListener('agent_fail', (e) => {
        try {
          const d = JSON.parse(e.data);
          d.agent = normAgent(d.agent);
          setCardState(d.agent, 'failed', d.error || '失败');
          addLog('ERROR', d.agent, '执行失败: ' + (d.error || ''));
        } catch (_) { /* ignore */ }
      });
      es.addEventListener('agent_blocked', (e) => {
        try {
          const d = JSON.parse(e.data);
          d.agent = normAgent(d.agent);
          setCardState(d.agent, 'review', d.reason || '等待审核');
          setProgress(parseInt(d.progress || '0'), `⏸️ 等待人工确认：${d.reason || ''}`);
          addLog('WARN', d.agent, `⏸️ 等待审核/编辑：${d.reason || '等待人工确认'}`);
          state.pipelineId = d.pipeline_id || state.pipelineId;
          renderDetail(d.agent, d.reason || '等待审核');
          // 审核弹窗置顶高亮
          const ds = document.getElementById('detailSection');
          if (ds) { ds.style.display = ''; ds.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
        } catch (_) { /* ignore */ }
      });
      es.addEventListener('review_approved', () => {
        setProgress(0, '✅ 已确认，继续管线');
      });
      es.addEventListener('review_updated', () => {
        setProgress(0, '💾 修改稿已提交，管线继续');
      });
      es.addEventListener('review_rejected', () => {
        setProgress(0, '❌ 已拒绝，等待修改');
      });
      es.addEventListener('pipeline_done', () => {
        setProgress(100, '✅ 全部完成！');
        addLog('SUCCESS', 'pipeline', '管线全部完成！');
        doneUI();
        refreshSnapshot();
        // 自动展开「音频（带音频成片）」详情，方便立刻预览 / 下载成品
        setTimeout(() => renderDetail('audio', ''), 600);
      });
      es.addEventListener('notes_generated', (e) => {
        try {
          const d = JSON.parse(e.data);
          addLog('SUCCESS', 'pipeline', '📝 创作笔记已生成：' + (d.path || ''));
        } catch (_) { /* ignore */ }
      });
      es.addEventListener('pipeline_cancelled', () => {
        doneUI();
        addLog('WARN', 'pipeline', '管线已取消');
        setProgress(0, '已取消');
      });
      es.addEventListener('pipeline_fail', (e) => {
        try {
          const d = JSON.parse(e.data);
          doneUI();
          setProgress(0, '❌ 管线失败');
          addLog('ERROR', 'pipeline', '管线失败: ' + (d.error || ''));
        } catch (_) { /* ignore */ }
      });
      es.onerror = () => {
        if (els.serverStatus) {
          els.serverStatus.className = 'server-status disconnected';
          els.serverStatus.innerHTML = '<span class="dot"></span>断线重连…';
        }
      };
      return es;
    } catch (e) {
      return null;
    }
  }

  function connectBackend() {
    connectSSE();
    setInterval(() => {
      if (state.pipelineId) refreshSnapshot();
    }, 6000);
    // 运行状态轮询：SSE 偶发断线也能实时反映进度
    setInterval(async () => {
      if (!state.pipelineId || !state.running) return;
      try {
        const resp = await api('/api/v1/pipeline/status/' + state.pipelineId);
        if (!resp.ok) return;
        const d = await resp.json();
        if (d.current_agent && AGENTS.includes(d.current_agent)) {
          const st = d.status === 'review' ? 'review' : 'running';
          setCardState(d.current_agent, st, d.status === 'review' ? '待确认' : '');
        }
        if (d.status === 'done') {
          setProgress(100, '✅ 全部完成！');
          doneUI();
          refreshSnapshot();
          setTimeout(() => renderDetail('audio', ''), 600);
        } else if (d.status === 'failed') {
          setCardState(d.current_agent, 'failed', d.error || '');
          doneUI();
          setProgress(0, '❌ 失败: ' + d.error);
        }
      } catch (_) { /* ignore */ }
    }, 4000);
  }

  // ═══ 快照刷新 ═══
  // 不覆盖「正在等待人工审核 / 执行中」的卡片，避免把 review 状态刷成 done
  function snapDone(agent, meta) {
    const cur = state.cards[agent]?.status;
    if (cur === 'review' || cur === 'running') return;
    setCardState(agent, 'done', meta);
  }
  async function refreshSnapshot() {
    try {
      const resp = await api('/api/v1/pipeline/snapshot/latest');
      if (!resp.ok) return;
      const snap = await resp.json();
      // 是否有可用断点（任一 agent 落盘过）→ 控制「断点续跑」按钮显隐
      state.hasCheckpoint = !!(
        snap.research_agent || snap.script_agent || snap.storyboard_agent ||
        snap.character_agent || snap.image_agent || snap.video_agent ||
        snap.video_compose_agent || snap.audio_agent
      );
      updateResumeBtn();
      updateAssets(snap);
      updateOverview(snap);

      if (snap.research_agent?.title) snapDone('research', '方案就绪');
      if (snap.script_agent?.episodes) snapDone('script', `${snap.script_agent.episodes.length}集`);
      if (snap.storyboard_agent?.episodes) {
        const eps = snap.storyboard_agent.episodes;
        let total = 0;
        eps.forEach((ep) => (total += (ep.shots || []).length));
        snapDone('storyboard', `${eps.length}集/${total}镜`);
      }
      if (snap.character_agent?.characters) snapDone('character', `${snap.character_agent.characters.length}角色`);
      if (snap.image_agent?.images) {
        const imgs = snap.image_agent.images;
        let total = 0;
        for (const k in imgs) total += Object.keys(imgs[k]).length;
        snapDone('image', `${total}张`);
      }
      if (snap.video_agent?.videos) snapDone('video', '视频就绪');
      if ((snap.compose_agent || snap.video_compose_agent)?.published) {
        const pub = (snap.compose_agent || snap.video_compose_agent).published;
        snapDone('compose', `${pub.length}集成片`);
      }
      if (snap.audio_agent?.final_video) snapDone('audio', '有声成片');
      if ((snap.publish_agent?.published || []).length) {
        const pub = snap.publish_agent.published;
        snapDone('publish', `${pub.length}条清单`);
      }
    } catch (_) { /* ignore */ }
  }
  function updateAssets(snap) {
    const ch = snap.character_agent?.characters || [];
    if (els.assetChars) {
      els.assetChars.innerHTML = ch.length
        ? ch.map((c) => {
            const img = c.image || c.asset?.controlnet_ref_path || c.portrait_path || '';
            const meta = [
              c.asset?.gender ? `性别 ${c.asset.gender}` : '',
              c.asset?.role ? c.asset.role : '',
              `复用 ${c.reuse_count || 1} 次`,
            ].filter(Boolean).join(' · ');
            const src = imgUrl(img);
            return `<div class="asset-item" data-open-img data-src="${esc(src)}" data-name="${esc(c.name)}" data-meta="${esc(meta)}">${src ? `<img src="${src}" onerror="this.remove()">` : '<div style="font-size:22px;padding:8px 0">👤</div>'}<div class="name">${esc(c.name)}</div><div class="count">${c.reuse_count || 1} 次复用</div></div>`;
          }).join('')
        : '<div class="asset-empty">暂无角色</div>';
    }

    // 场景背景：用分镜出图填充（资产库左侧第二区）。
    // 图落在 image_agent 的两层结构里（ep → shot → 图记录），按镜头号排序展示；
    // 优先取 local_path（出图后复制到 AIGC 本地图库的副本），没有再用裸 filename。
    if (els.assetScenes) {
      const images = snap.image_agent?.images || {};
      const scenes = [];
      for (const ek in images) {
        for (const sk in images[ek]) {
          const rec = images[ek][sk] || {};
          const file = rec.local_path || rec.filename || '';
          if (file) scenes.push({ shot: sk, src: imgUrl(file) });
        }
      }
      scenes.sort((a, b) => Number(a.shot) - Number(b.shot));
      els.assetScenes.innerHTML = scenes.length
        ? scenes.map((s) =>
            `<div class="asset-item" data-open-img data-src="${esc(s.src)}" data-name="镜头 ${esc(s.shot)}" data-meta="分镜出图">` +
            `<img src="${s.src}" onerror="this.remove()">` +
            `<div class="name">镜头 ${esc(s.shot)}</div></div>`
          ).join('')
        : '<div class="asset-empty">暂无场景</div>';
    }
  }

  // ═══ 资产放大预览 ═══
  function openImgModal(src, name, meta) {
    if (!els.imgModalOverlay) return;
    if (els.imgModalImg) els.imgModalImg.src = src || '';
    if (els.imgModalName) els.imgModalName.textContent = name || '';
    if (els.imgModalMeta) els.imgModalMeta.textContent = meta || '';
    els.imgModalOverlay.classList.add('open');
  }
  function closeImgModal() {
    if (els.imgModalOverlay) els.imgModalOverlay.classList.remove('open');
    if (els.imgModalImg) els.imgModalImg.src = '';
  }

  function updateOverview(snap) {
    const sc = snap.script_agent;
    const sb = snap.storyboard_agent;
    const episodes = sc?.episodes || sb?.episodes || [];
    let totalShots = 0;
    episodes.forEach((ep) => (totalShots += (ep.shots || []).length));
    if (els.ovEpisodes) els.ovEpisodes.textContent = episodes.length || '—';
    if (els.ovShots) els.ovShots.textContent = totalShots || '—';
    if (els.ovStatus) {
      els.ovStatus.textContent = state.running ? '执行中' : '空闲';
      els.ovStatus.className = state.running ? 'ov-status-running' : 'ov-status-idle';
    }
    if (els.ovTitle && sc?.title) els.ovTitle.textContent = sc.title;
    if (els.ovTime && state.startTime) els.ovTime.textContent = fmt((Date.now() - state.startTime) / 1000);

    if (els.checkpointList) {
      els.checkpointList.innerHTML = AGENTS.map((s) => {
        const done = snap[s + '_agent'] ? true : false;
        return `<div class="cp-item${done ? ' done' : ''}"><span class="cp-dot"></span>${done ? '✅' : '▫️'} ${AGENT_LABELS[s]}</div>`;
      }).join('');
    }
  }

  // ═══ 启动/停止 ═══
  async function startPipeline() {
    const text = els.storyInput.value.trim();
    if (!text) return;

    state.startTime = Date.now();
    state.running = true;
    resetAllCards();
    closeDetail();
    setProgress(0, '启动中…');
    if (els.generateBtn) { els.generateBtn.disabled = true; els.generateBtn.textContent = '⏳ 制作中…'; }
    if (els.resumeBtn) els.resumeBtn.style.display = 'none';
    if (els.stopBtn) els.stopBtn.style.display = '';
    const style = state.selectedStyles && state.selectedStyles.length ? state.selectedStyles : ['写实风格'];
    addLog('INFO', 'pipeline', `启动管线（风格：${style.join(' + ')}）…`);

    try {
      const resp = await api('/api/v1/pipeline/run', {
        method: 'POST',
        body: { input: text, style },
      });
      const r = await resp.json();
      if (r.success) {
        state.pipelineId = r.pipeline_id;
        setCardState('research', 'queued', '排队中');
        addLog('INFO', 'pipeline', `启动成功 ID=${r.pipeline_id}`);
      } else {
        throw new Error(r.error || '启动失败');
      }
    } catch (e) {
      doneUI();
      addLog('ERROR', 'pipeline', '启动失败: ' + e.message);
    }
  }

  // ═══ 断点续跑（resume）═══
  // 与 startPipeline 的区别：/run 请求带 resume:true，后端从最后一个
  // 已落盘 checkpoint 之后继续，而不是清空全部重头跑（可跳过已完成的耗时步骤）。
  async function resumePipeline() {
    let text = els.storyInput.value.trim();
    // 输入框没填故事时，回退到最近一次任务的原输入（仅用于通过接口的输入校验，
    // resume 模式实际执行由 checkpoint 驱动，不依赖该文本重新走 LLM）。
    if (!text || text.length < 2) {
      try {
        const resp = await api('/api/v1/pipeline/history');
        if (resp.ok) {
          const h = await resp.json();
          const recent = (h.history || []).find((x) => x.input && x.input.length >= 2);
          if (recent) {
            text = recent.input;
            if (els.storyInput) els.storyInput.value = text;
          }
        }
      } catch (_) { /* 忽略：继续用原输入 */ }
    }
    if (!text || text.length < 2) { addLog('WARN', 'pipeline', '续跑需要先输入一个故事（或存在历史任务）'); return; }

    state.startTime = Date.now();
    state.running = true;
    closeDetail();
    setProgress(0, '从断点续跑…');
    if (els.generateBtn) { els.generateBtn.disabled = true; els.generateBtn.textContent = '⏳ 续跑中…'; }
    if (els.resumeBtn) els.resumeBtn.style.display = 'none';
    if (els.stopBtn) els.stopBtn.style.display = '';
    const style = state.selectedStyles && state.selectedStyles.length ? state.selectedStyles : ['写实风格'];
    addLog('INFO', 'pipeline', `断点续跑（风格：${style.join(' + ')}）…`);

    try {
      const resp = await api('/api/v1/pipeline/run', {
        method: 'POST',
        body: { input: text, style, resume: true },
      });
      const r = await resp.json();
      if (r.success) {
        state.pipelineId = r.pipeline_id;
        addLog('INFO', 'pipeline', `续跑成功 ID=${r.pipeline_id}`);
      } else {
        throw new Error(r.error || '续跑失败');
      }
    } catch (e) {
      doneUI();
      addLog('ERROR', 'pipeline', '续跑失败: ' + e.message);
    }
  }

  function doneUI() {
    state.running = false;
    if (els.generateBtn) { els.generateBtn.disabled = false; els.generateBtn.textContent = '开始制作'; }
    if (els.stopBtn) els.stopBtn.style.display = 'none';
    updateResumeBtn();
  }

  // ═══ 断点续跑按钮显隐 ═══
  // 只要后端有 checkpoint（管线中断/部分完成），且当前没在跑，就显示「断点续跑」。
  function updateResumeBtn() {
    if (!els.resumeBtn) return;
    els.resumeBtn.style.display = (!state.running && state.hasCheckpoint) ? '' : 'none';
  }

  async function stopPipeline() {
    const pid = state.pipelineId;
    if (pid) {
      try {
        await api('/api/v1/pipeline/cancel/' + pid, { method: 'POST' });
        addLog('WARN', 'pipeline', '停止请求已发送');
      } catch (e) {
        addLog('ERROR', 'pipeline', '停止失败: ' + e.message);
      }
    }
    doneUI();
  }

  // ═══ 历史抽屉 ═══
  async function openHistory() {
    els.drawerOverlay.classList.add('open');
    els.historyDrawer.classList.add('open');
    try {
      const resp = await api('/api/v1/pipeline/history');
      const h = await resp.json();
      const items = h.history || [];
      els.drawerBody.innerHTML = items.length
        ? items.map((item) => `
            <div class="history-item">
              <div class="title">${esc(item.input || '无标题')}</div>
              <div class="meta">${esc(item.status || '?')} · ${esc((item.created_at || '').slice(0, 19))}</div>
              <div class="actions"><button class="btn btn-ghost btn-sm" onclick="alert('${esc(item.input || '')}')">查看</button></div>
            </div>`).join('')
        : '<div class="empty-state">暂无历史任务</div>';
    } catch (e) {
      els.drawerBody.innerHTML = '<div class="empty-state">加载失败</div>';
    }
  }

  function closeHistory() {
    els.drawerOverlay.classList.remove('open');
    els.historyDrawer.classList.remove('open');
  }

  // ═══ 模型库（按风格选择本地模型）═══
  let LOCAL_MODELS = { llm_models: [], image_ckpts: [], video_models: [] };

  function closeModelLib() {
    els.drawerOverlay.classList.remove('open');
    if (els.modelLibDrawer) els.modelLibDrawer.classList.remove('open');
  }
  function closeAllDrawers() {
    els.drawerOverlay.classList.remove('open');
    if (els.historyDrawer) els.historyDrawer.classList.remove('open');
    if (els.modelLibDrawer) els.modelLibDrawer.classList.remove('open');
    if (els.skillsDrawer) els.skillsDrawer.classList.remove('open');
  }

  // ═══ 技能库（可进化提示词词库）═══
  const SK_DIM_LABELS = {
    'image/topic': '图片·内容主题', 'image/format': '图片·文件格式',
    'image/color': '图片·色彩视觉', 'image/domain': '图片·应用领域', 'image/ai': '图片·AI领域',
    'image/scene': '图片·场景一致性', 'video/genre': '视频·内容题材', 'video/format': '视频·格式呈现',
    'video/tech': '视频·技术维度', 'video/camera': '视频·运镜镜头', 'video/motion': '视频·变形动效',
    'video/use': '视频·商业用途', 'video/gen': '视频·生成方式',
  };
  const SK_BASE_BLOCKS = [
    { key: 'photoreal', label: '📷 实拍质感', hint: '无条件注入 · 人像人/物像实拍' },
    { key: 'anatomy', label: '🧍 人体结构', hint: '正向词 · 防崩坏/防变形' },
    { key: 'anatomy_negative', label: '🚫 解剖负向', hint: '禁绝畸形脸/多余手指/插画感' },
    { key: 'emotion', label: '🎭 微表情', hint: '人物近景/特写 · 有戏' },
  ];
  let _skOpenCat = null;

  function closeSkills() {
    els.drawerOverlay.classList.remove('open');
    if (els.skillsDrawer) els.skillsDrawer.classList.remove('open');
  }

  function skWordsHtml(words) {
    const w = (words || '').trim();
    if (!w) return '<div class="sk-words">（空）</div>';
    const wordsList = w.split(',').map((x) => x.trim()).filter(Boolean);
    return '<div class="sk-words">' + wordsList.map((x) => esc(x)).join(' · ') + '</div>';
  }

  async function openSkills() {
    closeHistory();
    closeModelLib();
    els.drawerOverlay.classList.add('open');
    els.skillsDrawer.classList.add('open');
    try {
      // 基础词块
      let baseBlocks = '';
      for (const b of SK_BASE_BLOCKS) {
        let words = '';
        try {
          const resp = await api('/api/v1/skills/base/' + b.key);
          if (resp.ok) words = (await resp.json()).words || '';
        } catch (_) {}
        baseBlocks += `<div class="sk-base-box" data-base="${b.key}">${b.label}<small>${esc(b.hint)}</small><div class="sk-words" style="margin-top:4px">${words ? esc(wordPreview(words)) : '（空）'}</div></div>`;
      }
      // 维度
      const resp = await api('/api/v1/skills/categories');
      const dims = resp.ok ? (await resp.json()).dimensions : {};
      const chips = Object.keys(dims || {}).map((d) =>
        `<span class="sk-chip" data-dim="${esc(d)}">${esc(SK_DIM_LABELS[d] || d)}</span>`
      ).join('');
      els.skillsBody.innerHTML =
        `<div class="sk-section-title">🔧 基础词库（无条件或按场景注入，全流程生效）</div>` +
        `<div class="sk-base-grid">${baseBlocks}</div>` +
        `<div class="sk-section-title">🗂 图片 / 视频 分类维度</div>` +
        (chips ? `<div class="sk-dims">${chips}</div><div id="skDimBody"><div class="empty-state">点击上方任一维度，查看其分类词条</div></div>` : '<div class="empty-state">暂无维度数据</div>') +
        skEvolveHtml();
      // 绑定
      els.skillsBody.querySelectorAll('.sk-chip').forEach((c) => {
        c.addEventListener('click', () => loadSkDim(c.dataset.dim));
      });
      bindEvolve();
    } catch (e) {
      els.skillsBody.innerHTML = '<div class="empty-state">技能库加载失败（请确认后端已启动）</div>';
    }
  }

  // ═══ 自进化学习面板 ═══
  let _evCandidates = [];   // 提炼出的候选（save=false）
  const _evDefaultDim = 'image/topic';

  function skEvolveHtml() {
    const dimOpts = Object.keys(SK_DIM_LABELS).map((d) =>
      `<option value="${esc(d)}"${d === _evDefaultDim ? ' selected' : ''}>${esc(SK_DIM_LABELS[d] || d)}</option>`
    ).join('');
    return `<div class="sk-section-title">🧬 自进化学习 · 让词库自动学习好内容</div>` +
      `<div class="sk-evolve">` +
        `<div class="sk-evolve-label">素材：粘贴文稿 / 文章文本，或输入网页链接（视频平台可先复制口播文稿再粘贴）</div>` +
        `<textarea id="evSource" rows="4" placeholder="直接粘贴文稿文本，例如一段摄影教程 / 夜景描写；或输入 https:// 链接"></textarea>` +
        `<div class="sk-evolve-inline">` +
          `<select id="evDim">${dimOpts}</select>` +
          `<input id="evName" placeholder="目标分类名，如 自然风光">` +
          `<button class="btn btn-ghost btn-sm" id="evCollect">👀 预览采集</button>` +
        `</div>` +
        `<div class="sk-evolve-inline">` +
          `<button class="btn btn-ghost btn-sm" id="evGenerate">🪄 提炼候选</button>` +
          `<button class="btn btn-ghost btn-sm" id="evSave" style="display:none">✅ 确认落盘</button>` +
          `<span id="evStatus" class="sk-evolve-status"></span>` +
        `</div>` +
        `<div id="evPreview" class="sk-evolve-preview" style="display:none"></div>` +
        `<div id="evCand"></div>` +
      `</div>`;
  }

  function _evSetStatus(msg, err) {
    const el = document.getElementById('evStatus');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'sk-evolve-status' + (err ? ' err' : '');
  }

  function bindEvolve() {
    const el = (id) => document.getElementById(id);
    const src = el('evSource'), dim = el('evDim'), nameEl = el('evName');
    const btnCollect = el('evCollect'), btnGenerate = el('evGenerate'), btnSave = el('evSave');
    const prev = el('evPreview'), cand = el('evCand');
    if (!src || !btnGenerate) return;
    _evCandidates = [];

    btnCollect.addEventListener('click', async () => {
      const url = src.value.trim();
      if (!/^https?:\/\//i.test(url)) { _evSetStatus('预览采集需输入 http(s):// 链接', true); return; }
      prev.style.display = '';
      prev.innerHTML = '<div class="empty-state">抓取中…</div>';
      try {
        const r = await api('/api/v1/skills/learn/collect', { method: 'POST', body: { url, max_chars: 6000 } });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || '抓取失败');
        prev.innerHTML = `<div class="sk-evolve-preview-title">已提取 ${d.chars} 字（仅供预览，未落盘）：</div><pre>${esc(d.text)}</pre>`;
        _evSetStatus('采集成功，可点击「🪄 提炼候选」');
      } catch (e) {
        prev.innerHTML = `<div class="empty-state">❌ ${esc(e.message || e)}</div>`;
        _evSetStatus('采集失败', true);
      }
    });

    btnGenerate.addEventListener('click', async () => {
      const source = src.value.trim();
      const dimension = dim.value;
      const nm = nameEl.value.trim();
      if (!source) { _evSetStatus('请先填写素材', true); return; }
      if (!nm) { _evSetStatus('请填写目标分类名', true); return; }
      _evSetStatus('LLM 提炼中，本地模型需几十秒，请稍候…');
      btnGenerate.disabled = true;
      cand.innerHTML = '<div class="empty-state">提炼中…</div>';
      prev.style.display = 'none';
      _evCandidates = [];
      btnSave.style.display = 'none';
      try {
        const r = await api('/api/v1/skills/learn/from-source', { method: 'POST', body: { dimension, name: nm, source, max_chars: 6000, save: false } });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || '提炼失败');
        _evCandidates = d.candidates || [];
        if (!_evCandidates.length) {
          cand.innerHTML = `<div class="empty-state">${esc(d.reason || '未能提炼出词条')}</div>`;
          _evSetStatus(d.reason || '无候选', true);
          return;
        }
        cand.innerHTML = `<div class="sk-evolve-preview-title">提炼出 ${_evCandidates.length} 条候选（save=true 才会落盘）：</div>` +
          `<div class="sk-cand-list">` +
          _evCandidates.map((c, i) =>
            `<div class="sk-cand"><span class="id">${i + 1}</span><b>${esc(c.zh || '')}</b><i>${esc(c.en || '')}</i></div>`
          ).join('') + `</div>`;
        btnSave.style.display = '';
        _evSetStatus(`共 ${_evCandidates.length} 条候选，请审阅后确认落盘`);
      } catch (e) {
        cand.innerHTML = `<div class="empty-state">❌ ${esc(e.message || e)}</div>`;
        _evSetStatus('提炼失败', true);
      } finally {
        btnGenerate.disabled = false;
      }
    });

    btnSave.addEventListener('click', async () => {
      const dimension = dim.value;
      const nm = nameEl.value.trim();
      if (!_evCandidates.length) { _evSetStatus('没有可落盘的候选', true); return; }
      _evSetStatus('落盘中…');
      btnSave.disabled = true;
      try {
        // 直接落盘已审阅的候选，避免二次调用 LLM（from-source save=true 会重新提炼）
        const r = await api('/api/v1/skills/learn', { method: 'POST', body: { dimension, name: nm, entries: _evCandidates } });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || '落盘失败');
        cand.innerHTML = `<div class="empty-state">✅ 已落盘 ${d.added != null ? d.added + ' 条' : ''} 到「${esc(nm)}」</div>`;
        _evSetStatus('落盘成功，词库已自动刷新');
        btnSave.style.display = 'none';
        _evCandidates = [];
        if (dimension) loadSkDim(dimension); // 刷新该维度词条展示
      } catch (e) {
        cand.innerHTML = `<div class="empty-state">❌ ${esc(e.message || e)}</div>`;
        _evSetStatus('落盘失败', true);
      } finally {
        btnSave.disabled = false;
      }
    });
  }

  function wordPreview(words, n) {
    const l = (words || '').split(',').map((x) => x.trim()).filter(Boolean);
    return l.slice(0, n || 6).join(' · ') + (l.length > (n || 6) ? ' …' : '');
  }

  async function loadSkDim(dim) {
    // 高亮 chip
    els.skillsBody.querySelectorAll('.sk-chip').forEach((c) => c.classList.toggle('active', c.dataset.dim === dim));
    const box = document.getElementById('skDimBody');
    if (!box) return;
    box.innerHTML = '<div class="empty-state">加载中…</div>';
    try {
      const resp = await api('/api/v1/skills/block/' + dim);
      if (!resp.ok) { box.innerHTML = '<div class="empty-state">加载失败</div>'; return; }
      const data = await resp.json();
      const cats = data.categories || {};
      const names = Object.keys(cats);
      const label = SK_DIM_LABELS[dim] || dim;
      const items = names.map((name) => {
        // 词条计数
        const cnt = (cats[name] || '').split(',').filter((x) => x.trim()).length;
        return `<button class="sk-cat-btn" data-cat="${esc(name)}">${esc(name)}<span class="n">${cnt}词</span></button>` +
          `<div class="sk-cat-words" style="display:none">${skWordsHtml(cats[name])}</div>`;
      }).join('');
      box.innerHTML = `<div class="sk-section-title" style="margin-top:0">📚 ${esc(label)} 下的分类</div>${items || '<div class="empty-state">该维度暂无分类</div>'}`;
      box.querySelectorAll('.sk-cat-btn').forEach((b) => {
        b.addEventListener('click', () => {
          const w = b.nextElementSibling;
          const isOpen = w && w.style.display !== 'none';
          box.querySelectorAll('.sk-cat-words').forEach((x) => { x.style.display = 'none'; });
          if (w && !isOpen) w.style.display = '';
        });
      });
    } catch (e) {
      box.innerHTML = '<div class="empty-state">加载失败</div>';
    }
  }

  async function openModelLib() {
    closeHistory();
    els.drawerOverlay.classList.add('open');
    els.modelLibDrawer.classList.add('open');
    renderModelLib();
    try {
      const resp = await api('/api/v1/pipeline/local-models');
      if (resp.ok) {
        const d = await resp.json();
        LOCAL_MODELS.llm_models = d.llm_models || [];
        LOCAL_MODELS.image_ckpts = d.image_ckpts || [];
        LOCAL_MODELS.video_models = d.video_models || [];
      }
    } catch (_) { /* 服务未起时忽略，仍可访问本机模型 */ }
    // 刷新一次以填入本地模型下拉
    renderModelLib();
  }

  // 把一个值并入 options，保证当前已选值始终可选
  function _opt(options, current) {
    if (current && !options.includes(current)) options = [current].concat(options);
    return options;
  }

  function renderModelLib() {
    const box = els.modelLibBody;
    if (!box) return;
    const names = Object.keys(STYLES || {});
    if (!names.length) {
      box.innerHTML = '<div class="ml-empty">暂无风格配置（可先在 config/config.yaml 的 styles 中补充）</div>';
      return;
    }
    const llmEmpty = LOCAL_MODELS.llm_models.length
      ? ''
      : '<div class="ml-hint" style="color:var(--red)">⚠ 未获取到 Ollama 本地模型（请确认 Ollama 已启动）</div>';
    const ckptEmpty = LOCAL_MODELS.image_ckpts.length
      ? ''
      : '<div class="ml-hint" style="color:var(--red)">⚠ 未获取到 ComfyUI 出图模型（请确认 ComfyUI 已启动）</div>';
    const videoEmpty = LOCAL_MODELS.video_models.length
      ? ''
      : '<div class="ml-hint" style="color:var(--red)">⚠ 未获取到 ComfyUI 视频模型（请确认 ComfyUI 已启动）</div>';
    const videoTypeOpts = ['ltx', 'wan', 'svd', 'hunyuan']
      .map((m) => `<option value="${m}">${m}</option>`)
      .join('');

    const blocks = names.map((name) => {
      const st = STYLES[name] || {};
      const llmCur = st.llm_model || '';
      const ckptCur = st.image_ckpt || '';
      const videoCur = st.video_model || '';
      const optsLlm = (_opt(LOCAL_MODELS.llm_models.slice(), llmCur)).map((m) => `<option value="${esc(m)}"${m === llmCur ? ' selected' : ''}>${esc(m)}</option>`).join('');
      const optsCkpt = (_opt(LOCAL_MODELS.image_ckpts.slice(), ckptCur)).map((m) => `<option value="${esc(m)}"${m === ckptCur ? ' selected' : ''}>${esc(m)}</option>`).join('');
      const optsVideo = (_opt(LOCAL_MODELS.video_models.slice(), videoCur)).map((m) => `<option value="${esc(m)}"${m === videoCur ? ' selected' : ''}>${esc(m)}</option>`).join('');
      const modelTypeOpts = ['sd15', 'flux']
        .map((m) => `<option value="${m}"${(st.image_model_type === m) ? ' selected' : ''}>${m}</option>`)
        .join('');
      const curVType = st.video_model_type || 'ltx';
      const vTypeOpts = videoTypeOpts.replace(
        new RegExp(`value="${curVType}"`),
        `value="${curVType}" selected`
      );
      return `
        <div class="ml-block" data-style="${esc(name)}">
          <div class="ml-block-head">
            <span class="ml-style-name">🎨 ${esc(name)}</span>
            <span class="ml-hint">${esc(st.description || '')}</span>
          </div>
          <div class="ml-section-title">🧠 文字模型（LLM / 剧本·分镜）</div>
          <div class="ml-field">
            <label>文字模型</label>
            <select class="ml-select ml-llm">${optsLlm || '<option>—</option>'}</select>
          </div>
          <div class="ml-section-title">🎬 出图模型（图生图 / 出图）</div>
          <div class="ml-field">
            <label>出图模型（checkpoint）</label>
            <select class="ml-select ml-ckpt">${optsCkpt || '<option>—</option>'}</select>
          </div>
          <div class="ml-field">
            <label>出图引擎（image_model_type）</label>
            <select class="ml-select ml-ctype">${modelTypeOpts}</select>
          </div>
          <div class="ml-section-title">🎥 视频模型（图生视频）</div>
          <div class="ml-field">
            <label>视频模型</label>
            <select class="ml-select ml-vmodel">${optsVideo || '<option>—</option>'}</select>
          </div>
          <div class="ml-field">
            <label>视频引擎（video_model_type）</label>
            <select class="ml-select ml-vtype">${vTypeOpts}</select>
          </div>
          ${llmEmpty}
          ${ckptEmpty}
          ${videoEmpty}
          <div class="ml-hint">💡 ${esc(st.advice || '')}</div>
          <button class="ml-save" data-style="${esc(name)}">保存本风格</button>
        </div>`;
    }).join('');

    box.innerHTML = blocks + '<div class="ml-status">每个分区都只列出你本机已安装的模型：🧠 文字 / 🎬 出图 / 🎥 视频分开展示，避免选错。切换风格会自动使用这里选好的模型，点「保存本风格」立即生效。</div>';
    box.querySelectorAll('.ml-save').forEach((btn) => {
      btn.addEventListener('click', () => saveModelLib(btn.dataset.style, btn));
    });
  }

  async function saveModelLib(name, btn) {
    // 读取该风格块内的下拉值
    const block = btn.closest('.ml-block');
    const llm = block.querySelector('.ml-llm').value;
    const ckpt = block.querySelector('.ml-ckpt').value;
    const ctype = block.querySelector('.ml-ctype').value;
    const vmodel = block.querySelector('.ml-vmodel').value;
    const vtype = block.querySelector('.ml-vtype').value;
    try {
      const resp = await api(`/api/v1/pipeline/styles/${encodeURIComponent(name)}`, {
        method: 'PUT',
        body: { llm_model: llm, image_ckpt: ckpt, image_model_type: ctype,
                video_model: vmodel, video_model_type: vtype },
      });
      if (!resp.ok) { alert('保存失败：' + (await resp.text()).slice(0, 200)); return; }
      const d = await resp.json();
      if (d.styles) STYLES = d.styles;
      // 前端刷新风格说明卡
      renderStyleInfo(selStyles());
      const old = btn.textContent;
      btn.textContent = '✓ 已保存';
      btn.classList.add('saved');
      addLog('model', '模型库', `✅ 已保存「${name}」的模型配置（文字=${llm}, 出图=${ckpt}, 视频=${vmodel}` + (ctype ? `, 出图引擎=${ctype}` : '') + (vtype ? `, 视频引擎=${vtype}` : '') + '）');
      setTimeout(() => { btn.textContent = old; btn.classList.remove('saved'); }, 1500);
    } catch (e) {
      alert('保存失败：' + e.message);
    }
  }

  // ═══ Tab 切换 ═══
  function bindTabs() {
    $$('.side-tab').forEach((btn) => {
      btn.addEventListener('click', () => {
        $$('.side-tab').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const tab = btn.dataset.tab;
        const contentAsset = document.getElementById('tab-asset');
        const contentTask = document.getElementById('tab-task');
        if (contentAsset) contentAsset.style.display = tab === 'asset' ? 'block' : 'none';
        if (contentTask) contentTask.style.display = tab === 'task' ? 'block' : 'none';
      });
    });
  }

  // ═══ 初始化 ═══
  // ═══ 风格 → 模型 说明 ═══
  let STYLES = {}; // { 风格名: {...} }
  function renderStyleInfo(names) {
    const box = els.styleInfo;
    if (!box) return;
    const list = (Array.isArray(names) && names.length) ? names : selStyles();
    if (!list.length) { box.innerHTML = '<div class="si-empty">请在下方选择风格（可多选）</div>'; return; }
    const primary = list[0];
    const st = STYLES[primary];
    if (!st) { box.innerHTML = '<div class="si-empty">该风格暂无说明（可在 config/config.yaml 的 styles 中补充）</div>'; return; }
    const label = (list.length > 1 ? list.join(' + ') : primary);
    const descs = list.map((n) => {
      const s = STYLES[n];
      return s && s.description ? `<div class="si-desc">${esc(s.description)}</div>` : '';
    }).join('');
    const models = (st.recommended_models || []).map((m) => `<kbd>${esc(m)}</kbd>`).join('');
    box.innerHTML =
      `<div class="si-title">🎨 ${esc(label)}${list.length > 1 ? '<span class="si-sub">（自由组合）</span>' : ''}</div>` +
      descs +
      (st.advice ? `<div class="si-advice"><b>💡 建议：</b>${esc(st.advice)}（主风格决定出图/视频底模，其余合并进关键词）</div>` : '') +
      (models ? `<div class="si-models"><b>⌨️ 主风格推荐模型：</b>${models}</div>` : '') +
      `<div class="si-models" style="margin-top:3px"><b>🧠 文字：</b><kbd>${esc(st.llm_model || '-')}</kbd> · <b>🎬 出图：</b><kbd>${esc(st.image_ckpt || '-')}</kbd> · <b>🎥 视频：</b><kbd>${esc(st.video_model || '-')}</kbd></div>`;
  }
  function renderChips(names) {
    if (!els.styleChips) return;
    els.styleChips.innerHTML = '';
    const sel = selStyles();
    names.forEach((n) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'style-chip' + (sel.includes(n) ? ' active' : '');
      chip.textContent = n;
      chip.dataset.style = n;
      chip.addEventListener('click', () => toggleStyle(n));
      els.styleChips.appendChild(chip);
    });
  }
  function toggleStyle(name) {
    const sel = state.selectedStyles ? state.selectedStyles.slice() : [];
    const idx = sel.indexOf(name);
    if (idx >= 0) {
      sel.splice(idx, 1);
      if (!sel.length) return; // 至少保留一个风格
    } else {
      sel.push(name); // 后选的在后面，第一个为主风格
    }
    state.selectedStyles = sel;
    $$('.style-chip').forEach((c) => c.classList.toggle('active', sel.includes(c.dataset.style)));
    renderStyleInfo(sel);
  }
  async function loadStyles() {
    try {
      const resp = await api('/api/v1/pipeline/styles');
      const d = await resp.json();
      const styles = d.styles || {};
      STYLES = styles;
      const names = Object.keys(styles);
      if (names.length) {
        // 保留用户已选的合法风格；否则默认选中第一个
        const valid = (state.selectedStyles || []).filter((n) => names.includes(n));
        state.selectedStyles = valid.length ? valid : [names[0]];
        renderChips(names);
        renderStyleInfo(selStyles());
      }
    } catch (_) {
      // 后端未启动/无 styles 配置时，回退到静态风格，保证多选仍可用
      if (els.styleChips && !els.styleChips.children.length) {
        const names = ['写实风格', '日系动漫', '国风古风', '赛博朋克'];
        STYLES = Object.fromEntries(names.map((n) => [n, {}]));
        state.selectedStyles = ['写实风格'];
        renderChips(names);
        renderStyleInfo(selStyles());
      }
    }
  }

  function init() {
    collectEls();

    // 暴露全局（供 HTML onclick 使用）
    window.selectCard = (agent) => renderDetail(agent, '');
    window.toggleDrawer = () => {
      const isOpen = els.historyDrawer.classList.contains('open');
      if (isOpen) closeHistory(); else openHistory();
    };

    resetAllCards();
    setProgress(0, '空闲');
    if (els.logbar) els.logbar.classList.remove('expanded');
    if (els.detailSection) els.detailSection.style.display = 'none';

    bindLogin();
    checkLogin();

    if (els.generateBtn) els.generateBtn.addEventListener('click', startPipeline);
    if (els.resumeBtn) els.resumeBtn.addEventListener('click', resumePipeline);
    if (els.stopBtn) els.stopBtn.addEventListener('click', stopPipeline);
    if (els.detailClose) els.detailClose.addEventListener('click', closeDetail);
    if (els.drawerClose) els.drawerClose.addEventListener('click', closeHistory);
    if (els.drawerOverlay) els.drawerOverlay.addEventListener('click', closeAllDrawers);
    if (els.historyBtn) els.historyBtn.addEventListener('click', openHistory);
    if (els.modelLibBtn) els.modelLibBtn.addEventListener('click', openModelLib);
    if (els.modelLibClose) els.modelLibClose.addEventListener('click', closeModelLib);
    if (els.skillsBtn) els.skillsBtn.addEventListener('click', openSkills);
    if (els.skillsClose) els.skillsClose.addEventListener('click', closeSkills);
    if (els.logHandle) els.logHandle.addEventListener('click', () => {
      if (els.logbar) els.logbar.classList.toggle('expanded');
    });

    // 资产放大预览：网格点击 + 弹窗关闭
    // 角色定妆照与场景背景（分镜图）两个网格都要绑，否则点场景图不会弹预览
    const bindAssetPreview = (grid) => {
      if (!grid) return;
      grid.addEventListener('click', (e) => {
        const item = e.target.closest('[data-open-img]');
        if (!item) return;
        openImgModal(item.getAttribute('data-src'), item.getAttribute('data-name'), item.getAttribute('data-meta'));
      });
    };
    bindAssetPreview(els.assetChars);
    bindAssetPreview(els.assetScenes);
    if (els.imgModalClose) els.imgModalClose.addEventListener('click', closeImgModal);
    if (els.imgModalOverlay) {
      els.imgModalOverlay.addEventListener('click', (e) => {
        if (e.target === els.imgModalOverlay) closeImgModal();
      });
    }
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeImgModal();
    });

    // 结构化就地编辑：输入同步到编辑模型，添加/删除触发重渲染
    if (els.detailBody) {
      els.detailBody.addEventListener('input', (e) => {
        const el = e.target;
        const path = el.getAttribute && el.getAttribute('data-path');
        if (!path) return;
        const root = el.closest('[data-edit-root]');
        if (!root) return;
        const model = _edit[root.getAttribute('data-edit-root')];
        if (!model) return;
        setByPath(model, path, el.type === 'number' ? Number(el.value) : el.value);
      });
      els.detailBody.addEventListener('click', (e) => {
        const addBtn = e.target.closest('.ef-add');
        if (addBtn) { onEditAdd(addBtn); return; }
        const delBtn = e.target.closest('.ef-del');
        if (delBtn) { onEditDel(delBtn); return; }
      });
    }

    bindTabs();

    // 风格多选 + 说明卡
    loadStyles();
    renderStyleInfo(selStyles());

    connectBackend();
    setTimeout(refreshSnapshot, 800);
    restoreActiveReview(); // 刷新后恢复正在等待人工确认的审核断点
    setInterval(() => {
      fetch(API + '/health', { mode: 'cors' }).catch(() => {});
    }, 30000);
  }

  // ═══ 恢复活跃审核断点 ═══
  // SSE 是实时无回放的，页面刷新后 pipelineId / running 会丢失。
  // 启动时查询后端，若有正在等待人工确认（review）的管线，则恢复状态并重新渲染审核面板。
  async function restoreActiveReview() {
    try {
      const resp = await api('/api/v1/pipeline/active');
      if (!resp.ok) return;
      const d = await resp.json();
      const list = d.active || [];
      // 优先选一个处于 review（等待人工确认）的管线；否则选最近 running 的
      const review = list.find((p) => p.status === 'review') || null;
      if (!review) return;
      state.pipelineId = review.pipeline_id;
      state.running = true;
      if (els.generateBtn) { els.generateBtn.disabled = true; els.generateBtn.textContent = '⏳ 制作中…'; }
      if (els.stopBtn) els.stopBtn.style.display = '';
      const agent = review.current_agent || '';
      const reason = review.review_reason || '等待人工确认';
      if (agent && AGENTS.includes(normAgent(agent))) {
        setCardState(normAgent(agent), 'review', reason);
        setProgress(review.progress || 0, `⏸️ 等待人工确认：${reason}`);
        addLog('WARN', normAgent(agent), `⏸️ 恢复审核断点：${reason}`);
        renderDetail(agent, reason);
      }
    } catch (_) { /* ignore */ }
  }

  // app.js 可能由 index.html 动态加载（loadScript），此时 DOMContentLoaded 或许已触发，
  // 因此若文档已就绪则立即 init，否则等 DOMContentLoaded 后再 init。
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
