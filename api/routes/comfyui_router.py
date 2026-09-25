"""ComfyUI 工作流模板路由（供前端下载/复用，两种格式）

- GET /api/v1/comfyui/workflows                → 模板清单
- GET /api/v1/comfyui/workflows/{kind}         → JSON（画布 UI 格式，默认）
    ?format=api   → 后端执行的 API 格式
    ?download=1   → 触发下载，文件名按模板 preset 命名

用户在 ComfyUI 里的用法：
  UI 格式文件可直接「拖入画布」或 Workflow → Open；
  API 格式文件也可拖入/导入（ComfyUI 会加载节点，仅不带排版）。
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response

from providers.comfyui.workflow_templates import (
    TEMPLATES, api_workflow, ui_workflow,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/comfyui", tags=["comfyui"])


def _by_kind(kind: str) -> dict:
    for t in TEMPLATES:
        if t["kind"] == kind:
            return t
    raise HTTPException(status_code=404, detail=f"未知模板 kind: {kind}")


@router.get("/workflows")
async def list_workflows():
    """返回可复用工作流模板清单（名称/说明/文件名）。"""
    return {"workflows": TEMPLATES}


@router.get("/workflows/{kind}")
async def get_workflow(
    kind: str,
    format: str = Query("ui", pattern="^(ui|api)$"),
    download: int = Query(0, ge=0, le=1),
):
    """按 kind 返回工作流 JSON（默认画布 UI 格式，方便拖进 ComfyUI 调整）。"""
    meta = _by_kind(kind)
    wf = ui_workflow(kind) if format == "ui" else api_workflow(kind)
    body = json.dumps(wf, ensure_ascii=False, indent=2)
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{meta["filename"]}"'
    return Response(content=body, headers=headers)
