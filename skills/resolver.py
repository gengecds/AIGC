#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：skills/resolver.py
作用：Skills 知识库的统一加载与输出模块。

它把 skills/ 下所有 YAML 词库（基础质感、人体解剖、情绪导演、图片/视频各维度）
加载成"可直接拼进 prompt 的英文词块"，供分镜/出图/视频等 Agent 调用。

为什么单独一个模块：
1. 与 providers/style_library.py 解耦——那是"风格"引擎，这里是"技能/知识"引擎，都可独立进化。
2. 词块统一从这里取，避免多 Agent 各写一份、数据不一致。
3. 加载失败一律回退内置兜底，保证管线不崩。

用法：
    from skills.resolver import (
        photoreal_block, anatomy_block, anatomy_negative,
        emotion_micro_block, category_block, list_categories
    )
    prompt = subject + ", " + photoreal_block() + ", " + anatomy_block()
"""

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

import yaml

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent

# 各维度的 YAML 文件：图片 5 类、视频 5 类
_GROUP_FILES = [
    "image/topic.yaml", "image/format.yaml", "image/color.yaml",
    "image/domain.yaml", "image/ai.yaml", "image/scene.yaml",
    "video/genre.yaml", "video/format.yaml", "video/tech.yaml",
    "video/camera.yaml", "video/motion.yaml",
    "video/use.yaml", "video/gen.yaml",
    "style/visual.yaml",
]


# ── 底层加载 ────────────────────────────

def _read_yaml(rel: str) -> dict:
    fp = _SKILLS_DIR / rel
    if not fp.exists():
        return {}
    try:
        return yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[skills] 解析 {rel} 失败: {e}")
        return {}


def _collect_en(items) -> List[str]:
    """从 [{zh,en,note}] 或 [str] 列表里收集英文词"""
    out = []
    for it in items or []:
        if isinstance(it, dict):
            out.append(str(it.get("en", "") or it.get("zh", "")).strip())
        elif isinstance(it, str):
            out.append(it.strip())
    return [x for x in out if x]


@lru_cache(maxsize=None)
def _flat_blocks() -> Dict[str, List[str]]:
    """加载基础质感 / 人体解剖 / 情绪导演这三个"平铺词链表"文件。

    返回 {"photoreal": [...], "anatomy_pos": [...], "anatomy_neg": [...], "emotion": [...]}
    """
    data = {}
    # 基础摄影师质感：photoreal.yaml 内多组（camera/lighting/texture/quality/boost）
    pr = _read_yaml("base/photoreal.yaml")
    pr_flat: List[str] = []
    for _bucket, items in pr.items():
        pr_flat += _collect_en(items)
    data["photoreal"] = pr_flat

    # 人体解剖：正向 / 负向
    an = _read_yaml("base/anatomy.yaml")
    data["anatomy_pos"] = _collect_en(an.get("positive") or [])
    data["anatomy_neg"] = _collect_en(an.get("negative") or [])

    # 情绪导演：所有情绪分组的英文词
    em = _read_yaml("emotion_director/words.yaml")
    em_flat: List[str] = []
    for _bucket, items in em.items():
        em_flat += _collect_en(items)
    data["emotion"] = em_flat
    return data


@lru_cache(maxsize=None)
def _group_categories() -> Dict[str, Dict[str, List[str]]]:
    """加载图片/视频各维度的分类词库。

    返回 {"image/topic": {"自然与风景": [...en词], ...}, "video/genre": {...}, ...}
    """
    out: Dict[str, Dict[str, List[str]]] = {}
    for rel in _GROUP_FILES:
        file_dir = rel.rsplit("/", 1)[0]
        file_key = rel.rsplit("/", 1)[1].replace(".yaml", "")
        key = f"{file_dir}/{file_key}"
        out[key] = {}
        data = _read_yaml(rel)
        for g in data.get("groups") or []:
            if not isinstance(g, dict):
                continue
            name = g.get("name")
            if not name:
                continue
            out[key][name] = _collect_en(g.get("words") or [])
    return out


# ── 对外输出 ────────────────────────────

def photoreal_block() -> str:
    """无条件注入的"摄影师实拍质感"英文词块。"""
    return ", ".join(_flat_blocks()["photoreal"])


def anatomy_block() -> str:
    """人体结构/一致性正向词块（防崩坏、防变形）。"""
    return ", ".join(_flat_blocks()["anatomy_pos"])


def anatomy_negative() -> str:
    """人体/质量负向词块（畸形脸、多余手指、插画感、水印等）。"""
    return ", ".join(_flat_blocks()["anatomy_neg"])


@lru_cache(maxsize=None)
def _emotion_pairs() -> List[dict]:
    """返回情绪词条原始结构（含 zh/en/note），供 hint 匹配与注入。"""
    em = _read_yaml("emotion_director/words.yaml")
    return [it for _bucket, items in em.items() for it in (items or [])]


def emotion_micro_block(hint: str = "") -> str:
    """微表情英文词块。可用 hint（如"悲伤/强忍"）过滤，无则返回全部。

    用于人物近景/特写，让角色"有戏"而不像摆拍。
    hint 命中词条的 中文/英文/备注 任一子串即优先返回；若 hint 命中不了任何词条
    （说明是陌生情绪），则回退到"中性有戏表情"而非硬塞一种具体情绪，避免表情错位。
    """
    pairs = _emotion_pairs()
    if hint:
        hint_l = hint.lower()
        matched = []
        for it in pairs:
            zh = str(it.get("zh", "")).strip().lower()
            en = str(it.get("en", "")).strip().lower()
            note = str(it.get("note", "")).strip().lower()
            hit = False
            # 中文无空格，不能按空白切分——直接做子串匹配：词条的 zh 是否出现在 hint 里
            if zh and len(zh) >= 2 and zh in hint_l:
                hit = True
            if not hit:
                # 英文词条按词切分，命中任一单词即可
                if en and any(w and w in hint_l for w in en.replace(",", " ").split()):
                    hit = True
            if not hit:
                # 备注里的中文关键词（如"克制""落泪"）作为作者工具词也参与匹配
                if note and any(w and len(w) >= 2 and w in hint_l
                               for w in note.replace("，", " ").replace(",", " ").split()):
                    hit = True
            if hit:
                matched.append(str(it.get("en", "") or it.get("zh", "")).strip())
        if matched:
            return ", ".join([x for x in matched if x])
        # hint 有值但没匹配到具体情绪 → 中性"有戏"表情（不指定喜怒，安全不冲突）
        return ("natural micro-expression, lively eyes with catchlight, "
                "relaxed facial muscles, subtle emotional nuance")
    # 无 hint：返回完整微表情词表（供分镜 LLM 挑选用）
    return ", ".join([x for x in (_collect_en(pairs)) if x])


def category_block(dimension: str, name: str) -> str:
    """取某维度某分类的英文词块。

    dimension 形如 "image/topic"、"video/genre"；name 为分类名（如"自然与风景"）。
    找不到返回空串。
    """
    cats = _group_categories().get(dimension, {})
    words = cats.get(name, [])
    return ", ".join(words)


# ── 自动题材判定（image_agent 用）──────────────
# 只有"真的会出现在画面上"的角色才触发人物题材配方。旁白/声线/画外音不是画面角色：
# 实测它们会被当成"单人角色"，把空镜/落叶镜套上整段人像特写配方，渲染成人物特写。
# 纯空镜/物件镜一律不注入题材原型配方——分镜自带的 sd_prompt 已完整描述场景，
# 而原型配方（如"sharp animal eyes, macro flower"）会整段压过场景语义。
_NON_VISUAL_NAME_HINTS = ("旁白", "声线", "配音", "解说", "画外", "吟诵", "独白", "朗诵",
                          "narrator", "voiceover", "voice-over")


def visible_characters(shot: dict) -> list[str]:
    """分镜里会出现在画面上的角色名（旁白/声线/画外音类剔除）。"""
    chars = shot.get("characters") or []
    if not isinstance(chars, list):
        return []
    out = []
    for c in chars:
        name = str(c or "").strip()
        if not name:
            continue
        if any(h in name.lower() for h in _NON_VISUAL_NAME_HINTS):
            continue
        out.append(name)
    return out


def _hit_kw(text_lower: str, kw: str) -> bool:
    """中英文关键词统一匹配：英文=单词边界（防误命中），中文=子串。"""
    if kw.isascii():
        return re.search(r"\b" + re.escape(kw) + r"\b", text_lower) is not None
    return kw in text_lower


# 只有这些景别才允许套「单人电影特写」配方；中/全/远等景别套特写配方会把
# 中景硬拽成胸像大特写、丢掉分镜场景（实测 16/16 个含人物镜头全被拽爆）。
_CLOSEUP_SHOT_TYPES = ("近", "特写", "近景", "大特写")


def is_closeup_shot(shot: dict) -> bool:
    """是否近距离人像景别（近/特写/大特写/近景）。

    这类景别才适合套「单人电影特写」配方、也才适合挂胸像定妆照做角色锁定：
    中/全/远挂胸像参考图会把它的构图与背景一起搬过来（实测中景被拽成灰底胸像）。
    """
    return str(shot.get("shot_type") or "").strip() in _CLOSEUP_SHOT_TYPES


def shot_topic_name(shot: dict) -> str:
    """据分镜内容 + 景别推断题材分组名（空镜返回空串=不注入题材词块）。

    - 近/特写/大特写 + 单人 → 人物与肖像·单人电影特写；≥3 人 → 人物与肖像·群体合影
    - 中/全/远等景别 → ""（特写配方含 close-up head-and-shoulders framing 与
      blurred neutral background，套在中景上会丢掉场景与动作，交给分镜自带的
      sd_prompt 描述更准）
    - 双人 / 无画面角色 → ""（既非单人特写也非群体合影，硬套任一配方都会跑偏）
    """
    chars = visible_characters(shot)
    if len(chars) >= 3:
        return "人物与肖像·群体合影"
    if len(chars) == 1 and is_closeup_shot(shot):
        return "人物与肖像·单人电影特写"
    return ""


def shot_topic_block(shot: dict) -> str:
    """据分镜内容返回题材词块（供 image_agent._shot_skills 无条件叠加）。

    拿不到题材时返回空串，不影响原有 photoreal/anatomy 质量下限。
    """
    name = shot_topic_name(shot)
    return category_block("image/topic", name) if name else ""


# ── 自动匹配（video_agent 用）────────────────
# 视频层词块：运镜(camera)、题材(genre)、用途(use)。
# 与 image 的 shot_topic 一致——不从"用户给了什么词"照搬，而是从分镜内容
# (camera_movement/scene/action/background/sd_prompt)自动推断该镜属于哪一类，
# 再取出该类"可执行的整套镜头语言配方"，避免 AI 拿到抽象词只能自由发挥。
# 匹配规则：中文=子串；英文=单词边界（_hit_kw 统一处理）。

# 运镜：分镜的 camera_movement 多为中文（如"缓慢前推，由中景推至近景"），故以中文关键词为主
_CAMERA_KEYWORDS = [
    ("推镜头", ["前推", "推进", "推近", "推镜", "慢推", "dolly in", "push in", "push-in"], []),
    ("拉镜头", ["拉远", "拉出", "后拉", "拉镜", "pull out", "dolly out", "pull-out"], []),
    ("摇镜头", ["摇摄", "摇镜", "水平摇", "panning", "pan shot"], []),
    ("移镜头", ["横移", "横向移动", "平行移动", "侧移", "tracking", "trucking"], []),
    ("环绕镜头", ["环绕", "旋转", "绕圈", "绕行", "转一周", "360", "orbit", "revolve"], []),
    ("升降镜头", ["升降", "抬升", "上升", "下降", "吊臂", "crane", "pedestal"], []),
    ("甩镜头", ["甩", "快速甩", "whip pan", "swish"], []),
    ("闯入式镜头", ["闯入", "探入", "斯皮尔伯格", "intrusion"], []),
    ("电影运镜风格", ["迈克尔贝", "手持", "电影运镜", "摄影机真实", "handheld", "cinematic camera"], []),
]

# 题材：从 scene/action 推断整条片子的题材归属（影视/新闻/Vlog/知识/游戏/科技）
_GENRE_KEYWORDS = [
    ("影视与综艺", ["影视", "电影", "电视剧", "剧集", "综艺", "情节剧", "film", "drama", "variety show", "tv series"], []),
    ("新闻与资讯", ["新闻", "资讯", "播报", "记者", "专访", "news", "report", "interview", "anchor"], []),
    ("生活与Vlog", ["vlog", "日常", "生活", "旅行", "开箱", "记录", "life", "daily", "vlog"], []),
    ("知识与教育", ["知识", "教育", "课程", "课堂", "学习", "教程", "讲解", "edu", "course", "tutorial", "lesson", "learn"], []),
    ("娱乐与游戏", ["游戏", "电竞", "娱乐", "电玩", "赛事", "game", "gaming", "esports", "entertainment"], []),
    ("科技与数码", ["科技", "数码", "手机", "笔记本", "平板", "人工智能", "tech", "digital", "gadget", "device", "smartphone"], []),
]

# 用途：从 scene/action 推断"这条视频干啥用"（广告/监控/医疗/AI训练/直播带货）
_USE_KEYWORDS = [
    ("广告与宣传片", ["广告", "宣传", "品牌", "种草", "卖点", "promo", "advert", "brand", "product hero", "campaign"], []),
    ("监控与安防", ["监控", "安防", "摄像头", "安保", "取证", "surveillance", "security", "cctv", "monitoring"], []),
    ("医疗与工业", ["医疗", "手术", "医院", "诊", "工业", "车间", "工厂", "生产", "medical", "surgery", "hospital", "industrial", "factory", "procedure"], []),
    ("AI训练", ["训练数据", "数据集", "ai训练", "采集数据", "dataset", "annotation", "training data"], []),
    ("直播带货", ["直播", "带货", "主播", "直播间", "卖货", "live commerce", "livestream", "host selling"], []),
]


def _match_group(text: str, table) -> str:
    """按中英文关键词推断某维度分组名（复用 image 的 _hit_kw 规则），未命中返回空串。"""
    if not text:
        return ""
    text_lower = text.lower()
    for name, zh_kws, en_kws in table:
        for kw in zh_kws + en_kws:
            if _hit_kw(text_lower, kw):
                return name
    return ""


def _group_name(text: str, table) -> str:
    """_match_group 的别名，语义更清晰。"""
    return _match_group(text, table)


def shot_camera_name(shot: dict) -> str:
    """据分镜 camera_movement/transition 推断运镜分组名（推/拉/摇/环绕…）。"""
    text = " ".join(str(shot.get(k) or "") for k in ("camera_movement", "transition", "action"))
    return _group_name(text, _CAMERA_KEYWORDS)


def shot_camera_block(shot: dict) -> str:
    """据分镜内容返回运镜词块（video/camera）。拿不到返回空串。"""
    name = shot_camera_name(shot)
    return category_block("video/camera", name) if name else ""


def shot_genre_name(shot: dict) -> str:
    """据分镜 scene/action 推断内容题材分组名（影视/新闻/Vlog/知识/游戏/科技）。"""
    text = " ".join(str(shot.get(k) or "") for k in ("scene", "action", "background", "sd_prompt"))
    return _group_name(text, _GENRE_KEYWORDS)


def shot_genre_block(shot: dict) -> str:
    """据分镜内容返回内容题材词块（video/genre）。拿不到返回空串。"""
    name = shot_genre_name(shot)
    return category_block("video/genre", name) if name else ""


def shot_use_name(shot: dict) -> str:
    """据分镜 scene/action 推断用途分组名（广告/监控/医疗/AI训练/直播带货）。"""
    text = " ".join(str(shot.get(k) or "") for k in ("scene", "action", "background", "sd_prompt"))
    return _group_name(text, _USE_KEYWORDS)


def shot_use_block(shot: dict) -> str:
    """据分镜内容返回用途词块（video/use）。拿不到返回空串。"""
    name = shot_use_name(shot)
    return category_block("video/use", name) if name else ""


# ── 视觉风格（image/video 共用）──────────────
# 决定"画面长什么样"的修饰层，可单选亦可多风格合并（如"赛博朋克 + 国风古风"）。
# 与 image/topic 一致——不从"用户给了什么词"照搬，而是按文本关键词自动推断风格分组，
# 再取出该风格的可组合英文词块。中文=子串；英文=单词边界（_hit_kw 统一处理）。
_STYLE_KEYWORDS = [
    ("国风古风", ["国风", "古风", "汉服", "工笔", "敦煌", "壁画", "国潮", "中式的"],
     ["chinese", "guofeng", "hanfu", "gongbi", "dunhuang", "ink wash", "oriental"]),
    ("复古港风", ["港风", "复古港", "胶片", "港片", "老香港", "复古旧"],
     ["hong kong", "retro film", "vintage"],
     ),
    ("蒸汽波", ["蒸汽波", "vaporwave"],
     ["vaporwave", "glitch retro"]),
    ("蒸汽朋克", ["蒸汽朋克", "steampunk", "黄铜机械", "齿轮飞艇"],
     ["steampunk", "brass machinery", "airship"]),
    ("赛博朋克", ["赛博朋克", "赛博", "霓虹雨夜", "机械义体", "cyberpunk"],
     ["cyberpunk", "neon city", "cyber"]),
    ("治愈系ins", ["治愈", "ins风", "清新", "暖调", "慢生活", "森系"],
     ["healing", "instagram", "cozy", "cosy", "pastel soft", "airlight"]),
    ("极简主义", ["极简", "留白", "莫兰迪", "高级灰", "minimal", "简约"],
     ["minimalist", "minimal", "morandi", "negative space"]),
    ("日系动漫", ["日系", "动漫", "二次元", "吉卜力", "新海诚", "赛璐璐", "anime"],
     ["anime", "ghibli", "shinkai", "cel shading", "japanese anime"]),
    ("3D卡通", ["3d卡通", "皮克斯", "3d风格", "黏土", "卡通渲染", "pixar"],
     ["pixar", "3d cartoon", "clay", "3d render"],),
    ("Q版卡通", ["q版", "q版卡通", "二头身", "萌系", "chibi", "可爱卡通"],
     ["chibi", "kawaii", "cute cartoon"]),
    ("像素艺术", ["像素", "像素风", "pixel", "16bit", "16-bit", "复古游戏"],
     ["pixel art", "16-bit", "retro game", "8-bit"]),
    ("古典油画", ["油画", "古典油绘", "油彩", "heavy"],
     ["oil painting", "impasto", "oil on canvas"]),
    ("水彩", ["水彩", "水彩晕染", "watercolor"],
     ["watercolor", "water color"]),
    ("素描", ["素描", "铅笔", "速写", "线稿", "sketch", "pencil"],
     ["sketch", "pencil", "graphite", "lineart"]),
    ("电影级质感", ["电影感", "电影级", "大片", "2.35", "宽银幕", "浅景深", "cinematic"],
     ["cinematic", "film grade", "blockbuster", "anamorphic", "widescreen"]),
]


def _style_words(name: str) -> List[str]:
    """取某视觉风格分组下的英文词（条目边界保留，供合并去重）。未命中返回空列表。"""
    if not name:
        return []
    cats = _group_categories().get("style/visual", {})
    return cats.get(name, []) or []


def style_name_by_text(text: str) -> str:
    """按文本关键词推断视觉风格分组名（国风古风/赛博朋克/治愈系…）。未命中返回空串。"""
    return _match_group(str(text or ""), _STYLE_KEYWORDS)


def style_block(name: str) -> str:
    """取某视觉风格英文词块（style/visual）。未命中返回空串。"""
    return ", ".join(_style_words(name))


def style_block_bytext(text: str) -> str:
    """按文本自动匹配视觉风格词块；未命中返回空串。"""
    return style_block(style_name_by_text(text))


def merge_styles(*names: str) -> str:
    """合并多个视觉风格（如"赛博朋克","国风古风"→ 赛博朋克国风）。

    取各风格英文词块的并集（按词条去重、保序拼接），未命中/无效风格自动跳过。
    """
    seen = set()
    out = []
    for n in names:
        for word in _style_words(str(n or "")):
            if word and word not in seen:
                seen.add(word)
                out.append(word)
    return ", ".join([x for x in out if x])


def list_categories() -> Dict[str, List[str]]:
    """列出所有可用的维度及其分类名（供前端/自进化引擎遍历）。"""
    return {k: list(v.keys()) for k, v in _group_categories().items()}


def add_category_words(dimension: str, name: str, words: List[dict]) -> int:
    """向某维度某分类追加新词（自进化引擎调用；人工确认后落盘）。

    :param words: 每项 {"zh":..., "en":...}
    :return: 实际写入词条数
    """
    if dimension not in _GROUP_FILES and not dimension.endswith(".yaml"):
        # 允许传 "image/topic.yaml" 形式
        pass
    # 定位文件：dimension 传 KEY（如 image/topic）或直接文件名
    key = dimension
    if not key.startswith("image/") and not key.startswith("video/") and not key.startswith("style/"):
        return 0
    rel = key if key.endswith(".yaml") else f"{key}.yaml"
    fp = _SKILLS_DIR / rel
    data = _read_yaml(rel)
    groups = data.setdefault("groups", [])
    target = None
    for g in groups:
        if isinstance(g, dict) and g.get("name") == name:
            target = g
            break
    if target is None:
        target = {"name": name, "en": name, "words": []}
        groups.append(target)
    existing = {w.get("zh") for w in target.get("words", []) if isinstance(w, dict)}
    added = 0
    for w in words:
        zh = str(w.get("zh", "")).strip()
        en = str(w.get("en", "")).strip()
        if not zh and not en:
            continue
        if zh in existing:
            continue
        target.setdefault("words", []).append({"zh": zh, "en": en or zh})
        existing.add(zh)
        added += 1
    if added:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        _group_categories.cache_clear()
        logger.info(f"[skills] 已向 {rel}·{name} 追加 {added} 条词")
    return added


def add_flat_words(rel: str, bucket: str, words: List[dict]) -> int:
    """向"平铺词链表"文件（base/photoreal、base/anatomy、emotion_director/words）追加词条。

    :param rel: 如 "base/photoreal.yaml"、"base/anatomy.yaml"、"emotion_director/words.yaml"
    :param bucket: 分组名，如 photoreal 的 "camera"、anatomy 的 "positive"/"negative"、
                  words 里的 "sadness" 等
    :param words: 每项 {"zh":..., "en":...}
    :return: 实际写入词条数
    """
    if not rel.endswith(".yaml"):
        rel = f"{rel}.yaml"
    fp = _SKILLS_DIR / rel
    if not fp.exists():
        logger.warning(f"[skills] 平铺词库不存在，跳过多写: {rel}")
        return 0
    data = _read_yaml(rel) or {}
    items = data.setdefault(bucket, [])
    existing = {w.get("zh") for w in items if isinstance(w, dict)}
    added = 0
    for w in words:
        zh = str(w.get("zh", "")).strip()
        en = str(w.get("en", "")).strip()
        if not zh and not en:
            continue
        if zh in existing:
            continue
        items.append({"zh": zh, "en": en or zh, "note": str(w.get("note", "") or "")})
        existing.add(zh)
        added += 1
    if added:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        _flat_blocks.cache_clear()
        logger.info(f"[skills] 已向 {rel}·{bucket} 追加 {added} 条词")
    return added
