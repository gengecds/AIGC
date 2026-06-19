"""Agent 7 - 视频合成 + 发布（骨架，Phase 3 完善）

视频分段 + SRT字幕 → FFmpeg合成 → 发布
"""

import logging
import subprocess as sp
from datetime import datetime
from pathlib import Path

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)


class ComposeAgent(Agent):
    """Agent 7：视频合成 + 发布"""

    name = "compose_agent"

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        super().__init__(name="compose_agent")
        self.ffmpeg = ffmpeg_path

    async def run(self, videos_result, subtitle_result,
                  output_dir: str = "storage/output") -> AgentResult:
        logger.info("[ComposeAgent] 合成视频")

        # 兼容 AgentResult 对象和字典
        def _get_data(obj):
            if hasattr(obj, 'data'):
                return obj.data or {}
            return obj.get("data", {})

        videos = _get_data(videos_result).get("videos", {})
        subs = _get_data(subtitle_result).get("subtitles", [])
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        published = []

        for sub in subs:
            ep_num = sub["episode_number"]
            srt_path = sub["srt_path"]
            ep_key = f"ep_{ep_num}"
            ep_videos = videos.get(ep_key, {})

            if not ep_videos:
                logger.warning(f"[ComposeAgent] ep_{ep_num} 无视频分段，跳过")
                continue

            # 生成 ffmpeg concat list
            concat_file = output_path / f"concat_ep{ep_num}.txt"
            video_paths = []
            # 按 shot_id 排序
            sorted_shots = sorted(
                ep_videos.items(),
                key=lambda x: int(x[0]) if x[0].isdigit() else 0,
            )
            for shot_id, video_path in sorted_shots:
                if video_path and Path(video_path).exists():
                    video_paths.append(video_path)

            if not video_paths:
                logger.warning(f"[ComposeAgent] ep_{ep_num} 无可用视频文件")
                continue

            # 写入 concat 列表
            with open(concat_file, "w") as f:
                for vp in video_paths:
                    f.write(f"file '{Path(vp).absolute()}'\n")

            # FFmpeg 合成（视频 + 字幕）
            final_path = output_path / f"ep_{ep_num}_final.mp4"
            cmd = [
                self.ffmpeg, "-f", "concat", "-safe", "0",
                "-i", str(concat_file),
                "-vf", f"subtitles={srt_path}",
                "-c:v", "libx264", "-preset", "fast",
                "-y", str(final_path),
            ]

            try:
                sp.run(cmd, check=True, capture_output=True, text=True)
                published.append({
                    "episode_number": ep_num,
                    "final_path": str(final_path),
                    "segments": len(video_paths),
                })
                logger.info(f"[ComposeAgent] 合成完成: {final_path}")
            except sp.CalledProcessError as e:
                logger.error(f"[ComposeAgent] FFmpeg 失败: {e.stderr}")

        result = AgentResult(
            success=True,
            data={"published": published},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "episodes": len(published),
            },
        )
        return result
