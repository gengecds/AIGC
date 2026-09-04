#!/usr/bin/env python3
"""LTX-2.3 MLX 批量 9:16 镜头 demo（一次性脚本，验证 Provider 批量链路）

输入：frontend/prototype-assets 里 3 张真实角色/人物图（竖构图偏多）
输出：storage/output/ltx2_shot_<n>_*.mp4，3 段 576x1024 9:16 竖屏
耗时：单段约 12-15 分钟，3 段串行约 40 分钟（24GB 内存限制不能并发）
"""
import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")

sys.path.insert(0, "/Users/a715/git/AIGC")
from providers.ltx_mlx_provider import LTXMLXVideoProvider

# 3 个待生成长镜头（图 + 分镜级运动提示词）
SHOTS = [
    {"shot_id": "s1", "image_path": "/Users/a715/git/AIGC/frontend/prototype-assets/char_girl2.jpg",
     "prompt": "slow camera push-in toward the girl, hair and ribbon gently swaying, "
               "soft cinematic light, anime style, high detail",
     "duration": 4},
    {"shot_id": "s2", "image_path": "/Users/a715/git/AIGC/frontend/prototype-assets/char_girl3.jpg",
     "prompt": "gentle head turn with a warm smile, hair flowing softly, "
               "bright clean background, anime style, high detail",
     "duration": 4},
    {"shot_id": "s3", "image_path": "/Users/a715/git/AIGC/frontend/prototype-assets/actress1.jpg",
     "prompt": "slow subtle push-in, shoulders relaxing, natural breathing motion, "
               "cinematic lighting, realistic style, high detail",
     "duration": 4},
]


async def main() -> None:
    provider = LTXMLXVideoProvider()
    # 逐张打印进度（async for 行日志由 provider 内部处理）
    results = await provider.batch_generate(SHOTS)
    print("\n=== 批量完成 ===")
    for r in results:
        print(r.get("shot_id"), "->", r.get("local_path", r.get("filename")))


if __name__ == "__main__":
    asyncio.run(main())
