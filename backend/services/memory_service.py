# -*- coding: utf-8 -*-
"""记忆服务：提取、存储、召回（关键词+重要性+时间衰减）。"""
import math
from datetime import datetime, date, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models import Memory, User
from ..llm_client import extract_memories
import logging

logger = logging.getLogger(__name__)


def compute_relevance_score(memory: Memory, query: str) -> float:
    """计算记忆与查询的相关性分数。

    打分 = 关键词匹配分 * 0.5 + 重要性分 * 0.3 + 时间衰减分 * 0.2
    置顶记忆额外 +1.0
    """
    if not query:
        score = float(memory.importance) / 5.0
    else:
        # 关键词匹配（简单字符重叠）
        query_chars = set(query.lower())
        content_chars = set(memory.content.lower())
        if not query_chars:
            kw_score = 0.0
        else:
            overlap = len(query_chars & content_chars)
            kw_score = overlap / max(len(query_chars), 1)

        # 实体匹配
        entity_match = 0.0
        for ent in (memory.entities or []):
            if ent and ent in query:
                entity_match += 0.2
        kw_score = min(1.0, kw_score + entity_match)

        importance_score = memory.importance / 5.0
        score = kw_score * 0.5 + importance_score * 0.3

    # 时间衰减：越近权重越高
    days_old = (date.today() - memory.memory_date).days
    decay = math.exp(-days_old / 30.0)  # 30天半衰期约21天
    score += decay * 0.2

    # 置顶加权
    if memory.is_pinned:
        score += 1.0

    return score


def recall_memories(db: Session, user_id: int, query: str, top_k: int = 5) -> List[Memory]:
    """召回 Top-K 相关记忆。"""
    all_memories = db.query(Memory).filter(Memory.user_id == user_id).all()
    if not all_memories:
        return []
    scored = [(m, compute_relevance_score(m, query)) for m in all_memories]
    scored.sort(key=lambda x: -x[1])
    return [m for m, s in scored[:top_k] if s > 0.05]


def extract_and_store(db: Session, user_id: int, user_message: str, ai_reply: str) -> List[Memory]:
    """从对话提取记忆并存入（重要性≥3才存）。"""
    extracted = extract_memories(user_message, ai_reply)
    new_memories = []
    for item in extracted:
        importance = item.get("importance", 3)
        if importance < 3:
            continue
        m = Memory(
            user_id=user_id,
            content=item.get("content", user_message[:100]),
            emotion=item.get("emotion"),
            importance=importance,
            entities=item.get("entities", []),
            source="chat",
            memory_date=date.today(),
        )
        db.add(m)
        new_memories.append(m)
    db.commit()
    for m in new_memories:
        db.refresh(m)
    return new_memories


def build_memory_context(memories: List[Memory]) -> str:
    """将召回的记忆构建为 system prompt 上下文。"""
    if not memories:
        return ""
    lines = ["【心镜记住的关于你的事】"]
    for m in memories:
        line = f"- {m.content}"
        if m.emotion:
            line += f"（情绪: {m.emotion}）"
        lines.append(line)
    return "\n".join(lines)
