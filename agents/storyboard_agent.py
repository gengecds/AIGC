"""Agent 2 - 分镜生成 + 校验层

剧本JSON → DeepSeek 拆分镜 → 校验层（shot_type/duration/prompt）→ 分镜JSON
"""

import json
import logging
from datetime import datetime

from agents.base import Agent, AgentResult
from providers.llm import DeepSeekProvider, OllamaProvider

logger = logging.getLogger(__name__)

VALID_SHOT_TYPES = {"远", "中", "近", "特写", "俯拍", "仰拍", "航拍", "跟随"}

STORYBOARD_SYSTEM_PROMPT = """你是一个专业的AI漫剧分镜师。请根据剧本生成详细的分镜表。

输出格式为JSON数组（每个元素是一个分镜）：
[
  {
    "shot_id": 1,
    "scene": "场景描述",
    "shot_type": "远/中/近/特写/俯拍/仰拍",
    "duration": 5,
    "camera_movement": "镜头运动（固定/推/拉/摇/移/跟）",
    "characters": ["角色1", "角色2"],
    "action": "角色动作描述",
    "background": "背景/环境描述",
    "lighting": "光影/色调（明亮/昏暗/逆光/暖色/冷色）",
    "dialogue": "本分镜中的对话内容",
    "sd_prompt": "完整的SD出图prompt（英文，包含主体、动作、场景、光影、画质关键词）",
    "sd_negative": "负面prompt（手上多余手指、畸形脸等）"
  }
]

要求：
1. 每个分镜的 shot_type 必须在以下范围：远、中、近、特写、俯拍、仰拍
2. duration 必须在 3-12 秒之间
3. sd_prompt 必须包含：主体描述 + 动作 + 场景 + 光影 + 画质关键词（masterpiece, best quality）
4. 一集建议 15-25 个分镜
5. 镜头切换要有节奏感，远景和中近景交替
6. 特写镜头用于关键情绪或对话转折点
7. 必须返回合法的JSON数组
"""


# ── 校验层 ────────────────────────────

class ShotValidator:
    """分镜校验层"""

    MAX_RETRIES = 3

    def validate(self, shots: list[dict]) -> list[dict]:
        """校验所有分镜，返回 errors 列表"""
        errors = []
        for i, shot in enumerate(shots):
            shot_id = shot.get("shot_id", i + 1)
            # shot_type
            st = shot.get("shot_type", "")
            if st not in VALID_SHOT_TYPES:
                errors.append({
                    "shot_id": shot_id,
                    "field": "shot_type",
                    "message": f"无效镜头类型: {st}，允许: {sorted(VALID_SHOT_TYPES)}",
                })
            # duration
            dur = shot.get("duration", 0)
            if not isinstance(dur, (int, float)) or dur < 3 or dur > 12:
                errors.append({
                    "shot_id": shot_id,
                    "field": "duration",
                    "message": f"无效时长: {dur}s，需在3-12秒范围内",
                })
            # sd_prompt
            prompt = shot.get("sd_prompt", "")
            if not prompt or len(prompt) < 20:
                errors.append({
                    "shot_id": shot_id,
                    "field": "sd_prompt",
                    "message": "sd_prompt 为空或过短 (<20字符)",
                })
            required_words = ["masterpiece", "best quality"]
            if not all(w in prompt.lower() for w in required_words):
                errors.append({
                    "shot_id": shot_id,
                    "field": "sd_prompt",
                    "message": f"sd_prompt 缺少画质关键词: {required_words}",
                    "severity": "warning",
                })
        return errors

    def fix_prompt(self, shot: dict) -> dict:
        """自动补全缺少的prompt元素（如有需要）"""
        prompt = shot.get("sd_prompt", "")
        needed = {
            "主体": shot.get("characters", []) or shot.get("action", ""),
            "场景": shot.get("background", ""),
            "光影": shot.get("lighting", ""),
        }
        for key, val in needed.items():
            if val and val not in prompt:
                prompt += f", {val}"
        shot["sd_prompt"] = prompt
        return shot


# ── Agent 2 ────────────────────────────

class StoryboardAgent(Agent):
    """Agent 2：分镜生成"""

    name = "storyboard_agent"

    def __init__(self, llm_provider: str = "deepseek"):
        super().__init__(name="storyboard_agent")
        if llm_provider == "deepseek":
            self.llm = DeepSeekProvider()
        else:
            self.llm = OllamaProvider()
        self.validator = ShotValidator()

    async def run(self, script: dict) -> AgentResult:
        logger.info("[StoryboardAgent] 开始拆分镜")

        if not script or "episodes" not in script:
            return AgentResult(
                success=False,
                error="剧本数据不完整，缺少 episodes 字段",
            )

        all_episodes = []
        retry_count = 0

        for ep in script.get("episodes", []):
            ep_input = json.dumps(ep, ensure_ascii=False, indent=2)
            characters_info = json.dumps(
                script.get("characters", []),
                ensure_ascii=False,
                indent=2,
            )

            prompt = (
                f"剧本：{ep_input}\n\n"
                f"角色列表：{characters_info}\n"
                f"请为本集生成分镜，要求合法的JSON数组。"
            )

            shots = None
            while retry_count < self.validator.MAX_RETRIES:
                try:
                    raw = await self.llm.generate(
                        system=STORYBOARD_SYSTEM_PROMPT,
                        user=prompt,
                        model="deepseek-chat",
                    )

                    raw = raw.strip()
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[1]
                        raw = raw.rsplit("```", 1)[0]

                    shots = json.loads(raw)

                    # 校验
                    errors = self.validator.validate(shots)
                    fatal = [e for e in errors if e.get("severity") != "warning"]
                    if fatal:
                        logger.warning(
                            f"[校验] 分镜校验失败: {len(fatal)}处错误"
                        )
                        prompt += (
                            f"\n\n⚠️ 上次输出校验失败，错误：{json.dumps(fatal, ensure_ascii=False)}"
                            f"\n请修正后重新生成。"
                        )
                        retry_count += 1
                        continue

                    # 自动补全
                    for shot in shots:
                        self.validator.fix_prompt(shot)

                    break

                except json.JSONDecodeError as e:
                    logger.warning(f"[Storyboard] JSON解析失败: {e}")
                    retry_count += 1

            if shots is None:
                return AgentResult(
                    success=False,
                    error=f"分镜生成失败（重试{self.validator.MAX_RETRIES}次后仍失败）",
                )

            all_episodes.append({
                "episode_number": ep.get("episode_number", 1),
                "title": ep.get("title", ""),
                "shots": shots,
            })

        result = AgentResult(
            success=True,
            data={"episodes": all_episodes},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "total_shots": sum(
                    len(ep["shots"]) for ep in all_episodes
                ),
            },
        )

        logger.info(
            f"[StoryboardAgent] 完成: "
            f"{result.metadata['total_shots']}个分镜"
        )
        return result
