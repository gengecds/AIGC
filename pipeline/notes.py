"""创作笔记生成器 — 每次创作完成后自动触发，生成 Markdown 笔记。

目录决策（按优先顺序）：
1. 读取环境变量 OBSIDIAN_VAULT（绝对路径）→ 写入该 Obsidian 笔记库；
2. 未配置 → 回落项目内 storage/notes/。

用法:
    from pipeline.notes import generate_note_from_results
    await generate_note_from_results(results, user_input="...", styles=["写实风格"],
                                     story_id=1, pipeline_id="pipe_xxx")

说明：
- 本模块只「写笔记」，不会把任何内容沉淀进 skills 词库（那是 ingest 的职责，
  需用户人工确认后由 /api/v1/skills/learn 触发）。
- 写作粒度：当前为开发阶段的「每创作一集/一次管线」一条笔记。
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# 模块加载时从项目根 .env 读取 OBSIDIAN_VAULT，确保 API 与 CLI 两个入口都能读到。
# 用纯标准库解析，避免依赖可能未安装的第三方 dotenv 库。
def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key == "OBSIDIAN_VAULT" and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")
    except Exception:
        pass


_load_env_file()

# 各阶段 agent 的 key（与 AGENTS_PIPELINE / checkpoint 前缀一致）
RESEARCH = "research_agent"
SCRIPT = "script_agent"
STORYBOARD = "storyboard_agent"
CHARACTER = "character_agent"
IMAGE = "image_agent"
VIDEO = "video_agent"
SUBTITLE = "subtitle_agent"
COMPOSE = "video_compose_agent"
AUDIO = "audio_agent"
PUBLISH = "publish_agent"


# ── 基础工具 ──────────────────────────

def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_notes_dir() -> Path:
    """决定笔记写入目录并确保存在。"""
    vault = os.environ.get("OBSIDIAN_VAULT", "").strip().strip('"').strip("'")
    if vault:
        p = Path(vault).expanduser()
        if not p.is_absolute():
            p = _project_root() / p
    else:
        p = _project_root() / "storage" / "notes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get(results: dict, key: str) -> Any:
    """取某个 agent 的 data（可能是 dict 或 list，取决于阶段输出）。"""
    entry = results.get(key)
    if isinstance(entry, dict) and "data" in entry:
        return entry.get("data")
    return entry


def _tostr(value, sep="、") -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        parts = [str(x) for x in value if x not in (None, "")]
        return sep.join(parts)
    if isinstance(value, dict):
        return sep.join(f"{k}: {x}" for k, x in value.items())
    return str(value)


def _line(label, value) -> str:
    v = _tostr(value)
    if not v:
        return ""
    return f"- **{label}**：{v}\n"


def _value(data, *keys, default=""):
    for k in keys:
        v = data.get(k) if isinstance(data, dict) else None
        if v not in (None, "", [], {}):
            return v
    return default


def _safe_name(title: str, fallback: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\s：；（）()【】\[\]·,，。.!！？]+", "_", title or "")
    name = re.sub(r"_+", "_", name).strip("_")
    return (name[:60] or fallback)


# ── 各阶段渲染 ────────────────────────

def _render_research(d: dict) -> str:
    if not isinstance(d, dict) or not d:
        return ""
    out = ["### 基本信息\n"]
    out.append(_line("目标受众", d.get("target_audience")))
    out.append(_line("风格方向", d.get("style_direction")))
    out.append(_line("叙事结构", d.get("narrative_structure")))
    out.append(_line("BGM 情绪", d.get("bgm_mood")))
    out.append(_line("关键词", d.get("key_words")))
    out.append(_line("渲染引擎", d.get("render_engine_words")))
    out.append(_line("光影强化", d.get("lighting_words")))
    out.append(_line("镜头语言", d.get("camera_words")))
    out.append(_line("场景音效", d.get("scene_sounds")))
    for c in d.get("candidate_words") or []:
        if isinstance(c, dict) and (c.get("zh") or c.get("en")):
            out.append(f"- 候选词：{c.get('zh','')} / {c.get('en','')} — {c.get('note','')}\n")
    for i, p in enumerate(d.get("copy_points") or [], 1):
        out.append(f"- 文案要点 {i}. {p}\n")
    return "".join(out)


def _render_script(d: dict) -> str:
    if not isinstance(d, dict) or not d:
        return ""
    out = ["### 概要\n"]
    out.append(_line("题材类型", d.get("genre")))
    out.append(_line("一句话梗概", d.get("summary")))
    for c in d.get("characters") or []:
        if isinstance(c, dict):
            out.append(
                f"- **{c.get('name','?')}**（{_tostr([c.get('role',''), c.get('gender','')], ' / ')}）\n"
            )
            if c.get("appearance"):
                out.append(f"  - 外形：{c['appearance']}\n")
            if c.get("personality"):
                out.append(f"  - 性格：{c['personality']}\n")
    for ep in d.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        out.append(
            f"\n### 第 {ep.get('episode_number','?')} 集「{ep.get('title','')}」\n"
        )
        if ep.get("plot"):
            out.append(f"{ep['plot']}\n")
        for dlg in ep.get("dialogues") or []:
            if isinstance(dlg, dict):
                out.append(f"- {dlg.get('character','')}：{dlg.get('line','')}\n")
    return "".join(out)


def _render_storyboard(d: dict) -> str:
    if not isinstance(d, dict):
        return ""
    out = []
    for ep in d.get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        out.append(
            f"### 第 {ep.get('episode_number','?')} 集「{ep.get('title','')}」\n"
        )
        for shot in ep.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            head = _tostr([shot.get("shot_type", ""), f"{shot.get('duration','')}s"], " ")
            out.append(f"#### 镜头 {shot.get('shot_id','?')} — [{head}]\n")
            out.append(_line("场景", shot.get("scene")))
            out.append(_line("镜头运动", shot.get("camera_movement")))
            out.append(_line("动作", shot.get("action")))
            out.append(_line("情绪", shot.get("emotion")))
            out.append(_line("背景", shot.get("background")))
            out.append(_line("光影", shot.get("lighting")))
            out.append(_line("台词", shot.get("dialogue")))
            if shot.get("sd_prompt"):
                out.append(f"- **SD Prompt**：\n  ```text\n  {shot['sd_prompt']}\n  ```\n")
            out.append("\n")
    return "".join(out)


def _render_characters(d: dict) -> str:
    if not isinstance(d, dict):
        return ""
    out = []
    for c in d.get("characters") or []:
        if not isinstance(c, dict):
            continue
        asset = c.get("asset") or {}
        out.append(f"- **{c.get('name','?')}**（{c.get('status','')}）\n")
        for key, label in (("appearance", "外形"), ("personality", "性格"),
                           ("role", "定位"), ("portrait_path", "定妆照")):
            v = asset.get(key) or c.get(key)
            if v:
                out.append(f"  - {label}：{v}\n")
    return "".join(out)


def _render_images(d: dict) -> str:
    if not isinstance(d, dict):
        return ""
    out = []
    for ep, shots in (d.get("images") or {}).items():
        if not isinstance(shots, dict):
            continue
        for sid, info in shots.items():
            if isinstance(info, dict):
                fn = info.get("filename") or info.get("local_path")
                if fn:
                    out.append(f"- 出图 ep{ep} / shot{sid}：`{fn}`\n")
    return "".join(out)


def _render_videos(d: dict) -> str:
    if not isinstance(d, dict):
        return ""
    out = []
    for ep, shots in (d.get("videos") or {}).items():
        if not isinstance(shots, dict):
            continue
        for sid, info in shots.items():
            if isinstance(info, dict):
                fn = info.get("local_path") or info.get("filename")
                if fn:
                    out.append(f"- 视频 ep{ep} / shot{sid}：`{fn}`\n")
    return "".join(out)


def _render_subtitles(d: dict) -> str:
    if not isinstance(d, dict):
        return ""
    items = d.get("subtitles") or d.get("srt_files") or []
    out = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                p = _value(it, "srt_path", "file_path") or _value(it, "filename")
                if p:
                    out.append(f"- `{p}`\n")
    elif isinstance(items, dict):
        for p in items.values():
            if p:
                out.append(f"- `{p}`\n")
    return "".join(out)


def _render_compose(d: dict) -> str:
    if not isinstance(d, dict):
        return ""
    out = []
    for it in d.get("published") or []:
        if isinstance(it, dict):
            p = _value(it, "final_path", "file_path")
            if p:
                out.append(f"- 成片 ep{it.get('episode_number','?')}：`{p}`\n")
    return "".join(out)


def _render_audio(d: dict) -> str:
    if not isinstance(d, dict):
        return ""
    out = []
    if d.get("final_video"):
        out.append(f"- 成品视频：`{d['final_video']}`\n")
    if d.get("audio_track"):
        out.append(f"- 人声轨：`{d['audio_track']}`\n")
    if d.get("bgm"):
        out.append(f"- BGM：`{d['bgm']}`\n")
    for v in d.get("voices") or []:
        if isinstance(v, dict):
            out.append(f"- 台词「{v.get('text','')}」({v.get('duration','')}s)\n")
    return "".join(out)


def _render_publish(d: Any) -> str:
    if isinstance(d, list):
        items = d
    elif isinstance(d, dict):
        items = d.get("published") or d.get("manifest") or d.get("publish") or []
        if not items:
            for v in d.values():
                if isinstance(v, list):
                    items = v
                    break
    else:
        return ""
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict):
            p = _value(it, "file_path", "final_path")
            if p:
                out.append(
                    f"- ep{it.get('episode_number','?')}：`{p}` "
                    f"({it.get('file_size_kb','')}KB, 字幕:{it.get('has_subtitles','')})\n"
                )
    return "".join(out)


# ── 组装笔记 ──────────────────────────

_SECTIONS = [
    (RESEARCH, "一、方案（研究）", _render_research),
    (SCRIPT, "二、剧本", _render_script),
    (STORYBOARD, "三、分镜", _render_storyboard),
    (CHARACTER, "四、角色定妆", _render_characters),
    (IMAGE, "五、出图", _render_images),
    (VIDEO, "六、视频", _render_videos),
    (SUBTITLE, "七、字幕", _render_subtitles),
    (COMPOSE, "八、合成", _render_compose),
    (AUDIO, "九、音频", _render_audio),
    (PUBLISH, "十、发布", _render_publish),
]


def build_note_markdown(results: dict, *, user_input: str = "", styles=None,
                        story_id=None, pipeline_id: str = "") -> str:
    """把管线结果组装成一篇 Markdown 创作笔记。"""
    research = _get(results, RESEARCH)
    script = _get(results, SCRIPT)
    title = _value(research if isinstance(research, dict) else {}, "title") \
        or _value(script if isinstance(script, dict) else {}, "title") or "未命名创作"

    now = datetime.now()
    head = [
        f"# {title} — 创作笔记\n",
        f"\n> 生成时间：{now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"  ·  故事ID:{story_id or '—'}",
        f"  ·  风格：{_tostr(styles or []) or '—'}\n",
    ]
    if pipeline_id:
        head.append(f"> 管线：`{pipeline_id}`\n")
    if user_input:
        head.append(f"> 原始输入：{user_input[:120]}\n")

    body = []
    for key, label, renderer in _SECTIONS:
        d = _get(results, key)
        rendered = renderer(d)
        if not rendered:
            continue
        body.append(f"\n---\n\n## {label}\n{rendered}")

    return "".join(head) + "\n" + "\n".join(body)


# ── 写盘与异步入口 ─────────────────────

def auto_write_creation_note(results: dict, *, user_input: str = "", styles=None,
                             story_id=None, pipeline_id: str = "") -> Path:
    """同步写出笔记文件，返回路径；失败返回空路径。"""
    try:
        content = build_note_markdown(results, user_input=user_input, styles=styles,
                                      story_id=story_id, pipeline_id=pipeline_id)
        dirpath = resolve_notes_dir()
        research = _get(results, RESEARCH)
        script = _get(results, SCRIPT)
        title = _value(research if isinstance(research, dict) else {}, "title") \
            or _value(script if isinstance(script, dict) else {}, "title") or "创作笔记"
        now = datetime.now()
        stem = _safe_name(title, "创作笔记")
        path = dirpath / f"{now.strftime('%Y-%m-%d')}_{stem}.md"
        n = 1
        while path.exists():
            path = dirpath / f"{now.strftime('%Y-%m-%d')}_{stem}_{n}.md"
            n += 1
        path.write_text(content, encoding="utf-8")
        return path
    except Exception:
        return Path("")


async def generate_note_from_results(results: dict, *, user_input: str = "",
                                     styles=None, story_id=None,
                                     pipeline_id: str = ""):
    """异步入口：写盘放到线程执行，避免阻塞事件循环；失败静默。"""
    import asyncio
    try:
        return await asyncio.to_thread(
            auto_write_creation_note, results,
            user_input=user_input, styles=styles,
            story_id=story_id, pipeline_id=pipeline_id,
        )
    except Exception:
        return None
