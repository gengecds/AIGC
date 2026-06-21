"""Mock Provider — 零API/GPU费用开发模式

继承 ImageProvider / VideoProvider / LLMProvider 抽象基类。
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from providers.base import ImageProvider, VideoProvider, LLMProvider


MOCK_SCRIPT_DATA = {
    "title": "程序员猫娘逃生记",
    "genre": "奇幻/科幻/搞笑",
    "summary": "一个程序员穿越到猫娘世界，被猫娘公主关在城堡里当宠物，最终用Java代码逃出生天",
    "characters": [
        {"name": "小林", "gender": "男", "age": "青年", "appearance": "戴眼镜，微胖，格子衬衫，程序员的标志性造型", "personality": "聪明机智但有点宅", "role": "主角"},
        {"name": "雪莉", "gender": "女", "age": "青年", "appearance": "白色猫耳，蓝色眼睛，粉色和服，优雅高贵", "personality": "傲娇但善良", "role": "女主角"},
        {"name": "小黑", "gender": "男", "age": "少年", "appearance": "黑色猫耳，黄色眼睛，管家服，总是面无表情", "personality": "忠诚沉默", "role": "配角"}
    ],
    "episodes": [
        {
            "episode_number": 1,
            "title": "穿越！猫娘世界",
            "plot": "小林在写bug时意外穿越到猫娘世界，被公主雪莉捡到",
            "dialogues": [
                {"scene": "城堡大厅", "character": "小林", "line": "这是哪？我刚才还在debug……"},
                {"scene": "城堡大厅", "character": "雪莉", "line": "人类！你为什么会出现在我的城堡？"},
                {"scene": "城堡大厅", "character": "小林", "line": "我也想知道啊！我是程序员，不是什么间谍！"}
            ]
        },
        {
            "episode_number": 2,
            "title": "逃出生天！",
            "plot": "小林用Java代码黑入猫娘世界的系统，打开了城堡大门",
            "dialogues": [
                {"scene": "书房", "character": "小林", "line": "这个世界的系统竟然是Java写的……"},
                {"scene": "书房", "character": "小黑", "line": "你想做什么？"},
                {"scene": "城堡大门", "character": "雪莉", "line": "你……你竟然真的逃出去了！"}
            ]
        }
    ]
}

logger = logging.getLogger(__name__)


class MockImageProvider(ImageProvider):
    """Mock 出图——生成纯色占位图"""

    def __init__(self, output_dir: str = "storage/output"):
        self.output_dir = output_dir
        logger.info("[MockImageProvider] 已初始化，0 API 费用")

    async def generate(self, prompt: str,
                       ref_image: Optional[str] = None,
                       seed: Optional[int] = None,
                       **kwargs) -> str:
        """生成一张纯色占位图"""
        out = Path(self.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        shot_id = kwargs.get("shot_id", "mock")
        filename = out / f"shot_{shot_id}.png"
        # 生成 512×512 纯色 PNG
        import struct, zlib

        def _create_png(w, h, r, g, b):
            def chunk(ctype, data):
                c = ctype + data
                return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
            ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
            raw = b""
            for _ in range(h):
                raw += b"\x00" + bytes([r, g, b]) * w
            return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

        seed_val = (seed or hash(prompt)) & 0xFFFFFF
        r = seed_val >> 16 & 0xFF
        g = seed_val >> 8 & 0xFF
        b = seed_val & 0xFF
        with open(filename, "wb") as f:
            f.write(_create_png(128, 128, max(r, 30), max(g, 30), max(b, 30)))
        logger.info(f"[MockImageProvider] 生成占位图: {filename} ({r:02x}{g:02x}{b:02x})")
        return str(filename)


class MockVideoProvider(VideoProvider):
    """Mock 视频——生成极短占位视频"""

    def __init__(self, output_dir: str = "storage/output",
                 ffmpeg_path: str = "ffmpeg"):
        self.output_dir = output_dir
        self.ffmpeg = ffmpeg_path
        logger.info("[MockVideoProvider] 已初始化，0 API 费用")

    async def generate(self, input_image: str,
                       prompt: str,
                       duration: int = 5,
                       **kwargs) -> str:
        """生成 1 秒纯色占位视频"""
        out = Path(self.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        shot_id = kwargs.get("shot_id", "mock")
        filename = out / f"shot_{shot_id}.mp4"

        import subprocess as sp
        cmd = [
            self.ffmpeg, "-f", "lavfi",
            "-i", f"color=c=0x1a1a2e:s=640x480:d=1:r=10",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-y", str(filename),
        ]
        try:
            sp.run(cmd, check=True, capture_output=True, text=True)
        except Exception as e:
            logger.warning(f"[MockVideoProvider] FFmpeg 失败: {e}，创建空文件")
            filename.write_bytes(b"\x00" * 512)

        logger.info(f"[MockVideoProvider] 生成占位视频: {filename}")
        return str(filename)


MOCK_STORYBOARD_DATA = [
    {"shot_id": 1, "shot_type": "远", "description": "城堡外观，宏伟的中世纪建筑，周围环绕着樱花树", "scene": "城堡外景", "action": "小林被猫娘士兵押送到城堡", "duration": 5, "sd_prompt": "宏伟的中世纪城堡，周围樱花飘落，奇幻风格，阳光明媚，杰作"},
    {"shot_id": 2, "shot_type": "中", "description": "雪莉公主坐在王座上，优雅地喝茶", "scene": "城堡大厅", "action": "雪莉看着被押进来的小林", "duration": 4, "sd_prompt": "白色猫耳少女身穿粉色和服，坐在华丽王座上喝茶，傲娇表情，杰作"},
    {"shot_id": 3, "shot_type": "近", "description": "小林被绑着，一脸懵逼", "scene": "城堡大厅", "action": "小林难以置信地看着猫娘们", "duration": 3, "sd_prompt": "戴眼镜的年轻男性被绳子绑着，一脸震惊，格子衬衫，背景华丽城堡大厅"},
    {"shot_id": 4, "shot_type": "特写", "description": "笔记本屏幕显示代码，周围环境模糊", "scene": "城堡书房", "action": "小林偷偷用笔记本编程", "duration": 5, "sd_prompt": "笔记本屏幕上显示Java代码，手指在键盘上快速敲击，键盘特写"},
    {"shot_id": 5, "shot_type": "远", "description": "城堡大门缓缓打开，小林逃跑的背影", "scene": "城堡大门", "action": "小林趁乱逃出城堡", "duration": 6, "sd_prompt": "城堡大门打开，一个年轻人回头看了一下然后跑出去，夕阳剪影，杰作"}
]


class MockLLMProvider(LLMProvider):
    """Mock LLM——根据 system_prompt 返回合适的测试数据"""

    async def generate(self, prompt: str,
                       system_prompt: Optional[str] = None,
                       model: Optional[str] = None,
                       **kwargs) -> str:
        # 根据 system_prompt 判断该返回什么
        if system_prompt and "分镜" in system_prompt:
            return json.dumps(MOCK_STORYBOARD_DATA, ensure_ascii=False, indent=2)
        return json.dumps(MOCK_SCRIPT_DATA, ensure_ascii=False, indent=2)

    async def chat(self, messages: list,
                   model: Optional[str] = None,
                   **kwargs) -> str:
        last = messages[-1].get("content", "") if messages else ""
        # 检查 system message 中是否有"分镜"关键词
        sys_msg = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        if "分镜" in sys_msg:
            return json.dumps(MOCK_STORYBOARD_DATA, ensure_ascii=False, indent=2)
        return json.dumps(MOCK_SCRIPT_DATA, ensure_ascii=False, indent=2)

