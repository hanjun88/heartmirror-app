# -*- coding: utf-8 -*-
"""AI 对话路由：危机扫描 → 记忆召回 → 人格注入 → LLM → 记忆提取。"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ChatMessage, User
from ..schemas import ChatRequest, ChatResponse, ChatHistoryOut, MemoryOut
from ..divination import SafetyInput, check_safe_boundary
from ..engine_client import arbitrate
from ..llm_client import chat_completion, SOUL_PERSONAS
from ..services.memory_service import recall_memories, extract_and_store, build_memory_context
from .auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

CRISIS_REPLY_TEMPLATE = (
    "我听到你说的话了，你的安全最重要。\n\n"
    "如果你现在有伤害自己的念头，请立刻拨打全国心理援助热线：12356（24小时）。\n"
    "你也可以联系紧急联系人，或者前往最近的医院急诊。\n\n"
    "你不是一个人，有很多人愿意帮助你。请先照顾好自己。"
)

# 安全降级回复：引擎不可用时不允许进入普通 LLM 自由对话
SAFE_DEGRADED_REPLY_TEMPLATE = (
    "系统当前正在维护，暂时无法提供智能陪伴对话。\n\n"
    "如果你情绪上很难受，可以先尝试做几次缓慢的深呼吸；"
    "也欢迎稍后再回来，或直接联系真人心理咨询师。\n\n"
    "如遇紧急心理危机，请拨打全国心理援助热线：12356（24小时）。"
)


def _persist_assistant_reply(db: Session, user_id: int, content: str, crisis: bool):
    """落库一条 AI 回复（用户消息已由调用方落库或不落库视情况而定）。"""
    ai_msg = ChatMessage(
        user_id=user_id, role="assistant", content=content,
        is_crisis_response=crisis,
    )
    db.add(ai_msg)
    db.commit()


@router.post("", response_model=ChatResponse)
def chat(body: ChatRequest, db: Session = Depends(get_db),
         user: User = Depends(get_current_user)):
    # 1. 危机扫描（硬约束，不走 LLM）
    #
    # P3 接线：先以显式 SafetyInput 建模对话内容，再过 Symbolic Lock 运行时守卫。
    # 关键不变量（ADR-DIV-001）：**守卫命中 ≠ 安全流程失败**——
    # 检测到象征污染时剥离污染数据、保留原始用户文本，然后**继续**安全裁决。
    safety_input = SafetyInput(user_message=body.message)
    boundary_ok, boundary_violations = check_safe_boundary(safety_input)
    if not boundary_ok:
        # 剥离污染 + 审计留痕；绝不 raise 进安全路径
        logger.warning(
            "[SYMBOLIC_LOCK_VIOLATION] 剥离象征污染后继续安全流程: user=%s violations=%s",
            user.id, boundary_violations,
        )
        safety_input = SafetyInput(user_message=body.message)  # 纯净重建

    arb_result = arbitrate(safety_input.user_message, user_id=str(user.id))

    # 第二层 fail-closed 门：即使 arbitrate 返回异常格式或缺失字段，
    # 也默认走安全降级，而不是冒险进入普通 LLM 自由对话。
    raw_is_crisis = arb_result.get("is_crisis")
    target_level = arb_result.get("target_level")
    is_crisis = raw_is_crisis is True
    if not isinstance(target_level, str) or not target_level:
        # 异常返回：降级处理
        target_level = "SAFE_DEGRADED"

    if is_crisis:
        crisis_msg = ChatMessage(
            user_id=user.id, role="user", content=body.message, is_crisis_response=True
        )
        db.add(crisis_msg)
        ai_msg = ChatMessage(
            user_id=user.id, role="assistant", content=CRISIS_REPLY_TEMPLATE,
            is_crisis_response=True,
        )
        db.add(ai_msg)
        db.commit()
        return ChatResponse(
            reply=CRISIS_REPLY_TEMPLATE,
            is_crisis=True,
            new_memories=[],
        )

    # 安全降级门：引擎不可用/异常时，禁止进入普通 LLM 自由对话
    if target_level == "SAFE_DEGRADED":
        degraded_msg = ChatMessage(
            user_id=user.id, role="user", content=body.message, is_crisis_response=False,
        )
        db.add(degraded_msg)
        ai_msg = ChatMessage(
            user_id=user.id, role="assistant", content=SAFE_DEGRADED_REPLY_TEMPLATE,
            is_crisis_response=False,
        )
        db.add(ai_msg)
        db.commit()
        return ChatResponse(
            reply=SAFE_DEGRADED_REPLY_TEMPLATE,
            is_crisis=False,
            new_memories=[],
        )

    # 2. 存储用户消息
    user_msg = ChatMessage(user_id=user.id, role="user", content=body.message)
    db.add(user_msg)
    db.commit()

    # 3. 记忆召回
    recalled = recall_memories(db, user.id, body.message, top_k=5)
    memory_context = build_memory_context(recalled)

    # 4. 构建 system prompt（人格 + 用户档案 + 记忆）
    persona_cfg = SOUL_PERSONAS.get(user.soul_persona, SOUL_PERSONAS["warm"])
    system_parts = [persona_cfg["system_prompt"]]

    # User 层档案注入
    profile_parts = []
    if user.attachment_type:
        profile_parts.append(f"用户依恋类型: {user.attachment_type}")
    if user.love_language:
        profile_parts.append(f"用户爱语: {user.love_language}")
    if user.conflict_style:
        profile_parts.append(f"用户冲突风格: {user.conflict_style}")
    if profile_parts:
        system_parts.append("【用户档案】" + "；".join(profile_parts))

    if memory_context:
        system_parts.append(memory_context)

    system_prompt = "\n\n".join(system_parts)

    # 5. 取最近 20 轮对话历史
    history = db.query(ChatMessage).filter(
        ChatMessage.user_id == user.id
    ).order_by(ChatMessage.created_at.desc()).limit(20).all()
    history.reverse()

    messages = [{"role": "system", "content": system_prompt}]
    for h in history:
        messages.append({"role": h.role, "content": h.content})

    # 6. LLM 生成回复
    reply_text = chat_completion(messages, persona=user.soul_persona)

    # 7. 存储 AI 回复
    ai_msg = ChatMessage(user_id=user.id, role="assistant", content=reply_text)
    db.add(ai_msg)
    db.commit()
    db.refresh(ai_msg)

    # 8. 记忆提取（异步感：同步执行但不阻塞太久）
    new_memories = []
    try:
        new_memories = extract_and_store(db, user.id, body.message, reply_text)
    except Exception as e:
        logger.error("记忆提取失败: %s", e)

    return ChatResponse(
        reply=reply_text,
        is_crisis=False,
        new_memories=[MemoryOut.model_validate(m) for m in new_memories],
    )


@router.get("/history", response_model=list[ChatHistoryOut])
def chat_history(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                 limit: int = 50):
    msgs = db.query(ChatMessage).filter(
        ChatMessage.user_id == user.id
    ).order_by(ChatMessage.created_at.desc()).limit(limit).all()
    msgs.reverse()
    return msgs
