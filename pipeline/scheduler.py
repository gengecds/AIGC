"""Pipeline 调度器 v2 - 串联所有 Agent（含审核断点）"""

import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Callable, Awaitable, Any
from pathlib import Path

from agents.base import Agent, AgentResult
from pipeline.retry import retry_async

logger = logging.getLogger(__name__)


class PipelineState:
    """管线状态管理（checkpoint落盘）"""

    def __init__(self, checkpoint_dir: str = "storage/checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def save_checkpoint(self, agent_name: str, result: AgentResult):
        path = self.checkpoint_dir / f"{agent_name}_checkpoint.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"[Checkpoint] {agent_name} 结果已保存到 {path}")

    def load_checkpoint(self, agent_name: str) -> Optional[dict]:
        path = self.checkpoint_dir / f"{agent_name}_checkpoint.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def has_checkpoint(self, agent_name: str) -> bool:
        return self.load_checkpoint(agent_name) is not None

    def clear(self):
        for f in self.checkpoint_dir.glob("*_checkpoint.json"):
            f.unlink()
        logger.info("[Checkpoint] 所有 checkpoint 已清除")

    def load_all(self) -> dict:
        results = {}
        for f in sorted(self.checkpoint_dir.glob("*_checkpoint.json")):
            name = f.name.replace("_checkpoint.json", "")
            with open(f, "r", encoding="utf-8") as fh:
                results[name] = json.load(fh)
        return results

    def get_last_completed_agent(self, agent_names: list[str]) -> Optional[str]:
        completed = []
        for name in agent_names:
            if self.has_checkpoint(name):
                completed.append(name)
        if not completed:
            return None
        last_idx = agent_names.index(completed[-1])
        for i in range(last_idx + 1):
            if agent_names[i] not in completed:
                return None
        return completed[-1]

    @property
    def agent_names(self) -> list[str]:
        names = []
        for f in sorted(self.checkpoint_dir.glob("*_checkpoint.json")):
            names.append(f.name.replace("_checkpoint.json", ""))
        return names


class ReviewBlock(BaseException):
    """审核中断信号（非错误，等待确认）"""
    def __init__(self, agent_name: str, reason: str, data: dict):
        self.agent_name = agent_name
        self.reason = reason
        self.data = data
        super().__init__(f"⏸️ 等待审核: {reason}")


class Pipeline:
    def __init__(self, pipeline_id: str = None):
        self.state = PipelineState()
        self.agents: List[Agent] = []
        self.pipeline_id = pipeline_id or f"p_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        self._on_agent_start: Optional[Callable] = None
        self._on_agent_complete: Optional[Callable] = None
        self._on_agent_fail: Optional[Callable] = None
        self._on_pipeline_complete: Optional[Callable] = None
        # 审核回调
        self._on_review_needed: Optional[Callable[[str, str, dict], Awaitable]] = None
        self._review_lock = asyncio.Event()
        self._review_lock.set()
        self._review_data: dict = {}
        self._paused = False

    def set_callbacks(
        self,
        on_agent_start: Optional[Callable] = None,
        on_agent_complete: Optional[Callable] = None,
        on_agent_fail: Optional[Callable] = None,
        on_pipeline_complete: Optional[Callable] = None,
        on_review_needed: Optional[Callable] = None,
    ):
        self._on_agent_start = on_agent_start
        self._on_agent_complete = on_agent_complete
        self._on_agent_fail = on_agent_fail
        self._on_pipeline_complete = on_pipeline_complete
        self._on_review_needed = on_review_needed

    def approve_review(self):
        self._review_lock.set()

    def reject_review(self):
        self._review_data["rejected"] = True
        self._review_lock.set()

    async def wait_for_review(self, reason: str, data: dict, agent_name: str):
        self._review_lock.clear()
        self._review_data = {"rejected": False, "reason": reason, "data": data}
        self._paused = True
        logger.info(f"[Pipeline] ⏸️ 等待审核: {reason}")
        if self._on_review_needed:
            await self._on_review_needed(agent_name, reason, data)
        await self._review_lock.wait()
        self._paused = False
        rejected = self._review_data.get("rejected", False)
        logger.info(f"[Pipeline] ▶️ 审核结果: {'拒绝' if rejected else '确认'} {reason}")
        return {"approved": not rejected, "data": self._review_data.get("data", data)}

    def _get(self, data: dict, key: str, default=None):
        return data.get(key, {}).get("data", {}) if data.get(key) else (default or {})

    @property
    def agent_names(self) -> list[str]:
        return [a.name for a in self.agents]

    async def run(self, agents: List[Agent], user_input: str,
                  resume: bool = False,
                  enable_review: bool = True) -> Dict:
        """
        执行整条管线
        agents: 按顺序传入 Agent 实例列表
        resume: 是否从断点恢复
        enable_review: 是否启用审核断点（storyboard/character 后暂停）
        带 callback 支持：on_agent_start / on_agent_complete / on_agent_fail
        """
        self.agents = agents
        results = {}
        total = len(agents)

        # AGENTS_PIPELINE: 固定 Agent 列表，用于断点续传定位
        AGENTS_PIPELINE = [
            "script_agent", "storyboard_agent", "character_agent",
            "image_agent", "video_agent", "subtitle_agent",
            "video_compose_agent", "compose_agent", "publish_agent",
        ]

        if resume:
            last_agent = self.state.get_last_completed_agent(AGENTS_PIPELINE)
            if last_agent:
                for name in AGENTS_PIPELINE:
                    cp = self.state.load_checkpoint(name)
                    if cp:
                        results[name] = cp
                logger.info(f"[Pipeline] 从断点恢复: {last_agent} 之后继续")
                start_idx = AGENTS_PIPELINE.index(last_agent) + 1
                if start_idx >= len(AGENTS_PIPELINE):
                    return {"success": True, "pipeline_id": self.pipeline_id, "results": results}
                agents_to_run = [a for a in agents if a.name in AGENTS_PIPELINE[start_idx:]]
            else:
                self.state.clear()
                agents_to_run = agents
        else:
            self.state.clear()
            agents_to_run = agents

        for agent in agents_to_run:
            logger.info(f"[Pipeline] 开始执行: {agent.name}")
            name = agent.name
            global_idx = AGENTS_PIPELINE.index(name) if name in AGENTS_PIPELINE else 0
            total = len(AGENTS_PIPELINE)

            # callback: agent 开始
            if self._on_agent_start:
                await self._on_agent_start(name, global_idx, total)

            script_data = self._get(results, "script_agent")
            storyboard_data = self._get(results, "storyboard_agent")
            character_data = self._get(results, "character_agent")
            image_data = self._get(results, "image_agent")
            video_data = self._get(results, "video_agent")
            subtitle_data = self._get(results, "subtitle_agent")

            try:
                if name == "script_agent":
                    result = await retry_async(agent.run, user_input)
                elif name == "storyboard_agent":
                    result = await retry_async(agent.run, script_data)
                    # 审核断点 1：分镜完成后等待确认
                    if enable_review and result.success:
                        rd = result.data or {}
                        await self.wait_for_review(
                            "storyboard_approval", rd, name
                        )
                elif name == "character_agent":
                    result = await retry_async(agent.run, script_data)
                    # 审核断点 2：角色定妆照确认
                    if enable_review and result.success:
                        rd = result.data or {}
                        await self.wait_for_review(
                            "character_approval", rd, name
                        )
                elif name == "image_agent":
                    char_assets = {
                        c["name"]: c.get("asset", {})
                        for c in character_data.get("characters", []) or []
                    }
                    result = await retry_async(agent.run, storyboard_data, char_assets)
                elif name == "video_agent":
                    result = await retry_async(agent.run,
                        AgentResult(success=True, data={"images": image_data.get("images", {})}),
                        storyboard_data,
                    )
                elif name == "subtitle_agent":
                    result = await retry_async(agent.run, script_data, storyboard_data)
                elif name == "video_compose_agent":
                    result = await retry_async(agent.run,
                        AgentResult(success=True, data={"videos": video_data.get("videos", {})}),
                        AgentResult(success=True, data={"subtitles": subtitle_data.get("subtitles", [])}),
                    )
                elif name == "compose_agent":
                    result = await retry_async(agent.run,
                        AgentResult(success=True, data={"videos": video_data.get("videos", {})}),
                        AgentResult(success=True, data={"subtitles": subtitle_data.get("subtitles", [])}),
                    )
                elif name == "publish_agent":
                    compose_data = self._get(results, "video_compose_agent")
                    if not compose_data:
                        compose_data = self._get(results, "compose_agent")
                    result = await retry_async(agent.run,
                        AgentResult(success=True, data={"published": compose_data.get("published", [])})
                    )
                else:
                    # 未知 Agent ——尝试直接 run
                    result = await self._run_unknown_agent(agent, AGENTS_PIPELINE, name, results)
                    if result is None:
                        logger.error(f"[Pipeline] 未知 Agent {name}，跳过")
                        results[name] = {"error": f"未知 Agent: {name}"}
                        continue
            except ReviewBlock:
                return {
                    "success": False,
                    "paused": True,
                    "paused_at": name,
                    "pipeline_id": self.pipeline_id,
                    "results": results,
                }
            except Exception as e:
                logger.error(f"[Pipeline] {name} 异常: {e}")
                if self._on_agent_fail:
                    await self._on_agent_fail(name, global_idx, total, str(e))
                return {
                    "success": False,
                    "failed_at": name,
                    "error": str(e),
                    "results": results,
                }

            if result is None or not result.success:
                error = result.error if result else "Agent 未返回结果"
                logger.error(f"[Pipeline] {name} 执行失败: {error}")
                results[name] = result.to_dict() if result else {"error": error}
                if self._on_agent_fail:
                    await self._on_agent_fail(name, global_idx, total, error)
                return {
                    "success": False,
                    "failed_at": name,
                    "results": results,
                }

            results[name] = result.to_dict()
            self.state.save_checkpoint(name, result)
            logger.info(f"[Pipeline] {name} 完成")

            meta = result.metadata if hasattr(result, 'metadata') else {}
            if self._on_agent_complete:
                await self._on_agent_complete(name, global_idx, total, meta)

        final = {
            "success": True,
            "pipeline_id": self.pipeline_id,
            "results": results,
        }
        if self._on_pipeline_complete:
            await self._on_pipeline_complete(final)
        return final

    async def _run_unknown_agent(self, agent, name_to_idx: list,
                                 name: str, results: dict) -> Optional[AgentResult]:
        """尝试运行未知 Agent"""
        try:
            from inspect import signature
            sig = signature(agent.run)
            # 尝试传入 results
            return await agent.run(results)
        except TypeError:
            return None

    def get_result(self, results: dict, field: str):
        d = results.get(field, {})
        if isinstance(d, dict) and "data" in d:
            return d.get("data", {})
        return {}
