"""Agent 7a - 视频合成（从 ComposeAgent 拆分出）

视频分段 + SRT字幕 → FFmpeg合成 → 输出完整 MP4
"""

import logging
import subprocess as sp
from datetime import datetime
from pathlib import Path

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)


class VideoComposeAgent(Agent):
    """Agent 7a：视频合成"""

    name = "video_compose_agent"

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        super().__init__(name="video_compose_agent")
        self.ffmpeg = ffmpeg_path

    async def run(self, videos_result, subtitle_result,
                  output_dir: str = "storage/output") -> AgentResult:
        logger.info("[VideoComposeAgent] 合成视频")

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
                logger.warning(f"[VideoComposeAgent] ep_{ep_num} 无视频分段，跳过")
                continue

            concat_file = output_path / f"concat_ep{ep_num}.txt"
            video_paths = []
            sorted_shots = sorted(
                ep_videos.items(),
                key=lambda x: int(x[0]) if x[0].isdigit() else 0,
            )
            for shot_id, video_info in sorted_shots:
                if not video_info:
                    continue
                if isinstance(video_info, dict):
                    path = video_info.get("local_path", video_info.get("filename", ""))
                else:
                    path = str(video_info)
                if path and Path(path).exists():
                    video_paths.append(path)

            if not video_paths:
                logger.warning(f"[VideoComposeAgent] ep_{ep_num} 无可用视频文件")
                continue

            is_mock = False
            if video_paths:
                first_vid = Path(video_paths[0])
                if first_vid.stat().st_size < 1024:
                    is_mock = True

            final_path = output_path / f"ep_{ep_num}_final.mp4"

            if is_mock:
                duration = len(video_paths) * 5
                cmd = [
                    self.ffmpeg, "-f", "lavfi",
                    "-i", f"color=c=0x1a1a2e:s=1920x1080:d={duration}:r=24",
                    "-c:v", "libx264", "-preset", "fast",
                    "-y", str(final_path),
                ]
            else:
                with open(concat_file, "w") as f:
                    for vp in video_paths:
                        f.write(f"file '{Path(vp).absolute()}'\n")
                cmd = [
                    self.ffmpeg, "-f", "concat", "-safe", "0",
                    "-i", str(concat_file),
                    "-c:v", "libx264", "-preset", "fast",
                    "-y", str(final_path),
                ]

            # 叠加字幕
            has_srt = Path(srt_path).exists() and Path(srt_path).stat().st_size > 0
            if has_srt and not is_mock:
                subtitle_cmd = [
                    self.ffmpeg, "-y",
                    "-i", str(final_path),
                    "-vf", f"subtitles={srt_path}",
                    "-c:a", "copy",
                    str(output_path / f"ep_{ep_num}_with_sub.mp4"),
                ]
                try:
                    sp.run(subtitle_cmd, check=True, capture_output=True, text=True)
                    final_with_sub = str(output_path / f"ep_{ep_num}_with_sub.mp4")
                    published.append({
                        "episode_number": ep_num,
                        "final_path": final_with_sub,
                        "segments": len(video_paths),
                        "has_subtitles": True,
                    })
                    logger.info(f"[VideoComposeAgent] 合成完成（含字幕）: {final_with_sub}")
                    continue
                except sp.CalledProcessError:
                    logger.warning("[VideoComposeAgent] 字幕叠加失败，输出无字幕版")

            try:
                sp.run(cmd, check=True, capture_output=True, text=True)
                published.append({
                    "episode_number": ep_num,
                    "final_path": str(final_path),
                    "segments": len(video_paths),
                    "has_subtitles": False,
                })
                logger.info(f"[VideoComposeAgent] 合成完成: {final_path}")
            except sp.CalledProcessError as e:
                logger.error(f"[VideoComposeAgent] FFmpeg 失败: {e.stderr}")

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
