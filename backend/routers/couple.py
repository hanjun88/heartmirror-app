# -*- coding: utf-8 -*-
"""双人配对路由。"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, CouplePair, CoupleSession
from ..schemas import (
    CoupleInviteOut, CoupleJoin, CoupleStatusOut,
    CoupleSessionCreate, CoupleSessionOut, CoupleMessageCreate
)
from ..services import couple_service
from .auth import get_current_user

router = APIRouter(prefix="/couple", tags=["couple"])


@router.post("/invite", response_model=CoupleInviteOut)
def create_invite(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # 未成年人不可用配对功能
    if user.is_minor:
        raise HTTPException(status_code=403, detail="未成年人模式暂不支持双人配对")
    pair = couple_service.create_invite(db, user.id)
    return CoupleInviteOut(
        invite_code=pair.invite_code,
        expires_at=pair.created_at + timedelta(hours=24),
        status=pair.status,
    )


@router.post("/join", response_model=CoupleStatusOut)
def join(body: CoupleJoin, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.is_minor:
        raise HTTPException(status_code=403, detail="未成年人模式暂不支持双人配对")
    pair = couple_service.join_pair(db, body.invite_code, user.id)
    if not pair:
        raise HTTPException(status_code=404, detail="邀请码无效或已过期")
    return CoupleStatusOut(status=pair.status, pair_id=pair.id)


@router.get("/status", response_model=CoupleStatusOut)
def pair_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    status = couple_service.get_pair_status(db, user.id)
    return CoupleStatusOut(**status)


@router.post("/session", response_model=CoupleSessionOut)
def create_session(body: CoupleSessionCreate, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    status = couple_service.get_pair_status(db, user.id)
    if status.get("status") != "connected":
        raise HTTPException(status_code=400, detail="需要先完成配对")
    session = couple_service.create_session(db, status["pair_id"])
    return CoupleSessionOut.model_validate(session)


@router.post("/session/{session_id}/message")
def session_message(session_id: int, body: CoupleMessageCreate,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    result = couple_service.add_message(db, session_id, user.id, body.content)
    return result


@router.post("/session/{session_id}/end", response_model=CoupleSessionOut)
def end_session(session_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    summary = couple_service.end_session(db, session_id)
    session = db.query(CoupleSession).filter(CoupleSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return CoupleSessionOut.model_validate(session)
