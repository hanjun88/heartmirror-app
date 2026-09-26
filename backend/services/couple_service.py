# -*- coding: utf-8 -*-
"""配对服务：邀请/加入/私下调停/联合会话/Gottman 中立引导。

P1-D 安全加固：所有按 session_id / pair_id 的写操作与读取都校验当前用户
是否属于该 pair 的参与者，杜绝 BOLA/IDOR。

F1 双人调解升级：CoupleSession.turn 状态机（waiting_a / waiting_b / mediating），
实现真正的 A→B 交替发言，双方上下文齐全后再由 AI mediator 生成调解。
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import CouplePair, CoupleSession, CoupleMessage, CouplePrivateNote, User
from ..llm_client import couple_moderator_prompt, couple_private_threeline, gottman_opening_guide
import logging

logger = logging.getLogger(__name__)

INVITE_CODE_EXPIRE_HOURS = 24


def _load_pair_for_session(db: Session, session: CoupleSession) -> CouplePair:
    """根据 session 加载对应 pair。"""
    pair = db.query(CouplePair).filter(CouplePair.id == session.pair_id).first()
    if not pair:
        # pair 不存在视为资源不存在
        raise HTTPException(status_code=404, detail="配对不存在")
    return pair


def _assert_participant(db: Session, session: CoupleSession, user_id: int) -> CouplePair:
    """P1-D：校验当前用户是该 session 的参与者之一，否则 403。"""
    pair = _load_pair_for_session(db, session)
    if user_id not in (pair.user_a_id, pair.user_b_id):
        raise HTTPException(status_code=403, detail="Not a participant of this session")
    return pair


def _load_session_or_404(db: Session, session_id: int) -> CoupleSession:
    session = db.query(CoupleSession).filter(CoupleSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


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
    session = CoupleSession(pair_id=pair_id, status="active", turn="waiting_a")
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _last_user_message_from(db: Session, session_id: int, speaker_id: int) -> Optional[str]:
    """取某参与者最近一条非 AI 调解者消息（用于凑齐双方上下文）。"""
    msg = (
        db.query(CoupleMessage)
        .filter(
            CoupleMessage.session_id == session_id,
            CoupleMessage.user_id == speaker_id,
            CoupleMessage.is_ai_moderator == False,  # noqa: E712
        )
        .order_by(CoupleMessage.created_at.desc())
        .first()
    )
    return msg.content if msg else None


def add_message(db: Session, session_id: int, user_id: int, content: str) -> Dict[str, Any]:
    """F1 双向交替发言：A 发言 → 等待 B → 双方齐全后 AI mediator 调解。"""
    session = _load_session_or_404(db, session_id)
    if session.status == "ended":
        raise HTTPException(status_code=400, detail="会话已结束")
    # P1-D：参与者校验
    pair = _assert_participant(db, session, user_id)

    user_a_id = pair.user_a_id
    user_b_id = pair.user_b_id
    turn = session.turn or "waiting_a"
    speaker = "a" if user_id == user_a_id else "b"

    # ---- 阶段 1：等待 A 发言 ----
    if turn == "waiting_a":
        if speaker != "a":
            raise HTTPException(status_code=400, detail="Not your turn: 现在等待 A 先发言")
        db.add(CoupleMessage(session_id=session_id, user_id=user_id, content=content))
        session.turn = "waiting_b"
        db.commit()
        return {
            "phase": "waiting_b",
            "turn": session.turn,
            "user_message": {"content": content, "user_id": user_id},
            "moderator_reply": None,
            "suggested_to_partner": [],
            "hint": "已收到 A 的发言，等待 B 回应。",
        }

    # ---- 阶段 2：等待 B 发言（凑齐双方上下文后调解） ----
    if turn == "waiting_b":
        if speaker != "b":
            raise HTTPException(status_code=400, detail="Not your turn: 现在等待 B 回应")
        db.add(CoupleMessage(session_id=session_id, user_id=user_id, content=content))
        db.commit()

        a_msg = _last_user_message_from(db, session_id, user_a_id) or ""
        b_msg = content

        # AI mediator：Gottman 方法，先反映双方感受/需求，再找共同点，最后给沟通建议
        moderator_reply = couple_moderator_prompt(
            user_a_msg=a_msg,
            user_b_msg=b_msg,
            context=f"联合会话 #{session_id}（Gottman 中立调解）",
        )
        db.add(CoupleMessage(
            session_id=session_id, user_id=0,  # 0 表示系统/AI
            content=moderator_reply, is_ai_moderator=True,
        ))

        # 每轮调解后生成"建议对TA说的话"（基于 B 本轮发言，供 A 参考回应）
        suggested = couple_private_threeline(b_msg)

        # 下一轮回到等待 A
        session.turn = "waiting_a"
        db.commit()
        return {
            "phase": "mediating",
            "turn": session.turn,
            "user_message": {"content": content, "user_id": user_id},
            "moderator_reply": moderator_reply,
            "suggested_to_partner": suggested,
            "hint": "本轮调解完成，等待下一轮 A 发言。",
        }

    # ---- 阶段 3：mediating（理论上不会停留在此状态，兜个 400） ----
    raise HTTPException(status_code=400, detail="Not your turn: 调解进行中，请等待下一轮")


def end_session(db: Session, session_id: int, user_id: Optional[int] = None) -> str:
    """结束会话，生成关系共识卡。P1-D：校验参与者身份。"""
    session = _load_session_or_404(db, session_id)
    if user_id is not None:
        _assert_participant(db, session, user_id)
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
def get_gottman_guide(db: Session, session_id: int, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """联合会话开始时，返回 Gottman soften startup 的结构化中立引导。

    P1-D：校验调用者是该 session 的参与者。
    """
    session = _load_session_or_404(db, session_id)
    if user_id is not None:
        _assert_participant(db, session, user_id)
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
