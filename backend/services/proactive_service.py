# -*- coding: utf-8 -*-
"""主动 Agent：定时检查 + 推送通知。"""
import logging
from datetime import datetime, date, timedelta

from sqlalchemy.orm import Session

from ..models import User, Diary, Notification
from ..database import SessionLocal

logger = logging.getLogger(__name__)


def check_evening_greetings():
    """每天 21:00 检查用户当日是否有情绪记录，无则生成晚间问候。"""
    db = SessionLocal()
    try:
        today = date.today()
        users = db.query(User).all()
        for user in users:
            # 检查今天是否有日记
            has_diary = db.query(Diary).filter(
                Diary.user_id == user.id,
                Diary.created_at >= datetime.combine(today, datetime.min.time()),
            ).first()
            if not has_diary:
                # 检查是否已有今日通知
                existing = db.query(Notification).filter(
                    Notification.user_id == user.id,
                    Notification.notification_type == "evening_greeting",
                    Notification.created_at >= datetime.combine(today, datetime.min.time()),
                ).first()
                if not existing:
                    n = Notification(
                        user_id=user.id,
                        title="晚间问候",
                        body="今天过得怎么样？花一分钟记录一下今天的情绪吧，哪怕只是一句话。",
                        notification_type="evening_greeting",
                    )
                    db.add(n)
        db.commit()
        logger.info("晚间问候检查完成")
    except Exception as e:
        logger.error("晚间问候检查失败: %s", e)
    finally:
        db.close()


def check_high_intensity_care():
    """检测连续 3 天情绪强度≥7 的负面用户，生成关怀通知。"""
    db = SessionLocal()
    try:
        negative_labels = {"难过", "焦虑", "愤怒", "委屈", "孤独", "害怕", "绝望"}
        users = db.query(User).all()
        today = date.today()

        for user in users:
            high_streak = 0
            for i in range(3):
                day = today - timedelta(days=i)
                day_diaries = db.query(Diary).filter(
                    Diary.user_id == user.id,
                    Diary.created_at >= datetime.combine(day, datetime.min.time()),
                    Diary.created_at <= datetime.combine(day, datetime.max.time()),
                ).all()
                has_high_negative = any(
                    d.emotion_label in negative_labels and d.intensity >= 7
                    for d in day_diaries
                )
                if has_high_negative:
                    high_streak += 1
                else:
                    break

            if high_streak >= 3:
                existing = db.query(Notification).filter(
                    Notification.user_id == user.id,
                    Notification.notification_type == "care",
                    Notification.created_at >= datetime.utcnow() - timedelta(hours=12),
                ).first()
                if not existing:
                    n = Notification(
                        user_id=user.id,
                        title="心镜的关怀",
                        body="注意到你这几天情绪都比较沉重。如果你需要聊聊，我在这里。如果感到难以承受，请拨打心理援助热线 12356。",
                        notification_type="care",
                    )
                    db.add(n)
        db.commit()
        logger.info("高强度情绪关怀检查完成")
    except Exception as e:
        logger.error("关怀检查失败: %s", e)
    finally:
        db.close()


def check_recall_notifications():
    """检测连续 7 天未登录用户，生成召回通知。"""
    db = SessionLocal()
    try:
        week_ago = datetime.utcnow() - timedelta(days=7)
        inactive_users = db.query(User).filter(User.last_login_at < week_ago).all()
        for user in inactive_users:
            existing = db.query(Notification).filter(
                Notification.user_id == user.id,
                Notification.notification_type == "recall",
                Notification.created_at >= datetime.utcnow() - timedelta(days=1),
            ).first()
            if not existing:
                n = Notification(
                    user_id=user.id,
                    title="心镜想你了",
                    body="好久不见！最近怎么样？回来记录一下你的情绪吧，我一直在这里等你。",
                    notification_type="recall",
                )
                db.add(n)
        db.commit()
        logger.info("召回通知检查完成")
    except Exception as e:
        logger.error("召回检查失败: %s", e)
    finally:
        db.close()


def start_scheduler():
    """启动 APScheduler 定时任务。"""
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    # 每天 21:00 晚间问候
    scheduler.add_job(check_evening_greetings, "cron", hour=21, minute=0, id="evening_greeting")
    # 每 6 小时检查高强度情绪
    scheduler.add_job(check_high_intensity_care, "interval", hours=6, id="high_intensity_care")
    # 每天 10:00 检查召回
    scheduler.add_job(check_recall_notifications, "cron", hour=10, minute=0, id="recall")

    scheduler.start()
    logger.info("主动 Agent 调度器已启动")
    return scheduler
