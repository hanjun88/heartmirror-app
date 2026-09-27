# -*- coding: utf-8 -*-
"""P5a：DivinationEnrichment 双源链路（引擎历法 + Provider 规则推演）测试。

全部用 mock 注入 engine_bazi_fn / engine_natal_fn / provider_fn，
**不发真实 HTTP、不真算历法**。真实三仓链路见 CI E2E job。

覆盖（与任务规格一一对应）：
  test_p5a_dual_source_bazi_flow              —— 双源调用链 + symbols 合并
  test_p5a_missing_birth_info_degraded        —— user 无 birth_datetime 时 degraded
  test_p5a_engine_unavailable_degraded         —— 引擎抛异常时 degraded
  test_p5a_provider_unavailable_fallback       —— Provider 不可用时 natal 仍作侧注
  test_p5a_enrich_never_raises               —— 两源都抛异常时 enrich 不抛
  test_p5a_enrichment_not_in_crisis_branch    —— 危机分支不调用 enrichment（回归）
  test_p5a_natal_symbolic_annotation         —— natal 正确标注 symbolic，confidence 在区间
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, ANY

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.divination import (  # noqa: E402
    DivinationEnrichment,
    SYMBOLIC_CONFIDENCE_MIN,
    SYMBOLIC_CONFIDENCE_MAX,
)


# ------------------------------------------------------------------ helpers
def _user(birth_datetime="1990-05-20T14:30:00", sex="男", lat=39.9, lon=116.4):
    return SimpleNamespace(
        birth_datetime=birth_datetime, sex=sex,
        birth_lat=lat, birth_lon=lon,
    )


def _bazi_chart():
    return {
        "available": True,
        "day_master": "丙",
        "day_master_wx": "火",
        "strength": "身强",
        "pillars": {
            "year": {"gan": "甲", "zhi": "子"},
            "month": {"gan": "癸", "zhi": "巳"},
            "day": {"gan": "丙", "zhi": "寅"},
            "hour": {"gan": "甲", "zhi": "午"},
        },
        "is_symbolic_annotation": True,
        "confidence": 0.39,
    }


def _natal_chart():
    return {
        "available": True,
        "datetime_utc": "1990-05-20T06:30:00",
        "bodies": [
            {"name": "太阳", "sign": "金牛", "degree": 29.3},
            {"name": "月亮", "sign": "双鱼", "degree": 12.1},
        ],
        "angles": {"ascendant": "巨蟹"},
        "is_symbolic_annotation": True,
        "confidence": 0.38,
        "disclaimer": "西方星盘为象征层旁注，置信度低，不构成科学结论。",
    }


# ------------------------------------------------------------------ 1. 双源主链路
def test_p5a_dual_source_bazi_flow():
    engine_bazi = MagicMock(return_value=_bazi_chart())
    engine_natal = MagicMock(return_value=_natal_chart())
    provider = MagicMock(return_value={
        "domain": "bazi",
        "matched_rules": [{"id": "r1"}],
        "conclusions": ["命主火旺，宜静守", "官星暗藏，感情需耐心"],
        "total_weight": 2,
        "matched_count": 2,
    })

    enr = DivinationEnrichment(
        provider_fn=provider, engine_bazi_fn=engine_bazi, engine_natal_fn=engine_natal,
    )
    result = enr.enrich("今天和伴侣吵了一架", user=_user())

    # 源 A：引擎 bazi 以 (birth_dt, sex) 调用
    engine_bazi.assert_called_once_with("1990-05-20T14:30:00", "男")
    # 源 A：引擎 natal 以 (dt, lat, lon) 调用
    engine_natal.assert_called_once_with("1990-05-20T14:30:00", 39.9, 116.4)

    # 源 B：provider 以 ("bazi", BaziInput payload) 调用，payload 由四柱映射而来
    provider.assert_called_once()
    domain_arg, payload_arg = provider.call_args.args
    assert domain_arg == "bazi"
    assert payload_arg["year_gan"] == "甲"
    assert payload_arg["year_zhi"] == "子"
    assert payload_arg["day_gan"] == "丙"
    assert payload_arg["hour_zhi"] == "午"
    assert payload_arg["ri_zhu_wx"] == "火"

    # symbols 合并：natal 西方旁注 + Provider 两条 conclusions
    assert result.is_usable is True
    assert result.degraded is False
    domains = {s.domain for s in result.symbols}
    assert "western" in domains          # natal 旁注
    assert "bazi" in domains            # Provider 规则结论
    contents = " ".join(s.content for s in result.symbols)
    assert "命主火旺" in contents
    assert "官星暗藏" in contents
    assert "西方本命盘" in contents


# ------------------------------------------------------------------ 2. 缺出生信息
def test_p5a_missing_birth_info_degraded():
    engine_bazi = MagicMock()
    engine_natal = MagicMock()
    provider = MagicMock()

    enr = DivinationEnrichment(
        provider_fn=provider, engine_bazi_fn=engine_bazi, engine_natal_fn=engine_natal,
    )
    no_birth = SimpleNamespace(birth_datetime=None, sex="男", birth_lat=None, birth_lon=None)
    result = enr.enrich("随便聊聊", user=no_birth)

    assert result.degraded is True
    assert result.is_usable is False
    assert result.symbols == ()
    # 缺出生信息时任何源都不应被调用
    engine_bazi.assert_not_called()
    engine_natal.assert_not_called()
    provider.assert_not_called()


# ------------------------------------------------------------------ 3. 引擎不可用
def test_p5a_engine_unavailable_degraded():
    engine_bazi = MagicMock(side_effect=RuntimeError("lunar-python exploded"))
    engine_natal = MagicMock(side_effect=RuntimeError("swisseph exploded"))
    provider = MagicMock()

    enr = DivinationEnrichment(
        provider_fn=provider, engine_bazi_fn=engine_bazi,
        engine_natal_fn=engine_natal,
    )
    result = enr.enrich("随便聊聊", user=_user())

    # 引擎两源都抛异常 -> 无八字可推、无 natal 旁注 -> degraded，绝不外溢
    assert result.degraded is True
    assert result.is_usable is False
    assert result.symbols == ()
    # 无八字 payload -> Provider 不被调用
    provider.assert_not_called()


# ------------------------------------------------------------------ 4. Provider 不可用但 natal 仍作侧注
def test_p5a_provider_unavailable_fallback():
    engine_bazi = MagicMock(return_value=_bazi_chart())
    engine_natal = MagicMock(return_value=_natal_chart())
    provider = MagicMock(side_effect=ConnectionError("provider unreachable"))

    events = []
    enr = DivinationEnrichment(
        provider_fn=provider, engine_bazi_fn=engine_bazi, engine_natal_fn=engine_natal,
        security_sink=lambda e, d: events.append(e),
    )
    result = enr.enrich("随便聊聊", user=_user())

    # Provider 挂了，但引擎 natal 旁注仍交付 -> 侧注可渲染（部分降级）
    assert any(s.domain == "western" for s in result.symbols)
    rendered = DivinationEnrichment.render_annotation(result)
    assert "西方本命盘" in rendered
    # Provider 不可用事件已上报
    assert "DIVINATION_PROVIDER_UNAVAILABLE" in events


# ------------------------------------------------------------------ 5. enrich 永不抛异常
def test_p5a_enrich_never_raises():
    engine_bazi = MagicMock(side_effect=RuntimeError("boom bazi"))
    engine_natal = MagicMock(side_effect=RuntimeError("boom natal"))
    provider = MagicMock(side_effect=RuntimeError("boom provider"))

    enr = DivinationEnrichment(
        provider_fn=provider, engine_bazi_fn=engine_bazi, engine_natal_fn=engine_natal,
    )
    # 三个源全部抛异常，enrich 必须返回 degraded 结果而不是抛
    result = enr.enrich("随便聊聊", user=_user())
    assert isinstance(result.degraded, bool)
    assert result.degraded is True
    assert result.symbols == ()


# ------------------------------------------------------------------ 7. natal symbolic 标注
def test_p5a_natal_symbolic_annotation():
    engine_bazi = MagicMock(return_value=_bazi_chart())
    engine_natal = MagicMock(return_value=_natal_chart())
    provider = MagicMock(return_value={"domain": "bazi", "conclusions": ["x"]})

    enr = DivinationEnrichment(
        provider_fn=provider, engine_bazi_fn=engine_bazi, engine_natal_fn=engine_natal,
    )
    result = enr.enrich("聊", user=_user())

    natal_sym = next(s for s in result.symbols if s.domain == "western")
    # natal 结果本身即标注为 symbolic 旁注
    assert natal_sym.engine == "xinjing-natal"
    # confidence 被夹在硬锁区间 [0.36, 0.40]
    assert SYMBOLIC_CONFIDENCE_MIN <= natal_sym.confidence <= SYMBOLIC_CONFIDENCE_MAX
    # natal 旁注不经过 Provider：provider 只收到 bazi 域
    provider.assert_called_once_with("bazi", ANY)


# ------------------------------------------------------------------ 6. 危机分支不调用 enrichment（回归）
def test_p5a_enrichment_not_in_crisis_branch(client, monkeypatch):
    """chat 路由：危机（CRISIS）分支完全不调用 enrichment.enrich。"""
    from backend.routers import chat as chat_mod

    crisis_arb = {
        "is_crisis": True, "matched_terms": ["自杀"],
        "target_level": "CRISIS", "intervention": "hotline", "hotline": "12356",
    }
    monkeypatch.setattr(chat_mod, "arbitrate", lambda text, user_id="anonymous": dict(crisis_arb))

    mock_enrichment = MagicMock()
    monkeypatch.setattr(chat_mod, "_divination_enrichment", mock_enrichment)

    # 注册一个用户（P5a：该用户带出生信息，双源本可触发，但危机分支应跳过）
    r = client.post("/auth/register", json={
        "email": "p5a_crisis@example.com", "password": "test123",
        "nickname": "p5a", "age": 25,
    })
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    resp = client.post("/chat", headers=headers, json={"message": "我不想活了"})
    assert resp.status_code == 200
    assert resp.json()["is_crisis"] is True
    # 关键回归断言：危机路径完全不调用 enrichment
    mock_enrichment.enrich.assert_not_called()


# ------------------------------------------------------------------ chat 路由 fixture（复用 P4 风格）
@pytest.fixture(scope="function")
def client():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.database import Base, get_db
    import backend.main  # noqa: F401
    from backend.main import app

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
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
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()
