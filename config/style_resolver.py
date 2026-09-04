"""风格 → 模型 解析器

单用户本地工作时，用模块级「当前风格」记录本次管线使用的风格，
OllamaProvider / ComfyUI 客户端据此选择对应的 LLM 模型与出图模型。

并发防御：用 threading.Lock 保护，避免多 pipeline 时竞态（本地单用户基本不会触发）。
"""

import threading

from config.settings import settings

_lock = threading.Lock()
_active_styles: list[str] = ["写实风格"]


def _styles_raw() -> dict:
    """返回 styles 原始 dict（settings 的 __getattr__ 只会返回代理，这里直接取底层数据）。"""
    raw = getattr(settings, "_data", {}) or {}
    return raw.get("styles", {}) or {}


def list_styles() -> dict:
    """返回所有风格及其配置（含给前端展示的 description/advice/recommended_models）。"""
    return _styles_raw()


def get_style_entry(name: str | None = None) -> dict | None:
    """获取某个风格的配置条目；name 为空时用当前主风格（多风格时的第一个）。"""
    styles = _styles_raw()
    name = name or _primary_name()
    if not name:
        return None
    entry = styles.get(name)
    if isinstance(entry, dict):
        return entry
    return None


def _primary_name() -> str:
    """当前激活风格里的主风格（列表第一个，决定底模/模型）。"""
    return _active_styles[0] if _active_styles else "写实风格"


def set_active_styles(names: list[str] | None):
    """设置本次管线使用的风格列表（可多选自由组合）。

    顺序保留；第一个作为「主风格」，决定出图/视频底模等模型选择。
    """
    global _active_styles
    clean: list[str] = []
    for n in (names or []):
        if isinstance(n, str) and n.strip() and n not in clean:
            clean.append(n.strip())
    if not clean:
        clean = ["写实风格"]
    with _lock:
        _active_styles = clean


def set_active_style(name: str):
    """设置本次管线使用的风格（单风格，等价于只选一个）。"""
    set_active_styles([name] if name else ["写实风格"])


def current_style() -> str:
    """当前主风格名（多风格时返回第一个）。"""
    return _primary_name()


def current_styles() -> list[str]:
    """当前所有激活风格。"""
    return list(_active_styles)


def style_label(sep: str = " + ") -> str:
    """当前所选风格的展示标签（如「赛博朋克 + 国风古风」）。"""
    return sep.join(_active_styles)


def llm_model_for_style(name: str | None = None, default: str | None = None) -> str:
    """当前/指定风格对应的 LLM 模型（剧本/分镜用）。"""
    entry = get_style_entry(name)
    if entry and entry.get("llm_model"):
        return entry["llm_model"]
    return default or getattr(settings.ollama, "model", "qwen3:8b")


def image_ckpt_for_style(name: str | None = None, default: str | None = None) -> str | None:
    """当前/指定风格对应的出图 checkpoint（需已在 ComfyUI 的 models/checkpoints 下）。"""
    entry = get_style_entry(name)
    if entry and entry.get("image_ckpt"):
        return entry["image_ckpt"]
    return default


def image_model_type_for_style(name: str | None = None, default: str | None = None) -> str | None:
    """当前/指定风格对应的出图引擎类型（sd15 / flux）。"""
    entry = get_style_entry(name)
    if entry and entry.get("image_model_type"):
        return entry["image_model_type"]
    return default


def video_model_type_for_style(name: str | None = None, default: str | None = None) -> str | None:
    """当前/指定风格对应的视频引擎类型（ltx_mlx / minimax_h3 已废弃）。

    注意：视频引擎现在固定为 LTX-2.3 MLX（见 config.yaml engine.video_engine），
    风格表里该字段仅作为能力路由的兜底值保留。
    """
    entry = get_style_entry(name)
    if entry and entry.get("video_model_type"):
        return entry["video_model_type"]
    return default or "ltx_mlx"


