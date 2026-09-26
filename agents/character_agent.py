"""Agent 3 - 角色定妆照 + 双轨制资产

- 检查角色是否已入库（is_asset_library）
- 已入库 → 跳过，复用ControlNet参考图
- 未入库 → ComfyUI/Mock生成定妆照 → 入库
"""

import json, logging, os, re
from datetime import datetime
from typing import Optional, Union

from agents.base import Agent, AgentResult
from providers.base import ImageProvider

logger = logging.getLogger(__name__)

# 只出声不露脸的角色（旁白、纯人声/AI声线等）：不该生成定妆照，
# 更不该把定妆照当参考图喂给下游出图（否则空镜/物件镜会被强行糊上一张人脸）。
_NON_VISUAL_NAME_HINTS = ("旁白", "声线", "配音", "narrator", "voiceover")
_NON_VISUAL_APPEARANCE_HINTS = (
    "不出现在画面", "不需要出现", "仅作为人声", "仅作为字幕", "画外音", "只出声",
)

# 中文外貌设定对 SD1.5 的 CLIP 几乎等于噪声：实测「56岁…鬓边碎发」被渲染成年轻少女、
# 「12岁，圆脸，短发」被渲染成银发成年雌雄莫辨角色 —— 定妆照写错会顺着参考图污染全部镜头。
# 因此定妆照 prompt 先转成英文（LLM 优先，失败用确定性兜底）。
_PORTRAIT_SYS = (
    "你是 SD1.5 出图提示词工程师。把中文角色设定改写为一段英文人像 prompt。"
    "必须写清：年龄（数字+年龄词）、性别、脸型、发型发色、肤色、服装、该年龄的外貌特征"
    "（如 wrinkles / gray hair / round face / child-like proportions）。"
    "只输出一行英文 prompt，逗号分隔，禁止任何解释、禁止中文。"
)
_AGE_EN = {"少年": "young teenager", "青年": "young adult", "中年": "middle-aged", "老年": "elderly"}
_GENDER_EN = {"男": "man", "女": "woman"}

# 动漫底模（Anything V5 + 新海诚 LoRA）有强烈的"年轻可爱少女"先验：实测正向词
# 「56-year-old elderly woman, salt-and-pepper hair, deep crow's feet wrinkles, dark red
# cotton padded jacket」仍被画成双丸子头紫瞳少女。必须补年龄档强调词 + 反向年龄词才压得住。
_AGE_YOUTH_NEG = ("young, youth, loli, child, teenage, baby face, smooth flawless skin, "
                  "youthful glow, cute girl")
_AGE_MATURE_NEG = "old, aged, elderly, wrinkles, gray hair, mature adult, tall, grown-up"
# 设定里年龄写法不统一：「56岁」「50出头」「12岁」
_AGE_NUM_RE = re.compile(r"(\d{1,2})\s*(?:岁|出头)")


def _age_cues(char: dict) -> tuple[list[str], list[str]]:
    """按年龄档给出（正向强调词, 负向排除词），用于对抗底模的年龄先验。"""
    appearance = str(char.get("appearance") or "")
    age_label = str(char.get("age") or "")
    m = _AGE_NUM_RE.search(appearance)
    num = int(m.group(1)) if m else None
    # 优先信标准的年龄档（老年/少年）；数字只作补充。50「出头」仍属中年，
    # 给中年角色套「elderly / gray hair」会把模型劈成一老一中两个人（实测王婶）。
    is_old = age_label == "老年" or (num is not None and num >= 55)
    is_child = age_label == "少年" or (num is not None and num <= 14)
    if is_old:
        return (["mature elderly person, aged face, visible wrinkles, weathered skin, gray hair"],
                _AGE_YOUTH_NEG)
    if is_child:
        return (["child, kid, small stature, child-like proportions, youthful features"],
                _AGE_MATURE_NEG)
    return [], ""


