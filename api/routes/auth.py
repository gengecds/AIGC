"""认证路由 - 登录/注册/登出（SQLite 用户表 + Bearer Token）

本地工具级认证：密码用 sha256+salt 哈希存储（不引外部依赖），
token 用 secrets 随机生成存库，前端存 localStorage 每次请求带 Bearer 头。
"""

import hashlib
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from db.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Pydantic 模型 ──────────────────────────

class RegisterReq(BaseModel):
    username: str
    password: str


class LoginReq(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    created_at: str


# ── 工具 ──────────────────────────

def _hash_password(password: str, salt: str) -> str:
    """sha256(salt + password)，简单可逆性足够（本地单用户工具）"""
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def get_current_user(request: Request):
    """FastAPI 依赖：校验 Authorization: Bearer <token>，返回 User"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token = auth[len("Bearer "):].strip()
    db = get_session()
    try:
        from db.models import User
        user = db.query(User).filter_by(token=token).first()
        if not user:
            raise HTTPException(401, "登录已失效，请重新登录")
        return user
    finally:
        db.close()


# ── 接口 ──────────────────────────

@router.post("/register")
def register(req: RegisterReq):
    """注册新用户，成功后自动登录（返回 token）"""
    username = req.username.strip()
    password = req.password
    if not username or len(username) < 2:
        raise HTTPException(400, "用户名至少2个字符")
    if len(password) < 4:
        raise HTTPException(400, "密码至少4位")

    db = get_session()
    try:
        from db.models import User
        if db.query(User).filter_by(username=username).first():
            raise HTTPException(409, "用户名已存在")
        salt = secrets.token_hex(8)
        token = secrets.token_hex(32)
        user = User(
            username=username,
            salt=salt,
            password_hash=_hash_password(password, salt),
            token=token,
        )
        db.add(user)
        db.commit()
        logger.info(f"[Auth] 新用户注册: {username}")
        return {"success": True, "token": token, "user": UserOut(
            id=user.id, username=user.username,
            created_at=user.created_at.isoformat(),
        )}
    finally:
        db.close()


@router.post("/login")
def login(req: LoginReq):
    """登录，返回 token"""
    db = get_session()
    try:
        from db.models import User
        user = db.query(User).filter_by(username=req.username.strip()).first()
        if not user or user.password_hash != _hash_password(req.password, user.salt):
            raise HTTPException(401, "用户名或密码错误")
        # 每次登录刷新 token（旧的立即失效）
        user.token = secrets.token_hex(32)
        db.commit()
        logger.info(f"[Auth] 用户登录: {user.username}")
        return {"success": True, "token": user.token, "user": UserOut(
            id=user.id, username=user.username,
            created_at=user.created_at.isoformat(),
        )}
    finally:
        db.close()


@router.post("/logout")
def logout(user=Depends(get_current_user)):
    """登出：清空 token"""
    db = get_session()
    try:
        from db.models import User
        u = db.query(User).filter_by(id=user.id).first()
        if u:
            u.token = ""
            db.commit()
        return {"success": True}
    finally:
        db.close()


@router.get("/me")
def me(user=Depends(get_current_user)):
    """当前登录用户信息（同时用于前端校验登录态）"""
    return {"success": True, "user": UserOut(
        id=user.id, username=user.username,
        created_at=user.created_at.isoformat(),
    )}
