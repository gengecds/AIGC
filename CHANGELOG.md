# CHANGELOG

## 2026-08-30 — Phase 3 · 视频层 Skills 接入 + 题材/用途配方深化

### 背景
用户强调「我提供给你的……需要你去分析和梳理」，不能照抄零散片段。此前 image 题材层已接入；
本轮把最碎片化的 `video/genre`、`video/use` 两个维度，按「每个维度 = 一套可执行系统」重写，
并把 video 层词块真正接入 `video_agent` 生成管线（此前 video 层完全没进数据流，LTX 收到空 prompt 回退默认值）。

### 完成
- **统一骨架**：每个维度拆成若干可独立执行的子维度（原每类仅 1~2 个模糊短语，AI 只能自由发挥）。
- **video/genre.yaml 重写**：6 类题材（影视与综艺 / 新闻与资讯 / 生活与Vlog / 知识与教育 / 娱乐与游戏 / 科技与数码），
  每类 6 维配方（镜头惯例 / 光影色调 / 节奏与剪辑 / 构图特色 / 氛围与配乐 / 负向约束）。
- **video/use.yaml 重写**：5 类用途（广告与宣传片 / 监控与安防 / 医疗与工业 / AI训练 / 直播带货），
  每类 6 维配方（制作目的 / 镜头惯例 / 光影色调 / 节奏与剪辑 / CTA氛围 / 负向约束）。
- **image/scene.yaml 补构图**：新增「构图与取景」分组（纵深与分层 / 前景框架 / 广角与透视 / 大远景交代镜头 /
  对称与居中 / 三分法与引导线 / 留白与减法），共 7 条。
- **resolver.py 视频层自动匹配**：新增 `shot_camera_name/block`、`shot_genre_name/block`、`shot_use_name/block`，
  据分镜 `camera_movement/scene/action/background/sd_prompt` 用中英文关键词自动推断运镜/题材/用途分组，
  复用 `_hit_kw` 统一匹配规则（中文=子串 / 英文=单词边界）。
- **video_agent.py 接入**：构造 `video_data` 时用 `shot_map[sid]` 反查分镜，`_shot_prompt()` 拼装
  「sd_prompt + 运镜/题材/用途词块」写入 `prompt` 字段，使 `ComfyLTXVideoProvider` 不再回退默认 "cinematic motion..."。

### 修复
- **video/genre.yaml、video/use.yaml 的 note 内嵌 ASCII 双引号**：导致整文件 YAML 解析失败（该维度全部词块丢失），
  已改为全角引号「」。

### 验证
- `resolver` 三个新接口对样例分镜（缓慢前推 / Vlog 记录 / 手机广告 / 环绕广告）均正确输出对应词块。
- `video_agent.py` 通过 `py_compile`、AST 解析、模块 import 三连验证。

## 2026-08-30 — Phase 2 · Skills 知识库扩充

### 背景
用户反馈「分镜出图提示词太简洁、AI 抓不住想要的画面」，以「人物与肖像」为例，旧词库只拆出
`cinematic close-up portrait · natural skin texture · group portrait · layered depth · shallow focus · clean studio headshot · neutral background`
7 个短语。问题在于：① 特写 / 群体 / 证件照三种不同景别被串成一条，语义自相冲突；② 每类仅 1~2 个词，
缺乏镜头参数、光线方向、皮肤质感、眼神等细节，AI 只能自由发挥，无法复现意图。

### 完成
- **skills/image/topic.yaml 重写**：把「人物与肖像」拆成三个可独立出图的完整题材，每个题材提供景别构图 /
  镜头景深 / 光线方向 / 皮肤真实质感 / 眼神微表情 / 头发细节 / 背景分离 / 画质后缀等 8 层英文细节，
  词条间可无缝叠加、不冲突；同步细化「自然与风景」「动植物」「建筑与城市」「商品与静物」「艺术与抽象」。
  - `人物与肖像·单人电影特写`（85mm f/1.8、伦勃朗主光、可见毛孔/次表面散射、发丝、奶油虚化）
  - `人物与肖像·群体合影`（前后错位层次、统一光线、人脸一致性、景深全脸清晰）
  - `人物与肖像·棚拍证件照质感`（端正居中、统一柔光、无畸变 85mm、纯灰渐变背景）
- **resolver.py**：`_GROUP_FILES` 已纳入第一阶段新增的 `image/scene.yaml`、`video/camera.yaml`、`video/motion.yaml`；
  `category_block()` / `list_categories()` 已验证可正确返回新拆分的题材词块（`人物与肖像` 旧名已拆分故返回空）。
- **题材词块自动注入**：`resolver.py` 新增 `shot_topic_block(shot)`，据分镜 `characters` 数量（单人→单人电影特写 /
  多人→群体合影）及 `scene/action/background` 关键词（自然/动植物/建筑/商品/艺术）自动匹配题材；
  `image_agent.py` 的 `_shot_skills()` 已无条件叠加该题材词块，与 photoreal/anatomy/emotion 共同拼入 `sd_prompt`，
  解决「提示词只有景别、缺题材摄影细节」问题。
- **无人物镜头的兜底匹配**：`shot_topic_name()` 对无人物镜头先用 `scene/action/background` 中文匹配，未命中再兜底匹配
  英文 `sd_prompt`；`_TOPIC_KEYWORDS` 为每类补英文关键词，英文用单词边界（防 `fish` 误命中 `selfish`），中文用子串。

### 修复
- **前端加载失败**：config.yaml 端口 8000→8888；api/main.py 补 CORSMiddleware；
  frontend/src/views/PipelineView.vue `API_BASE` 8000→8888；frontend/app.js `SK_DIM_LABELS` 补充新维度中文标签。
  重启后端后 skills 各维度接口均返回 200。

### 定位结论（出图效果分析）
旧 7 词拼接后，同一 prompt 同时要求「特写 + 群体 + 证件照」，SD 无法同时满足 → 大概率生成一张
「一群人排排站、脸部被裁切、背景又说要净又要虚」的混乱图；且无光圈/光线方向/皮肤/眼神细节，
人物呈现塑料感、眼神空洞、背景与主体不分层。新词库按题材分块给出可直接执行的摄影参数，出图可控、可复现。

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
