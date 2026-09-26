# -*- coding: utf-8 -*-
"""合规模块：使用时长限制/AI披露/危机熔断/通知。"""
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, UsageLog, Notification
from ..schemas import NotificationOut
from .auth import get_current_user
from ..config import DAILY_USAGE_LIMIT_SECONDS, MINOR_DAILY_LIMIT_SECONDS, CRISIS_HOTLINE

router = APIRouter(tags=["compliance"])


def check_usage_limit(db: Session, user: User) -> dict:
    """检查用户当日使用时长，返回超限状态。"""
    today = date.today()
    log = db.query(UsageLog).filter(
        UsageLog.user_id == user.id, UsageLog.log_date == today
    ).first()
    used_seconds = log.seconds_used if log else 0
    limit = MINOR_DAILY_LIMIT_SECONDS if user.is_minor else DAILY_USAGE_LIMIT_SECONDS
    remaining = max(0, limit - used_seconds)
    return {
        "used_seconds": used_seconds,
        "limit_seconds": limit,
        "remaining_seconds": remaining,
        "is_over_limit": used_seconds >= limit,
    }


@router.get("/compliance/status")
def compliance_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """返回用户合规状态。"""
    usage = check_usage_limit(db, user)
    return {
        "usage": usage,
        "ai_disclaimer": "（AI 生成，仅供参考）",
        "crisis_hotline": CRISIS_HOTLINE,
        "is_minor": user.is_minor,
    }


@router.post("/compliance/tick")
def tick_usage(db: Session = Depends(get_db), user: User = Depends(get_current_user),
               seconds: int = 30):
    """前端定时上报使用时长。"""
    today = date.today()
    log = db.query(UsageLog).filter(
        UsageLog.user_id == user.id, UsageLog.log_date == today
    ).first()
    if not log:
        log = UsageLog(user_id=user.id, log_date=today, seconds_used=0)
        db.add(log)
    log.seconds_used += seconds
    db.commit()

    usage = check_usage_limit(db, user)
    if usage["is_over_limit"]:
        raise HTTPException(
            status_code=429,
            detail=f"今日使用时长已达上限（{usage['limit_seconds']//3600}小时），请休息一下，回到现实生活中吧。",
        )
    return {"remaining_seconds": usage["remaining_seconds"]}


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                      unread_only: bool = False):
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        q = q.filter(Notification.is_read == False)  # noqa: E712
    return q.order_by(Notification.created_at.desc()).limit(20).all()


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    n = db.query(Notification).filter(
        Notification.id == notification_id, Notification.user_id == user.id
    ).first()
    if not n:
        raise HTTPException(status_code=404, detail="通知不存在")
    n.is_read = True
    db.commit()
    return {"detail": "已标记为已读"}
