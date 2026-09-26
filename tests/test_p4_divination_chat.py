# -*- coding: utf-8 -*-
"""P4：DivinationEnrichment 接入 chat.py 非危机分支测试。

验证硬约束：
1. 危机分支完全不调用 enrichment
2. 非危机分支 enrichment 结果拼入 system prompt
3. enrichment 异常不阻断主流程
4. degraded（is_usable=False）结果不渲染
5. enrichment 数据绝不进入 arbitrate 输入
"""
import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.database import Base, get_db  # noqa: E402
from backend import models  # noqa: E402
import backend.main  # noqa: E402,F401
from backend.divination.types import DivinationResult, SymbolicAnnotation  # noqa: E402


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


def _usable_div_result():
    """构造一个 is_usable=True 的 DivinationResult。"""
    return DivinationResult(
        domain="liuyao",
        symbols=(
            SymbolicAnnotation(
                domain="liuyao",
                engine="test_engine",
                content="卦象提示：当前关系处于震荡期，宜静不宜动。",
                confidence=0.38,
            ),
        ),
    )


def _degraded_div_result():
    """构造一个 is_usable=False 的 degraded DivinationResult。"""
    return DivinationResult(
        domain="liuyao", degraded=True,
        reason="provider 未配置（占星增强未启用）",
    )


def _patch_non_crisis_mocks(monkeypatch, enrichment_mock=None):
    """统一 mock arbitrate → 非危机 REPAIR；mock 记忆服务；可选 mock enrichment。"""
    from backend.routers import chat as chat_mod

    normal_arb = {
        "is_crisis": False,
        "matched_terms": [],
        "target_level": "REPAIR",
        "intervention": "",
        "hotline": None,
        "is_symbolic_annotation": False,
    }
    monkeypatch.setattr(chat_mod, "arbitrate", lambda text, user_id="anonymous": dict(normal_arb))
    monkeypatch.setattr(chat_mod, "recall_memories", lambda db, uid, text, top_k=5: [])
    monkeypatch.setattr(chat_mod, "build_memory_context", lambda recalled: "")
    monkeypatch.setattr(chat_mod, "extract_and_store", lambda db, uid, msg, reply: [])

    if enrichment_mock is not None:
        monkeypatch.setattr(chat_mod, "_divination_enrichment", enrichment_mock)


# ============ P4 测试 ============

def test_enrichment_only_non_crisis(client, monkeypatch):
    """危机输入时 enrichment.enrich 不被调用。"""
    from backend.routers import chat as chat_mod

    # arbitrate 返回危机
    crisis_arb = {
        "is_crisis": True,
        "matched_terms": ["自杀"],
        "target_level": "CRISIS",
        "intervention": "hotline",
        "hotline": "12356",
    }
    monkeypatch.setattr(chat_mod, "arbitrate", lambda text, user_id="anonymous": dict(crisis_arb))

    mock_enrichment = MagicMock()
    monkeypatch.setattr(chat_mod, "_divination_enrichment", mock_enrichment)

    h = _reg(client, "p4_crisis@example.com")
    r = client.post("/chat", headers=h, json={"message": "我不想活了"})
    assert r.status_code == 200
    assert r.json()["is_crisis"] is True
    # 关键断言：危机路径完全不调用 enrichment
    mock_enrichment.enrich.assert_not_called()


def test_enrichment_added_to_prompt(client, monkeypatch):
    """非危机输入时，mock enrichment 返回 usable result，LLM 收到的 system content 包含侧注文本。"""
    from backend.routers import chat as chat_mod

    captured_messages = []

    def fake_chat_completion(messages, persona="warm"):
        captured_messages.append(messages)
        return "LLM 正常回复"

    monkeypatch.setattr(chat_mod, "chat_completion", fake_chat_completion)

    mock_enrichment = MagicMock()
    mock_enrichment.enrich.return_value = _usable_div_result()
    _patch_non_crisis_mocks(monkeypatch, enrichment_mock=mock_enrichment)

    h = _reg(client, "p4_ok@example.com")
    r = client.post("/chat", headers=h, json={"message": "今天和伴侣吵了一架，心里很乱"})
    assert r.status_code == 200
    assert r.json()["reply"] == "LLM 正常回复"

    # enrich 必须被调用一次
    mock_enrichment.enrich.assert_called_once_with("今天和伴侣吵了一架，心里很乱")

    # system message 必须包含侧注标记和象征内容
    assert len(captured_messages) == 1
    system_content = captured_messages[0][0]["content"]
    assert "占星侧注" in system_content
    assert "仅供参考" in system_content
    assert "不影响安全评估" in system_content
    assert "卦象提示" in system_content


