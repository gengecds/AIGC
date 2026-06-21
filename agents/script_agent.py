"""Agent 1 - 剧本生成

用户输入（一句话/大纲/小说）→ DeepSeek 解析 → 结构化剧本 JSON
"""

import json
import logging
import os
from datetime import datetime
from typing import Union

from agents.base import Agent, AgentResult
from providers.base import LLMProvider
from providers.llm import DeepSeekProvider, OllamaProvider

logger = logging.getLogger(__name__)

# 剧本结构 prompt
SCRIPT_SYSTEM_PROMPT = """你是一个专业的AI漫剧剧本创作助手。请根据用户输入生成结构化剧本。

输出格式为JSON：
{
  "title": "剧本标题",
  "genre": "题材类型（科幻/奇幻/都市/古风/末世等）",
  "summary": "故事简介（100字以内）",
  "characters": [
    {
      "name": "角色名",
      "gender": "男/女",
      "age": "青年/中年/老年",
      "appearance": "外貌特征描述（用于SD出图）",
      "personality": "性格描述",
      "role": "主角/配角/反派"
    }
  ],
  "episodes": [
    {
      "episode_number": 1,
      "title": "第1集标题",
      "plot": "本集剧情概要",
      "dialogues": [
        {
          "scene": "场景描述",
          "character": "说话角色",
          "line": "对话内容"
        }
      ]
    }
  ]
}

要求：
1. 如果用户只输入一句话，自动扩展为一个完整剧本大纲
2. 对话框对话按场景分组，每个场景的对话连续
3. 角色外貌描写要详细，包含发型/脸型/身材/服饰（用于后续SD出图）
4. 一集建议4-6个场景，每个场景3-8句对话
5. 必须返回合法的JSON，不要包含其他文字
"""


class ScriptAgent(Agent):
    """Agent 1：剧本生成"""

    name = "script_agent"

    def __init__(self, llm_provider: Union[str, LLMProvider] = "deepseek"):
        super().__init__(name="script_agent")
        if isinstance(llm_provider, LLMProvider):
            self.llm = llm_provider
        elif llm_provider == "deepseek":
            self.llm = DeepSeekProvider()
        else:
            self.llm = OllamaProvider()

    async def run(self, user_input: str) -> AgentResult:
        logger.info(f"[ScriptAgent] 收到输入: {user_input[:50]}...")

        if not user_input or len(user_input.strip()) < 2:
            return AgentResult(
                success=False,
                error="输入内容太短，请输入至少2个字符",
            )

        try:
            raw = await self.llm.generate(
                prompt=user_input,
                system_prompt=SCRIPT_SYSTEM_PROMPT,
                model="deepseek-chat",
            )

            # 清理可能的 markdown 包装
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                raw = raw.rsplit("```", 1)[0]

            script = json.loads(raw)

            # 基本校验
            if "title" not in script:
                script["title"] = user_input[:20]

            result = AgentResult(
                success=True,
                data=script,
                metadata={
                    "agent": self.name,
                    "timestamp": datetime.utcnow().isoformat(),
                    "characters": len(script.get("characters", [])),
                    "episodes": len(script.get("episodes", [])),
                },
            )

            logger.info(
                f"[ScriptAgent] 完成: {script['title']}, "
                f"{result.metadata['characters']}角色, "
                f"{result.metadata['episodes']}集"
            )
            return result

        except json.JSONDecodeError as e:
            logger.error(f"[ScriptAgent] JSON解析失败: {e}")
            return AgentResult(
                success=False,
                error=f"LLM返回格式错误，不是合法JSON: {str(e)}",
                data={"raw_output": raw},
            )
        except Exception as e:
            logger.error(f"[ScriptAgent] 错误: {e}")
            return AgentResult(
                success=False,
                error=str(e),
            )
