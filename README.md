# AI 漫剧创作平台

> 全自主开发的 AI 漫剧创作系统
> 工作目录：`/Users/mac/git/AIGC`

## 项目概况

一句话/大纲/小说 → 剧本生成 → 角色定妆 → 分镜出图 → 图生视频 → 字幕合成 → 成品漫剧

## 技术栈

| 层 | 技术 |
|:---|:---|
| 后端 | Python FastAPI |
| 前端 | Vue 3 |
| 出图引擎 | ComfyUI + Stable Diffusion（云GPU AutoDL） |
| 视频引擎 | ComfyUI + HunyuanVideo / LTX-Video（同一云GPU） |
| LLM | DeepSeek API（主） / Ollama qwen3:14b（备） |
| 数据库 | SQLite（开发）→ PostgreSQL（生产） |
| 视频合成 | FFmpeg + MoviePy |

## 目录结构

```
AIGC/
├── agents/              # 7个Agent
│   ├── base.py          # Agent 基类
│   ├── script.py        # 剧本生成
│   ├── storyboard.py    # 分镜规划
│   ├── character.py     # 角色定妆照
│   ├── image_gen.py     # 出图（ComfyUI+SD）
│   ├── video_gen.py     # 图生视频（ComfyUI+HunyuanVideo）
│   ├── subtitle.py      # 字幕生成
│   └── compose.py       # 视频合成
├── providers/           # AI API 适配层
│   ├── base.py          # 抽象基类
│   ├── deepseek.py      # DeepSeek LLM
│   ├── ollama.py        # Ollama 本地 LLM
│   ├── tongyi.py        # 通义万相（开发备用）
│   └── comfyui/         # ComfyUI 封装
│       ├── client.py    # ComfyUIClient 核心
│       ├── sd_workflows.py
│       └── video_workflows.py
├── pipeline/            # 管线调度
│   ├── scheduler.py     # 调度器
│   └── state.py         # 状态/checkpoint 管理
├── db/                  # 数据库
│   ├── models.py        # SQLAlchemy 模型
│   ├── database.py      # 连接/会话管理
│   └── migrations/      # Alembic 迁移
├── api/                 # FastAPI Web 接口
│   ├── main.py          # 入口
│   └── routes/          # 路由
├── workflows/           # ComfyUI 工作流 JSON 模板
├── storage/             # 本地文件存储
├── config/              # 配置
│   └── config.yaml
├── tests/               # 测试
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── README.md
└── CHANGELOG.md
```

## 开发进度

### Phase 0 ✅ 项目脚手架
- [x] 目录结构搭建
- [x] Agent 基类
- [x] Provider 抽象基类
- [x] ComfyUIClient 骨架
- [x] Pipeline 调度器骨架
- [x] 数据库模型骨架
- [x] FastAPI 入口
- [x] 配置文件

### Phase 1 📝 Agent 1~3：剧本+分镜+角色（进行中）
- [ ] Agent 1 剧本生成
- [ ] Agent 2 分镜规划
- [ ] Agent 3 角色定妆照
- [ ] Web 前端

### Phase 2 🎬 ComfyUI 集成
- [ ] ComfyUI 工作流模板
- [ ] 云GPU 部署
- [ ] 出图管道测试
- [ ] 图生视频管道测试

### Phase 3 🎞️ Agent 4~7：出图+视频+字幕+合成
- [ ] Agent 4 出图
- [ ] Agent 5 视频生成
- [ ] Agent 6 字幕
- [ ] Agent 7 合成

### Phase 4 🚀 全流程+优化
- [ ] 端到端跑通
- [ ] Docker 部署
- [ ] 性能优化
