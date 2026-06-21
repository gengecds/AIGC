"""ComfyUI Provider - 通过 ComfyUI API 实现 SD 出图和视频生成"""

import logging
import json
import copy
import requests as _requests
from typing import Optional
from pathlib import Path

from providers.base import ImageProvider, VideoProvider
from providers.comfyui.client import ComfyUIClient


# 代理豁免：避免被 Grammarly 7890 本地代理拦截
def _noget(url, **kw):
    kw["proxies"] = {"http": None, "https": None}
    return _requests.get(url, **kw)

logger = logging.getLogger(__name__)


class ComfySDImageProvider(ImageProvider):
    """ComfyUI + SD 图片生成"""

    def __init__(self, client: Optional[ComfyUIClient] = None):
        from config.settings import settings
        cfg = settings.comfyui
        self.client = client or ComfyUIClient(
            server_addr=cfg.server_addr,
            server_port=cfg.server_port,
        )
        self._ckpt = "Realistic-Vision-V5.1.safetensors"

    async def generate(
        self,
        prompt: str,
        ref_image: Optional[str] = None,
        seed: Optional[int] = None,
        **kwargs,
    ) -> list[dict]:
        """单张图片生成，返回输出图片信息列表 [{filename, subfolder, type}]"""
        wf = ComfyUIClient.build_txt2img_workflow(
            ckpt_name=self._ckpt,
            prompt=prompt,
            negative_prompt=kwargs.get("negative_prompt", ""),
            width=kwargs.get("width", 512),
            height=kwargs.get("height", 512),
            seed=seed or 42,
            steps=kwargs.get("steps", 12),
            cfg=kwargs.get("cfg", 7.5),
        )
        resp = await self.client.queue_prompt(wf)
        prompt_id = resp["prompt_id"]
        result = await self.client.wait_for_completion(prompt_id)
        if not result["success"]:
            raise RuntimeError(f"ComfyUI 生成失败: {result.get('error', '?')}")
        # 提取所有输出图片的文件信息
        images = []
        for node_out in result.get("outputs", {}).values():
            for img in node_out.get("images", []):
                images.append({
                    "filename": img["filename"],
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                    "prompt_id": prompt_id,
                })
        return images

    async def batch_generate(self, shots: list[dict]) -> list[dict]:
        """批量提交 → 后台轮询收集（不阻塞主流程）"""
        import asyncio, time
        import requests

        # 1. 全部提交，不等待
        submitted = []
        for shot in shots:
            prompt = shot.get("sd_prompt", "")
            seed = max(0, shot.get("seed", 0) or 0)
            wf = ComfyUIClient.build_txt2img_workflow(
                ckpt_name=self._ckpt,
                prompt=prompt,
                negative_prompt=shot.get("negative_prompt", ""),
                width=int(shot.get("width", 512)),
                height=int(shot.get("height", 512)),
                seed=seed,
                steps=int(shot.get("steps", 12)),
                cfg=float(shot.get("cfg", 7.5)),
            )
            resp = await self.client.queue_prompt(wf)
            submitted.append({
                "prompt_id": resp["prompt_id"],
                "shot_id": shot.get("shot_id", ""),
            })

        if not submitted:
            return []

        # 2. 后台轮询完成情况
        timeout = max(30, len(submitted) * 25)
        pending_ids = {s["prompt_id"]: s for s in submitted}
        history_url = f"{self.client.base_url}/history"
        start = time.time()
        while pending_ids and (time.time() - start) < timeout:
            time.sleep(3)
            try:
                hist = _noget(history_url, timeout=5).json()
            except Exception:
                continue
            for pid in list(pending_ids.keys()):
                if pid in hist and hist[pid].get("status", {}).get("completed", False):
                    s = pending_ids.pop(pid)
                    logger.info(f"[SD] 完成: shot={s['shot_id']}")

        # 3. 收集结果
        flat = []
        for s in submitted:
            pid = s["prompt_id"]
            try:
                hist = _noget(history_url, timeout=5).json()
                if pid in hist:
                    outputs = hist[pid]["outputs"]
                    for node_out in outputs.values():
                        for img in node_out.get("images", []):
                            flat.append({
                                "filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output"),
                                "prompt_id": pid,
                                "shot_id": s["shot_id"],
                            })
            except Exception as e:
                logger.warning(f"获取结果失败: {e}")

        return flat


