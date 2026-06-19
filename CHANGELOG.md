# Changelog - AI 漫剧创作平台

## 2026-06-19 - Phase 0 启动

- 创建项目目录结构
- 实现 Agent 基类（agents/base.py）
- 实现 Provider 抽象基类（providers/base.py）
- 实现 ComfyUIClient 骨架（providers/comfyui/client.py）
- 实现 Pipeline 调度器+状态管理（pipeline/scheduler.py, pipeline/state.py）
- 实现数据库模型+连接（db/models.py, db/database.py）
- 实现 FastAPI 入口+配置（api/main.py, config/config.yaml）
- 添加 requirements.txt

### 关键决策（已确认）
- 风格不绑定，换模型换风格
- 开发完成必须实测
- 生成时间不限，按实际走
- 直接上 Web（Vue 3）
- 断点续传按需实现
- 一次跑1个任务，并行度看服务器能力
