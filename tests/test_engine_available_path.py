# -*- coding: utf-8 -*-
"""引擎可用路径测试：验证 chat 路由在引擎正常仲裁时走 LLM 自由对话分支。

CI 默认设置 XINJING_ENGINE_PATH=/nonexistent-engine-path，导致 arbitrate 永远返回
SAFE_DEGRADED，正常 LLM 路径无覆盖。本测试通过 mock arbitrate 返回正常路由结果
（非 CRISIS、非 SAFE_DEGRADED），验证 chat 路由确实走到 chat_completion 调用。

标记：@pytest.mark.engine_available
CI 命令：pytest -m engine_available
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.database import Base, get_db  # noqa: E402
from backend import models  # noqa: E402
import backend.main  # noqa: E402,F401


pytestmark = pytest.mark.engine_available


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

    from backend.main import app
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


def test_chat_engine_available_takes_llm_path(client, monkeypatch):
    """引擎可用（arbitrate 返回 REPAIR 等正常路由）时，chat 必须调用 LLM 而非降级。"""
    from backend.routers import chat as chat_mod

    # 1. Mock arbitrate → 引擎正常仲裁，非危机、非降级
    normal_arb_result = {
        "is_crisis": False,
        "matched_terms": [],
        "target_level": "REPAIR",
        "intervention": "",
        "hotline": None,
        "is_symbolic_annotation": False,
    }
    monkeypatch.setattr(chat_mod, "arbitrate", lambda text, user_id="anonymous": dict(normal_arb_result))

    # 2. Mock LLM → 返回固定回复，验证 chat_completion 被调用
    llm_called = {"count": 0}

    def fake_chat_completion(messages, persona="warm"):
        llm_called["count"] += 1
        return "这是来自 LLM 的正常陪伴回复。"

    monkeypatch.setattr(chat_mod, "chat_completion", fake_chat_completion)

    # 3. Mock memory 服务，避免依赖外部 embedding
    monkeypatch.setattr(chat_mod, "recall_memories", lambda db, uid, text, top_k=5: [])
    monkeypatch.setattr(chat_mod, "build_memory_context", lambda recalled: "")
    monkeypatch.setattr(chat_mod, "extract_and_store", lambda db, uid, msg, reply: [])

    # 4. 发送 chat 请求
    h = _reg(client, "engine_ok@example.com")
    r = client.post("/chat", headers=h, json={"message": "今天和朋友聊得很开心"})

    assert r.status_code == 200, r.text
    data = r.json()

    # 关键断言：走了 LLM 路径（而非降级路径）
    assert llm_called["count"] == 1, "chat_completion should have been called once on the normal LLM path"
    assert data["is_crisis"] is False
    assert data["reply"] == "这是来自 LLM 的正常陪伴回复。"
    # 降级文案不应出现
    assert "维护" not in data["reply"]
    assert "12356" not in data["reply"]


def test_arbitrate_normal_result_does_not_trigger_safe_degraded(monkeypatch):
    """直接验证：当 _engine_available=True 且引擎函数正常返回时，arbitrate 不返回 SAFE_DEGRADED。

    使用 monkeypatch.setattr，测试结束后自动恢复 engine_client 模块状态，
    避免污染同进程内后续的真实引擎契约测试（R2）。
    """
    from backend import engine_client

    class _FakeVerdict:
        is_crisis = False
        matched_terms = []

    class _FakeTargetNode:
        target_level = type("Lvl", (), {"name": "REPAIR"})()
        output_payload = {"intervention": "repair guidance"}

    class _FakeChain:
        is_terminal_crisis = False
        nodes = [_FakeTargetNode()]

    class _FakeArbiter:
        def route(self, ctx):
            return _FakeChain()

    monkeypatch.setattr(engine_client, "_engine_available", True)
    monkeypatch.setattr(engine_client, "_crisis_scan_fn", lambda text: _FakeVerdict())
    monkeypatch.setattr(engine_client, "_MetaArbiter", _FakeArbiter)
    monkeypatch.setattr(engine_client, "_RiskAssessment", lambda: type("RA", (), {})())
    monkeypatch.setattr(engine_client, "_CaseContext", lambda **kw: type("CC", (), kw)())

    result = engine_client.arbitrate("今天想聊聊关系修复")
    assert result["is_crisis"] is False
    assert result["target_level"] == "REPAIR"
    assert result["target_level"] != "SAFE_DEGRADED"
