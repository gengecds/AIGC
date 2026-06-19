"""Pipeline 调度器 - 串联所有 Agent"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

from agents.base import BaseAgent, AgentResult

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
        """从断点恢复：返回最后一个已完成的 agent 名称"""
        completed = [n for n in agent_names if self.is_completed(n)]
        if not completed:
            return None
        # 按顺序取最后一个
        for name in agent_names:
            if name == completed[-1]:
                return name
        return completed[-1]


class Pipeline:
    """管线调度器 - 串联执行 Agent 列表"""

    def __init__(self, agents: List[BaseAgent], state: Optional[PipelineState] = None):
        self.agents = agents
        self.state = state or PipelineState()
        self.pipeline_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    @property
    def agent_names(self) -> List[str]:
        return [a.name for a in self.agents]

    async def run(self, input_data: Dict, resume: bool = False) -> Dict[str, dict]:
        """
        执行整条管线
        - resume=False: 从头开始
        - resume=True: 从断点继续
        """
        results = {}

        if resume:
            last_agent = self.state.get_last_completed_agent(self.agent_names)
            if last_agent:
                # 加载已有结果到 results
                for name in self.agent_names:
                    cp = self.state.load_checkpoint(name)
                    if cp:
                        results[name] = cp
                logger.info(f"[Pipeline] 从断点恢复: {last_agent} 之后继续")
                # 从最后一个已完成的 agent 之后开始
                start_idx = self.agent_names.index(last_agent) + 1
                if start_idx >= len(self.agent_names):
                    logger.info("[Pipeline] 所有 Agent 已完成")
                    return results
                agents_to_run = self.agents[start_idx:]
            else:
                agents_to_run = self.agents
        else:
            self.state.clear()
            agents_to_run = self.agents

        for agent in agents_to_run:
            logger.info(f"[Pipeline] 开始执行: {agent.name}")

            # 把前面已生成的结果传递给当前 agent
            agent_input = {
                "pipeline_id": self.pipeline_id,
                "input": input_data,
                "previous_results": results,
            }

            result = await agent.run_with_retry(agent_input)

            if not result.success:
                logger.error(f"[Pipeline] {agent.name} 执行失败: {result.error}")
                results[agent.name] = result.to_dict()
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
