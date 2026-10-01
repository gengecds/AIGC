"""Agent 5 - 图生视频（本地 MLX 引擎：LTX-2.3）

传入分镜图列表 → LTX-2.3 MLX 原生引擎批量图生视频 → 输出 mp4 分段
视频由 ltx-2-mlx CLI 直接生成到 storage/output，不走 ComfyUI。
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from agents.base import Agent, AgentResult
from providers.base import VideoProvider
from skills.resolver import shot_camera_block, shot_genre_block, shot_use_block

logger = logging.getLogger(__name__)


class VideoGenAgent(Agent):
    """Agent 5：批量图生视频（本地 LTX-2.3 MLX）"""

    name = "video_agent"

    def __init__(self, use_comfyui: bool = False, comfy_client=None,
                 video_provider: VideoProvider | None = None):
        super().__init__(name="video_agent")
        self.use_comfyui = use_comfyui
        if video_provider is not None:
            self.video_provider = video_provider
        elif use_comfyui:
            # 固定用 LTX-2.3 MLX 本地引擎（配置/风格若还指向 minimax_h3 等已废弃引擎，
            # 也一律按 ltx_mlx 处理，见 _make_comfy_provider 内注释）
            self.video_provider = self._make_mlx_provider()
        else:
            from providers.mock_provider import MockVideoProvider
            self.video_provider = MockVideoProvider()

    @staticmethod
    def _make_mlx_provider():
        """创建视频 Provider：只支持 LTX-2.3 MLX 本地引擎。

        历史背景：config.yaml engine.video_engine 曾支持 'ltx'/'minimax_h3' 多引擎，
        对应 ComfyUI 视频 Provider 已随模型一并删除；视频统一走 MLX 原生引擎，
        故这里不再做引擎分发，直接返回 LTXMLXVideoProvider。
        """
        from providers.ltx_mlx_provider import LTXMLXVideoProvider
        logger.info("[VideoGenAgent] 视频引擎: LTX-2.3 MLX (本地原生, 9:16)")
        return LTXMLXVideoProvider()

    async def run(self, images_result, storyboard: dict | None = None) -> AgentResult:
        logger.info("[VideoGenAgent] 开始图生视频")

        if hasattr(images_result, 'data'):
            images_data = images_result.data or {}
        else:
            images_data = images_result if isinstance(images_result, dict) else {}

        images = images_data.get("images", {}) or {}

        # 解析分镜时长：shot_id → duration(秒)，用于决定每段视频生成长度
        # 短镜头切太快会显得"AI 味重"，真人广告镜头一般 3.5-5 秒
        sb_data = storyboard.data if hasattr(storyboard, "data") else (storyboard or {})
        sb_data = sb_data or {}
        durations: dict[str, int] = {}
        shot_map: dict[str, dict] = {}
        for ep in sb_data.get("episodes", []) or []:
            for shot in ep.get("shots", []) or []:
                sid = str(shot.get("shot_id"))
                durations[sid] = int(shot.get("duration", 5))
                shot_map[sid] = shot

        def _shot_length(sid: str) -> int:
            # 时长(秒) × 25fps → 帧数；上限 129 帧（≈5.2s）避免单段过长生成太慢。
            # 下限取 33 帧（≈1.4s @24fps，且满足 LTX 的 8n+1 帧数约束）——原下限 88 帧
            # （3.5s）会把所有短镜头强行拉长，做不出快剪节奏（复刻短视频时 1.3s/镜被顶到 3.5s）。
            d = durations.get(str(sid), 5)
            return max(33, min(129, int(d * 25)))

        def _shot_prompt(shot: dict | None) -> str:
            """按分镜内容自动拼装视频提示词：场景描述 + 运镜/题材/用途词块。

            不从"用户给了什么词"照搬，而是用 resolver 对 camera_movement/
            scene/action/background 等字段做中英文推断，取出对应整套镜头语言配方，
            让 LTX 拿到可执行的运动描述，而非回退到默认 "cinematic motion..."。
            """
            if not shot:
                return ""
            parts = []
            sd = str(shot.get("sd_prompt") or "").strip()
            if sd:
                parts.append(sd)
            for block in (shot_camera_block(shot), shot_genre_block(shot), shot_use_block(shot)):
                if block:
                    parts.append(block)
            return ", ".join(parts)

        all_results = {}
        for ep_key, ep_images in images.items():
            video_data = []
            for sid, img_info in ep_images.items():
                # 关键：image_agent 存的是 ComfyUI 的文件名（图片实体在 ComfyUI
                # output 目录）。LTX 需要本地真实文件，这里先同步落地成绝对路径。
                from providers.comfyui_provider import sync_image_to_local
                image_path = sync_image_to_local(img_info) or img_info.get("filename", "")
                video_data.append({
                    "shot_id": sid,
                    "image_path": image_path,
                    "subfolder": img_info.get("subfolder", ""),
                    "prompt_id": img_info.get("prompt_id", ""),
                    "prompt": _shot_prompt(shot_map.get(str(sid))),
                    "length": _shot_length(sid),
                })

            videos = await self.video_provider.batch_generate(video_data)

            # 处理返回结果：LTX/Mock 都返回视频文件（webm/mp4），复制到本地
            output_dir = Path("storage/output")
            output_dir.mkdir(parents=True, exist_ok=True)

            ep_videos = {}
            for item in videos:
                sid = item.get("shot_id", "")
                fname = item.get("filename", "")
                local_path = await self._download_maybe(fname, output_dir)
                ep_videos[sid] = {
                    "local_path": local_path or fname,
                    "filename": fname,
                    "shot_id": sid,
                }

            all_results[ep_key] = ep_videos

        total_videos = sum(len(v) for v in all_results.values())
        logger.info(f"[VideoGenAgent] 完成: {total_videos}段视频")

        return AgentResult(
            success=True,
            data={"videos": all_results},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "total_videos": total_videos,
            },
        )

    async def _download_maybe(self, fname: str, output_dir: Path) -> str:
        """获取视频文件到本地 output 目录（本地模式）

        直接从本机 ComfyUI 的 output/ 目录复制，无任何网络/SSH 调用。
        """
        if not fname:
            return ""
        local_path = str(output_dir / fname)
        if Path(local_path).exists():
            return local_path
        # 如果已经是本地路径，直接返回
        if Path(fname).exists():
            return fname
        # 从本机 ComfyUI output 目录复制（ComfyUI 生成的文件都在这里）
        comfy_output = Path(os.environ.get(
            "COMFY_OUTPUT_DIR",
            "/Users/a715/git/ComfyUI/ComfyUI-Installs/ComfyUI/ComfyUI/output",
        ))
        src = comfy_output / fname
        if src.exists():
            import shutil
            shutil.copy(src, local_path)
            logger.info(f"[VideoGenAgent] 本地复制: {src} -> {local_path}")
            return local_path
        logger.warning(f"[VideoGenAgent] 找不到视频文件: {fname}")
        return ""
