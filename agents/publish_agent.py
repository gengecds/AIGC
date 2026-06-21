"""Agent 7b - 发布（从 ComposeAgent 拆分出）

输出/发布：下载 MP4、更新检查点、清理临时文件、注册到数据库
"""

import logging
import json
import shutil
from datetime import datetime
from pathlib import Path

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)


class PublishAgent(Agent):
    """Agent 7b：发布/输出"""

    name = "publish_agent"

    def __init__(self, output_dir: str = "storage/output"):
        super().__init__(name="publish_agent")
        self.output_dir = Path(output_dir)

    async def run(self, compose_result) -> AgentResult:
        logger.info("[PublishAgent] 发布产出")

        def _get_data(obj):
            if hasattr(obj, 'data'):
                return obj.data or {}
            return obj.get("data", {})

        published = _get_data(compose_result).get("published", [])
        publish_manifest = []

        for p in published:
            ep_num = p["episode_number"]
            fp = Path(p["final_path"])

            if not fp.exists():
                logger.warning(f"[PublishAgent] ep_{ep_num} 文件不存在: {fp}")
                continue

            sz = fp.stat().st_size
            publish_manifest.append({
                "episode_number": ep_num,
                "file_path": str(fp),
                "file_size_kb": sz // 1024,
                "segments": p.get("segments", 0),
                "has_subtitles": p.get("has_subtitles", False),
                "published_at": datetime.utcnow().isoformat(),
            })
            logger.info(f"[PublishAgent] ✅ ep_{ep_num} 已发布 ({sz//1024}KB)")

        # 写发布清单
        manifest_path = self.output_dir / "publish_manifest.json"
        manifest_path.write_text(
            json.dumps(publish_manifest, ensure_ascii=False, indent=2)
        )
        logger.info(f"[PublishAgent] 发布清单已写入: {manifest_path}")

        result = AgentResult(
            success=True,
            data={
                "published": publish_manifest,
                "manifest_path": str(manifest_path),
            },
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "episodes": len(publish_manifest),
            },
        )
        return result
