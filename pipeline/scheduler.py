"""Pipeline 调度器 v2 - 串联所有 Agent（含审核断点）"""

import json
import logging
import asyncio
import copy
import time
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
        self._on_agent_progress: Optional[Callable] = None
        self._on_pipeline_complete: Optional[Callable] = None
        # 审核回调
        self._on_review_needed: Optional[Callable[[str, str, dict], Awaitable]] = None
        self._review_lock = asyncio.Event()
        self._review_lock.set()
        self._review_data: dict = {}
        self._paused = False
        self._cancelled = False

    def set_callbacks(
        self,
        on_agent_start: Optional[Callable] = None,
        on_agent_complete: Optional[Callable] = None,
        on_agent_fail: Optional[Callable] = None,
        on_pipeline_complete: Optional[Callable] = None,
        on_review_needed: Optional[Callable] = None,
        on_agent_progress: Optional[Callable] = None,
    ):
        self._on_agent_start = on_agent_start
        self._on_agent_complete = on_agent_complete
        self._on_agent_fail = on_agent_fail
        self._on_pipeline_complete = on_pipeline_complete
        self._on_review_needed = on_review_needed
        self._on_agent_progress = on_agent_progress

    def approve_review(self):
        self._review_lock.set()

    def cancel(self):
        """请求取消管线：置标志并唤醒审核等待，让 run() 尽快以取消态退出。"""
        self._cancelled = True
        self._review_lock.set()
        logger.warning(f"[Pipeline] 🛑 收到取消请求: {self.pipeline_id}")

    def _check_cancelled(self):
        """合作式取消检查点：已请求取消则抛 CancelledError 让任务立即收尾。"""
        if self._cancelled:
            raise asyncio.CancelledError(f"pipeline {self.pipeline_id} cancelled")

    def reject_review(self):
        self._review_data["rejected"] = True
        self._review_lock.set()

    def submit_edit(self, data: dict):
        """用户提交修改稿：把修改后的内容写回审核数据并放行管线"""
        if not isinstance(data, dict):
            data = {}
        self._review_data["data"] = data
        self._review_data["rejected"] = False
        self._review_lock.set()
        logger.info(f"[Pipeline] 📝 用户提交修改稿: 字段={list(data.keys())}")

    async def _editable_review(self, agent_name: str, result: AgentResult, label: str):
        """通用「可编辑确认断点」：落盘 → 等待 → 合并覆盖。

        - 先 save_checkpoint，让前端在断点时能通过 snapshot/latest 拿到完整数据渲染编辑表单
        - 等待用户在前端修改并提交（或直接确认/拒绝重跑）
        - 若用户提交了 {agent_name: 修改稿}，则把修改稿「合并」覆盖到原结果（保留其它字段）
        """
        rd = result.data or {}
        self.state.save_checkpoint(agent_name, result)
        reviewed = await self.wait_for_review(f"{agent_name}_approval", rd, agent_name)
        submitted = reviewed.get("data") or {}
        if (
            reviewed.get("approved")
            and isinstance(submitted, dict)
            and agent_name in submitted
            and isinstance(submitted.get(agent_name), dict)
        ):
            # 修改稿「合并」覆盖：保留原结果其它字段，只用用户改动的字段覆盖
            updated = dict(rd) if isinstance(rd, dict) else rd
            updated.update(submitted.get(agent_name) or {})
            result = AgentResult(success=True, data=updated, metadata=result.metadata)
            logger.info(f"[Pipeline] 📝 {label}已按用户修改稿合并覆盖")
        return result

    async def wait_for_review(self, reason: str, data: dict, agent_name: str):
        self._check_cancelled()
        self._review_lock.clear()
        self._review_data = {"rejected": False, "reason": reason, "data": data}
        self._paused = True
        logger.info(f"[Pipeline] ⏸️ 等待审核: {reason}")
        if self._on_review_needed:
            await self._on_review_needed(agent_name, reason, data)
        await self._review_lock.wait()
        self._paused = False
        self._check_cancelled()
        rejected = self._review_data.get("rejected", False)
        logger.info(f"[Pipeline] ▶️ 审核结果: {'拒绝' if rejected else '确认'} {reason}")
        return {"approved": not rejected, "data": self._review_data.get("data", data)}

    def _get(self, data: dict, key: str, default=None):
        return data.get(key, {}).get("data", {}) if data.get(key) else (default or {})

    @property
    def agent_names(self) -> list[str]:
        return [a.name for a in self.agents]

    @staticmethod
    def _base_pipeline() -> list[str]:
        """官方完整节点顺序（未按模型能力裁剪）。
        intel_agent 在最前（情报前置，是否执行由 _effective_pipeline 依据 intel_enabled/INTEL_ENABLED 决定）。
        research_agent 其次：需求 → 制作方案 → 剧本（流程升级）。
        compose_agent 已被 video_compose_agent 取代，不在列表中（否则断点续传永远不连续）。
        """
        return [
            "intel_agent", "research_agent", "script_agent", "storyboard_agent",
            "character_agent", "image_agent", "video_agent",
            "subtitle_agent", "video_compose_agent",
            "audio_agent", "publish_agent",
        ]

    def _effective_pipeline(self) -> list[str]:
        """按当前视频模型能力，动态确定实际执行的节点顺序。

        核心：管线不是「写死」的，而是依据所选模型的能力（config.capabilities）自动增删节点。
        例如 MiniMax H3 一次生成「视频+原生音频」，则无需 audio_agent 后期配音；
        未来若出现更「全能」的模型（一次生成即含全部要素），也只需更新能力注册表即可。
        """
        from config.capabilities import pipeline_extra, pipeline_skip
        from config.style_resolver import video_model_type_for_style
        # 情报前置节点由 intel_enabled()/INTEL_ENABLED 决定（读取 config.intel.enabled）；不依赖视频模型能力
        from intel.service import intel_enabled as _intel_enabled
        video_model = video_model_type_for_style(default="ltx") or "ltx"
        skip = pipeline_skip(video_model)
        extra = pipeline_extra(video_model)
        order = []
        for name in self._base_pipeline():
            if name == "intel_agent" and not _intel_enabled():
                logger.info("[Pipeline] 情报能力未开启 (intel_enabled=False)，跳过节点 intel_agent")
                continue
            if name in skip:
                logger.info(
                    f"[Pipeline] 视频引擎 {video_model} 能力: 跳过节点 {name}"
                )
                continue
            order.append(name)
        # 预留：把模型额外需要的节点插入（默认无）
        for name in extra:
            if name not in order:
                order.append(name)
        return order


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

        # 动态节点顺序：按当前视频模型能力自动增删（如 H3 自带音频则跳过 audio_agent）
        AGENTS_PIPELINE = self._effective_pipeline()

        # 过滤掉「按模型能力应跳过」的 Agent（传入列表里可能仍包含，如前端固定拼接了 AudioAgent）
        kept, dropped = [], []
        for a in agents:
            (kept if a.name in AGENTS_PIPELINE else dropped).append(a)
        for a in dropped:
            logger.info(f"[Pipeline] 按模型能力跳过节点: {a.name}")
        if dropped:
            agents = kept
            self.agents = kept

        total = len(AGENTS_PIPELINE)
        agent_by_name = {getattr(a, "name", ""): a for a in agents}
        # 配音预测量（逐句 TTS 真实时长）：分镜/字幕/成片/混音四处共用的时长基准，
        # 在分镜之前算好并透传给下游；管线无 audio_agent 时保持 None（按分镜默认时长）。
        voice_plan: list | None = None

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
                    # 所有节点均已落盘（全完成续跑）：走统一完成回调，
                    # 否则 SSE 不会广播 pipeline_done、_active 状态也不会收尾
                    final = {
                        "success": True,
                        "pipeline_id": self.pipeline_id,
                        "results": results,
                        "resumed_complete": True,
                    }
                    if self._on_pipeline_complete:
                        await self._on_pipeline_complete(final)
                    return final
                agents_to_run = [a for a in agents if a.name in AGENTS_PIPELINE[start_idx:]]
            else:
                self.state.clear()
                agents_to_run = agents
        else:
            self.state.clear()
            agents_to_run = agents

        for agent in agents_to_run:
            self._check_cancelled()
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
                if name == "intel_agent":
                    # 情报前置：读取素材 → 爆款拆解 → 落盘 reference_cases（无情报源时静默成功）
                    result = await retry_async(agent.run, user_input,
                                               max_retries=3, retry_delay=2)
                elif name == "research_agent":
                    # Ollama json 模式偶发输出异常：多给重试次数
                    result = await retry_async(agent.run, user_input,
                                               max_retries=5, retry_delay=8)
                    # 审核断点 0：方案生成后允许用户在前端修改完善并提交，确认后再进入剧本
                    if enable_review and result.success:
                        rd = result.data or {}
                        # 先落盘，前端在断点时通过 snapshot/latest 才能拿到方案数据
                        self.state.save_checkpoint(name, result)
                        reviewed = await self.wait_for_review(
                            "research_approval", rd, name
                        )
                        submitted = reviewed.get("data") or {}
                        sub = submitted.get("research_agent") or submitted.get("research") or {}
                        if reviewed.get("approved") and isinstance(submitted, dict) and (submitted.get("research_agent") or submitted.get("research")):
                            # 方案修改稿「合并」覆盖：保留原方案其它字段，只用用户改动的字段覆盖
                            updated = dict(rd)
                            updated.update(sub or {})
                            result = AgentResult(success=True, data=updated, metadata=result.metadata)
                            logger.info("[Pipeline] 📝 方案已按用户修改稿合并覆盖")
                elif name == "script_agent":
                    # 剧本消费研究方案（文案要点/风格/渲染引擎关键词等）
                    research_data = self._get(results, "research_agent") or {}
                    result = await retry_async(agent.run, user_input, research_data,
                                               max_retries=5, retry_delay=8)
                    # 审核断点 0：剧本/方案生成后允许用户在前端修改完善并提交
                    if enable_review and result.success:
                        rd = result.data or {}
                        # 关键：先保存 checkpoint，前端在断点时通过 snapshot/latest 才能拿到剧本数据。
                        # 否则 save_checkpoint 在断点之后才执行，前端编辑表单会因拿不到剧本而空白。
                        self.state.save_checkpoint(name, result)
                        reviewed = await self.wait_for_review(
                            "script_approval", rd, name
                        )
                        submitted = reviewed.get("data") or {}
                        sub = submitted.get("script_agent") or submitted.get("script") or {}
                        sub_research = submitted.get("research_agent") or submitted.get("research") or {}
                        if reviewed.get("approved") and isinstance(submitted, dict) and (submitted.get("script_agent") or submitted.get("script")):
                            # 用户提交了修改稿：覆盖剧本（及其中的方案）供后续 Agent 使用
                            updated = dict(rd)
                            updated.update(sub or {})
                            result = AgentResult(success=True, data=updated, metadata=result.metadata)
                            if isinstance(sub_research, dict) and sub_research:
                                # 方案修改稿要「合并」而不是「整体替换」：保留原方案其它字段，
                                # 只用用户改动的字段覆盖（否则只剩 style_direction 等少数字段，其余全丢）
                                merged_research = dict(research_data)
                                merged_research.update(sub_research)
                                research_result = AgentResult(
                                    success=True, data=merged_research, metadata={}
                                )
                                results["research_agent"] = research_result.to_dict()
                                self.state.save_checkpoint("research_agent", research_result)
                                logger.info("[Pipeline] 📝 方案已按用户修改稿合并覆盖")
                elif name == "storyboard_agent":
                    # 先跑配音预测量（TTS 真实时长）：分镜按「每句台词一镜」生成，
                    # 镜头时长 = 该句配音时长 + 停顿，成片时长随之 ≈ 配音总时长，
                    # 台词逐句顺序落位不重叠、字幕与画面精确对位。
                    if voice_plan is None:
                        audio_agent = agent_by_name.get("audio_agent")
                        if audio_agent is not None and hasattr(audio_agent, "plan_voices"):
                            try:
                                # 逐句 TTS 合成较慢（首次含模型加载），先打一条开始日志，
                                # 让用户在分镜前的等待期能看到管线仍在推进
                                logger.info("[Pipeline] ⏳ 开始配音预测量（逐句 TTS 合成）…")
                                t0 = time.time()

                                # plan_voices 跑在线程里，进度回调需切回事件循环才能广播 SSE
                                loop = asyncio.get_running_loop()

                                def _emit_voice_progress(info: dict):
                                    cb = self._on_agent_progress
                                    if cb is None:
                                        return
                                    try:
                                        asyncio.run_coroutine_threadsafe(
                                            cb("audio_agent", "voice_plan", info), loop
                                        )
                                    except Exception:
                                        pass

                                voice_plan = await asyncio.to_thread(
                                    audio_agent.plan_voices, script_data,
                                    on_progress=_emit_voice_progress,
                                ) or []
                                total_voice = sum(
                                    float(p.get("slot") or 0) for p in voice_plan
                                )
                                logger.info(
                                    f"[Pipeline] ✅ 配音预测量: {len(voice_plan)} 句，"
                                    f"合计 {total_voice:.1f}s，用时 {time.time() - t0:.1f}s"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"[Pipeline] 配音预测量失败，按分镜默认时长生成: {e}"
                                )
                                voice_plan = []
                    result = await retry_async(agent.run, script_data,
                                               voice_plan=voice_plan,
                                               max_retries=5, retry_delay=8)
                    # 审核断点：分镜完成后等待用户确认/可编辑
                    if enable_review and result.success:
                        result = await self._editable_review(name, result, "分镜")
                elif name == "character_agent":
                    result = await retry_async(agent.run, script_data)
                    # 审核断点：角色定妆照确认/可编辑
                    if enable_review and result.success:
                        result = await self._editable_review(name, result, "定妆照")
                elif name == "image_agent":
                    char_assets = {
                        c["name"]: c.get("asset", {})
                        for c in character_data.get("characters", []) or []
                    }
                    # 本地 M4 出图速度可接受，处理全部分镜（批量超时已在 Provider 中加大）
                    result = await retry_async(agent.run, storyboard_data, char_assets)
                    # 审核断点：出图完成后等待用户确认/可编辑
                    if enable_review and result.success:
                        result = await self._editable_review(name, result, "出图")
                elif name == "video_agent":
                    result = await retry_async(agent.run,
                        AgentResult(success=True, data={"images": image_data.get("images", {})}),
                        storyboard_data,
                    )
                    # 审核断点：视频生成后等待用户确认/可编辑
                    if enable_review and result.success:
                        result = await self._editable_review(name, result, "视频")
                elif name == "subtitle_agent":
                    result = await retry_async(agent.run, script_data, storyboard_data)
                    # 审核断点：字幕生成后等待用户确认/可编辑
                    if enable_review and result.success:
                        result = await self._editable_review(name, result, "字幕")
                elif name == "video_compose_agent":
                    # 传入分镜（每镜时长）→ 成片按分镜拉伸/补齐，与字幕时间轴对齐
                    result = await retry_async(agent.run,
                        AgentResult(success=True, data={"videos": video_data.get("videos", {})}),
                        AgentResult(success=True, data={"subtitles": subtitle_data.get("subtitles", [])}),
                        storyboard_result=AgentResult(success=True, data=storyboard_data or {}),
                    )
                    # 审核断点：合成完成后等待用户确认/可编辑
                    if enable_review and result.success:
                        result = await self._editable_review(name, result, "合成")
                elif name == "compose_agent":
                    result = await retry_async(agent.run,
                        AgentResult(success=True, data={"videos": video_data.get("videos", {})}),
                        AgentResult(success=True, data={"subtitles": subtitle_data.get("subtitles", [])}),
                    )
                elif name == "audio_agent":
                    compose_data = self._get(results, "video_compose_agent")
                    if not compose_data:
                        compose_data = self._get(results, "compose_agent")
                    # 传入研究方案（BGM 情绪/场景音效）供音频合成使用
                    research_data = self._get(results, "research_agent") or {}
                    # 传入分镜（镜头时长/角色）用于配音音色分配与时间轴对齐；
                    # 复用分镜前算好的配音预测量（含已合成 WAV 与逐句真实时长），
                    # 避免二次 TTS、并保证混音顺序与分镜/字幕完全一致。
                    result = await retry_async(agent.run,
                        AgentResult(success=True, data={"published": compose_data.get("published", [])}),
                        AgentResult(success=True, data=script_data or {}),
                        AgentResult(success=True, data={"srt_files": subtitle_data.get("srt_files", {})}),
                        research_data,
                        AgentResult(success=True, data=storyboard_data or {}),
                        voice_plan=voice_plan,
                    )
                    # 审核断点：音频合成后等待用户确认/可编辑
                    if enable_review and result.success:
                        result = await self._editable_review(name, result, "音频")
                elif name == "publish_agent":
                    compose_data = self._get(results, "video_compose_agent")
                    if not compose_data:
                        compose_data = self._get(results, "compose_agent")
                    # 若有音频成片，用带音频的版本发布
                    audio_data = self._get(results, "audio_agent")
                    published = compose_data.get("published", [])
                    if audio_data and audio_data.get("final_video"):
                        for p in published:
                            p["final_path"] = audio_data["final_video"]
                    result = await retry_async(agent.run,
                        AgentResult(success=True, data={"published": published})
                    )
                    # 审核断点：发布前等待用户确认/可编辑
                    if enable_review and result.success:
                        result = await self._editable_review(name, result, "发布")
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
