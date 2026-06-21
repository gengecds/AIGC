"""结果回传模块 - 从远端 ComfyUI 拉取生成结果到本地 storage/"""

import asyncio, logging, os, re
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "storage" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class ResultFetcher:
    """从 ComfyUI HTTP API 下载图片/视频到本地 output 目录"""

    def __init__(self, server_addr: str = "127.0.0.1",
                 server_port: int = 8188):
        self.base_url = f"http://{server_addr}:{server_port}"
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(60))

    async def fetch_image(self, filename: str, subfolder: str = "",
                          img_type: str = "output",
                          local_name: Optional[str] = None) -> str:
        """下载单张图片，返回本地路径"""
        params = {"filename": filename, "subfolder": subfolder, "type": img_type}
        resp = await self._http.get(f"{self.base_url}/view", params=params)
        resp.raise_for_status()

        local_name = local_name or filename
        local_path = OUTPUT_DIR / local_name
        local_path.write_bytes(resp.content)
        logger.info(f"[Fetch] {filename} -> {local_path} ({len(resp.content)/1024:.0f}KB)")
        return str(local_path)

    async def fetch_video(self, filename: str, subfolder: str = "",
                          img_type: str = "output",
                          local_name: Optional[str] = None) -> str:
        """下载视频文件"""
        return await self.fetch_image(filename, subfolder, img_type, local_name)

    async def fetch_results(self, results: dict, prefix: str = "shot") -> dict:
        """从 pipeline results 中提取所有 ComfyCI 输出并下载

        results 结构:
            image_agent: {data: {images: {ep_1: {shot_id: {filename,subfolder,type}}}}}
            video_agent: {data: {videos: {ep_1: {shot_id: [{filename,...}]}}}}
        返回映射: {ep_1: {shot_1: {local_path: ..., filename: ...}}}
        """
        output = {}

        # 下载图片
        images = (
            results.get("image_agent", {}).get("data", {}).get("images", {})
            or results.get("image_agent", {}).get("images", {})
        )
        for ep_key, ep_images in images.items():
            if ep_key not in output:
                output[ep_key] = {}
            for sid, img_info in ep_images.items():
                if isinstance(img_info, dict) and "filename" in img_info:
                    local = await self.fetch_image(
                        filename=img_info["filename"],
                        subfolder=img_info.get("subfolder", ""),
                        img_type=img_info.get("type", "output"),
                        local_name=f"{prefix}_{ep_key}_{sid}.png",
                    )
                    output[ep_key][sid] = {"local_path": local, **img_info}

        # 下载视频
        videos = (
            results.get("video_agent", {}).get("data", {}).get("videos", {})
            or results.get("video_agent", {}).get("videos", {})
        )
        for ep_key, ep_videos in videos.items():
            if ep_key not in output:
                output[ep_key] = {}
            for sid, vid_list in ep_videos.items():
                # vid_list 可以是单个 dict 或 list of dict
                if isinstance(vid_list, list):
                    for vi in vid_list:
                        if isinstance(vi, dict) and "filename" in vi:
                            local = await self.fetch_video(
                                filename=vi["filename"],
                                subfolder=vi.get("subfolder", ""),
                                img_type=vi.get("type", "output"),
                                local_name=f"{prefix}_{ep_key}_{sid}.mp4",
                            )
                            output[ep_key][sid + "_video"] = {"local_path": local, **vi}
                elif isinstance(vid_list, dict) and "filename" in vid_list:
                    local = await self.fetch_video(
                        filename=vid_list["filename"],
                        subfolder=vid_list.get("subfolder", ""),
                        img_type=vid_list.get("type", "output"),
                        local_name=f"{prefix}_{ep_key}_{sid}.mp4",
                    )
                    output[ep_key][sid + "_video"] = {"local_path": local, **vid_list}

        return output

    async def close(self):
        await self._http.aclose()
