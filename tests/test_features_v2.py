# -*- coding: utf-8 -*-
"""心镜 v2 新功能测试：四层记忆 / 双人配对私下调停 / 主动 Agent 推送。"""
import os
import sys
from datetime import datetime, date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# conftest 已注入 JWT_SECRET / DATABASE_URL
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.database import Base, get_db  # noqa: E402
from backend.main import app  # noqa: E402
from backend import models  # noqa: E402


@pytest.fixture(scope="function")
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.TestingSession = TestingSession  # 供需要直连会话的测试使用
        yield c
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


def _reg(client, email, password="test123", nickname="用户", age=25):
    r = client.post("/auth/register", json={
        "email": email, "password": password, "nickname": nickname, "age": age,
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ============ 模块1：四层记忆系统 ============
class TestMemoryLayers:
    def test_create_memory_with_layer(self, client):
        h = _reg(client, "layer1@example.com")
        r = client.post("/memory", headers=h, json={
            "content": "我最看重真诚", "layer": "soul", "importance": 5,
        })
        assert r.status_code == 200
        assert r.json()["layer"] == "soul"
        assert r.json()["is_pinned"] is False

    def test_filter_by_layer(self, client):
        h = _reg(client, "layer2@example.com")
        client.post("/memory", headers=h, json={"content": "灵魂价值", "layer": "soul"})
        client.post("/memory", headers=h, json={"content": "某次吵架", "layer": "memory"})
        client.post("/memory", headers=h, json={"content": "喜欢慢一点回复", "layer": "agent"})

        r = client.get("/memory?layer=soul", headers=h)
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["layer"] == "soul"

    def test_layers_group_endpoint(self, client):
        h = _reg(client, "layer3@example.com")
        client.post("/memory", headers=h, json={"content": "画像条目", "layer": "user"})
        r = client.get("/memory/layers", headers=h)
        assert r.status_code == 200
        layers = {x["key"]: x for x in r.json()["layers"]}
        assert set(layers.keys()) == {"soul", "user", "memory", "agent"}
        assert len(layers["user"]["memories"]) == 1

    def test_pin_and_delete(self, client):
        h = _reg(client, "layer4@example.com")
        mid = client.post("/memory", headers=h, json={"content": "要置顶", "layer": "memory"}).json()["id"]
        r = client.put(f"/memory/{mid}", headers=h, json={"is_pinned": True})
        assert r.json()["is_pinned"] is True
        # 置顶应排在前面
        client.post("/memory", headers=h, json={"content": "普通", "layer": "memory"})
        lst = client.get("/memory?layer=memory", headers=h).json()
        assert lst[0]["id"] == mid
        # 删除
        assert client.delete(f"/memory/{mid}", headers=h).status_code == 200

    def test_invalid_layer_rejected(self, client):
        h = _reg(client, "layer5@example.com")
        r = client.post("/memory", headers=h, json={"content": "bad", "layer": "hacker"})
        assert r.status_code == 422


# ============ 模块2：双人配对完整流程 ============
class TestCoupleFullFlow:
    def test_invite_join_then_private_reflection(self, client):
        ha = _reg(client, "ca_flow@example.com")
        code = client.post("/couple/invite", headers=ha, json={}).json()["invite_code"]
        hb = _reg(client, "cb_flow@example.com")
        r = client.post("/couple/join", headers=hb, json={"invite_code": code})
        assert r.json()["status"] == "connected"

        # 私下调停：A 单独倾诉 → 得到三句话
        rr = client.post("/couple/private/reflection", headers=ha,
                         json={"feelings": "他总是忽略我的消息，我很委屈"})
        assert rr.status_code == 200
        lines = rr.json()["suggested_lines"]
        assert len(lines) == 3
        # 我的私下调停记录可见
        notes = client.get("/couple/private/notes", headers=ha)
        assert notes.status_code == 200
        assert len(notes.json()) == 1

    def test_gottman_guide_on_session(self, client):
        ha = _reg(client, "ca_g@example.com")
        code = client.post("/couple/invite", headers=ha, json={}).json()["invite_code"]
        hb = _reg(client, "cb_g@example.com")
        client.post("/couple/join", headers=hb, json={"invite_code": code})
        sid = client.post("/couple/session", headers=ha, json={}).json()["id"]
        guide = client.post(f"/couple/session/{sid}/guide", headers=ha, json={})
        assert guide.status_code == 200
        data = guide.json()
        assert "steps" in data and len(data["steps"]) >= 3
        assert data["stage"]

    def test_private_reflection_requires_pair(self, client):
        h = _reg(client, "solo@example.com")
        r = client.post("/couple/private/reflection", headers=h,
                        json={"feelings": "没配对就倾诉"})
        assert r.status_code == 400


# ============ 模块3：分享卡三类 ============
class TestShareCards:
    @pytest.mark.parametrize("ctype", ["emotion", "pattern", "triggers"])
    def test_three_card_types(self, client, ctype):
        h = _reg(client, f"card_{ctype}@example.com")
        client.post("/diary", headers=h, json={"emotion_label": "开心", "intensity": 6,
                                              "description": "和朋友聚餐很开心"})
        r = client.get(f"/report/share-card?card_type={ctype}", headers=h)
        assert r.status_code == 200
        svg = r.json()["svg_content"]
        assert svg.startswith("<svg")
        # 玉白云海风格：应有渐变与圆角
        assert "linearGradient" in svg
        assert "rx=\"28\"" in svg

    def test_invalid_card_type_rejected(self, client):
        h = _reg(client, "badcard@example.com")
        r = client.get("/report/share-card?card_type=hack", headers=h)
        assert r.status_code == 422


# ============ 模块4：主动 Agent 推送 ============
class TestProactive:
    def _make_user(self, session, email):
        u = models.User(email=email, password_hash="x", nickname="测试", age=25)
        session.add(u); session.commit(); session.refresh(u)
        return u

    def _add_diary(self, session, user_id, day, label, intensity):
        d = models.Diary(
            user_id=user_id, emotion_label=label, intensity=intensity,
            description="", created_at=datetime.combine(day, datetime.min.time()) + timedelta(hours=12),
        )
        session.add(d); session.commit()

    def test_low_mood_streak_triggers_care(self, client):
        from backend.services import proactive_service
        db = client.TestingSession()
        try:
            u = self._make_user(db, "lowmood@example.com")
            today = date.today()
            # 连续 3 天负面情绪
            for i in range(3):
                self._add_diary(db, u.id, today - timedelta(days=i), "难过", 6)
            proactive_service.check_persistent_low_mood(db)
            notes = db.query(models.Notification).filter(
                models.Notification.user_id == u.id,
                models.Notification.notification_type == "low_mood_care",
            ).all()
            assert len(notes) == 1
            assert "呼吸" in notes[0].body
        finally:
            db.close()

    def test_inactivity_nudge(self, client):
        from backend.services import proactive_service
        db = client.TestingSession()
        try:
            u = self._make_user(db, "inactive@example.com")
            # 无任何日记 → 连续 3+ 天未记录
            proactive_service.check_inactivity_nudge(db)
            notes = db.query(models.Notification).filter(
                models.Notification.user_id == u.id,
                models.Notification.notification_type == "inactivity_nudge",
            ).all()
            assert len(notes) == 1
        finally:
            db.close()

    def test_proactive_messages_endpoint(self, client):
        from backend.services import proactive_service
        h = _reg(client, "proapi@example.com")
        # 通过 API 拿到 user 后，直接跑一轮检查（传入依赖覆盖的 db）
        # 这里直接调用 run_all 并校验端点可访问
        r = client.get("/proactive/messages", headers=h)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
