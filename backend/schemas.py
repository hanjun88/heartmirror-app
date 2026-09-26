# -*- coding: utf-8 -*-
"""Pydantic 请求/响应模型。"""
from datetime import datetime, date
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, EmailStr, Field


# ---- Auth ----
class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    nickname: Optional[str] = None
    age: Optional[int] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserProfileUpdate(BaseModel):
    nickname: Optional[str] = None
    birth_datetime: Optional[str] = None
    birth_lat: Optional[float] = None
    birth_lon: Optional[float] = None
    sex: Optional[str] = None
    soul_persona: Optional[str] = None


class UserProfileOut(BaseModel):
    id: int
    email: str
    nickname: str
    age: Optional[int] = None
    is_minor: bool
    attachment_type: Optional[str] = None
    love_language: Optional[str] = None
    conflict_style: Optional[str] = None
    emotion_baseline: float
    soul_persona: str
    birth_datetime: Optional[str] = None
    sex: Optional[str] = None

    class Config:
        from_attributes = True


# ---- Diary ----
class DiaryCreate(BaseModel):
    emotion_label: str = Field(..., description="情绪标签")
    intensity: int = Field(..., ge=1, le=10)
    description: str = ""
    tags: List[str] = Field(default_factory=list)


class DiaryOut(BaseModel):
    id: int
    emotion_label: str
    intensity: int
    description: str
    tags: List[str]
    created_at: datetime

    class Config:
        from_attributes = True


class DiaryTrends(BaseModel):
    avg_intensity_7d: float
    avg_intensity_30d: float
    high_freq_triggers: List[Dict[str, Any]]
    emotion_distribution: Dict[str, int]


# ---- Memory（四层：soul/user/memory/agent） ----
class MemoryCreate(BaseModel):
    content: str
    layer: str = "memory"  # soul/user/memory/agent
    emotion: Optional[str] = None
    importance: int = Field(default=3, ge=1, le=5)
    entities: List[str] = Field(default_factory=list)


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    layer: Optional[str] = None
    emotion: Optional[str] = None
    importance: Optional[int] = Field(default=None, ge=1, le=5)
    is_pinned: Optional[bool] = None


class MemoryOut(BaseModel):
    id: int
    layer: str
    content: str
    emotion: Optional[str] = None
    importance: int
    entities: List[str]
    memory_date: date
    is_pinned: bool
    source: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Chat ----
class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    is_crisis: bool = False
    new_memories: List[MemoryOut] = Field(default_factory=list)
    ai_disclaimer: str = "（AI 生成，仅供参考）"


class ChatHistoryOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Report ----
class WeeklyReport(BaseModel):
    emotion_distribution: Dict[str, int]
    intensity_trend: List[Dict[str, Any]]
    high_freq_triggers: List[str]
    relationship_insights: str
    weekly_highlights: List[str]
    next_week_suggestions: List[str]
    generated_at: datetime


class ShareCard(BaseModel):
    title: str
    card_type: str = "emotion"
    emotion_keywords: str
    calmest_day: str
    summary: str
    invite_link: str
    svg_content: str


# ---- Couple ----
class CoupleInviteCreate(BaseModel):
    pass


class CoupleInviteOut(BaseModel):
    invite_code: str
    expires_at: datetime
    status: str


class CoupleJoin(BaseModel):
    invite_code: str


class CoupleStatusOut(BaseModel):
    status: str
    partner_nickname: Optional[str] = None
    pair_id: Optional[int] = None


class CoupleSessionCreate(BaseModel):
    pass


class CoupleSessionOut(BaseModel):
    id: int
    status: str
    turn: str = "waiting_a"
    started_at: datetime
    ended_at: Optional[datetime] = None
    consensus_summary: str = ""

    class Config:
        from_attributes = True


class CoupleMessageCreate(BaseModel):
    content: str


class CouplePrivateReflectionCreate(BaseModel):
    feelings: str = Field(..., description="用户私下对 AI 倾诉的内容")


class CouplePrivateReflectionOut(BaseModel):
    id: int
    feelings: str
    suggested_lines: List[str]
    created_at: datetime

    class Config:
        from_attributes = True


class GottmanGuideOut(BaseModel):
    session_id: int
    stage: str            # 开场 / 表达 / 倾听 / 共识
    steps: List[str]
    prompt: str


# ---- Assessment ----
class AssessmentSubmit(BaseModel):
    answers: List[int] = Field(..., description="答案编号列表")


class AssessmentResultOut(BaseModel):
    assessment_type: str
    result: Dict[str, Any]
    created_at: datetime


# ---- Notification ----
class NotificationOut(BaseModel):
    id: int
    title: str
    body: str
    notification_type: str
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ---- Feedback ----
class FeedbackCreate(BaseModel):
    message_id: int
    feedback_type: str = Field(..., pattern="^(like|dislike)$")
    comment: str = ""