def _fallback_portrait_prompt(name: str, gender: str, char: dict) -> str:
    """LLM 不可用时的兜底：抽取年龄数字 + 年龄档 + 性别，至少把年龄/性别说成英文。

    不保留中文 appearance —— 实测把中文外貌原样拼进去（SD1.5 视为噪声）会把
    「12岁男孩」画成穿红和服的少女，噪声比留白更糟。
    """
    appearance = str(char.get("appearance") or "")
    bits = []
    m = _AGE_NUM_RE.search(appearance)
    if m:
        bits.append(f"{m.group(1)} years old")
    age_word = _AGE_EN.get(str(char.get("age") or ""))
    if age_word:
        bits.append(age_word)
    bits.append(_GENDER_EN.get(gender, "person"))
    return f"portrait of a {' '.join(bits)}"


def is_visual_character(char: dict) -> bool:
    """判断角色是否需要视觉形象（定妆照）。"""
    name = str(char.get("name") or "")
    appearance = str(char.get("appearance") or "")
    if any(h in name for h in _NON_VISUAL_NAME_HINTS):
        return False
    return not any(h in appearance for h in _NON_VISUAL_APPEARANCE_HINTS)


class CharacterDesignAgent(Agent):
    """Agent 3：角色定妆照 + 资产入库"""

    name = "character_agent"

    def __init__(self, use_comfyui: bool = False,
                 comfy_client=None,
                 image_provider: Union[str, ImageProvider, None] = None,
                 llm_provider=None):
        super().__init__(name="character_agent")
        self._comfy_client = comfy_client
        if llm_provider is None:
            self.llm = None
        else:
            from providers.llm import LLMProvider, get_llm_provider
            if isinstance(llm_provider, LLMProvider):
                self.llm = llm_provider
            else:
                self.llm = get_llm_provider(llm_provider)
        if isinstance(image_provider, ImageProvider):
            self.image_provider = image_provider
        elif use_comfyui and comfy_client:
            from providers.comfyui_provider import ComfySDImageProvider
            self.image_provider = ComfySDImageProvider(client=comfy_client)
        else:
            from providers.mock_provider import MockImageProvider
            self.image_provider = MockImageProvider()

    async def run(self, script: dict, db_assets: dict | None = None) -> AgentResult:
        logger.info("[CharacterAgent] 开始处理角色定妆照")

        characters = script.get("characters", [])
        if not characters:
            return AgentResult(success=False, error="剧本中没有角色数据")

        db_assets = db_assets or {}
        results = []
        scores: list[int] = []

        for char in characters:
            name = char.get("name", "")
            existing = db_assets.get(name)

            if not is_visual_character(char):
                # 旁白/声线类角色：无视觉形象，不生成定妆照也不给参考图
                logger.info(f"[CharacterAgent] 角色'{name}'不出现在画面中，跳过定妆照")
                results.append({"name": name, "status": "skipped", "asset": {
                    "name": name, "is_visual": False,
                    "portrait_path": None, "controlnet_ref_path": None,
                }})
                continue

            if existing and existing.get("is_asset_library"):
                logger.info(f"[双轨制] 角色'{name}'已入库，跳过生成")
                results.append({"name": name, "status": "skipped", "asset": existing})
                continue

            # 生成定妆照 - 角色正面半身
            appearance = char.get("appearance", "")
            gender = char.get("gender", "男")
            body = await self._portrait_prompt_body(name, gender, char)
            # 风格感知：动漫风格下注入实拍质感词（photorealistic / 影棚光）会把
            # 动漫底模拽向写实，实测出灰调成人像；负向词同步走风格过滤。
            from config.style_resolver import (
                style_keywords, lora_triggers_for_style, style_is_anime,
                filter_conflicting_negative,
            )
            from skills.resolver import photoreal_block, anatomy_block, anatomy_negative
            extra = []
            if not style_is_anime():
                extra.append(photoreal_block())
            extra.append(anatomy_block())
            parts = [body]
            age_pos, age_neg = _age_cues(char)
            parts += age_pos
            parts += ["solo, single character, one person only, centered",
                      "front view, upper body, looking at camera, detailed face"]
            parts += extra
            parts += ["masterpiece, best quality, highly detailed", "8k"]
            if not style_is_anime():
                parts.append("photorealistic")
            parts += list(style_keywords()) + list(lora_triggers_for_style())
            prompt = ", ".join(p for p in parts if p)
            # 定妆照要作 IP-Adapter 参考图，出双人会把"两个人"写进下游单人镜头（实测小砚定妆照画了一大一小两人）
            neg_parts = [anatomy_negative(),
                         "multiple people, two people, group, crowd, extra person"]
            if age_neg:
                neg_parts.append(age_neg)
            negative = filter_conflicting_negative(", ".join(neg_parts))

            first_img, qc = await self._portrait_with_qc(
                name, prompt, seed=hash(name) % (2**31), negative=negative
            )
            image_path = first_img.get("filename", f"storage/output/char_{name}.png")
            if qc.get("score") is not None:
                scores.append(qc["score"])
                logger.info(f"[CharacterAgent] 定妆照质检 {name}: {qc.get('reason')} "
                            f"tags={qc.get('tags')} defects={qc.get('defects')}")

            asset = {
                "name": name,
                "is_visual": True,
                "gender": gender,
                "appearance": appearance,
                "personality": char.get("personality", ""),
                "role": char.get("role", ""),
                "portrait_path": image_path,
                "controlnet_ref_path": image_path,
                "is_asset_library": True,
                "generated_at": datetime.utcnow().isoformat(),
            }
            results.append({"name": name, "status": "generated", "asset": asset})
            logger.info(f"[CharacterAgent] 生成定妆照: {name} -> {image_path}")

        return AgentResult(
            success=True,
            data={"characters": results},
            metadata={
                "agent": self.name, "timestamp": datetime.utcnow().isoformat(),
                "total": len(results),
                "generated": sum(1 for r in results if r["status"] == "generated"),
                "skipped": sum(1 for r in results if r["status"] == "skipped"),
                "qc_checked": len(scores),
                "qc_avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            },
        )

    async def _portrait_prompt_body(self, name: str, gender: str, char: dict) -> str:
        """把中文角色设定转成英文人像描述（LLM 优先，失败/中文残留走确定性兜底）。"""
        fallback = _fallback_portrait_prompt(name, gender, char)
        if not self.llm:
            return fallback
        desc = (f"角色名：{name}\n性别：{gender}\n年龄档：{char.get('age', '')}\n"
                f"外貌设定：{char.get('appearance', '')}")
        for attempt in range(2):
            try:
                text = await self.llm.generate(desc, system_prompt=_PORTRAIT_SYS)
                text = " ".join(str(text or "").split()).strip().strip('"')
                if text and not re.search(r"[\u4e00-\u9fff]", text):
                    return text
                logger.warning(f"[CharacterAgent] {name} 定妆照英文 prompt 不合格（第{attempt + 1}次），"
                               f"残余中文：{text[:80]}")
            except Exception as e:
                logger.warning(f"[CharacterAgent] {name} 定妆照英文 prompt 生成失败: {e}")
        logger.warning(f"[CharacterAgent] {name} 定妆照英文 prompt 重试用尽，用兜底")
        return fallback

    async def _portrait_with_qc(self, name: str, prompt: str, seed: int,
                                negative: str = ""):
        """生成定妆照 + 质检自评；不合格换 seed 自动重生成（最多 max_retries 次）。

        返回 (first_img, qc)；质检不可用时 qc 为 skipped（放行），绝不中断管线。
        """
        from providers.comfyui_provider import sync_image_to_local
        from providers.image_critic import ImageCritic, next_seed

        critic = ImageCritic()
        best_img: dict = {}
        best_qc: dict = {}
        cur_seed = seed
        for attempt in range(critic.max_retries + 1 if critic.enabled else 1):
            image_results = await self.image_provider.generate(
                prompt=prompt, seed=cur_seed, negative_prompt=negative
            )
            first = image_results[0] if isinstance(image_results, list) and image_results else {}
            if not first:
                continue
            best_img = first
            if not critic.enabled:
                break
            local = sync_image_to_local(first)
            qc = await critic.evaluate(local, expect=f"角色定妆照：{name}")
            best_qc = qc
            if qc["passed"]:
                break
            if attempt < critic.max_retries:
                logger.warning(f"[CharacterAgent] 定妆照 {name} 质检不达标（{qc.get('reason')}），重生成")
                cur_seed = next_seed(cur_seed)
        return best_img, best_qc