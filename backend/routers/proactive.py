# -*- coding: utf-8 -*-
"""主动 Agent 路由：查看待发送/已发送的主动推送消息。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import NotificationOut
from .auth import get_current_user
from ..services import proactive_service

router = APIRouter(prefix="/proactive", tags=["proactive"])


@router.get("/messages", response_model=list[NotificationOut])
def my_proactive_messages(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """返回当前用户的主动推送消息列表。

    is_read=False 视为待发送/未读，is_read=True 视为已发送/已读。
    """
    return proactive_service.list_proactive_messages(db, user.id)


@router.post("/run-all")
def run_now(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """手动触发一次全部主动检查（便于演示/测试，不阻塞）。"""
    proactive_service.run_all_checks(db)
    return {"detail": "已执行一轮主动检查"}
