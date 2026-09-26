# -*- coding: utf-8 -*-
"""情绪复盘报告路由。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..schemas import WeeklyReport, ShareCard
from ..services.report_service import generate_weekly_report, generate_share_card
from .auth import get_current_user

router = APIRouter(prefix="/report", tags=["report"])


@router.get("/weekly", response_model=WeeklyReport)
def weekly_report(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return generate_weekly_report(db, user.id)


@router.get("/share-card", response_model=ShareCard)
def share_card(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return generate_share_card(db, user.id)
