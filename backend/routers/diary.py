# -*- coding: utf-8 -*-
"""情绪日记路由。"""
from datetime import datetime, timedelta, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..models import Diary, User
from ..schemas import DiaryCreate, DiaryOut, DiaryTrends
from .auth import get_current_user

router = APIRouter(prefix="/diary", tags=["diary"])


@router.post("", response_model=DiaryOut)
def create_diary(body: DiaryCreate, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    diary = Diary(
        user_id=user.id,
        emotion_label=body.emotion_label,
        intensity=body.intensity,
        description=body.description,
        tags=body.tags,
    )
    db.add(diary)
    db.commit()
    db.refresh(diary)
    return diary


@router.get("", response_model=list[DiaryOut])
def list_diaries(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    emotion_label: Optional[str] = None,
):
    q = db.query(Diary).filter(Diary.user_id == user.id)
    if start_date:
        q = q.filter(Diary.created_at >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        q = q.filter(Diary.created_at <= datetime.combine(end_date, datetime.max.time()))
    if emotion_label:
        q = q.filter(Diary.emotion_label == emotion_label)
    q = q.order_by(Diary.created_at.desc())
    offset = (page - 1) * page_size
    return q.offset(offset).limit(page_size).all()


@router.get("/trends", response_model=DiaryTrends)
def diary_trends(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # 7天平均
    week_avg = db.query(func.avg(Diary.intensity)).filter(
        Diary.user_id == user.id, Diary.created_at >= week_ago
    ).scalar() or 0.0

    # 30天平均
    month_avg = db.query(func.avg(Diary.intensity)).filter(
        Diary.user_id == user.id, Diary.created_at >= month_ago
    ).scalar() or 0.0

    # 情绪分布
    dist_rows = db.query(Diary.emotion_label, func.count(Diary.id)).filter(
        Diary.user_id == user.id, Diary.created_at >= month_ago
    ).group_by(Diary.emotion_label).all()
    emotion_dist = {label: count for label, count in dist_rows}

    # 高频触发词（从 description 简单提取）
    diaries = db.query(Diary.description).filter(
        Diary.user_id == user.id, Diary.created_at >= month_ago,
        Diary.description != ""
    ).all()
    word_freq: dict[str, int] = {}
    for (desc,) in diaries:
        # 简单分词：按空格和标点分割
        import re
        words = re.findall(r'[\u4e00-\u9fa5]{2,}', desc)
        for w in words:
            if len(w) >= 2:
                word_freq[w] = word_freq.get(w, 0) + 1
    top_words = sorted(word_freq.items(), key=lambda x: -x[1])[:10]
    triggers = [{"word": w, "count": c} for w, c in top_words]

    return DiaryTrends(
        avg_intensity_7d=round(float(week_avg), 1),
        avg_intensity_30d=round(float(month_avg), 1),
        high_freq_triggers=triggers,
        emotion_distribution=emotion_dist,
    )
