# -*- coding: utf-8 -*-
"""报告服务：周复盘 + 分享卡片（玉白云海风格）。"""
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


def generate_share_card(db: Session, user_id: int, card_type: str = "emotion") -> Dict[str, Any]:
    """生成分享卡片（玉白云海风格）。

    card_type: emotion 情绪画像 / pattern 关系模式 / triggers 吵架触发器
    """
    report = generate_weekly_report(db, user_id)

    keywords = "、".join(report["high_freq_triggers"][:3]) or "平静"

    calmest_day = "—"
    if report["intensity_trend"]:
        calm = min(report["intensity_trend"], key=lambda x: x["avg_intensity"])
        calmest_day = calm["date"]

    total = sum(report["emotion_distribution"].values())
    summary = f"本周记录{total}条 · {keywords}"

    titles = {
        "emotion": ("本周情绪画像", "你的情绪云图"),
        "pattern": ("关系模式洞察", "看见相处的节奏"),
        "triggers": ("吵架触发器", "识别情绪的引信"),
    }
    title, subtitle = titles.get(card_type, titles["emotion"])

    svg = _build_yunhai_svg(title, subtitle, card_type, report, keywords, calmest_day, summary)

    return {
        "title": title,
        "card_type": card_type,
        "emotion_keywords": keywords,
        "calmest_day": calmest_day,
        "summary": summary,
        "invite_link": "https://heartmirror.app/invite",
        "svg_content": svg,
    }


# ---------- 玉白云海风格 SVG 渲染 ----------
def _esc(text: Any) -> str:
    s = str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_yunhai_svg(title: str, subtitle: str, card_type: str,
                      report: Dict[str, Any], keywords: str,
                      calmest_day: str, summary: str) -> str:
    """玉白云海：淡雅白 / 浅青 / 淡墨渐变 + 云海纹理 + 圆角卡片。"""
    body = _svg_body(card_type, report, keywords, calmest_day)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="640" '
        'viewBox="0 0 420 640" font-family="-apple-system, PingFang SC, Microsoft YaHei, sans-serif">'
        '<defs>'
        '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#ffffff"/>'
        '<stop offset="55%" stop-color="#eef5f7"/>'
        '<stop offset="100%" stop-color="#dce9ee"/>'
        '</linearGradient>'
        '<linearGradient id="ink" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0%" stop-color="#8fb3bf"/>'
        '<stop offset="100%" stop-color="#5a7a8a"/>'
        '</linearGradient>'
        '<radialGradient id="sun" cx="50%" cy="50%" r="50%">'
        '<stop offset="0%" stop-color="#ffffff" stop-opacity="0.9"/>'
        '<stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>'
        '</radialGradient>'
        '</defs>'
        '<rect x="0" y="0" width="420" height="640" rx="28" fill="url(#bg)"/>'
        '<ellipse cx="80" cy="120" rx="120" ry="34" fill="#ffffff" opacity="0.55"/>'
        '<ellipse cx="330" cy="90" rx="110" ry="30" fill="#ffffff" opacity="0.45"/>'
        '<ellipse cx="210" cy="560" rx="200" ry="46" fill="#ffffff" opacity="0.5"/>'
        '<ellipse cx="60" cy="600" rx="130" ry="30" fill="#ffffff" opacity="0.4"/>'
        '<circle cx="340" cy="150" r="70" fill="url(#sun)"/>'
        '<circle cx="210" cy="92" r="30" fill="none" stroke="url(#ink)" stroke-width="3"/>'
        '<path d="M196 92 q14 -18 28 0 q-14 18 -28 0 Z" fill="#7a9a8a" opacity="0.8"/>'
        f'<text x="210" y="150" text-anchor="middle" font-size="23" font-weight="700" fill="#4a6a7a">{_esc(title)}</text>'
        f'<text x="210" y="176" text-anchor="middle" font-size="13" fill="#9db8c2">{_esc(subtitle)}</text>'
        '<line x1="150" y1="192" x2="270" y2="192" stroke="#cfe0e6" stroke-width="1.5"/>'
        f'{body}'
        f'<text x="210" y="596" text-anchor="middle" font-size="12" fill="#8aa5b0">{_esc(summary)}</text>'
        '<text x="210" y="618" text-anchor="middle" font-size="11" fill="#b6c8cf">心镜 · 看见自己，遇见平静</text>'
        '</svg>'
    )


def _svg_body(card_type: str, report: Dict[str, Any],
              keywords: str, calmest_day: str) -> str:
    if card_type == "emotion":
        dist = report["emotion_distribution"]
        max_c = max(dist.values()) if dist else 1
        rows = []
        y = 230
        for label, count in sorted(dist.items(), key=lambda x: -x[1])[:5]:
            w = max(20, int(180 * count / max_c))
            rows.append(
                f'<text x="60" y="{y+12}" font-size="13" fill="#5a7a8a">{_esc(label)}</text>'
                f'<rect x="130" y="{y}" width="{w}" height="16" rx="8" fill="url(#ink)" opacity="0.75"/>'
                f'<text x="{140+w}" y="{y+12}" font-size="11" fill="#8aa5b0">{count}</text>'
            )
            y += 34
        if not rows:
            rows.append('<text x="210" y="260" text-anchor="middle" font-size="13" fill="#9db8c2">本周还没有记录，开始记录吧</text>')
        return "\n  ".join(rows)

    if card_type == "pattern":
        insights = report.get("relationship_insights", "")
        chunks = [insights[i:i+18] for i in range(0, len(insights), 18)][:5]
        tspans = "".join(f'<tspan x="60" dy="{i*22}">{_esc(c)}</tspan>' for i, c in enumerate(chunks))
        return (
            '<rect x="40" y="220" width="340" height="210" rx="16" fill="#ffffff" opacity="0.7"/>'
            '<text x="60" y="250" font-size="13" fill="#7a9a8a" font-weight="600">关系模式</text>'
            f'<text x="60" y="282" font-size="13" fill="#5a6a72">{tspans}</text>'
            f'<text x="60" y="410" font-size="12" fill="#9db8c2">最平静的一天：{_esc(calmest_day)}</text>'
        )

    triggers = report["high_freq_triggers"][:6] or ["暂无明显触发"]
    chips = []
    positions = [(60, 250), (200, 250), (120, 310), (260, 310), (80, 370), (230, 370)]
    for (x, y), w in zip(positions, triggers):
        chips.append(
            f'<rect x="{x}" y="{y}" width="{max(76, len(w)*16+24)}" height="30" rx="15" fill="#e3eef2"/>'
            f'<text x="{x+12}" y="{y+20}" font-size="13" fill="#5a7a8a">⚡ {_esc(w)}</text>'
        )
    return "\n  ".join(chips)
