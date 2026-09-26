# -*- coding: utf-8 -*-
"""安全加固测试：P0-B fail-closed / P1-C JWT / P1-D BOLA / F1 双向调解 / CORS。"""
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.database import Base, get_db  # noqa: E402
from backend import models  # noqa: E402  确保建表前模型已注册
import backend.main  # noqa: E402,F401


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

    from backend.main import app  # noqa: E402
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


def _reg(client, email, password="test123", age=25):
    r = client.post("/auth/register", json={
        "email": email, "password": password, "nickname": email.split("@")[0], "age": age,
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_pair(client, email_a="sec_a@example.com", email_b="sec_b@example.com"):
    """建立 A/B 配对并创建一个 session，返回 (headers_a, headers_b, session_id)。"""
    ha = _reg(client, email_a)
    hb = _reg(client, email_b)
    code = client.post("/couple/invite", headers=ha, json={}).json()["invite_code"]
    r = client.post("/couple/join", headers=hb, json={"invite_code": code})
    assert r.status_code == 200
    sid = client.post("/couple/session", headers=ha, json={}).json()["id"]
    return ha, hb, sid


# ============ P0-B：引擎 fail-closed ============
class TestP0BFailClosed:
    def test_engine_exception_no_crisis_returns_safe_degraded(self, monkeypatch):
        """引擎抛异常且无危机关键词 → SAFE_DEGRADED，绝不返回 REPAIR。"""
        from backend import engine_client

        monkeypatch.setattr(engine_client, "_engine_available", True)

        def _boom(*args, **kwargs):
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(engine_client, "_crisis_scan_fn", _boom)
        result = engine_client.arbitrate("今天有点不开心，想找人聊聊")
        assert result["is_crisis"] is False
        assert result["target_level"] == "SAFE_DEGRADED"
        assert result.get("error") == "engine_unavailable"

    def test_engine_exception_with_crisis_keyword_triggers_crisis(self, monkeypatch):
        """引擎抛异常 + 危机关键词 → 硬规则兜底 is_crisis=True。"""
        from backend import engine_client

        monkeypatch.setattr(engine_client, "_engine_available", True)

        def _boom(*args, **kwargs):
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(engine_client, "_crisis_scan_fn", _boom)
        result = engine_client.arbitrate("我真的不想活了，想跳楼")
        assert result["is_crisis"] is True
        assert result["target_level"] == "CRISIS"
        assert result.get("error") == "engine_unavailable_hard_rule_triggered"

    def test_engine_unavailable_no_crisis_returns_safe_degraded(self, monkeypatch):
        """引擎整体不可用（加载失败）且无关键词 → SAFE_DEGRADED。"""
        from backend import engine_client
        monkeypatch.setattr(engine_client, "_engine_available", False)
        result = engine_client.arbitrate("普通心情，想聊聊")
        assert result["is_crisis"] is False
        assert result["target_level"] == "SAFE_DEGRADED"

    def test_chat_safe_degraded_blocks_llm(self, client, monkeypatch):
        """chat 路由在 SAFE_DEGRADED 时返回降级文案，不走 LLM 自由对话。"""
        from backend.routers import chat as chat_mod
        monkeypatch.setattr(
            chat_mod, "arbitrate",
            lambda text, user_id="anonymous": {
                "is_crisis": False, "target_level": "SAFE_DEGRADED",
                "matched_terms": [], "intervention": "", "hotline": "12356",
            },
        )
        h = _reg(client, "deg@example.com")
        r = client.post("/chat", headers=h, json={"message": "今天想随便聊聊"})
        assert r.status_code == 200
        data = r.json()
        assert data["is_crisis"] is False
        # 降级文案特征：提到维护/热线
        assert "维护" in data["reply"] or "12356" in data["reply"]

    def test_chat_abnormal_arbiter_output_fails_closed(self, client, monkeypatch):
        """arbitrate 返回缺字段的异常格式 → 第二层门走安全降级。"""
        from backend.routers import chat as chat_mod
        monkeypatch.setattr(
            chat_mod, "arbitrate",
            lambda text, user_id="anonymous": {"weird": True},  # 缺 is_crisis / target_level
        )
        h = _reg(client, "abn@example.com")
        r = client.post("/chat", headers=h, json={"message": "随便说点什么"})
        assert r.status_code == 200
        # 不应崩溃、不应走普通 LLM
        assert "维护" in r.json()["reply"] or "12356" in r.json()["reply"]


# ============ P1-C：JWT_SECRET 强制校验 ============
class TestP1CJwtSecret:
    def test_missing_jwt_secret_raises(self, monkeypatch):
        """JWT_SECRET 未设置时，import config 应直接 RuntimeError。"""
        monkeypatch.delenv("JWT_SECRET", raising=False)
        if "backend.config" in sys.modules:
            del sys.modules["backend.config"]
        with pytest.raises(RuntimeError):
            import backend.config  # noqa: F401

    def test_short_jwt_secret_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "too-short")
        if "backend.config" in sys.modules:
            del sys.modules["backend.config"]
        with pytest.raises(RuntimeError):
            import backend.config  # noqa: F401


# ============ P1-D：Couple session BOLA/IDOR ============
class TestP1DBola:
    def test_non_participant_cannot_send_message(self, client):
        ha, hb, sid = _make_pair(client)
        # 第三方用户 C
        hc = _reg(client, "sec_c@example.com")
        r = client.post(f"/couple/session/{sid}/message", headers=hc, json={"content": "我能插嘴吗"})
        assert r.status_code == 403

    def test_non_participant_cannot_end_session(self, client):
        ha, hb, sid = _make_pair(client)
        hc = _reg(client, "sec_c2@example.com")
        r = client.post(f"/couple/session/{sid}/end", headers=hc)
        assert r.status_code == 403

    def test_non_participant_cannot_get_guide(self, client):
        ha, hb, sid = _make_pair(client)
        hc = _reg(client, "sec_c3@example.com")
        r = client.post(f"/couple/session/{sid}/guide", headers=hc)
        assert r.status_code == 403

    def test_participant_can_use_guide(self, client):
        ha, hb, sid = _make_pair(client)
        r = client.post(f"/couple/session/{sid}/guide", headers=ha)
        assert r.status_code == 200
        assert "stage" in r.json()


# ============ F1：双向交替发言状态机 ============
class TestF1DualMediation:
    def test_turn_alternation(self, client):
        ha, hb, sid = _make_pair(client)

        # B 抢先发言 → 400（现在等 A）
        r = client.post(f"/couple/session/{sid}/message", headers=hb, json={"content": "B 先说"})
        assert r.status_code == 400

        # A 发言 → 进入 waiting_b
        r = client.post(f"/couple/session/{sid}/message", headers=ha, json={"content": "我觉得我们最近冷战"})
        assert r.status_code == 200
        data = r.json()
        assert data["phase"] == "waiting_b"
        assert data["moderator_reply"] is None

        # A 又发言 → 400（现在等 B）
        r = client.post(f"/couple/session/{sid}/message", headers=ha, json={"content": "A 又说"})
        assert r.status_code == 400

        # B 发言 → 触发 mediator 调解
        r = client.post(f"/couple/session/{sid}/message", headers=hb, json={"content": "我也觉得委屈"})
        assert r.status_code == 200
        data = r.json()
        assert data["phase"] == "mediating"
        assert data["moderator_reply"]
        # 每轮调解后应给出"建议对TA说的话"
        assert "suggested_to_partner" in data

        # 调解后 turn 回到 waiting_a，下一轮 A 可发言
        r = client.post(f"/couple/session/{sid}/message", headers=ha, json={"content": "第二轮 A"})
        assert r.status_code == 200
        assert r.json()["phase"] == "waiting_b"

    def test_end_session_requires_participant(self, client):
        ha, hb, sid = _make_pair(client)
        r = client.post(f"/couple/session/{sid}/end", headers=ha)
        assert r.status_code == 200
        assert r.json()["status"] == "ended"


# ============ P1-E：CORS allowlist ============
class TestP1ECors:
    def test_cors_uses_explicit_origins(self):
        from backend.config import WEB_ORIGINS
        assert "*" not in WEB_ORIGINS
        assert "http://localhost:3000" in WEB_ORIGINS
