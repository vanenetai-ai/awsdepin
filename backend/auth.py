import secrets
import logging
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from database import get_db
from models import User, AuthToken

logger = logging.getLogger(__name__)

TOKEN_EXPIRE_DAYS = 30


def create_token(db: Session, user: User) -> str:
    """为用户创建认证 token"""
    token = secrets.token_urlsafe(48)
    auth_token = AuthToken(
        user_id=user.id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS),
    )
    db.add(auth_token)
    user.last_login_at = datetime.utcnow()
    db.commit()
    return token


def get_user_by_token(db: Session, token: str) -> User | None:
    """通过 token 获取用户"""
    auth = (
        db.query(AuthToken)
        .filter(
            AuthToken.token == token,
            AuthToken.is_active == True,
            AuthToken.expires_at > datetime.utcnow(),
        )
        .first()
    )
    if not auth:
        return None
    return auth.user


def get_or_create_user(db: Session, telegram_id: int, username: str = None, display_name: str = None) -> User:
    """获取或创建用户"""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        user = User(
            telegram_id=telegram_id,
            telegram_username=username,
            display_name=display_name or username or str(telegram_id),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"New user created: {user.display_name} (tg:{telegram_id})")
    else:
        if username and user.telegram_username != username:
            user.telegram_username = username
        if display_name and user.display_name != display_name:
            user.display_name = display_name
        db.commit()
    return user


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI 依赖：从请求中提取当前用户"""
    # 优先从 header 获取
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    # 其次从 query param 获取
    if not token:
        token = request.query_params.get("token", "")
    # 其次从 cookie 获取
    if not token:
        token = request.cookies.get("auth_token", "")

    if not token:
        raise HTTPException(status_code=401, detail="未登录，请通过 Telegram 获取登录链接")

    user = get_user_by_token(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期，请重新获取登录链接")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    return user
