"""Agent 4 - 出图（骨架，Phase 2 实现）

传入分镜列表 → ComfyUI+SD 批量出图（ControlNet锁角色）→ 输出图片列表
开发阶段使用 MockImageProvider 占位
"""

import logging
from datetime import datetime

from agents.base import Agent, AgentResult
from providers.base import ImageProvider

# 影视级质感英文关键词（出图时统一注入，提升"一眼真实"感）
from agents.research_agent import RENDER_ENGINE_EN, LIGHTING_EN

# ── Skills 知识库：质量下限（无条件注入，专治"人不像人鬼不像鬼"）──
# photoreal_block()：摄影师实拍质感；anatomy_block()：人体结构/一致性；
# anatomy_negative()：畸形脸/多余手指/插画感等要禁绝的词。
# shot_topic_name()/category_block()：据分镜自动匹配题材词块（有角色→人物组；
#   无人物→ scene/action/background 中文匹配，未命中再基于 sd_prompt 兜底匹配英文题材）。
from skills.resolver import (
    photoreal_block, anatomy_block,
    anatomy_negative, emotion_micro_block,
    shot_topic_name, category_block,
)

logger = logging.getLogger(__name__)

# 每镜 sd_prompt 固定追加的质感词（避免重复冗长，挑代表性组合）
_QUALITY_TAIL = ", " + ", ".join(RENDER_ENGINE_EN[:2] + LIGHTING_EN[:2]) + ", masterpiece, best quality, highly detailed, photorealistic, 8k"

# 负向词：基础 anatomy_negative 无条件追加到每个镜头的 sd_negative
_SKILLS_NEGATIVE = anatomy_negative()


def _shot_skills(shot: dict) -> str:
    """为单镜叠加 Skills 词块。

    - 题材词块（分层匹配，专注"无人物镜头"的清与准）：
        1) 有角色 → 人物组（单人电影特写 / 群体合影）；
        2) 无人物 → 先用 scene/action/background 中文匹配（自然/建筑/商品等）；
        3) 仍未命中 → 兜底匹配英文 sd_prompt（shot_topic_name 已内置该策略）。
    - 无条件：摄影师实拍质感 + 人体结构
    - 人物近景/特写：再叠加微表情词（让角色"有戏"而不像摆拍）
    """
    parts = []
    topic_name = shot_topic_name(shot)
    topic_block = category_block("image/topic", topic_name) if topic_name else ""
    if topic_block:
        parts.append(topic_block)
    parts.extend([photoreal_block(), anatomy_block()])
    shot_type = shot.get("shot_type", "")
    chars = shot.get("characters") or []
    if chars and shot_type in ("近", "特写", "近景", "大特写"):
        hint = shot.get("emotion") or shot.get("action") or ""
        emo = emotion_micro_block(hint)
        if emo:
            parts.append(emo)
    return ", ".join([p for p in parts if p])


