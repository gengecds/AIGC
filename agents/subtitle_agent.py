"""Agent 6 - 字幕生成（FFmpeg SRT + drawtext）

根据剧本对话 + 分镜时长 → 生成SRT字幕文件
"""

import logging
from datetime import datetime

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)


class SubtitleAgent(Agent):
    """Agent 6：字幕生成"""

    name = "subtitle_agent"

    def __init__(self):
        super().__init__(name="subtitle_agent")

    async def run(self, script: dict, storyboard: dict,
                  output_dir: str = "storage/output") -> AgentResult:
        logger.info("[SubtitleAgent] 生成字幕")

        episodes = storyboard.get("data", {}).get("episodes", []) or storyboard.get("episodes", [])
        script_episodes = script.get("episodes", [])
        all_srt = []

        for ep in episodes:
            ep_num = ep.get("episode_number", 1)
            shots = ep.get("shots", [])
            srt_lines = []
            time_cursor = 0  # 秒

            for shot in shots:
                dur = shot.get("duration", 5)
                dialogue = shot.get("dialogue", "")

                if dialogue:
                    start_s = time_cursor
                    end_s = time_cursor + dur

                    def fmt_time(seconds: int) -> str:
                        h = seconds // 3600
                        m = (seconds % 3600) // 60
                        s = seconds % 60
                        return f"{h:02d}:{m:02d}:{s:02d},000"

                    srt_lines.append(f"{len(srt_lines) + 1}")
                    srt_lines.append(
                        f"{fmt_time(start_s)} --> {fmt_time(end_s)}"
                    )
                    srt_lines.append(dialogue)
                    srt_lines.append("")

                time_cursor += dur

            srt_content = "\n".join(srt_lines)
            srt_path = f"{output_dir}/ep_{ep_num}.srt"
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(srt_content)

            all_srt.append({
                "episode_number": ep_num,
                "srt_path": srt_path,
            })
            logger.info(f"[SubtitleAgent] 字幕已生成: {srt_path}")

        result = AgentResult(
            success=True,
            data={"subtitles": all_srt},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "files": len(all_srt),
            },
        )
        return result
