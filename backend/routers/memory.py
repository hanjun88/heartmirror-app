# -*- coding: utf-8 -*-
"""四层记忆系统路由：Soul/User/Memory/Agent。

- Soul   灵魂层：核心价值观 / AI 人格
- User   用户画像层：依恋类型 / 爱语 / 冲突风格
- Memory 事件记忆层：具体发生过的事
- Agent  Agent 行为偏好层：用户对陪伴方式的偏好反馈
每条记忆支持 pinned（置顶）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Memory, User, Feedback, MEMORY_LAYERS
from ..schemas import MemoryCreate, MemoryUpdate, MemoryOut, FeedbackCreate
from .auth import get_current_user
from ..services.memory_service import recall_memories, compute_relevance_score

router = APIRouter(prefix="/memory", tags=["memory"])


def _validate_layer(layer: str) -> str:
    if layer not in MEMORY_LAYERS:
        raise HTTPException(status_code=422, detail=f"layer 必须是 {MEMORY_LAYERS} 之一")
    return layer


@router.get("", response_model=list[MemoryOut])
def list_memories(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    layer: str = Query("", description="按层过滤：soul/user/memory/agent"),
    query: str = Query("", description="召回关键词"),
    limit: int = Query(200, ge=1, le=500),
):
    q = db.query(Memory).filter(Memory.user_id == user.id)
    if layer:
        _validate_layer(layer)
        q = q.filter(Memory.layer == layer)

    if query:
        memories = q.all()
        scored = [(m, compute_relevance_score(m, query)) for m in memories]
        scored.sort(key=lambda x: (-x[1], -x[0].is_pinned, -x[0].importance))
        return [m for m, s in scored[:limit]]
    # 置顶优先，其次按创建时间倒序
    return q.order_by(Memory.is_pinned.desc(), Memory.created_at.desc()).limit(limit).all()


@router.get("/layers")
def list_memory_layers(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """按四层分组返回记忆（前端 Tab 展示用）。"""
    groups: dict[str, list] = {k: [] for k in MEMORY_LAYERS}
    rows = db.query(Memory).filter(Memory.user_id == user.id).all()
    for m in rows:
        key = m.layer if m.layer in groups else "memory"
        groups[key].append(m)
    # 组内置顶优先
    for key in groups:
        groups[key].sort(key=lambda m: (not m.is_pinned, m.created_at))
    return {
        "layers": [
            {"key": k, "label": _LAYER_LABELS[k], "memories": [MemoryOut.model_validate(m).model_dump() for m in groups[k]]}
            for k in MEMORY_LAYERS
        ]
    }


_LAYER_LABELS = {
    "soul": "灵魂层 · 核心价值观",
    "user": "画像层 · 关于你",
    "memory": "事件层 · 经历",
    "agent": "偏好层 · 陪伴方式",
}


@router.post("", response_model=MemoryOut)
def create_memory(body: MemoryCreate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    _validate_layer(body.layer)
    m = Memory(
        user_id=user.id,
        layer=body.layer,
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
    if "layer" in update_data and update_data["layer"] is not None:
        _validate_layer(update_data["layer"])
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