class ImageGenAgent(Agent):
    """Agent 4：批量出图（ComfyUI + SD）"""

    name = "image_agent"

    def __init__(self, use_comfyui: bool = False, comfy_client=None,
                 image_provider: ImageProvider | None = None):
        super().__init__(name="image_agent")
        if image_provider is not None:
            self.image_provider = image_provider
        elif use_comfyui:
            from providers.comfyui_provider import ComfySDImageProvider
            self.image_provider = ComfySDImageProvider(client=comfy_client)
        else:
            from providers.mock_provider import MockImageProvider
            self.image_provider = MockImageProvider()

    async def run(self, storyboard: dict,
                  character_assets: dict | None = None) -> AgentResult:
        logger.info("[ImageGenAgent] 开始批量出图")

        episodes = storyboard.get("episodes", [])
        all_results = {}

        for ep in episodes:
            ep_num = ep.get("episode_number", 1)
            shots = ep.get("shots", [])

            shot_data = []
            for shot in shots:
                # 取第一个角色名作为 ref_image 查询 key
                char_list = shot.get("characters") or []
                first_char = char_list[0] if isinstance(char_list, list) and char_list else None
                ref_path = None
                if character_assets and first_char:
                    asset = character_assets.get(first_char, {})
                    ref_path = asset.get("controlnet_ref_path")
                # 原始 sd_prompt + 风格合并关键词（多风格自由组合）+ LoRA 触发词 + Skills 质量块 + 影视级质感词
                from config.style_resolver import (
                    style_keywords, lora_triggers_for_style, filter_conflicting_negative,
                )
                _style_kw = style_keywords()
                _lora_trig = lora_triggers_for_style()
                _skills = _shot_skills(shot)
                _neg = (shot.get("sd_negative", "").strip() + ", " + _SKILLS_NEGATIVE).strip() \
                    if shot.get("sd_negative", "").strip() else _SKILLS_NEGATIVE
                prompt = shot.get("sd_prompt", "")
                if _style_kw:
                    prompt = f"{prompt}, {', '.join(_style_kw)}" if prompt else ", ".join(_style_kw)
                if _lora_trig:
                    prompt = f"{prompt}, {', '.join(_lora_trig)}" if prompt else ", ".join(_lora_trig)
                if _skills:
                    prompt = f"{prompt}, {_skills}" if prompt else _skills
                shot_data.append({
                    "shot_id": str(shot["shot_id"]),
                    "sd_prompt": (prompt + _QUALITY_TAIL).strip(),
                    "sd_negative": filter_conflicting_negative(_neg),
                    "seed": shot.get("seed", -1),
                    "ref_image": ref_path,
                    "controlnet_type": "control_v11p_sd15_canny.pth",
                    "controlnet_image": ref_path,
                    "controlnet_strength": 0.8,
                    "width": int(shot.get("width", 768)),
                    "height": int(shot.get("height", 768)),
                })

            ep_images = await self._generate_with_qc(shot_data)
            all_results[f"ep_{ep_num}"] = ep_images

        total = sum(len(v) for v in all_results.values())
        scores = [v.get("qc", {}).get("score") for ep in all_results.values()
                  for v in ep.values() if v.get("qc", {}).get("score") is not None]
        result = AgentResult(
            success=True,
            data={"images": all_results},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "total_images": total,
                "qc_checked": len(scores),
                "qc_avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            },
        )
        logger.info(f"[ImageGenAgent] 完成: {total}张图"
                    + (f"，质检均分 {result.metadata['qc_avg_score']}" if scores else ""))
        return result

    async def _generate_with_qc(self, shot_data: list[dict]) -> dict:
        """批量出图 + 质检自评；不合格的镜头换 seed 自动重生成（最多 max_retries 次）。

        流程：出图 → 落地本地 → 视觉模型打 tag → 按 tag 算分 → 不达标则重出。
        质检不可用（无图 / 无 Key / 模型报错）时一律放行，绝不因质检中断整条管线。
        """
        from providers.comfyui_provider import sync_image_to_local
        from providers.image_critic import ImageCritic, next_seed

        critic = ImageCritic()
        if not critic.enabled:
            image_list = await self.image_provider.batch_generate(shot_data)
            return self._index_images(image_list, shot_data)

        pending = list(shot_data)
        best: dict[str, dict] = {}
        attempt = 0
        while pending and attempt <= critic.max_retries:
            image_list = await self.image_provider.batch_generate(pending)
            got = self._index_images(image_list, pending)
            next_pending = []
            for shot in pending:
                sid = str(shot.get("shot_id"))
                img = got.get(sid)
                if not img:
                    # 本次没出图：未到重试上限就再试一次
                    if attempt < critic.max_retries:
                        next_pending.append({**shot, "seed": next_seed(shot.get("seed"))})
                    continue
                local = sync_image_to_local(img)
                qc = await critic.evaluate(local, expect=shot.get("sd_prompt", ""))
                logger.info(f"[ImageGenAgent] 质检 shot={sid}: {qc.get('reason')} "
                            f"tags={qc.get('tags')} defects={qc.get('defects')}")
                if qc["passed"]:
                    best[sid] = {**img, "qc": qc, "local_path": local}
                elif attempt < critic.max_retries:
                    logger.warning(f"[ImageGenAgent] shot={sid} 质检不达标（{qc.get('reason')}），重生成")
                    next_pending.append({**shot, "seed": next_seed(shot.get("seed"))})
                else:
                    logger.warning(f"[ImageGenAgent] shot={sid} 质检仍不达标，保留当前结果")
                    best[sid] = {**img, "qc": qc, "local_path": local}
            pending = next_pending
            attempt += 1
        return best

    @staticmethod
    def _index_images(image_list: list[dict], shots: list[dict]) -> dict:
        """把 batch_generate 的扁平结果按 shot_id 归位（缺失 shot_id 时按顺序兜底）。"""
        indexed: dict[str, dict] = {}
        for i, img in enumerate(image_list):
            sid = img.get("shot_id") or (shots[i].get("shot_id") if i < len(shots) else str(i))
            indexed[str(sid)] = img
        return indexed
