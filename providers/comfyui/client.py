"""ComfyUIClient - ComfyUI REST API + WebSocket 封装"""

import json
import uuid
import asyncio
import logging
from typing import Optional, Callable, Awaitable, Dict, List
from pathlib import Path
from urllib.parse import urljoin

import httpx
import websockets

logger = logging.getLogger(__name__)


class ComfyUIClient:
    """ComfyUI 客户端，管理工作流提交/监听/下载"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8188,
                 output_dir: str = "storage/output"):
        self.base_url = f"http://{host}:{port}"
        self.ws_url = f"ws://{host}:{port}/ws"
        self.client_id = str(uuid.uuid4())
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 异步回调队列：已完成的分段立即下载落盘
        self._completed_queue: asyncio.Queue = asyncio.Queue()
        self._progress_callback: Optional[Callable[[int, int, str], Awaitable[None]]] = None

    def set_progress_callback(self, cb: Callable[[int, int, str], Awaitable[None]]):
        """设置进度回调：completed/total/shot_id"""
        self._progress_callback = cb

    # ── 基础 API ────────────────────────────

    async def queue_workflow(self, workflow: dict) -> str:
        """提交工作流，返回 prompt_id"""
        payload = {"prompt": workflow, "client_id": self.client_id}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                urljoin(self.base_url, "/prompt"),
                json=payload, timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            prompt_id = data.get("prompt_id", "")
            logger.info(f"工作流已提交: prompt_id={prompt_id}")
            return prompt_id

    async def upload_image(self, image_path: str, image_type: str = "input") -> dict:
        """上传图片到 ComfyUI"""
        import aiofiles
        async with httpx.AsyncClient() as client:
            async with aiofiles.open(image_path, "rb") as f:
                files = {
                    "image": (image_path.split("/")[-1], await f.read(), "image/png"),
                }
            resp = await client.post(
                urljoin(self.base_url, "/upload/image"),
                files=files,
                data={"type": image_type, "overwrite": "true"},
                timeout=120,
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"图片已上传: {result.get('name', '')}")
            return result

    async def download_output(self, filename: str, subfolder: str = "") -> bytes:
        """下载生成结果（图片/视频）"""
        params = {"filename": filename, "subfolder": subfolder, "type": "output"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                urljoin(self.base_url, "/view"),
                params=params, timeout=120,
            )
            resp.raise_for_status()
            return resp.content

    async def _get_history(self, prompt_id: str) -> dict:
        """获取任务执行历史"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                urljoin(self.base_url, f"/history/{prompt_id}"),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_queue_size(self) -> int:
        """获取当前队列长度"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(urljoin(self.base_url, "/queue"), timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return len(data.get("queue_running", [])) + len(data.get("queue_pending", []))

    # ── 异步回调队列：边生成边落盘 ──────────

    async def batch_run_sd(self, shots: List[dict]) -> Dict[str, str]:
        """
        批量出图 — 异步回调队列模式
        shots: [{"shot_id": 1, "sd_prompt": "...", "seed": 6789, ...}, ...]
        返回: {shot_id: local_file_path}
        """
        total = len(shots)
        results: Dict[str, str] = {}

        # 启动后台消费者——每完成一个立即下载
        consumer = asyncio.create_task(self._output_consumer(results, total))

        # 逐个提交（不等待完成）
        for shot in shots:
            workflow = self._build_sd_workflow(
                prompt=shot["sd_prompt"],
                negative=shot.get("sd_negative", ""),
                ref_image=shot.get("ref_image"),
                seed=shot.get("seed", -1),
            )
            prompt_id = await self.queue_workflow(workflow)
            # 把监听任务推到后台
            asyncio.create_task(
                self._watch_and_enqueue(prompt_id, shot["shot_id"])
            )

        # 等待消费者完成
        await consumer
        return results

    async def batch_run_video(self, images: List[dict]) -> Dict[str, str]:
        """
        批量图生视频 — 异步回调队列模式
        images: [{"shot_id": 1, "image_path": "...", "motion": "...", "duration": 5}, ...]
        返回: {shot_id: local_file_path}
        """
        total = len(images)
        results: Dict[str, str] = {}
        consumer = asyncio.create_task(self._output_consumer(results, total))

        for img in images:
            workflow = self._build_video_workflow(
                input_image=img["image_path"],
                prompt=img.get("motion", ""),
                duration=img.get("duration", 5),
            )
            prompt_id = await self.queue_workflow(workflow)
            asyncio.create_task(
                self._watch_and_enqueue(prompt_id, img["shot_id"])
            )

        await consumer
        return results

    async def _watch_and_enqueue(self, prompt_id: str, shot_id: str):
        """后台监听单个任务完成，完成后推入下载队列"""
        try:
            result = await self.wait_for_completion(prompt_id)
            output_info = self._extract_output_info(result)
            await self._completed_queue.put({
                "shot_id": shot_id,
                "prompt_id": prompt_id,
                "output_info": output_info,
            })
        except Exception as e:
            logger.error(f"任务 {prompt_id}(shot={shot_id}) 失败: {e}")
            await self._completed_queue.put({
                "shot_id": shot_id,
                "prompt_id": prompt_id,
                "error": str(e),
            })

    async def _output_consumer(self, results: dict, total: int):
        """后台消费者：每完成一个立即下载落盘，更新进度"""
        completed = 0
        while completed < total:
            item = await self._completed_queue.get()
            shot_id = item["shot_id"]

            if "error" in item:
                logger.warning(f"[废片] shot={shot_id}: {item['error']}，等待重试...")
                # 废片后续由外层 retry 逻辑处理
                results[shot_id] = None
            else:
                output_info = item["output_info"]
                local_path = await self._download_and_save(
                    output_info["filename"],
                    output_info.get("subfolder", ""),
                    shot_id,
                )
                results[shot_id] = local_path
                logger.info(f"[已完成] shot={shot_id} -> {local_path}")

            completed += 1
            if self._progress_callback:
                await self._progress_callback(completed, total, shot_id)

    async def _download_and_save(self, filename: str, subfolder: str,
                                  shot_id: str) -> str:
        """下载并保存到本地 storage/output 目录"""
        data = await self.download_output(filename, subfolder)
        ext = Path(filename).suffix
        local_name = f"shot_{shot_id}{ext}"
        local_path = self.output_dir / local_name
        with open(local_path, "wb") as f:
            f.write(data)
        return str(local_path)

    # ── 单任务等待 ────────────────────────

    async def wait_for_completion(self, prompt_id: str, timeout: int = 600) -> dict:
        """WebSocket 监听直到单个任务完成"""
        async with websockets.connect(
            f"{self.ws_url}?clientId={self.client_id}"
        ) as ws:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                data = json.loads(msg)
                msg_type = data.get("type", "")

                if msg_type == "executing":
                    node = data.get("data", {}).get("node")
                    if node is None:
                        break

                elif msg_type == "progress":
                    prog = data.get("data", {})
                    logger.debug(f"进度: {prog.get('value', 0)}/{prog.get('max', 1)}")

        return await self._get_history(prompt_id)

    # ── 工作流构建（骨架，Phase 2 实现） ────

    def _build_sd_workflow(self, **kwargs) -> dict:
        raise NotImplementedError("Phase 2: 从 workflows/sd_shot.json 加载模板")

    def _build_video_workflow(self, **kwargs) -> dict:
        raise NotImplementedError("Phase 2: 从 workflows/img2video.json 加载模板")

    def _extract_output_info(self, history_result: dict) -> dict:
        """从 history 结果解析输出文件信息"""
        raise NotImplementedError("Phase 2: 解析 history 结果")
