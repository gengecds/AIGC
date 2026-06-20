"""Agent 2 - 分镜生成 + 校验层

剧本JSON → DeepSeek 拆分镜 → 校验层（shot_type/duration/prompt）→ 分镜JSON
"""

import json
import logging
from datetime import datetime

from agents.base import Agent, AgentResult
from providers.llm import DeepSeekProvider, OllamaProvider

logger = logging.getLogger(__name__)

VALID_SHOT_TYPES = {"远", "中", "近", "特写", "俯拍", "仰拍", "航拍", "跟随", "全景", "远景", "中景", "近景", "广角", "大特写"}

# 景别标准化映射
SHOT_TYPE_MAP = {
    "全景": "远", "远景": "远", "广角": "远", "大远景": "远",
    "中景": "中", "中近景": "中",
    "近景": "近",
    "特写": "特写", "大特写": "特写", "局部特写": "特写",
    "俯视": "俯拍", "俯瞰": "俯拍",
    "仰视": "仰拍", "低角度": "仰拍",
    "航拍": "航拍", "空中": "航拍",
    "跟拍": "跟随", "移动": "跟随", "跟随": "跟随",
}

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

    def normalize_shots(self, shots: list[dict]) -> list[dict]:
        """标准化所有分镜，修正 shot_type 等"""
        for shot in shots:
            st = shot.get("shot_type", "")
            if st in SHOT_TYPE_MAP:
                shot["shot_type"] = SHOT_TYPE_MAP[st]
            elif st not in VALID_SHOT_TYPES:
                shot["shot_type"] = "中"  # 默认回退
            # 确保 duration 合法
            dur = shot.get("duration", 0)
            if not isinstance(dur, (int, float)) or dur < 3 or dur > 12:
                shot["duration"] = 5
            # 确保 sd_prompt 存在
            prompt = shot.get("sd_prompt", "")
            if not prompt or len(prompt) < 20:
                prompt = shot.get("action", "") or shot.get("scene", "")
                shot["sd_prompt"] = prompt + ", masterpiece, best quality"
            # 补画质词
            if "masterpiece" not in prompt.lower() or "best quality" not in prompt.lower():
                shot["sd_prompt"] = prompt + ", masterpiece, best quality"
        return shots
    
    def validate(self, shots: list[dict]) -> list[dict]:
        """校验所有分镜，返回 warnings 列表（不再阻塞）"""
        warnings = []
        for i, shot in enumerate(shots):
            shot_id = shot.get("shot_id", i + 1)
            st = shot.get("shot_type", "")
            if st not in VALID_SHOT_TYPES:
                warnings.append({
                    "shot_id": shot_id,
                    "field": "shot_type",
                    "message": f"无效镜头类型: {st}，已自动修正",
                })
            dur = shot.get("duration", 0)
            if not isinstance(dur, (int, float)) or dur < 3 or dur > 12:
                warnings.append({
                    "shot_id": shot_id,
                    "field": "duration",
                    "message": f"无效时长: {dur}s，已自动修正",
                })
        return warnings

    def fix_prompt(self, shot: dict) -> dict:
        """自动补全缺少的prompt元素（如有需要）"""
        prompt = shot.get("sd_prompt", "")
        needed = {
            "主体": shot.get("action", ""),
            "场景": shot.get("background", ""),
            "光影": shot.get("lighting", ""),
        }
        for key, val in needed.items():
            if isinstance(val, str) and val and val not in prompt:
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

            raw = await self.llm.generate(
                system=STORYBOARD_SYSTEM_PROMPT,
                user=prompt,
                model="deepseek-chat",
            )

            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                raw = raw.rsplit("```", 1)[0]

            try:
                shots = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.error(f"[Storyboard] JSON解析失败: {e}")
                return AgentResult(
                    success=False,
                    error=f"LLM返回格式错误: {str(e)}",
                )

            # 标准化（修正shot_type等），不再硬校验阻止
            self.validator.normalize_shots(shots)
            warnings = self.validator.validate(shots)
            if warnings:
                logger.warning(f"[校验] 分镜有{warnings}处需修正，已自动处理")

            # 自动补全prompt
            for shot in shots:
                self.validator.fix_prompt(shot)

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
