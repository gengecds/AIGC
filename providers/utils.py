#!/usr/bin/env python3
"""
工具函数：帧图片 → 视频（FFmpeg）
"""

import logging
import subprocess as sp
from pathlib import Path

logger = logging.getLogger(__name__)

def frames_to_video(frame_files: list, output_path: str, fps: int = 24) -> str:
    """将 PNG 帧序列合成为 MP4 视频

    Args:
        frame_files: 帧文件名列表（已排序）
        output_path: 输出 MP4 路径
        fps: 帧率，默认 24

    Returns:
        输出视频路径，失败返回空字符串
    """
    if not frame_files:
        logger.warning("frames_to_video: 无帧输入")
        return ""

    # 找帧文件的实际路径（ComfyUI output 目录或本地路径）
    frame_paths = []
    for fname in frame_files:
        # 尝试多个可能路径
        for base in [
            "/root/ComfyUI/output",
            "/root/ComfyUI/input",
        ]:
            p = Path(base) / fname
            # 需要实际存在
            frame_paths.append(fname)
            break

    # 写入 concat 列表或直接用 pattern
    import os

    # 方法1: concat demuxer（需要实际文件列表）
    # 简化：直接通过 SSH 在 GPU 上合成
    return _fallback_concat(frame_files, output_path, fps)

def _fallback_concat(frame_files: list, output_path: str, fps: int) -> str:
    """在 GPU 机上用 FFmpeg 合成帧→视频"""
    if not frame_files:
        return ""

    out = Path(output_path)
    # 确保文件名安全
    pattern = frame_files[0].rsplit("_", 1)[0] + "_%05d.png"
    output_remote = f"/root/ComfyUI/output/{out.name}"

    cmd = [
        "sshpass", "-p", "900917_19871002-Gz",
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-p", "30476",
        "root@connect.bjb2.seetacloud.com",
        f"cd /root/ComfyUI/output && "
        f"ffmpeg -y -framerate {fps} -pattern_type sequence -start_number 1 "
        f"-i \"{pattern}\" "
        f"-c:v libx264 -preset fast -pix_fmt yuv420p "
        f"{output_remote} 2>/dev/null && ls {output_remote}"
    ]

    try:
        result = sp.run(" ".join(cmd), shell=True, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            # 从 GPU 下载回来
            dl_cmd = [
                "sshpass", "-p", "900917_19871002-Gz",
                "scp", "-o", "StrictHostKeyChecking=no",
                "-P", "30476",
                f"root@connect.bjb2.seetacloud.com:{output_remote}",
                str(out),
            ]
            sp.run(dl_cmd, capture_output=True, timeout=30)
            if out.exists():
                logger.info(f"帧→视频合成成功: {out} ({out.stat().st_size / 1024:.0f}KB)")
                return str(out)
    except Exception as e:
        logger.warning(f"GPU 帧→视频合成失败: {e}")

    return ""

def download_from_gpu(filename: str, local_dir: str = "storage/output") -> str:
    """从 GPU 机下载文件到本地"""
    import subprocess
    local_path = Path(local_dir) / filename
    local_path.parent.mkdir(parents=True, exist_ok=True)

    # 尝试多个远程位置
    for remote_base in ["/root/ComfyUI/output", "/root/ComfyUI/input"]:
        cmd = [
            "sshpass", "-p", "900917_19871002-Gz",
            "scp", "-o", "StrictHostKeyChecking=no",
            "-P", "30476",
            f"root@connect.bjb2.seetacloud.com:{remote_base}/{filename}",
            str(local_path),
        ]
        try:
            sp.run(cmd, capture_output=True, timeout=30)
            if local_path.exists():
                return str(local_path)
        except Exception:
            continue

    return ""
