"""数据库模型 - SQLAlchemy"""

from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON, Float
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class Story(Base):
    """故事主表"""
    __tablename__ = "stories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), default="", comment="剧名")
    genre = Column(String(100), default="", comment="题材")
    original_input = Column(Text, default="", comment="用户原始输入")
    story_outline = Column(Text, default="", comment="故事梗概")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Episode(Base):
    """剧集"""
    __tablename__ = "episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_id = Column(Integer, nullable=False, comment="关联故事ID")
    episode_number = Column(Integer, nullable=False, comment="第几集")
    summary = Column(Text, default="", comment="本集概要")
    script_content = Column(Text, default="", comment="剧本原始内容")
    created_at = Column(DateTime, default=datetime.utcnow)


class Character(Base):
    """角色"""
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_id = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False, comment="角色名")
    gender = Column(String(10), default="", comment="性别")
    appearance = Column(Text, default="", comment="外貌描述")
    personality = Column(Text, default="", comment="性格描述")
    consistency_seed = Column(Integer, default=0, comment="角色一致性种子")
    portrait_path = Column(String(500), default="", comment="头像路径")
    full_body_path = Column(String(500), default="", comment="全身照路径")
    controlnet_ref_path = Column(String(500), default="", comment="ControlNet参考图路径")
    created_at = Column(DateTime, default=datetime.utcnow)


class Shot(Base):
    """分镜"""
    __tablename__ = "shots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    episode_id = Column(Integer, nullable=False)
    scene_id = Column(Integer, default=0, comment="所属场景ID")
    shot_number = Column(Integer, default=0, comment="分镜序号")
    description = Column(Text, default="", comment="分镜描述")
    shot_type = Column(String(50), default="", comment="景别: 远/中/近/特写")
    camera_movement = Column(String(50), default="", comment="运镜")
    duration = Column(Float, default=5.0, comment="时长(秒)")
    character_name = Column(String(100), default="", comment="出现角色")
    sd_prompt = Column(Text, default="", comment="SD提示词")
    sd_negative = Column(Text, default="", comment="SD负面提示词")
    video_motion = Column(Text, default="", comment="视频动效提示词")
    image_path = Column(String(500), default="", comment="出图路径")
    video_path = Column(String(500), default="", comment="视频路径")
    status = Column(String(50), default="pending", comment="状态: pending/done/failed")
    created_at = Column(DateTime, default=datetime.utcnow)


class PipelineJob(Base):
    """管线执行记录"""
    __tablename__ = "pipeline_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    story_id = Column(Integer, nullable=False)
    status = Column(String(50), default="running", comment="running/done/failed")
    current_agent = Column(String(100), default="", comment="当前执行到哪个Agent")
    error = Column(Text, default="", comment="错误信息")
    result = Column(JSON, default={}, comment="最终产出元数据")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── 数据库连接 ────────────────────────────

def init_db(db_url: str = "sqlite:///storage/ai_drama.db"):
    """初始化数据库（开发阶段 SQLite）"""
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return engine, Session
