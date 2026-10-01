"""Agent 4 - 出图（骨架，Phase 2 实现）

传入分镜列表 → ComfyUI+SD 批量出图（ControlNet锁角色）→ 输出图片列表
开发阶段使用 MockImageProvider 占位
"""

import logging
import re
from datetime import datetime

from agents.base import Agent, AgentResult
from providers.base import ImageProvider

# 影视级质感英文关键词（出图时统一注入，提升"一眼真实"感）
from agents.research_agent import RENDER_ENGINE_EN, LIGHTING_EN

# ── Skills 知识库：质量下限（只对"有画面角色"的镜头注入）──
# photoreal_block()：摄影师实拍质感（仅写实风格）；anatomy_block()：人体结构/一致性；
# anatomy_negative()：畸形脸/多余手指/插画感等要禁绝的词。
# shot_topic_name()/category_block()：据分镜自动匹配题材词块（单人/群体人像组；
#   空镜与物件镜不再套题材原型配方，交给分镜自带的 sd_prompt）。
from skills.resolver import (
    photoreal_block, anatomy_block,
    anatomy_negative, emotion_micro_block,
    shot_topic_name, category_block, visible_characters, is_closeup_shot,
)

logger = logging.getLogger(__name__)


def _quality_tail() -> str:
    """每镜 sd_prompt 固定追加的质感词（避免重复冗长，挑代表性组合）。

    - 动漫风格不加 photorealistic —— 它会把 Anything/Ghibli 类底模往写实方向拽。
    - 动漫风格也不用 unreal engine / 光追类词：它们是 3D 写实渲染词，而空镜（无画面
      角色）不会叠加动漫词块，实测空镜因此被渲染成 3D 写实风，与全片平涂赛璐璐不统一。
    """
    from config.style_resolver import style_is_anime, style_keywords
    if style_is_anime():
        tail = ["anime screencap", "cel shading", "flat color",
                "clean lineart"] + ["masterpiece", "best quality", "highly detailed"]
        # 叠加了电影感风格（如「电影级质感」）时去掉 flat color：平涂会把影调与
        # 体积光压平，正是电影质感要的东西（靠关键词里的 cinematic 判定，不写死风格名）
        if "cinematic" in " ".join(style_keywords()).lower():
            tail = [t for t in tail if t != "flat color"]
    else:
        tail = list(RENDER_ENGINE_EN[:2] + LIGHTING_EN[:2]) + [
            "masterpiece", "best quality", "highly detailed", "photorealistic",
        ]
    tail.append("8k")
    return ", " + ", ".join(tail)


# 负向词：基础 anatomy_negative 追加到每个镜头的 sd_negative
# （动漫风格下 filter_conflicting_negative 会把 illustration/anime/cartoon 等剔掉）
_SKILLS_NEGATIVE = anatomy_negative()


def _shot_skills(shot: dict) -> str:
    """为单镜叠加 Skills 词块。

    - 无画面角色（空镜/物件镜，含只挂"旁白/声线"的镜头）→ 不注入任何词块：
      实测无条件注入人像配方会把空镜渲染成人物大特写。
    - 有画面角色 → 题材词块（单人特写/群体合影）+ 人体结构；写实风格再加实拍质感。
    - 人物近景/特写：再叠加微表情词（让角色"有戏"而不像摆拍）
    """
    if not visible_characters(shot):
        return ""
    parts = []
    topic_name = shot_topic_name(shot)
    topic_block = category_block("image/topic", topic_name) if topic_name else ""
    if topic_block:
        parts.append(topic_block)
    from config.style_resolver import style_is_anime
    if not style_is_anime():
        parts.append(photoreal_block())
    parts.append(anatomy_block())
    if is_closeup_shot(shot):
        hint = shot.get("emotion") or shot.get("action") or ""
        emo = emotion_micro_block(hint)
        if emo:
            parts.append(emo)
    return ", ".join([p for p in parts if p])


def _lookup_asset(character_assets: dict, name: str) -> tuple[str, dict]:
    """先精确匹配资产键，再按包含关系兜底（'小砚' ↔ '小砚（少年林砚）'）。

    返回 (命中的资产键, 资产)；未命中返回 ("", {})。
    """
    if name in character_assets:
        return name, (character_assets[name] or {})
    for key, asset in character_assets.items():
        if name and (name in key or key in name):
            return key, (asset or {})
    return "", {}


def _asset_is_visual(name: str, asset: dict) -> bool:
    """资产是否代表一个有视觉形象的角色。

    兼容旧存档：缺少 is_visual 字段时，回退到「名称 + 外形描述」判断，
    否则旁白/声线类角色会被当成参考图来源。
    """
    flag = asset.get("is_visual")
    if flag is not None:
        return bool(flag)
    from agents.character_agent import is_visual_character
    return is_visual_character({"name": name, "appearance": asset.get("appearance", "")})


def _resolve_ref_image(shot: dict, character_assets: dict | None) -> str | None:
    """为单镜挑角色参考图。

    - 当前风格关掉参考图时直接不挂（见 config.yaml 各风格 ipadapter.enabled）：二次元
      底模下 CLIP-vision 参考图会把人物年轻化或崩坏，纯文生图反而更稳；
    - 只在近/特写类景别挂参考图：定妆照是胸像构图，中/全/远挂它会把构图与背景
      一起搬过去（实测中景被拽成灰底胸像，场景与动作全丢）；
    - 按 shot.characters 顺序取第一个「有视觉形象且匹配到资产」的角色：
      旁白/声线类（is_visual=False）不参与，空镜/物件镜因此不会被糊上一张人脸；
    - 角色名做宽松匹配，避免分镜里的 '小砚' 对不上资产键 '小砚（少年林砚）' 而丢参考图。
    """
    if not character_assets or not is_closeup_shot(shot):
        return None
    from config.style_resolver import ipadapter_for_style
    if not ipadapter_for_style().get("enabled"):
        return None
    names = shot.get("characters") or []
    if not isinstance(names, list):
        return None
    for name in names:
        key, asset = _lookup_asset(character_assets, str(name))
        if not asset or not _asset_is_visual(key, asset):
            continue
        ref = asset.get("controlnet_ref_path")
        if ref:
            return ref
    return None


