"""Agent 7 - 视频合成 + 发布（已拆分为 VideoComposeAgent + PublishAgent）

保留此文件作为兼容代理，内部委托给拆分后的 Agent。
"""

import logging

from agents.video_compose_agent import VideoComposeAgent
from agents.publish_agent import PublishAgent
from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)


class ComposeAgent(Agent):
    """Agent 7：已拆分为 VideoComposeAgent + PublishAgent"""

    name = "compose_agent"

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        super().__init__(name="compose_agent")
        self.composer = VideoComposeAgent(ffmpeg_path)
        self.publisher = PublishAgent()

    async def run(self, videos_result, subtitle_result,
                  output_dir: str = "storage/output") -> AgentResult:
        logger.info("[ComposeAgent] 委托 VideoComposeAgent + PublishAgent")
        r1 = await self.composer.run(videos_result, subtitle_result, output_dir)
        if not r1.success:
            return r1
        r2 = await self.publisher.run(r1)
        return r2
