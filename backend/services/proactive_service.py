# -*- coding: utf-8 -*-
"""主动 Agent：定时检查 + 推送通知。

设计：
- 每个 check 函数接受可选 db Session，便于测试注入内存库；
  不传时自行打开 SessionLocal（生产由 APScheduler 调用）。
- 主动消息统一写入 Notification 表，notification_type 区分：
  evening_greeting / care / recall / inactivity_nudge / low_mood_care
"""
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List

from sqlalchemy.orm import Session

from ..models import User, Diary, Notification
from ..database import SessionLocal
from ..config import CRISIS_HOTLINE

logger = logging.getLogger(__name__)

# 情绪评分阈值：连续多日日均情绪分低于该值视为情绪持续低落
LOW_MOOD_SCORE = -0.33
NEGATIVE_LABELS = {"难过", "焦虑", "愤怒", "委屈", "孤独", "害怕", "绝望"}
POSITIVE_LABELS = {"开心", "快乐", "满足", "感恩", "幸福", "平静"}


def _open_or_pass(db: Optional[Session]):
    """返回 (session, should_close)。"""
    if db is not None:
        return db, False
    return SessionLocal(), True


def _has_diary_on(db: Session, user_id: int, day: date) -> bool:
    return db.query(Diary).filter(
        Diary.user_id == user_id,
        Diary.created_at >= datetime.combine(day, datetime.min.time()),
        Diary.created_at <= datetime.combine(day, datetime.max.time()),
    ).first() is not None


def _daily_mood_score(db: Session, user_id: int, day: date) -> Optional[float]:
    """当日情绪评分：正情绪 +1，负情绪 -1，中性 0，取平均。无记录返回 None。"""
    diaries = db.query(Diary).filter(
        Diary.user_id == user_id,
        Diary.created_at >= datetime.combine(day, datetime.min.time()),
        Diary.created_at <= datetime.combine(day, datetime.max.time()),
    ).all()
    if not diaries:
        return None
    scores = []
    for d in diaries:
        if d.emotion_label in POSITIVE_LABELS:
            scores.append(1.0)
        elif d.emotion_label in NEGATIVE_LABELS:
            scores.append(-1.0)
        else:
            scores.append(0.0)
    return sum(scores) / len(scores)


def _already_notified_today(db: Session, user_id: int, ntype: str) -> bool:
    today = date.today()
    return db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.notification_type == ntype,
        Notification.created_at >= datetime.combine(today, datetime.min.time()),
    ).first() is not None


def check_evening_greetings(db: Optional[Session] = None):
    """每天 21:00：当日无情绪记录则生成晚间问候。"""
    sess, close = _open_or_pass(db)
    try:
        today = date.today()
        for user in sess.query(User).all():
            if not _has_diary_on(sess, user.id, today):
                if not _already_notified_today(sess, user.id, "evening_greeting"):
                    sess.add(Notification(
                        user_id=user.id, title="晚间问候",
                        body="今天过得怎么样？花一分钟记录一下今天的情绪吧，哪怕只是一句话。",
                        notification_type="evening_greeting",
                    ))
        sess.commit()
        logger.info("晚间问候检查完成")
    except Exception as e:
        logger.error("晚间问候检查失败: %s", e)
    finally:
        if close:
            sess.close()


def check_inactivity_nudge(db: Optional[Session] = None):
    """连续 3 天没记录情绪时，AI 主动询问。"""
    sess, close = _open_or_pass(db)
    try:
        today = date.today()
        for user in sess.query(User).all():
            # 最近 3 天（不含今天）都没有记录，且今天也没有
            days_missing = 0
            for i in range(1, 4):  # 昨天、前天、大前天
                if not _has_diary_on(sess, user.id, today - timedelta(days=i)):
                    days_missing += 1
                else:
                    break
            today_has = _has_diary_on(sess, user.id, today)
            if days_missing >= 3 and not today_has:
                if not _already_notified_today(sess, user.id, "inactivity_nudge"):
                    sess.add(Notification(
                        user_id=user.id, title="心镜想你了",
                        body="你已经连续几天没记录情绪了。最近是不是有点累？我在这里，想聊的时候随时找我。",
                        notification_type="inactivity_nudge",
                    ))
        sess.commit()
        logger.info("断签推送检查完成")
    except Exception as e:
        logger.error("断签推送检查失败: %s", e)
    finally:
        if close:
            sess.close()


