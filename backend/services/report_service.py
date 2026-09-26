# -*- coding: utf-8 -*-
"""报告服务：周复盘 + 分享卡片。"""
import json
import logging
from datetime import datetime, timedelta, date
from typing import List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from ..models import Diary, Memory
from ..llm_client import generate_report_insights

logger = logging.getLogger(__name__)

# 简单内存缓存
_report_cache: Dict[str, tuple[datetime, Any]] = {}
CACHE_TTL = timedelta(hours=1)


def generate_weekly_report(db: Session, user_id: int) -> Dict[str, Any]:
    """生成本周情绪复盘报告。"""
    cache_key = f"weekly_{user_id}"
    if cache_key in _report_cache:
        cached_time, cached_data = _report_cache[cache_key]
        if datetime.utcnow() - cached_time < CACHE_TTL:
            return cached_data

    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    # 情绪分布
    dist_rows = db.query(Diary.emotion_label, func.count(Diary.id)).filter(
        Diary.user_id == user_id, Diary.created_at >= week_ago
    ).group_by(Diary.emotion_label).all()
    emotion_distribution = {label: count for label, count in dist_rows}

    # 强度趋势（按天）
    diaries = db.query(Diary).filter(
        Diary.user_id == user_id, Diary.created_at >= week_ago
    ).order_by(Diary.created_at).all()

    daily_avg: Dict[str, List[int]] = {}
    for d in diaries:
        day_key = d.created_at.strftime("%m-%d")
        daily_avg.setdefault(day_key, []).append(d.intensity)
    intensity_trend = [
        {"date": day, "avg_intensity": round(sum(vals) / len(vals), 1)}
        for day, vals in sorted(daily_avg.items())
    ]

    # 高频触发词
    word_freq: Dict[str, int] = {}
    for d in diaries:
        if d.description:
            import re
            words = re.findall(r'[\u4e00-\u9fa5]{2,}', d.description)
            for w in words:
                word_freq[w] = word_freq.get(w, 0) + 1
    high_freq_triggers = [w for w, _ in sorted(word_freq.items(), key=lambda x: -x[1])[:10]]

    # 本周亮点（高强度积极情绪）
    positive_labels = {"开心", "快乐", "平静", "满足", "感恩", "幸福"}
    highlights = []
    for d in diaries:
        if d.emotion_label in positive_labels and d.intensity >= 7:
            highlights.append(f"{d.created_at.strftime('%m-%d')} {d.emotion_label}({d.intensity}分): {d.description[:50]}")

    # LLM 洞察
    recent_diaries = [
        {"emotion": d.emotion_label, "intensity": d.intensity, "desc": d.description[:100]}
        for d in diaries[-10:]
    ]
    recent_memories = [
        {"content": m.content, "emotion": m.emotion, "importance": m.importance}
        for m in db.query(Memory).filter(Memory.user_id == user_id).order_by(Memory.created_at.desc()).limit(10).all()
    ]
    insights = generate_report_insights(recent_diaries, recent_memories)

    # 下周建议
    suggestions = []
    if diaries:
        avg_intensity = sum(d.intensity for d in diaries) / len(diaries)
        if avg_intensity >= 7:
            suggestions.append("本周情绪强度偏高，下周尝试每天留10分钟做呼吸冥想")
            suggestions.append("记录让你压力最大的三件事，看看哪些可以放下")
        elif avg_intensity <= 4:
            suggestions.append("情绪平稳，下周可以尝试新的小挑战来增加活力")
        else:
            suggestions.append("继续保持情绪记录，尝试识别情绪触发模式")
    else:
        suggestions.append("本周还没有情绪记录，下周开始每天记录一次吧")

    result = {
        "emotion_distribution": emotion_distribution,
        "intensity_trend": intensity_trend,
        "high_freq_triggers": high_freq_triggers,
        "relationship_insights": insights,
        "weekly_highlights": highlights[:5],
        "next_week_suggestions": suggestions,
        "generated_at": now,
    }

    _report_cache[cache_key] = (now, result)
    return result


def generate_share_card(db: Session, user_id: int) -> Dict[str, Any]:
    """生成分享卡片（结构化，不暴露敏感内容）。"""
    report = generate_weekly_report(db, user_id)

    # 情绪关键词：取高频词前3
    keywords = "、".join(report["high_freq_triggers"][:3]) or "平静"

    # 最平静的一天
    calmest_day = "—"
    if report["intensity_trend"]:
        calm = min(report["intensity_trend"], key=lambda x: x["avg_intensity"])
        calmest_day = calm["date"]

    summary = f"本周记录{sum(report['emotion_distribution'].values())}条，情绪关键词：{keywords}"

    # SVG 卡片
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600" viewBox="0 0 400 600">
  <rect width="400" height="600" fill="#faf8f5" rx="20"/>
  <text x="200" y="80" text-anchor="middle" font-size="24" fill="#5a7a8a" font-family="sans-serif">心镜 · 本周情绪卡</text>
  <line x1="60" y1="110" x2="340" y2="110" stroke="#d4e6ed" stroke-width="2"/>
  <text x="200" y="160" text-anchor="middle" font-size="16" fill="#888" font-family="sans-serif">你的情绪关键词</text>
  <text x="200" y="200" text-anchor="middle" font-size="22" fill="#5a9a7a" font-family="sans-serif">{keywords}</text>
  <text x="200" y="260" text-anchor="middle" font-size="16" fill="#888" font-family="sans-serif">最平静的一天</text>
  <text x="200" y="300" text-anchor="middle" font-size="22" fill="#5a7a8a" font-family="sans-serif">{calmest_day}</text>
  <text x="200" y="360" text-anchor="middle" font-size="14" fill="#999" font-family="sans-serif">{summary}</text>
  <text x="200" y="450" text-anchor="middle" font-size="13" fill="#bbb" font-family="sans-serif">心镜 · 看见自己，遇见平静</text>
  <text x="200" y="480" text-anchor="middle" font-size="11" fill="#ccc" font-family="sans-serif">扫码记录你的情绪 →</text>
</svg>'''

    return {
        "title": "本周心镜记录",
        "emotion_keywords": keywords,
        "calmest_day": calmest_day,
        "summary": summary,
        "invite_link": "https://heartmirror.app/invite",
        "svg_content": svg,
    }
