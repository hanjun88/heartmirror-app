# -*- coding: utf-8 -*-
"""SQLAlchemy 数据模型。"""
from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Date, ForeignKey,
    Boolean, Text, JSON, Time
)
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(100), default="心镜用户")
    birth_datetime = Column(String(30), nullable=True)  # ISO 8601
    birth_lat = Column(Float, nullable=True)
    birth_lon = Column(Float, nullable=True)
    sex = Column(String(10), nullable=True)  # 男/女
    age = Column(Integer, nullable=True)
    is_minor = Column(Boolean, default=False)

    # User 层档案（测评后填入）
    attachment_type = Column(String(50), nullable=True)  # 安全型/焦虑型/回避型/恐惧型
    love_language = Column(String(255), nullable=True)  # JSON 排序结果
    conflict_style = Column(String(50), nullable=True)  # 竞争/合作/妥协/回避/迁就
    emotion_baseline = Column(Float, default=5.0)  # 情绪基线

    # Soul 层：AI 人格
    soul_persona = Column(String(50), default="warm")  # warm/rational/humor

    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, default=datetime.utcnow)

    # 关联
    diaries = relationship("Diary", back_populates="user", cascade="all, delete-orphan")
    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")
    chat_messages = relationship("ChatMessage", back_populates="user", cascade="all, delete-orphan")


class Diary(Base):
    __tablename__ = "diaries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    emotion_label = Column(String(50), nullable=False)  # 开心/难过/愤怒/焦虑/平静...
    intensity = Column(Integer, nullable=False)  # 1-10
    description = Column(Text, default="")
    tags = Column(JSON, default=list)  # ["工作","感情"...]
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="diaries")


class Memory(Base):
    """Memory 层：从对话/日记中提取的结构化记忆。"""
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    emotion = Column(String(50), nullable=True)
    importance = Column(Integer, default=3)  # 1-5
    entities = Column(JSON, default=list)  # 相关人物/事物
    memory_date = Column(Date, default=date.today)
    is_pinned = Column(Boolean, default=False)
    source = Column(String(30), default="chat")  # chat/diary/manual
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="memories")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(20), nullable=False)  # user/assistant
    content = Column(Text, nullable=False)
    is_crisis_response = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="chat_messages")


class Feedback(Base):
    """Agent 层：用户反馈（点赞/点踩）。"""
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=True)
    feedback_type = Column(String(20), nullable=False)  # like/dislike
    comment = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class CouplePair(Base):
    __tablename__ = "couple_pairs"

    id = Column(Integer, primary_key=True, index=True)
    user_a_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_b_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    invite_code = Column(String(20), unique=True, index=True, nullable=False)
    status = Column(String(20), default="pending")  # pending/connected/disconnected
    created_at = Column(DateTime, default=datetime.utcnow)
    connected_at = Column(DateTime, nullable=True)


class CoupleSession(Base):
    __tablename__ = "couple_sessions"

    id = Column(Integer, primary_key=True, index=True)
    pair_id = Column(Integer, ForeignKey("couple_pairs.id"), nullable=False)
    status = Column(String(20), default="active")  # active/ended
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    consensus_summary = Column(Text, default="")


class CoupleMessage(Base):
    __tablename__ = "couple_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("couple_sessions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    is_ai_moderator = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class AssessmentResult(Base):
    __tablename__ = "assessment_results"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    assessment_type = Column(String(50), nullable=False)  # attachment/love_language/conflict
    result_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    notification_type = Column(String(50), default="general")  # evening_greeting/care/recall
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class UsageLog(Base):
    """用户每日使用时长记录（合规用）。"""
    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    log_date = Column(Date, default=date.today)
    seconds_used = Column(Integer, default=0)
