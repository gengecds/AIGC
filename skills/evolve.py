#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：skills/evolve.py
作用：技能知识库的「自进化引擎」。

它负责把"学来的好东西"（网页摘要 / LLM 提炼 / 人工手写）解析成标准词条
{zh, en, note}，并按目标写入 skills/ 下对应 YAML，从而让整个 skills 体系
不断进化和自我完善，越用越懂你、越用越像"专业摄影师/导演"。

调用链（人工确认后落盘，避免污染词库）：
    text/entries  →  parse_word_entries()  →  ingest()  →  resolver.add_*_words()

用法：
    from skills.evolve import ingest, parse_word_entries, suggest_words_llm

    # 1) 从一段文本里提炼并写入
    n = ingest(dimension="image/topic", name="自然与风景",
               text="晨雾山峦：misty mountain range\n落日海面：sunset over the sea")
    # 2) 直接用结构化词条写入
    n = ingest(dimension="emotion_director", name="sadness",
               entries=[{"zh": "咬住下唇", "en": "biting lower lip"}])
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 允许直接作为"平铺词库"目标的 rel 路径（其余自动归入 image/video 分组）
_FLAT_FILES = {
    "photoreal": "base/photoreal.yaml",
    "anatomy": "base/anatomy.yaml",
    "emotion_director": "emotion_director/words.yaml",
}


# ── 解析 ───────────────────────────────

def parse_word_entries(text: str) -> List[dict]:
    """从一段文本里解析出 [{zh, en}] 词条。

    支持常见分隔写法，一行一条，或一行多条用 ; 分隔：
      - "晨雾山峦：misty mountain range"
      - "晨雾山峦: misty mountain range"
      - "晨雾山峦 → misty mountain range"
      - "晨雾山峦 -> misty mountain range"
      - "晨雾山峦 - misty mountain range"
    若某行没有英文，则中文当 zh、en 留空（后续回退用 zh）。
    """
    entries: List[dict] = []
    for line in (text or "").splitlines():
        line = line.strip().strip("。；;，,").strip()
        if not line:
            continue
        # 拆出多个 "zh: en" 用分号分隔（同一行多条）
        for seg in re.split(r"[;；]", line):
            seg = seg.strip()
            if not seg:
                continue
            m = re.match(
                r"^(.{1,30}?)\s*(?:：|:|\u2192|->|—|-)\s*(.{1,120})$",
                seg,
            )
            if m:
                zh, en = m.group(1).strip(), m.group(2).strip()
                entries.append({"zh": zh, "en": en})
            else:
                # 无分隔符 → 当作 zh
                entries.append({"zh": seg, "en": seg})
    return entries


# ── 写入路由 ───────────────────────────

def _flat_target(dimension: str, name: str) -> Optional[dict]:
    """识别平铺词库目标；非平铺返回 None。"""
    base = dimension.rstrip(".yaml")
    if base in _FLAT_FILES:
        return {"rel": _FLAT_FILES[base], "bucket": name}
    if "/" in dimension and dimension.split("/", 1)[0] in ("base", "emotion_director"):
        return {"rel": f"{dimension}.yaml", "bucket": name}
    return None


def ingest(
    dimension: str,
    name: str,
    text: Optional[str] = None,
    entries: Optional[List[dict]] = None,
) -> dict:
    """把解析出的词条写入指定目标，返回 {added, total, target}。

    dimension/name 语义：
      - 分组词库：dimension="image/topic"（或 "video/genre"），name="自然与风景"
      - 平铺词库：dimension="photoreal"（或 "base/photoreal" / "emotion_director"），
                  name 为分组（如 camera / positive / sadness）
      例：ingest("emotion_director", "sadness", text="咬住下唇: biting lower lip")
    """
    candidates = entries or parse_word_entries(text or "")
    if not candidates:
        return {"added": 0, "total": 0, "target": f"{dimension}|{name}"}

    flat = _flat_target(dimension, name)
    if flat:
        from skills.resolver import add_flat_words
        added = add_flat_words(flat["rel"], flat["bucket"], candidates)
        return {"added": added, "total": len(candidates),
                "target": f"{flat['rel']}·{flat['bucket']}"}

    from skills.resolver import add_category_words
    added = add_category_words(dimension, name, candidates)
    return {"added": added, "total": len(candidates),
            "target": f"{dimension}·{name}"}


# ── LLM 提炼（真正"学习网上好内容"的入口）──

async def suggest_words_llm(
    dimension: str,
    name: str,
    seed_material: str = "",
    model: str = "qwen3:8b",
) -> List[dict]:
    """用本地 Ollama 提炼某分类的优质提示词（供人工确认后 ingest）。

    输入 seed_material 可为参考/标题/摘要；模型会输出一批
    "中文：英文" 词条，然后走 parse_word_entries 解析。
    """
    try:
        from providers.llm import OllamaProvider
        llm = OllamaProvider(model=model)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[evolve] 无法加载 Ollama: {e}")
        return []

    system = (
        "你是一个顶级的 AI 图像/视频提示词工程师。请把你学到的、能被用于 "
        "Stable Diffusion / ComfyUI 出图的中文+英文提示词提炼出来。\n"
        "只输出一批 '中文：英文' 词条，每行一条，不要解释、不要编号、不要 markdown。\n"
        "英文要地道、具体、可直接拼进 sd_prompt，例如：\n"
        "清晨薄雾山谷：misty valley at dawn, soft fog rolling between ridges\n"
        "日落后蓝调时刻：blue hour after sunset, cool ambient light\n"
    )
    prompt = (
        f"目标分类：{dimension} / {name}\n"
        f"参考素材：{seed_material or '（无，凭你专业知识）'}\n"
        "请给出 10-20 条该分类下最能提升'真实感/专业摄影质感'的中英提示词。"
    )
    try:
        raw = await llm.generate(prompt=prompt, system_prompt=system, json_mode=False)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[evolve] LLM 提炼失败: {e}")
        return []
    return parse_word_entries(str(raw or ""))
