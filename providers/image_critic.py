"""出图质量自评（Image Critic）—— 打 tag → 按 tag 算分 → 判定是否合格

用途：character_agent / image_agent 每出一张图，先交给「视觉识别模型」打 tag，
再按固定权重表把 tag 折算成 0-100 分；低于阈值即判定不合格，交由 Agent 自动
重生成（最多 N 次），把「一眼差图」挡在成片之前。

为什么拆成「模型只打 tag + 本地按权重算分」而不是让模型直接给分？
  - 大模型直接打分方差大、口径漂移、不可解释；
  - 拆开后评分口径完全确定、可复现、可调参（改权重表即可），也便于写进日志排查。

配置（config.yaml → image_qc）：
  enabled: 是否开启（关掉则直接放行）
  model:   用哪个视觉模型打 tag（默认 deepseek-v4-flash）
  threshold: 合格分数线（默认 75）
  max_retries: 不合格时最多重生成几次（默认 2）
"""

import base64
import json
import logging
import random
import re
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

# 正向质量 tag 权重（命中累加）
_POSITIVE_WEIGHTS: dict[str, int] = {
    "画面清晰": 12,
    "主体突出": 10,
    "构图合理": 8,
    "人体结构正确": 12,
    "面部自然": 10,
    "光影自然": 6,
    "细节丰富": 8,
    "色彩和谐": 6,
    "符合提示词": 10,
    "风格统一": 8,
}

# 缺陷 tag 权重（命中扣减）
_DEFECT_WEIGHTS: dict[str, int] = {
    "严重模糊": 40,
    "轻微模糊": 15,
    "面部扭曲": 35,
    "多余手指": 30,
    "肢体错误": 30,
    "解剖错误": 30,
    "过曝": 15,
    "欠曝": 15,
    "噪点": 10,
    "畸变": 15,
    "水印": 20,
    "文字乱码": 15,
    "构图杂乱": 12,
    "低质量": 20,
}


def compute_score(tags: list[str], defects: list[str]) -> int:
    """按 tag 权重表算分（基准 50 分，正向加、缺陷扣，裁剪到 0-100）。"""
    score = 50
    for t in tags:
        score += _POSITIVE_WEIGHTS.get(str(t).strip(), 0)
    for d in defects:
        score -= _DEFECT_WEIGHTS.get(str(d).strip(), 0)
    return max(0, min(100, score))


def next_seed(seed) -> int:
    """重生成时换一个 seed，确保不会拿到同一张图。"""
    try:
        s = int(seed)
    except (TypeError, ValueError):
        s = -1
    if s < 0:
        return random.randint(1, 2**31 - 1)
    return (s + 1) % (2**31 - 1)


def _build_prompt(expect: str) -> str:
    pos = "、".join(_POSITIVE_WEIGHTS)
    neg = "、".join(_DEFECT_WEIGHTS)
    return (
        "你是严格的动漫/影视出图质检员。请观察这张生成图，按下面词表打标签，只输出 JSON。\n\n"
        f"【正向标签词表（画面越满足越该选）】\n{pos}\n\n"
        f"【缺陷标签词表（画面越有问题越该选）】\n{neg}\n\n"
        "要求：\n"
        "1. tags 只能从正向词表里选（可多选；没有合适的就给空数组）\n"
        "2. defects 只能从缺陷词表里选（可多选；没有明显缺陷就给空数组）\n"
        "3. subject_ok：画面主体（最该出现的人/物）是否清晰可辨（true/false）\n"
        "4. brief：一句话描述画面内容\n\n"
        f"【本图期望内容】{expect}\n\n"
        '只输出 JSON：{"tags": [], "defects": [], "subject_ok": true, "brief": ""}'
    )


def _parse_json(raw: str) -> dict:
    """从模型输出里抠出第一个 JSON 对象（容忍 ```json 围栏/前后废话）。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"无法解析质检 JSON: {text[:120]}")


class ImageCritic:
    """视觉识别模型打分器：图 → tag → 分数 → 是否合格。"""

    def __init__(self, model: str | None = None, threshold: int | None = None,
                 max_retries: int | None = None, enabled: bool | None = None):
        cfg = settings.image_qc
        self.enabled = bool(cfg.get("enabled", True)) if enabled is None else bool(enabled)
        self.model = model or cfg.get("model") or "deepseek-v4-flash"
        self.threshold = int(cfg.get("threshold", 75) if threshold is None else threshold)
        self.max_retries = int(cfg.get("max_retries", 2) if max_retries is None else max_retries)
        self._provider = None

    def _get_provider(self):
        if self._provider is None:
            from providers.llm import DeepSeekProvider
            self._provider = DeepSeekProvider(model=self.model)
        return self._provider

    async def evaluate(self, image_path: str, expect: str = "") -> dict:
        """给一张图打 tag 并算分。

        返回 dict：{passed, score, tags, defects, subject_ok, brief, skipped?, reason?}
        任何异常（图不存在 / 无 Key / 模型报错 / JSON 解析失败）都返回 skipped=True,
        passed=True —— 质检是「加分项」，绝不能因为它挂掉而中断整条管线。
        """
        result: dict = {
            "passed": True, "score": None, "tags": [], "defects": [],
            "subject_ok": None, "brief": "", "skipped": False, "reason": "",
        }
        if not self.enabled:
            result["skipped"] = True
            result["reason"] = "质检已关闭"
            return result

        path = Path(image_path) if image_path else None
        if path is None or not path.exists():
            result["skipped"] = True
            result["reason"] = f"图片不存在: {image_path}"
            return result

        try:
            b64 = base64.b64encode(path.read_bytes()).decode()
            suffix = path.suffix.lower().lstrip(".") or "png"
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "png")
            prompt = _build_prompt((expect or "")[:300])
            provider = self._get_provider()
            raw = await provider.chat(
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                    ],
                }],
                json_mode=True,
                max_tokens=3000,
                timeout=180,
            )
            data = _parse_json(raw)
        except Exception as e:
            logger.warning(f"[ImageCritic] 质检失败（放行）: {e}")
            result["skipped"] = True
            result["reason"] = f"{type(e).__name__}: {str(e)[:160]}"
            return result

        tags = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()]
        defects = [str(d).strip() for d in (data.get("defects") or []) if str(d).strip()]
        score = compute_score(tags, defects)
        subject_ok = bool(data.get("subject_ok", True))
        result.update({
            "score": score, "tags": tags, "defects": defects,
            "subject_ok": subject_ok, "brief": str(data.get("brief", ""))[:200],
        })
        result["passed"] = subject_ok and score >= self.threshold
        result["reason"] = f"score={score} (阈值{self.threshold})"
        return result
