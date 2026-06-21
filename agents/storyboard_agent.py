"""Agent 2 - 分镜生成 + 校验层

剧本JSON → DeepSeek 拆分镜 → 校验层（shot_type/duration/prompt）→ 分镜JSON
"""

import json
import logging
from datetime import datetime

from agents.base import Agent, AgentResult
from providers.base import LLMProvider
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
    "scene": "场景描述（中文）",
    "shot_type": "远/中/近/特写/俯拍/仰拍",
    "duration": 5,
    "camera_movement": "固定/推/拉/摇/移/跟",
    "characters": ["角色1", "角色2"],
    "action": "角色动作描述（中文）",
    "background": "背景/环境描述（中文）",
    "lighting": "明亮/昏暗/逆光/暖色/冷色/阴森",
    "dialogue": "本分镜中的对话内容",
    "sd_prompt": "完整的SD出图英文prompt（必须包含：主体+动作+场景+风格+光影+画质词）",
    "sd_negative": "负面prompt",
    "transition": "cut/dissolve/fade/wipe"
  }
]

要求：
1. shot_type 仅限：远、中、近、特写、俯拍、仰拍（不写其他词）
2. duration 3-8秒，对话场景6-8秒，动作场景3-4秒
3. sd_prompt 用英文，格式："<主体>, <动作>, <场景>, <风格关键词>, <光影>, masterpiece, best quality, highly detailed"
4. sd_negative 必须包含："bad anatomy, extra hands, deformed face, missing fingers, ugly, low quality, blurry"
5. 一集12-18个分镜，不要超过18个
6. **镜头节奏**：每3-5个分镜换一次景别，避免连续3个同景别。开场用远/全景建立空间，对话用中/近景，情绪转折用特写
7. **剧情连贯性**：相邻分镜的动作/位置要逻辑连续。角色A进屋(A shot1)→角色A走到桌前(A shot2)→坐下说话(A shot3)，不能跳场景
8. 同一个场景内，背景描述保持一致（同一间屋子不要变描述）
9. 动作和对话严格按剧本来，不要自创剧情
10. 必须返回合法的JSON数组，不要加任何注释或其他文本
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

    def __init__(self, llm_provider: Union[str, LLMProvider] = "deepseek"):
        super().__init__(name="storyboard_agent")
        if isinstance(llm_provider, LLMProvider):
            self.llm = llm_provider
        elif llm_provider == "deepseek":
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
                prompt=prompt,
                system_prompt=STORYBOARD_SYSTEM_PROMPT,
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
