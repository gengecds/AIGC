"""Agent 5 - 图生视频（Phase 2: ComfyUI+HunyuanVideo）

传入图片列表 → ComfyUI+HunyuanVideo 批量图生视频 → 输出视频分段
支持自动从 GPU 下载视频/帧合成
"""

import logging
import os
import subprocess as sp
import asyncio
from datetime import datetime
from pathlib import Path

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)

# GPU SSH 配置（优先环境变量，否则用默认值）
GPU_HOST = os.environ.get("GPU_HOST", "root@connect.bjb2.seetacloud.com")
GPU_PORT = os.environ.get("GPU_PORT", "30476")
GPU_PASS = os.environ.get("GPU_PASS", "900917_19871002-Gz")
GPU_OUTPUT_DIR = "/root/ComfyUI/output"


class VideoGenAgent(Agent):
    """Agent 5：批量图生视频"""

    name = "video_agent"

    def __init__(self, use_comfyui: bool = False, comfy_client=None):
        super().__init__(name="video_agent")
        self.use_comfyui = use_comfyui
        if use_comfyui and comfy_client:
            from providers.comfyui_provider import ComfyHunyuanVideoProvider
            self.video_provider = ComfyHunyuanVideoProvider(client=comfy_client)
        else:
            from providers.mock_provider import MockVideoProvider
            self.video_provider = MockVideoProvider()

    async def run(self, images_result, storyboard: dict | None = None) -> AgentResult:
        logger.info("[VideoGenAgent] 开始图生视频")

        if hasattr(images_result, 'data'):
            images_data = images_result.data or {}
        else:
            images_data = images_result if isinstance(images_result, dict) else {}

        images = images_data.get("images", {}) or {}

        all_results = {}
        for ep_key, ep_images in images.items():
            video_data = []
            for sid, img_info in ep_images.items():
                video_data.append({
                    "shot_id": sid,
                    "image_path": img_info.get("filename", ""),
                    "subfolder": img_info.get("subfolder", ""),
                    "prompt_id": img_info.get("prompt_id", ""),
                })

            videos = await self.video_provider.batch_generate(video_data)

            # 处理返回结果：如果是帧列表，合成+下载
            output_dir = Path("storage/output")
            output_dir.mkdir(parents=True, exist_ok=True)

            ep_videos = {}
            for item in videos:
                sid = item.get("shot_id", "")
                frames = item.get("frames", [])
                if frames:
                    # 帧模式：在 GPU 上合成视频并下载
                    video_path = await self._sync_video_from_frames(
                        frames, sid, ep_key, output_dir
                    )
                    ep_videos[sid] = {
                        "frames": frames,
                        "total_frames": len(frames),
                        "local_path": video_path,
                        "shot_id": sid,
                    }
                else:
                    # 视频文件模式（mock 或直接返回路径）
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

    async def _sync_video_from_frames(
        self, frames: list[dict], shot_id: str, ep_key: str, output_dir: Path
    ) -> str:
        """在 GPU 上合成 PN G 帧为 MP4 并下载到本地
        输入 frames: [{"filename": "hyvideo_output_00001_.png"}, ...] 每组 25 帧
        """
        if not frames:
            return ""

        local_path = str(output_dir / f"{ep_key}_shot_{shot_id}.mp4")
        if Path(local_path).exists():
            return local_path

        fnames = [f["filename"] for f in frames]
        fps = 24
        remote_dir = GPU_OUTPUT_DIR
        remote_out = f"{remote_dir}/{ep_key}_shot_{shot_id}.mp4"

        # 在 GPU 上用 concat demuxer 合成（适用于任意编号的帧，不依赖顺序 pattern）
        concat_file = f"/tmp/concat_{ep_key}_{shot_id}.txt"
        concat_lines = "\n".join(f"file '{remote_dir}/{f}'" for f in fnames)
        
        full_cmd = (
            f"printf '%s' '{concat_lines}' > {concat_file} && "
            f"/root/miniconda3/bin/ffmpeg -y -framerate {fps} "
            f"-f concat -safe 0 -i {concat_file} "
            f"-c:v libvpx-vp9 -b:v 1M -pix_fmt yuv420p {remote_out}"
        )
        ssh_cmd = (
            f"sshpass -p '{GPU_PASS}' ssh -o StrictHostKeyChecking=no "
            f"-p {GPU_PORT} {GPU_HOST} '{full_cmd}'"
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                ssh_cmd, stdout=sp.PIPE, stderr=sp.PIPE
            )
            _, stderr = await proc.communicate(timeout=120)
            if proc.returncode != 0:
                err = stderr.decode()[-300:] if stderr else "unknown"
                logger.warning(f"[VideoGenAgent] GPU 合成失败: {err}")
                return ""
        except asyncio.TimeoutError:
            logger.warning(f"[VideoGenAgent] GPU 合成超时")
            return ""
        except Exception as e:
            logger.warning(f"[VideoGenAgent] GPU 合成异常: {e}")
            return ""

        # 下载回本地
        dl_cmd = (
            f"sshpass -p '{GPU_PASS}' scp -o StrictHostKeyChecking=no "
            f"-P {GPU_PORT} {GPU_HOST}:{remote_out} {local_path}"
        )
        try:
            proc = await asyncio.create_subprocess_shell(dl_cmd)
            await proc.communicate()
            if Path(local_path).exists():
                sz = Path(local_path).stat().st_size
                logger.info(f"[VideoGenAgent] 合成+下载完成: {local_path} ({sz/1024:.0f}KB)")
                return local_path
        except Exception as e:
            logger.warning(f"[VideoGenAgent] 下载失败: {e}")
        return ""

    async def _download_file(self, remote_path: str, local_path: str) -> str:
        """从 GPU 下载文件"""
        cmd = (
            f"sshpass -p '{GPU_PASS}' scp -o StrictHostKeyChecking=no "
            f"-P {GPU_PORT} {GPU_HOST}:{remote_path} {local_path}"
        )
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdout=sp.PIPE, stderr=sp.PIPE
            )
            await proc.communicate()
            if Path(local_path).exists():
                sz = Path(local_path).stat().st_size
                logger.info(f"[VideoGenAgent] 下载完成: {local_path} ({sz/1024:.0f}KB)")
                return local_path
        except Exception as e:
            logger.warning(f"[VideoGenAgent] 下载失败: {e}")
        return ""

    async def _download_maybe(self, fname: str, output_dir: Path) -> str:
        """尝试下载单个文件（非帧模式）"""
        if not fname:
            return ""
        local_path = str(output_dir / fname)
        if Path(local_path).exists():
            return local_path
        return await self._download_file(
            f"{GPU_OUTPUT_DIR}/{fname}", local_path
        )
