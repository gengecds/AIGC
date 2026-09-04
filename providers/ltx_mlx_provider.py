"""LTX-2.3 MLX 图生视频 Provider（直接调 ltx-2-mlx CLI，不走 ComfyUI）

为什么单独做一个 Provider？
  - ltx-2-mlx 是 Apple Silicon 原生的 MLX 推理引擎（dgrauet/ltx-2-mlx），
    比 ComfyUI 走 PyTorch-MPS 快近一倍、int4 量化只占 ~11GB 内存，
    还支持更高分辨率。ComfyUI 里的 LTX 是老版 2B，质量不如 2.3。
  - 本 Provider 把 ltx-2-mlx 的 CLI 封装成项目 VideoProvider 接口，
    video_agent 无感切换：风格/配置选 ltx_mlx 引擎即走这里。

与 ComfyUI 类 Provider 的差异：
  - generate()/batch_generate() 直接在本地跑子进程（asyncio），输出 mp4
    落在 storage/output，无需轮询 /history。
  - 默认按 9:16 竖屏（576x1024）输出，适配漫剧/短视频；输入图先 cover 裁剪，
    避免模型拉伸变形。

内存纪律：M4 24GB 上单段在跑时内存/swap 几乎打满，所以 batch 只串行
（每段一个接一个），不做并发——并发会直接 OOM 挤爆 swap。
"""

import asyncio
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from providers.base import VideoProvider

logger = logging.getLogger(__name__)

# ltx-2-mlx 引擎的 .venv 路径与模型目录（集中在一处，便于改环境时维护）
LTX_MLX_VENV = "/Users/a715/git/ltx-2-mlx/.venv/bin/ltx-2-mlx"
LTX_MLX_MODEL = "/Users/a715/git/AIGC/storage/models/ltx-2.3-mlx-q4"
OUTPUT_DIR = Path("/Users/a715/git/AIGC/storage/output")

# 9:16 竖屏默认画布（必须 64 的倍数，ltx-2-mlx 内部对画布有此约束）
DEFAULT_WIDTH = 576
DEFAULT_HEIGHT = 1024
# distilled 引擎在 24GB 机器上的安全帧数上限（越多越慢越吃内存）
MAX_FRAMES = 97
FPS = 24


class LTXMLXVideoProvider(VideoProvider):
    """本地 ltx-2-mlx CLI 图生视频引擎。"""

    video_model_type = "ltx_mlx"

    # 子进程环境：必须清掉 7890 代理变量 + 关 NO_PROXY，否则 huggingface/
    # 大文件走代理会卡死；同时关掉可能存在的 nohup 继承干扰。
    _ENV = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    _ENV["NO_PROXY"] = "*"
    _ENV["no_proxy"] = "*"

    def __init__(self, cli_path: str = LTX_MLX_VENV, model_path: str = LTX_MLX_MODEL):
        """参数都带默认值：正常只需无参实例化；测试/换机时可按路径注入。"""
        self.cli_path = cli_path
        self.model_path = model_path

    # ── 内部工具 ──────────────────────────────────────────

    def _to_9x16_input(self, src: str) -> str:
        """把任意宽高比图片 cover 裁剪成 576x1024，返回新图路径。

        为什么 cover 而非直接拉伸？LTX 图生视频会按画布等比 resize 输入图，
        宽高比不一致会被硬拉变形；先裁成和目标一致的比例就不会变形。
        """
        from PIL import Image

        im = Image.open(src).convert("RGB")
        w, h = DEFAULT_WIDTH, DEFAULT_HEIGHT
        scale = max(w / im.width, h / im.height)          # 等比放大到铺满目标框
        im = im.resize((round(im.width * scale), round(im.height * scale)),
                       Image.LANCZOS)
        left = (im.width - w) // 2
        top = (im.height - h) // 2
        im = im.crop((left, top, left + w, top + h))
        out = OUTPUT_DIR / "ltx_input_9x16.png"
        im.save(out)
        return str(out)

    def _build_cmd(self, input_img: str, prompt: str, frames: int, output: str) -> list[str]:
        """拼出 ltx-2-mlx generate 子进程命令。

        固定蒸馏模式 --distilled + --low-ram：
          - --distilled 用官方 22B 蒸馏版，几秒级可用、画质与速度的平衡点；
          - --low-ram 让 transformer 分块从磁盘流式加载，防 24GB 内存 OOM；
          - --image 传 9:16 图 → I2V（图生视频）；
          - 输出自带 24fps + 原生音频轨（LTX-2 全模态）。
        """
        return [
            self.cli_path, "generate",
            "--prompt", prompt,
            "--image", input_img,
            "--distilled", "--low-ram",
            "-W", str(DEFAULT_WIDTH), "-H", str(DEFAULT_HEIGHT),
            "-f", str(frames), "--frame-rate", str(FPS),
            "--model", self.model_path,
            "-o", output,
        ]

    # ── VideoProvider 接口 ────────────────────────────────

    async def generate(self, input_image: str, prompt: str,
                       duration: int = 5, **kwargs) -> str:
        """单段图生视频：返回本地 mp4 绝对路径。"""
        shot_id = kwargs.get("shot_id", "")
        frames = min(int(kwargs.get("frames", 0) or 0) or round(float(duration) * FPS),
                     MAX_FRAMES)
        # 转 9:16 输入图（避免变形）
        local_img = self._to_9x16_input(input_image)
        # 输出文件名带时间戳，避免同名覆盖（批量/多次跑）
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = OUTPUT_DIR / f"ltx2_{shot_id or 'shot'}_{ts}.mp4"

        cmd = self._build_cmd(local_img, prompt, frames, str(out))
        logger.info(f"[LTXMLX] 生成 shot={shot_id} frames={frames} -> {out}")

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=self._ENV,
        )
        # 边跑边打日志，方便看进度/排查
        async for line in proc.stdout:
            logger.info(f"[LTXMLX] {line.decode(errors='ignore').rstrip()}")
        await proc.wait()
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"LTXMLX 生成失败 shot={shot_id} rc={proc.returncode}")
        return str(out)

    async def batch_generate(self, items: list[dict], **kwargs) -> list[dict]:
        """批量：串行逐段生成（内存限制，不能并发）。"""
        results = []
        for item in items:
            sid = item.get("shot_id", "")
            img = item.get("image_path", item.get("input_image", ""))
            prompt = item.get("prompt", item.get("video_motion", "")) or (
                "subtle camera push-in, gentle natural motion, cinematic lighting")
            duration = float(item.get("duration", 0) or 0) or 4.0
            try:
                path = await self.generate(
                    input_image=img, prompt=prompt, duration=duration,
                    shot_id=sid,
                    frames=item.get("length"),
                )
                results.append({
                    "shot_id": sid, "filename": path, "type": "mp4",
                    "local_path": path,
                })
            except Exception as e:  # 单段失败不中断整批，记日志继续
                logger.warning(f"[LTXMLX] 段失败 shot={sid}: {e}")
        return results
