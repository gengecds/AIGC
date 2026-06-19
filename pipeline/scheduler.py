"""Pipeline 调度器 - 串联所有 Agent"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)


class PipelineState:
    """管线状态/Checkpoint 管理"""

    def __init__(self, checkpoint_dir: str = "storage/checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.results: Dict[str, dict] = {}

    def save_checkpoint(self, agent_name: str, result: AgentResult):
        """保存单个 Agent 的执行结果"""
        self.results[agent_name] = result.to_dict()
        path = self.checkpoint_dir / f"{agent_name}_checkpoint.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"[Checkpoint] {agent_name} 结果已保存到 {path}")

    def load_checkpoint(self, agent_name: str) -> Optional[dict]:
        """加载已完成的 Agent 结果"""
        path = self.checkpoint_dir / f"{agent_name}_checkpoint.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def is_completed(self, agent_name: str) -> bool:
        return self.load_checkpoint(agent_name) is not None

    def clear(self):
        """清空所有 checkpoint"""
        for f in self.checkpoint_dir.glob("*_checkpoint.json"):
            f.unlink()
        self.results = {}

    def get_last_completed_agent(self, agent_names: List[str]) -> Optional[str]:
        """从断点恢复"""
        completed = [n for n in agent_names if self.is_completed(n)]
        if not completed:
            return None
        for name in agent_names:
            if name == completed[-1]:
                return name
        return completed[-1]


class Pipeline:
    """管线调度器 - 串联执行 Agent 列表"""

    def __init__(self, state: Optional[PipelineState] = None):
        self.state = state or PipelineState()
        self.pipeline_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    @property
    def agent_names(self) -> List[str]:
        return [a.name for a in self.agents]

    async def run(self, agents: List[Agent], user_input: str,
                  resume: bool = False) -> Dict:
        """
        执行整条管线
        agents: 按顺序传入 Agent 实例列表
        resume: 是否从断点恢复
        """
        self.agents = agents
        results = {}

        if resume:
            last_agent = self.state.get_last_completed_agent(self.agent_names)
            if last_agent:
                for name in self.agent_names:
                    cp = self.state.load_checkpoint(name)
                    if cp:
                        results[name] = cp
                logger.info(f"[Pipeline] 从断点恢复: {last_agent} 之后继续")
                start_idx = self.agent_names.index(last_agent) + 1
                if start_idx >= len(self.agent_names):
                    return {"success": True, "pipeline_id": self.pipeline_id, "results": results}
                agents_to_run = agents[start_idx:]
            else:
                agents_to_run = agents
        else:
            self.state.clear()
            agents_to_run = agents

        for agent in agents_to_run:
            logger.info(f"[Pipeline] 开始执行: {agent.name}")

            # 根据 agent 类型传递不同的输入
            result = None

            if agent.name == "script_agent":
                result = await agent.run(user_input)
            elif agent.name == "storyboard_agent":
                script_result = results.get("script_agent", {}).get("data", {})
                result = await agent.run(script_result)
            elif agent.name == "character_agent":
                script_result = results.get("script_agent", {}).get("data", {})
                result = await agent.run(script_result)
            elif agent.name == "image_agent":
                storyboard_result = results.get("storyboard_agent", {}).get("data", {})
                char_assets = {
                    c["name"]: c.get("asset", {})
                    for c in (results.get("character_agent", {}).get("data", {}).get("characters", []) or [])
                }
                result = await agent.run(storyboard_result, char_assets)
            elif agent.name == "video_agent":
                images_result_data = results.get("image_agent", {})
                result = await agent.run(AgentResult(success=True, data=images_result_data.get("data", {})))
            elif agent.name == "subtitle_agent":
                script_result = results.get("script_agent", {}).get("data", {})
                storyboard_result = results.get("storyboard_agent", {}).get("data", {})
                result = await agent.run(script_result, storyboard_result)
            elif agent.name == "compose_agent":
                videos_result_data = results.get("video_agent", {})
                subtitle_result_data = results.get("subtitle_agent", {})
                result = await agent.run(
                    AgentResult(success=True, data=videos_result_data.get("data", {})),
                    AgentResult(success=True, data=subtitle_result_data.get("data", {})),
                )

            if result is None or not result.success:
                error = result.error if result else "Agent 未返回结果"
                logger.error(f"[Pipeline] {agent.name} 执行失败: {error}")
                results[agent.name] = result.to_dict() if result else {"error": error}
                return {
                    "success": False,
                    "failed_at": agent.name,
                    "results": results,
                }

            results[agent.name] = result.to_dict()
            self.state.save_checkpoint(agent.name, result)
            logger.info(f"[Pipeline] {agent.name} 完成")

        return {
            "success": True,
            "pipeline_id": self.pipeline_id,
            "results": results,
        }
