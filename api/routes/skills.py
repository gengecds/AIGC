"""Skills 知识库 API 路由

把「图片/视频各维度分类」、「基础质感/人体解剖/情绪导演词库」以及「自进化引擎」
暴露成 HTTP 接口，供前端「技能库」页查看与人工导入/调用。

路由：
- GET  /api/v1/skills/categories              列出所有维度及其分类
- GET  /api/v1/skills/block/{dimension}       某维度的所有分类词
- GET  /api/v1/skills/base/{block}            某基础词块（photoreal/anatomy/anatomy_negative/emotion）
- POST /api/v1/skills/learn                   导入词条（ingest，人工确认后落盘）
- POST /api/v1/skills/learn/from-llm          LLM 提炼某分类词条（返回候选，需再确认）
"""

import asyncio
import logging

from fastapi import HTTPException
from pydantic import BaseModel
from typing import List, Optional

logger = logging.getLogger(__name__)

from skills.resolver import (
    photoreal_block, anatomy_block, anatomy_negative,
    emotion_micro_block, category_block, list_categories,
)


def register_skills_routes(app):
    """直接添加路由到 app（与 pipeline.py 一致的注册方式）"""

    @app.get("/api/v1/skills/categories")
    async def get_categories():
        return {"dimensions": list_categories()}

    @app.get("/api/v1/skills/block/{dimension:path}")
    async def get_block(dimension: str):
        """返回某维度所有分类的英文词块，如 /api/v1/skills/block/image/topic"""
        cats = list_categories().get(dimension, {})
        if not cats:
            raise HTTPException(404, f"未知维度: {dimension}")
        return {
            "dimension": dimension,
            "categories": {
                c: category_block(dimension, c) for c in cats
            },
        }

    @app.get("/api/v1/skills/base/{block}")
    async def get_base(block: str):
        """返回某基础词块，如 /api/v1/skills/base/anatomy_negative"""
        mapping = {
            "photoreal": photoreal_block,
            "anatomy": anatomy_block,
            "anatomy_negative": anatomy_negative,
            "emotion": emotion_micro_block,
        }
        fn = mapping.get(block)
        if not fn:
            raise HTTPException(404, f"未知基础词块: {block}")
        return {"block": block, "words": fn()}

    class LearnRequest(BaseModel):
        dimension: str
        name: str
        text: str = ""
        entries: List[dict] = []

    @app.post("/api/v1/skills/learn")
    async def learn(req: LearnRequest):
        """人工确认后，把词条写入对应技能词库（可传 text 或 entries）。"""
        from skills.evolve import ingest
        result = ingest(
            dimension=req.dimension,
            name=req.name,
            text=req.text,
            entries=req.entries,
        )
        await _notify_learned(req.dimension, req.name, result)
        return {"success": True, **result}

    class LearnLLMRequest(BaseModel):
        dimension: str
        name: str
        seed_material: str = ""
        model: str = "qwen3:8b"
        save: bool = False

    class CollectRequest(BaseModel):
        url: str
        max_chars: int = 6000

    class LearnSourceRequest(BaseModel):
        dimension: str
        name: str
        source: str
        model: str = "qwen3:8b"
        max_chars: int = 6000
        save: bool = False

    @app.post("/api/v1/skills/learn/from-llm")
    async def learn_from_llm(req: LearnLLMRequest):
        """用本地 LLM 提炼某分类的优质词条。save=False 只返回候选；save=True 直接落盘。"""
        from skills.evolve import suggest_words_llm, ingest
        try:
            candidates = await suggest_words_llm(
                req.dimension, req.name, req.seed_material, req.model
            )
        except Exception as e:
            raise HTTPException(500, f"LLM 提炼失败: {e}")
        if not candidates:
            return {"success": False, "total": 0, "candidates": [], "reason": "LLM 未返回有效词条"}
        if req.save:
            result = ingest(req.dimension, req.name, entries=candidates)
            return {"success": True, **result, "candidates": candidates}
        return {"success": True, "total": len(candidates), "candidates": candidates,
                "note": "save=true 才会落盘"}


    @app.post("/api/v1/skills/learn/collect")
    async def learn_collect(req: CollectRequest):
        """抓取一个 URL，返回提取后的干净正文（供人工预览/确认，不落盘）。"""
        from skills.collect import fetch_url_text
        try:
            text = await asyncio.to_thread(fetch_url_text, req.url, req.max_chars)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"抓取失败: {e}")
        return {"url": req.url, "chars": len(text), "text": text}

    @app.post("/api/v1/skills/learn/from-source")
    async def learn_from_source(req: LearnSourceRequest):
        """自进化闭环：source(链接或粘贴文本) → 采集 → LLM 提炼 → 候选。

        save=False 只返回候选供人工确认；save=True 才 ingest 落盘。
        video 类平台（如抖音）难直接抓正文，可粘贴文稿文本作 source。
        """
        from skills.collect import load_source_text
        from skills.evolve import suggest_words_llm, ingest
        try:
            material = await asyncio.to_thread(load_source_text, req.source, req.max_chars)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"采集失败: {e}")
        try:
            candidates = await suggest_words_llm(
                req.dimension, req.name, material, req.model
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"LLM 提炼失败: {e}")
        if not candidates:
            return {"success": False, "total": 0, "candidates": [],
                    "reason": "采集到了素材，但 LLM 未提炼出有效词条"}
        if req.save:
            result = ingest(req.dimension, req.name, entries=candidates)
            return {"success": True, **result, "candidates": candidates,
                    "material_chars": len(material)}
        return {"success": True, "total": len(candidates), "candidates": candidates,
                "material_chars": len(material), "note": "save=true 才会落盘"}


async def _notify_learned(dimension, name, result):
    """技能库变更后向前端广播（可选）。"""
    try:
        # 直接复用 pipeline 的 SSE 广播逻辑（存在则发，否则忽略）
        from api.routes.pipeline import _sse_broadcast
        await _sse_broadcast("skills_learned", {
            "dimension": dimension, "name": name, "added": result.get("added"),
        })
    except Exception:
        pass