def test_enrichment_failure_does_not_block(client, monkeypatch):
    """enrichment.enrich 抛异常时，对话仍正常返回 200。"""
    from backend.routers import chat as chat_mod

    def fake_chat_completion(messages, persona="warm"):
        return "即使占星挂了也能正常回复"

    monkeypatch.setattr(chat_mod, "chat_completion", fake_chat_completion)

    mock_enrichment = MagicMock()
    mock_enrichment.enrich.side_effect = RuntimeError("divination provider exploded")
    _patch_non_crisis_mocks(monkeypatch, enrichment_mock=mock_enrichment)

    h = _reg(client, "p4_fail@example.com")
    r = client.post("/chat", headers=h, json={"message": "随便聊聊"})
    # 关键断言：不崩溃，仍返回 200 + LLM 回复
    assert r.status_code == 200
    assert r.json()["reply"] == "即使占星挂了也能正常回复"
    assert r.json()["is_crisis"] is False


def test_enrichment_not_usable_skipped(client, monkeypatch):
    """enrichment 返回 degraded result（is_usable=False）时，prompt 中不包含侧注。"""
    from backend.routers import chat as chat_mod

    captured_messages = []

    def fake_chat_completion(messages, persona="warm"):
        captured_messages.append(messages)
        return "无侧注时的正常回复"

    monkeypatch.setattr(chat_mod, "chat_completion", fake_chat_completion)

    mock_enrichment = MagicMock()
    mock_enrichment.enrich.return_value = _degraded_div_result()
    _patch_non_crisis_mocks(monkeypatch, enrichment_mock=mock_enrichment)

    h = _reg(client, "p4_degraded@example.com")
    r = client.post("/chat", headers=h, json={"message": "今天想聊聊"})
    assert r.status_code == 200

    # enrich 被调用了，但结果是 degraded → 不渲染
    mock_enrichment.enrich.assert_called_once()
    system_content = captured_messages[0][0]["content"]
    assert "占星侧注" not in system_content
    assert "卦象提示" not in system_content


def test_enrichment_not_in_arb_result(client, monkeypatch):
    """enrichment 结果不出现在 arbitrate 的输入参数中。"""
    from backend.routers import chat as chat_mod

    arb_calls = []

    def capturing_arbitrate(text, user_id="anonymous"):
        arb_calls.append({"text": text, "user_id": user_id})
        return {
            "is_crisis": False,
            "matched_terms": [],
            "target_level": "REPAIR",
            "intervention": "",
            "hotline": None,
        }

    monkeypatch.setattr(chat_mod, "arbitrate", capturing_arbitrate)
    monkeypatch.setattr(chat_mod, "chat_completion", lambda messages, persona="warm": "ok")
    monkeypatch.setattr(chat_mod, "recall_memories", lambda db, uid, text, top_k=5: [])
    monkeypatch.setattr(chat_mod, "build_memory_context", lambda recalled: "")
    monkeypatch.setattr(chat_mod, "extract_and_store", lambda db, uid, msg, reply: [])

    # enrichment 返回 usable result，但它只能在 arbitrate 之后运行
    mock_enrichment = MagicMock()
    mock_enrichment.enrich.return_value = _usable_div_result()
    monkeypatch.setattr(chat_mod, "_divination_enrichment", mock_enrichment)

    h = _reg(client, "p4_arb@example.com")
    r = client.post("/chat", headers=h, json={"message": "关系修复话题"})
    assert r.status_code == 200

    # arbitrate 只被调用一次，且输入是纯用户文本，不含任何 divination 数据
    assert len(arb_calls) == 1
    assert arb_calls[0]["text"] == "关系修复话题"
    assert arb_calls[0]["user_id"]
    # 关键断言：arbitrate 输入中没有 symbols / divination_result / 象征层 等字段
    assert "symbols" not in arb_calls[0]
    assert "divination" not in arb_calls[0]["text"]
    assert "卦象" not in arb_calls[0]["text"]