# ── 年龄档词：对抗二次元底模的「年轻少女」先验 ──
# Anything V5 + 新海诚 LoRA 会把人一律渲染成年轻少女：实测 56 岁阿梅、12 岁小砚
# 都被画成少女，而 sd_prompt 里已有的 "56-year-old"/"12-year-old" 英文词不足以
# 对抗该先验。故按角色外貌里的年龄/性别再补加权正向年龄词 + 反向年龄负向词。
_CHILD_MAX_AGE = 14   # ≤ 该岁数算儿童
_ELDER_MIN_AGE = 45   # ≥ 该岁数算中老年


def _age_words(appearance: str, gender: str = "") -> tuple[list[str], list[str]]:
    """从角色外貌/性别字段抽年龄段与性别，返回 (正向年龄词, 负向反向词)。

    年龄只在外貌描述开头 10 字内找（appearance 惯例以「28岁，」「50出头，」起头），
    避免把后文的数量词误当年龄；拿不到就返回空（不猜年龄）。
    性别优先取 asset.gender 字段——外貌描述里常常不写「男/女」。
    """
    m = re.search(r"(\d{1,3})\s*(?:岁|出头|多)", (appearance or "")[:10])
    if not m:
        return [], []
    age = int(m.group(1))
    male = "男" in (gender or "") or "男" in (appearance or "")
    if age <= _CHILD_MAX_AGE:
        pos = ["(child:1.3)", "young kid", "(small body:1.2)"]
        neg = ["woman", "girl", "female", "adult", "mature face"]
        if male:
            neg.append("long hair")
        return pos, neg
    if age >= _ELDER_MIN_AGE:
        # 显式写出 elderly/old + woman/man 与皱纹松弛：只在提示词里写 "56-year-old"
        # 敌不过 Anything V5 的少女先验，实测仍被画成年轻女子。
        pos = ["(elderly man:1.4)", "(old man:1.35)"] if male else \
              ["(elderly woman:1.4)", "(old woman:1.35)", "sagging skin"]
        pos += ["(aged face:1.3)", "deep wrinkles", "gray hair", "weathered skin"]
        neg = ["young girl", "child", "loli", "schoolgirl",
               "smooth skin", "flawless skin"]
        neg += ["woman", "girl", "female"] if male else ["man", "boy"]
        return pos, neg
    pos = ["(man:1.25)", "(masculine face:1.2)"] if male else ["(woman:1.25)"]
    pos.append("(mature face:1.15)")
    neg = ["child", "young girl", "loli", "schoolgirl"]
    neg += ["woman", "girl", "female"] if male else ["man", "boy"]
    return pos, neg


def _age_cues(names: list, character_assets: dict | None) -> tuple[str, str]:
    """汇总一镜内所有画面角色的年龄档词，返回 (正向串, 负向串)。

    负向只在「本镜唯一画面角色」时给出：多角色同镜时各自的年龄反向词会互相打架
    （如 12 岁男孩 + 56 岁母亲，禁 child 与禁 elderly 同时成立就自相矛盾）。
    """
    if not character_assets:
        return "", ""
    pos, negs = [], []
    for name in names if isinstance(names, list) else []:
        key, asset = _lookup_asset(character_assets, str(name))
        if not asset or not _asset_is_visual(key, asset):
            continue
        p, n = _age_words(asset.get("appearance", ""), asset.get("gender", ""))
        pos += p
        negs.append(n)
    neg = negs[0] if len(negs) == 1 else []
    return ", ".join(dict.fromkeys(pos)), ", ".join(dict.fromkeys(neg))


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
                # 角色参考图：只认有视觉形象的角色，且角色名宽松匹配
                ref_path = _resolve_ref_image(shot, character_assets)
                # 原始 sd_prompt + 风格合并关键词（多风格自由组合）+ LoRA 触发词 + Skills 质量块 + 影视级质感词
                from config.style_resolver import (
                    style_keywords, lora_triggers_for_style, filter_conflicting_negative,
                )
                _style_kw = style_keywords()
                _lora_trig = lora_triggers_for_style()
                _skills = _shot_skills(shot)
                _age_pos, _age_neg = _age_cues(shot.get("characters") or [], character_assets)
                _neg = (shot.get("sd_negative", "").strip() + ", " + _SKILLS_NEGATIVE).strip() \
                    if shot.get("sd_negative", "").strip() else _SKILLS_NEGATIVE
                if _age_neg:
                    _neg = f"{_neg}, {_age_neg}"
                prompt = shot.get("sd_prompt", "")
                if _style_kw:
                    prompt = f"{prompt}, {', '.join(_style_kw)}" if prompt else ", ".join(_style_kw)
                if _lora_trig:
                    prompt = f"{prompt}, {', '.join(_lora_trig)}" if prompt else ", ".join(_lora_trig)
                if _skills:
                    prompt = f"{prompt}, {_skills}" if prompt else _skills
                if _age_pos:
                    prompt = f"{prompt}, {_age_pos}" if prompt else _age_pos
                shot_data.append({
                    "shot_id": str(shot["shot_id"]),
                    "sd_prompt": (prompt + _quality_tail()).strip(),
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
