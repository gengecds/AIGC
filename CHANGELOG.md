# CHANGELOG

## 2026-06-19 — Phase 1 完成

### 新增
- **MockProvider**: MockImageProvider + MockVideoProvider（0 API 费用，占位验证数据流）
- **LLM Provider**: DeepSeekProvider + OllamaProvider
- **Agent 1 - ScriptAgent**: 剧本生成（DeepSeek）
- **Agent 2 - StoryboardAgent**: 分镜生成 + ShotValidator 校验层（shot_type/duration/prompt）
- **Agent 3 - CharacterAgent**: 角色定妆照 + 双轨制资产判断（is_asset_library）
- **Agent 4 - ImageGenAgent**: 批量出图骨架（Mock）
- **Agent 5 - VideoGenAgent**: 图生视频骨架（Mock）
- **Agent 6 - SubtitleAgent**: SRT字幕生成
- **Agent 7 - ComposeAgent**: FFmpeg 视频合成骨架
- **Pipeline 调度器**: 支持断点恢复 + 7 Agent 串联
- **API 路由**: POST /api/v1/pipeline/run, GET /api/v1/pipeline/status
- **Vue 3 前端**: Vite 脚手架 + PipelineView（进度展示）
- **tests/test_pipeline.py**: 管线验证脚本（无 API key 用预设数据）

### 修改
- config.yaml: engine → mock, 移除通义万相, 加 style_profiles + human_review
- db/models.py: Character 加 is_asset_library 字段
- providers/comfyui/client.py: 重写为异步回调队列版本
- agents/base.py: Agent 继承 BaseAgent

### 移除
- 通义万相所有残留引用（6处）
- 错误的 sed 产物（FFmpeg真合成、MockProvider返回假...等乱文件）

## 2026-06-19 — Phase 0 脚手架
- 项目目录搭建
- ComfyUIClient 骨架
- DB models
- FastAPI 入口
- config.yaml
- README + CHANGELOG
