# -*- coding: utf-8 -*-
"""四层记忆系统路由：Soul/User/Memory/Agent。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Memory, User, Feedback
from ..schemas import MemoryCreate, MemoryUpdate, MemoryOut, FeedbackCreate
from .auth import get_current_user
from ..services.memory_service import recall_memories, compute_relevance_score

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=list[MemoryOut])
def list_memories(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    query: str = Query("", description="召回关键词"),
    limit: int = Query(20, ge=1, le=100),
):
    q = db.query(Memory).filter(Memory.user_id == user.id)
    if query:
        # 按相关性排序（置顶优先）
        memories = q.all()
        scored = [(m, compute_relevance_score(m, query)) for m in memories]
        scored.sort(key=lambda x: (-x[1], -x[0].is_pinned, -x[0].importance))
        return [m for m, s in scored[:limit]]
    return q.order_by(Memory.is_pinned.desc(), Memory.created_at.desc()).limit(limit).all()


@router.post("", response_model=MemoryOut)
def create_memory(body: MemoryCreate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    m = Memory(
        user_id=user.id,
        content=body.content,
        emotion=body.emotion,
        importance=body.importance,
        entities=body.entities,
        source="manual",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


@router.put("/{memory_id}", response_model=MemoryOut)
def update_memory(memory_id: int, body: MemoryUpdate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    m = db.query(Memory).filter(Memory.id == memory_id, Memory.user_id == user.id).first()
    if not m:
        raise HTTPException(status_code=404, detail="记忆不存在")
    update_data = body.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(m, k, v)
    db.commit()
    db.refresh(m)
    return m


@router.delete("/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    m = db.query(Memory).filter(Memory.id == memory_id, Memory.user_id == user.id).first()
    if not m:
        raise HTTPException(status_code=404, detail="记忆不存在")
    db.delete(m)
    db.commit()
    return {"detail": "记忆已删除"}


@router.post("/feedback")
def submit_feedback(body: FeedbackCreate, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Agent 层：用户反馈。"""
    fb = Feedback(
        user_id=user.id,
        message_id=body.message_id,
        feedback_type=body.feedback_type,
        comment=body.comment,
    )
    db.add(fb)
    db.commit()
    return {"detail": "感谢反馈"}
