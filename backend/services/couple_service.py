# -*- coding: utf-8 -*-
"""配对服务：邀请/加入/私下调停/联合会话/Gottman 中立引导。"""
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import Session

from ..models import CouplePair, CoupleSession, CoupleMessage, CouplePrivateNote, User
from ..llm_client import couple_moderator_prompt, couple_private_threeline, gottman_opening_guide
import logging

logger = logging.getLogger(__name__)

INVITE_CODE_EXPIRE_HOURS = 24


def create_invite(db: Session, user_id: int) -> CouplePair:
    """创建配对邀请。"""
    # 如果已有 pending 邀请，复用
    existing = db.query(CouplePair).filter(
        CouplePair.user_a_id == user_id,
        CouplePair.status == "pending"
    ).first()
    if existing:
        return existing

    code = secrets.token_urlsafe(8)[:8].upper()
    pair = CouplePair(
        user_a_id=user_id,
        invite_code=code,
        status="pending",
    )
    db.add(pair)
    db.commit()
    db.refresh(pair)
    return pair


def join_pair(db: Session, invite_code: str, user_id: int) -> Optional[CouplePair]:
    """通过 code 加入配对。"""
    pair = db.query(CouplePair).filter(
        CouplePair.invite_code == invite_code.upper(),
        CouplePair.status == "pending",
    ).first()
    if not pair:
        return None
    if pair.user_a_id == user_id:
        return None  # 不能自己配对自己
    pair.user_b_id = user_id
    pair.status = "connected"
    pair.connected_at = datetime.utcnow()
    db.commit()
    db.refresh(pair)
    return pair


def get_pair_status(db: Session, user_id: int) -> Optional[Dict[str, Any]]:
    """获取用户配对状态。"""
    pair = db.query(CouplePair).filter(
        ((CouplePair.user_a_id == user_id) | (CouplePair.user_b_id == user_id))
    ).order_by(CouplePair.created_at.desc()).first()
    if not pair:
        return {"status": "none"}

    result = {"status": pair.status, "pair_id": pair.id}
    if pair.status == "connected":
        partner_id = pair.user_b_id if pair.user_a_id == user_id else pair.user_a_id
        partner = db.query(User).filter(User.id == partner_id).first()
        result["partner_nickname"] = partner.nickname if partner else None
    return result


def create_session(db: Session, pair_id: int) -> CoupleSession:
    """创建联合会话。"""
    session = CoupleSession(pair_id=pair_id, status="active")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def add_message(db: Session, session_id: int, user_id: int, content: str) -> Dict[str, Any]:
    """在联合会话中发消息，AI 作为中立调解者。"""
    msg = CoupleMessage(session_id=session_id, user_id=user_id, content=content)
    db.add(msg)
    db.commit()

    # 获取会话上下文（最近几条消息）
    recent = db.query(CoupleMessage).filter(
        CoupleMessage.session_id == session_id
    ).order_by(CoupleMessage.created_at.desc()).limit(6).all()
    recent.reverse()

    # AI 调解
    moderator_reply = couple_moderator_prompt(
        user_a_msg=content,
        user_b_msg="",  # 简化：单次发言后调解
        context=f"联合会话 #{session_id}",
    )

    ai_msg = CoupleMessage(
        session_id=session_id, user_id=user_id,  # 系统消息
        content=moderator_reply, is_ai_moderator=True,
    )
    db.add(ai_msg)
    db.commit()

    return {
        "user_message": {"content": content, "user_id": user_id},
        "moderator_reply": moderator_reply,
    }


def end_session(db: Session, session_id: int) -> str:
    """结束会话，生成关系共识卡。"""
    session = db.query(CoupleSession).filter(CoupleSession.id == session_id).first()
    if not session:
        return ""
    session.status = "ended"
    session.ended_at = datetime.utcnow()

    # 简化：生成共识摘要
    msgs = db.query(CoupleMessage).filter(CoupleMessage.session_id == session_id).all()
    user_msgs = [m.content for m in msgs if not m.is_ai_moderator]
    consensus = (
        f"本次会话共{len(user_msgs)}条交流。"
        "建议双方回顾彼此的核心诉求，尝试用'我感到...因为...我希望...'的句式表达。"
        "——心镜关系共识卡（生成模板）"
    )
    session.consensus_summary = consensus
    db.commit()
    return consensus


# ---- 私下调停（联合会话前各自单独跟 AI 倾诉） ----
def private_reflect(db: Session, user_id: int, feelings: str) -> Optional[CouplePrivateNote]:
    """每人单独跟 AI 对话，AI 生成'建议对TA说的三句话'。"""
    status = get_pair_status(db, user_id)
    if status.get("status") != "connected" or not status.get("pair_id"):
        return None
    lines = couple_private_threeline(feelings)
    note = CouplePrivateNote(
        pair_id=status["pair_id"],
        user_id=user_id,
        feelings=feelings,
        suggested_lines=lines,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def list_my_private_notes(db: Session, user_id: int) -> List[CouplePrivateNote]:
    status = get_pair_status(db, user_id)
    if not status.get("pair_id"):
        return []
    return (
        db.query(CouplePrivateNote)
        .filter(CouplePrivateNote.pair_id == status["pair_id"],
                CouplePrivateNote.user_id == user_id)
        .order_by(CouplePrivateNote.created_at.desc())
        .all()
    )


# ---- Gottman 中立引导 ----
def get_gottman_guide(db: Session, session_id: int) -> Optional[Dict[str, Any]]:
    """联合会话开始时，返回 Gottman soften startup 的结构化中立引导。"""
    session = db.query(CoupleSession).filter(CoupleSession.id == session_id).first()
    if not session:
        return None
    guide = gottman_opening_guide()
    # 写入一条 AI 调解者开场消息
    opening = CoupleMessage(
        session_id=session_id, user_id=0,  # 0 表示系统/AI
        content=f"【中立引导】{guide['stage']}：{guide['prompt']}",
        is_ai_moderator=True,
    )
    db.add(opening)
    db.commit()
    return {"session_id": session_id, **guide}