def tts_for_style(name: str | None = None) -> tuple[str, str]:
    """当前/指定风格的 TTS 配置（音频配音用）：返回 (engine, voice_prompt)。

    风格里没有单独配置时，回退到 config.yaml 的 tts 段。
    """
    entry = get_style_entry(name)
    engine = entry.get("tts_engine") if entry and entry.get("tts_engine") else None
    voice = entry.get("voice_prompt") if entry and entry.get("voice_prompt") else None
    if not engine or not voice:
        tts_cfg = getattr(settings, "tts", None)
        engine = engine or getattr(tts_cfg, "engine", "piper") or "piper"
        voice = voice or getattr(tts_cfg, "voice_prompt", "") or ""
    return engine, voice


def subtitle_config_for_style(name: str | None = None) -> dict:
    """当前/指定风格的字幕生成配置（字幕步骤用）。

    subtitle_mode: rule(规则引擎,默认) / llm(大模型润色/翻译)
    subtitle_lang: zh / en / biling
    """
    entry = get_style_entry(name) or {}
    return {
        "subtitle_mode": entry.get("subtitle_mode") or "rule",
        "subtitle_lang": entry.get("subtitle_lang") or "zh",
    }


def _merge_keywords(names: list[str]) -> list[str]:
    """合并多个风格的关键词（去重保序）。"""
    out: list[str] = []
    for n in names:
        entry = get_style_entry(n)
        kw = entry.get("keywords") if entry and isinstance(entry.get("keywords"), list) else []
        for k in kw:
            if k not in out:
                out.append(k)
    return out


def _merge_neg_prompt(names: list[str]) -> str:
    """合并多个风格的主负向提示词（去重保序，逗号分隔）。"""
    parts: list[str] = []
    for n in names:
        entry = get_style_entry(n)
        neg = entry.get("neg_prompt") if entry and entry.get("neg_prompt") else ""
        for token in neg.split(","):
            t = token.strip()
            if t and t not in parts:
                parts.append(t)
    return ", ".join(parts)


def style_keywords(name: str | None = None) -> list[str]:
    """风格要注入到剧本/分镜 prompt 的关键词。

    name 给定→只取该风格；name 为空→合并当前所有激活风格的关键词（自由组合）。
    """
    if name is not None:
        entry = get_style_entry(name)
        if entry and isinstance(entry.get("keywords"), list):
            return entry["keywords"]
        return []
    return _merge_keywords(_active_styles)


def style_neg_prompt(name: str | None = None) -> str:
    """风格的主负向提示词。

    name 给定→只取该风格；name 为空→合并当前所有激活风格的负向词。
    """
    if name is not None:
        entry = get_style_entry(name)
        if entry and entry.get("neg_prompt"):
            return entry["neg_prompt"]
        return ""
    return _merge_neg_prompt(_active_styles)


# ── 白名单字段：前端「模型库」允许保存的字段（避免写入任意非法内容）──
_ALLOWED_UPDATE_FIELDS = {
    "llm_model", "image_ckpt", "image_model_type",
    "video_model", "video_model_type",
    "tts_engine", "voice_prompt", "subtitle_mode", "subtitle_lang",
    "description", "advice", "recommended_models",
    "keywords", "neg_prompt",
}


def update_style_config(name: str, updates: dict) -> dict | None:
    """把某个风格的模型配置写回 config.yaml，并刷新内存 settings 立即生效。

    :param name: 风格名（必须在 config.yaml 的 styles 里已存在）
    :param updates: 需更新的字段字典（llm_model / image_ckpt / image_model_type 等）
    :return: 更新后的风格配置；风格不存在返回 None
    """
    if not name or not isinstance(updates, dict):
        return get_style_entry(name)

    from pathlib import Path
    import yaml

    # 只保留白名单且非 None 的字段
    updates = {k: v for k, v in updates.items() if k in _ALLOWED_UPDATE_FIELDS and v is not None}
    if not updates:
        return get_style_entry(name)

    cfg_path = Path(__file__).parent / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    styles = data.setdefault("styles", {})
    if name not in styles:
        return None
    entry = styles[name]
    if not isinstance(entry, dict):
        entry = styles[name] = {}
    entry.update(updates)

    cfg_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # 刷新内存 settings，使正在运行的后端无需重启即可用新配置
    raw = getattr(settings, "_data", {}) or {}
    raw.setdefault("styles", {})[name] = entry
    return entry