class ComfyHunyuanVideoProvider(VideoProvider):
    """ComfyUI + HunyuanVideo 视频生成"""

    WF_PATH = Path(__file__).parent.parent / "workflows" / "img2video_hunyuan_v2.json"

    def __init__(self, client: Optional[ComfyUIClient] = None):
        from config.settings import settings
        cfg = settings.comfyui
        self.client = client or ComfyUIClient(
            server_addr=cfg.server_addr,
            server_port=cfg.server_port,
        )
        self._workflow_template = json.loads(self.WF_PATH.read_text())

    async def _cp_to_input(self, filename: str):
        """通过 SSH 把 output/ 目录的文件复制到 input/"""
        import subprocess
        try:
            subprocess.run([
                "sshpass", "-p", "900917_19871002-Gz",
                "ssh", "-p", "30476",
                "-o", "StrictHostKeyChecking=no",
                f"root@connect.bjb2.seetacloud.com",
                f"cp /root/ComfyUI/output/{filename} /root/ComfyUI/input/{filename}"
            ], capture_output=True, timeout=10)
        except Exception as e:
            logger.warning(f"SSH cp 失败: {e}")

    async def generate(self, input_image: str, prompt: str = "", **kwargs) -> list[dict]:
        # 确保图片在 input/ 目录
        await self._cp_to_input(input_image)

        wf = {k:v for k,v in json.loads(json.dumps(self._workflow_template)).items()
              if not k.startswith("_")}
        for nid, node in wf.items():
            if isinstance(node, dict) and node.get("class_type") == "LoadImage":
                node["inputs"]["image"] = input_image
            if isinstance(node, dict) and node.get("class_type") == "HyVideoTextEncode":
                node["inputs"]["prompt"] = prompt or "cinematic motion, high quality"
        resp = await self.client.queue_prompt(wf)
        prompt_id = resp["prompt_id"]
        result = await self.client.wait_for_completion(prompt_id)
        if not result["success"]:
            raise RuntimeError(f"HunyuanVideo 失败: {result.get('error', '?')}")
        # 返回输出文件信息
        videos = []
        for node_out in result.get("outputs", {}).values():
            for img in node_out.get("images", []):
                videos.append({
                    "filename": img["filename"],
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                    "prompt_id": prompt_id,
                })
        return videos

    async def batch_generate(self, images: list[dict]) -> list[list[dict]]:
        """批量提交视频 → 后台轮询收集（不阻塞）"""
        import asyncio, time
        import requests

        # 1. 全部推入ComfyUI队列
        submitted = []
        for img_info in images:
            input_image = img_info.get("image_path", img_info.get("image", ""))
            prompt = img_info.get("prompt", img_info.get("video_motion", ""))
            await self._cp_to_input(input_image)
            wf = self._build_video_workflow(input_image, prompt)
            resp = await self.client.queue_prompt(wf)
            submitted.append({
                "prompt_id": resp["prompt_id"],
                "shot_id": img_info.get("shot_id", ""),
                "image_path": input_image,
            })

        if not submitted:
            return []

        # 2. 后台轮询完成情况
        timeout = max(120, len(submitted) * 210)  # 每个视频~3.5分钟
        pending_ids = {s["prompt_id"]: s for s in submitted}
        history_url = f"{self.client.base_url}/history"
        queue_url = f"{self.client.base_url}/queue"
        start = time.time()
        while pending_ids and (time.time() - start) < timeout:
            time.sleep(10)
            try:
                hist = _noget(history_url, timeout=5).json()
            except Exception:
                continue
            for pid in list(pending_ids.keys()):
                if pid in hist and hist[pid].get("status", {}).get("completed", False):
                    s = pending_ids.pop(pid)
                    logger.info(f"[Video] 完成: shot={s['shot_id']}")
                    try:
                        q = _noget(queue_url, timeout=3).json()
                        r = len(q.get("queue_running", []))
                        p = len(q.get("queue_pending", []))
                        logger.info(f"[Video] 队列状态: {r}运行/{p}待处理")
                    except Exception:
                        pass

        total = len(submitted)
        done = total - len(pending_ids)
        logger.info(f"[Video] 视频批量完成: {done}/{total}")

        # 3. 收集结果
        all_results = []
        for s in submitted:
            pid = s["prompt_id"]
            try:
                hist = _noget(history_url, timeout=5).json()
                if pid in hist:
                    outputs = hist[pid]["outputs"]
                    frames = []
                    for node_out in outputs.values():
                        for img in node_out.get("images", []):
                            frames.append({
                                "filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output"),
                                "prompt_id": pid,
                                "shot_id": s["shot_id"],
                            })
                    # 把25帧打包为一个视频条目
                    # 排序确保帧顺序
                    frames.sort(key=lambda x: x["filename"])
                    all_results.append({
                        "shot_id": s["shot_id"],
                        "frames": frames,
                        "prompt_id": pid,
                        "total_frames": len(frames),
                    })
            except Exception as e:
                logger.warning(f"收集视频结果失败: {e}")

        return all_results

    def _build_video_workflow(self, input_image: str, prompt: str) -> dict:
        """构建 HyVideo 工作流（不含 LLM 调用，去除元数据字段）"""
        wf = {k: copy.deepcopy(v) for k, v in self._workflow_template.items()
              if not k.startswith("_")}
        for nid, node in wf.items():
            if isinstance(node, dict) and node.get("class_type") == "LoadImage":
                node["inputs"]["image"] = input_image
            if isinstance(node, dict) and node.get("class_type") == "HyVideoTextEncode":
                node["inputs"]["prompt"] = prompt or "cinematic motion, high quality"
        return wf