def check_persistent_low_mood(db: Optional[Session] = None):
    """连续 3 天情绪评分低于阈值：建议呼吸练习 / 转介专业帮助。"""
    sess, close = _open_or_pass(db)
    try:
        today = date.today()
        for user in sess.query(User).all():
            streak = 0
            for i in range(3):
                day = today - timedelta(days=i)
                score = _daily_mood_score(sess, user.id, day)
                if score is not None and score < LOW_MOOD_SCORE:
                    streak += 1
                else:
                    break
            if streak >= 3:
                existing = sess.query(Notification).filter(
                    Notification.user_id == user.id,
                    Notification.notification_type == "low_mood_care",
                    Notification.created_at >= datetime.utcnow() - timedelta(hours=24),
                ).first()
                if not existing:
                    sess.add(Notification(
                        user_id=user.id, title="心镜的陪伴",
                        body="注意到你这几天情绪都比较低沉。我们一起做一个 5 分钟的呼吸练习好吗？"
                             "（吸气4秒-屏息4秒-呼气6秒，重复几轮）。如果这种低落持续影响生活，"
                             f"建议寻求专业心理帮助，或拨打心理援助热线 {CRISIS_HOTLINE}。",
                        notification_type="low_mood_care",
                    ))
        sess.commit()
        logger.info("持续低落关怀检查完成")
    except Exception as e:
        logger.error("持续低落关怀检查失败: %s", e)
    finally:
        if close:
            sess.close()


def check_high_intensity_care(db: Optional[Session] = None):
    """检测连续 3 天情绪强度≥7 的负面用户，生成关怀通知。"""
    sess, close = _open_or_pass(db)
    try:
        today = date.today()
        for user in sess.query(User).all():
            high_streak = 0
            for i in range(3):
                day = today - timedelta(days=i)
                day_diaries = sess.query(Diary).filter(
                    Diary.user_id == user.id,
                    Diary.created_at >= datetime.combine(day, datetime.min.time()),
                    Diary.created_at <= datetime.combine(day, datetime.max.time()),
                ).all()
                has_high_negative = any(
                    d.emotion_label in NEGATIVE_LABELS and d.intensity >= 7
                    for d in day_diaries
                )
                if has_high_negative:
                    high_streak += 1
                else:
                    break

            if high_streak >= 3:
                existing = sess.query(Notification).filter(
                    Notification.user_id == user.id,
                    Notification.notification_type == "care",
                    Notification.created_at >= datetime.utcnow() - timedelta(hours=12),
                ).first()
                if not existing:
                    sess.add(Notification(
                        user_id=user.id, title="心镜的关怀",
                        body="注意到你这几天情绪都比较沉重。如果你需要聊聊，我在这里。"
                             f"如果感到难以承受，请拨打心理援助热线 {CRISIS_HOTLINE}。",
                        notification_type="care",
                    ))
        sess.commit()
        logger.info("高强度情绪关怀检查完成")
    except Exception as e:
        logger.error("关怀检查失败: %s", e)
    finally:
        if close:
            sess.close()


def check_recall_notifications(db: Optional[Session] = None):
    """检测连续 7 天未登录用户，生成召回通知。"""
    sess, close = _open_or_pass(db)
    try:
        week_ago = datetime.utcnow() - timedelta(days=7)
        for user in sess.query(User).filter(User.last_login_at < week_ago).all():
            existing = sess.query(Notification).filter(
                Notification.user_id == user.id,
                Notification.notification_type == "recall",
                Notification.created_at >= datetime.utcnow() - timedelta(days=1),
            ).first()
            if not existing:
                sess.add(Notification(
                    user_id=user.id, title="心镜想你了",
                    body="好久不见！最近怎么样？回来记录一下你的情绪吧，我一直在这里等你。",
                    notification_type="recall",
                ))
        sess.commit()
        logger.info("召回通知检查完成")
    except Exception as e:
        logger.error("召回检查失败: %s", e)
    finally:
        if close:
            sess.close()


# 主动推送消息类型清单（前端/端点展示用）
PROACTIVE_TYPES = ["evening_greeting", "inactivity_nudge", "low_mood_care", "care", "recall"]


def list_proactive_messages(db: Session, user_id: int) -> List[Notification]:
    """查看当前用户的主动推送消息（待发送=未读 / 已发送=已读）。"""
    return (
        db.query(Notification)
        .filter(Notification.user_id == user_id,
                Notification.notification_type.in_(PROACTIVE_TYPES))
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )


def run_all_checks(db: Optional[Session] = None):
    """手动触发全部检查（测试/管理端点用）。"""
    check_evening_greetings(db)
    check_inactivity_nudge(db)
    check_persistent_low_mood(db)
    check_high_intensity_care(db)
    check_recall_notifications(db)


def start_scheduler():
    """启动 APScheduler 定时任务。"""
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    scheduler.add_job(check_evening_greetings, "cron", hour=21, minute=0, id="evening_greeting")
    scheduler.add_job(check_inactivity_nudge, "cron", hour=9, minute=30, id="inactivity_nudge")
    scheduler.add_job(check_persistent_low_mood, "interval", hours=6, id="low_mood_care")
    scheduler.add_job(check_high_intensity_care, "interval", hours=6, id="high_intensity_care")
    scheduler.add_job(check_recall_notifications, "cron", hour=10, minute=0, id="recall")

    scheduler.start()
    logger.info("主动 Agent 调度器已启动")
    return scheduler
