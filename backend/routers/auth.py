# -*- coding: utf-8 -*-
"""Auth 路由：注册/登录/JWT/用户资料。"""
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import (
    UserRegister, UserLogin, TokenResponse, UserProfileUpdate, UserProfileOut
)
from ..config import (
    JWT_SECRET, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS
)

router = APIRouter(prefix="/auth", tags=["auth"])

bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(subject: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "exp": expire, "type": "access"}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(subject: str) -> str:
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": subject, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI 依赖：从 JWT 解析当前用户。"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise credentials_exception
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        if user_id is None or token_type != "access":
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise credentials_exception
    return user


@router.post("/register", response_model=TokenResponse)
def register(body: UserRegister, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="邮箱已注册")

    is_minor = body.age is not None and body.age < 18
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        nickname=body.nickname or f"心镜用户{body.email[:4]}",
        age=body.age,
        is_minor=is_minor,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    user.last_login_at = datetime.utcnow()
    db.commit()

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.get("/me", response_model=UserProfileOut)
def get_me(user: User = Depends(get_current_user)):
    return UserProfileOut.model_validate(user)


@router.put("/me", response_model=UserProfileOut)
def update_me(body: UserProfileUpdate, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    update_data = body.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(user, k, v)
    db.commit()
    db.refresh(user)
    return UserProfileOut.model_validate(user)


@router.delete("/me")
def delete_me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """GDPR/个保法：完全删除用户数据。"""
    db.delete(user)
    db.commit()
    return {"detail": "账户已删除，所有数据已清除"}
