#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：style_library.py
作用：风格词库（config/style_library.yaml）的加载与写入模块。

为什么拆出来：
1. 用户要求关键词词库"在执行中不断进化成长"——把词库从代码常量改成独立 YAML 文件，
   人工确认新词后直接编辑文件即可，不用改代码。
2. research_agent 与 image_agent 都依赖这套词库（中英文），统一从这里读，避免两份数据不一致。
3. 词条带英文版：中文词用于方案展示/文案，英文词用于 SD/ComfyUI 出图 prompt（英文更稳）。

用法：
    from providers.style_library import load_library
    lib = load_library()
    lib["render_engine_zh"]   # 渲染引擎中文词列表 ["虚幻引擎5.3 Lumen 全局光照", ...]
    lib["lighting_en"]        # 光影英文词列表
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# 词库文件位置（与 config.yaml 同目录）
_LIBRARY_PATH = Path(__file__).parent.parent / "config" / "style_library.yaml"


def load_library(path: Optional[Path] = None) -> Dict[str, List[str]]:
    """加载风格词库，返回四组词列表。

    返回结构：
    {
      "render_engine_zh": [中文词...],
      "render_engine_en": [英文词...],
      "lighting_zh":      [中文词...],
      "lighting_en":      [英文词...],
    }

    文件缺失/格式错误时回退到内置默认 8 条（保证不崩管线）。
    """
    lib_path = path or _LIBRARY_PATH
    # 内置兜底：文件被误删时也能跑（与初版硬编码一致）
    fallback = {
        "render_engine_zh": ["虚幻引擎5.3 Lumen 全局光照", "Nanite 虚拟几何体影视级资产",
                             "Octane X 渲染光谱光照", "Redshift RT 微秒面散射",
                             "V-Ray 6 全局光照物理相机"],
        "render_engine_en": ["unreal engine 5 lumen global illumination",
                             "nanite virtual geometry cinematic assets",
                             "octane render spectral lighting, film-grade materials",
                             "redshift subsurface scattering, photorealistic skin texture",
                             "v-ray 6 global illumination, physical camera"],
        "lighting_zh": ["光线追踪反射", "光线追踪柔和阴影", "路径追踪"],
        "lighting_en": ["ray-traced reflections", "ray-traced soft shadows",
                        "path tracing, physically correct lighting"],
    }

    if not lib_path.exists():
        logger.warning(f"词库文件不存在 {lib_path}，使用内置默认词库")
        return fallback

    try:
        data = yaml.safe_load(lib_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning(f"词库文件解析失败: {e}，使用内置默认词库")
        return fallback

    def _collect(items) -> List[str]:
        """提取词条列表：兼容 [{zh,en,note}] 或 [纯字符串] 两种格式"""
        out = []
        for it in items or []:
            if isinstance(it, dict):
                out.append(str(it.get("zh", "")).strip())
            elif isinstance(it, str):
                out.append(it.strip())
        return [x for x in out if x]

    def _collect_en(items) -> List[str]:
        """提取英文词条（无 en 时用中文占位，保证数量一致）"""
        out = []
        for it in items or []:
            if isinstance(it, dict):
                out.append(str(it.get("en", "") or it.get("zh", "")).strip())
            elif isinstance(it, str):
                out.append(it.strip())
        return [x for x in out if x]

    lib = {
        "render_engine_zh": _collect(data.get("render_engine")) or fallback["render_engine_zh"],
        "render_engine_en": _collect_en(data.get("render_engine")) or fallback["render_engine_en"],
        "lighting_zh": _collect(data.get("lighting")) or fallback["lighting_zh"],
        "lighting_en": _collect_en(data.get("lighting")) or fallback["lighting_en"],
    }
    logger.info(f"风格词库已加载: 渲染引擎 {len(lib['render_engine_zh'])} 条, 光影 {len(lib['lighting_zh'])} 条")
    return lib


def add_words(new_words: List[dict], path: Optional[Path] = None) -> int:
    """向词库追加新词条（进化机制：人工确认后调用）。

    :param new_words: 新词条列表，每项形如 {"zh": "中文", "en": "英文", "note": "适用场景"}
    :param path: 词库文件路径（默认 config/style_library.yaml）
    :return: 成功追加的词条数
    """
    lib_path = path or _LIBRARY_PATH
    if not lib_path.exists():
        # 文件不存在就写一个空模板
        lib_path.parent.mkdir(parents=True, exist_ok=True)
        lib_path.write_text("# 风格词库\nrender_engine: []\nlighting: []\n", encoding="utf-8")

    data = yaml.safe_load(lib_path.read_text(encoding="utf-8")) or {}
    added = 0
    for w in new_words:
        zh = str(w.get("zh", "")).strip()
        en = str(w.get("en", "")).strip()
        if not zh:
            continue
        # 判断应该放进渲染引擎还是光影（按 note/zh 关键词粗分类，人工后续可再调）
        bucket = "lighting" if any(k in (w.get("note", "") + zh) for k in ("光影", "光", "阴影", "反射", "追踪")) else "render_engine"
        items = data.setdefault(bucket, [])
        # 去重：中文已存在则跳过
        if any((isinstance(i, dict) and i.get("zh") == zh) or i == zh for i in items):
            continue
        items.append({"zh": zh, "en": en or zh, "note": w.get("note", "")})
        added += 1

    if added:
        lib_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        logger.info(f"词库已更新: 追加 {added} 条新词 → {lib_path}")
    return added
