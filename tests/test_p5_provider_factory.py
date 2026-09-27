# -*- coding: utf-8 -*-
"""P5a：provider_factory + provider_fn(domain, payload) 单元测试。

全部用 MagicMock + monkeypatch，**不发真实 HTTP**。验证：
  1. build_provider_fn 在 env/路径/import 各种失败场景下安全返回 None；
  2. 成功路径返回的 callable 正确调用 DivinationClient.analyze(domain, payload)；
  3. provider_fn 异常不被捕获（交给 enrich() 兜底）；
  4. enrich(text) 无 user 时走旧单源路径 provider_fn(domain)（P4 兼容）。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 让 tests/ 下直接 import backend.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.divination.enrichment import DivinationEnrichment  # noqa: E402
from backend.divination.provider_factory import (  # noqa: E402
    ENV_ENGINE_PATH,
    build_provider_fn,
)


# --------------------------------------------------------------------------- helpers
def _install_fake_divination_consumer(monkeypatch, fake_client_cls: object) -> None:
    """把假的 divination_consumer / divination_consumer.client 注入 sys.modules。"""
    pkg = types.ModuleType("divination_consumer")
    client_mod = types.ModuleType("divination_consumer.client")
    client_mod.DivinationClient = fake_client_cls
    pkg.client = client_mod
    monkeypatch.setitem(sys.modules, "divination_consumer", pkg)
    monkeypatch.setitem(sys.modules, "divination_consumer.client", client_mod)


# --------------------------------------------------------------------------- factory
def test_build_provider_fn_no_env_var_returns_none(monkeypatch):
    """XINJING_ENGINE_PATH 未设置时返回 None（enrichment 自动 degraded）。"""
    monkeypatch.delenv(ENV_ENGINE_PATH, raising=False)
    assert build_provider_fn() is None


def test_build_provider_fn_missing_path_returns_none(monkeypatch):
    """XINJING_ENGINE_PATH 指向不存在路径时返回 None。"""
    monkeypatch.setenv(ENV_ENGINE_PATH, "/nonexistent/path/for-p5-test")
    assert build_provider_fn() is None


def test_build_provider_fn_import_failure_returns_none(monkeypatch, tmp_path):
    """路径存在但 import divination_consumer 失败时返回 None（不抛异常）。"""
    monkeypatch.setenv(ENV_ENGINE_PATH, str(tmp_path))
    monkeypatch.setitem(sys.modules, "divination_consumer", None)
    monkeypatch.setitem(sys.modules, "divination_consumer.client", None)
    assert build_provider_fn() is None


def test_build_provider_fn_success(monkeypatch, tmp_path):
    """env + 路径 + import 都成功时，返回一个 callable。"""
    fake_client_cls = MagicMock()
    monkeypatch.setenv(ENV_ENGINE_PATH, str(tmp_path))
    _install_fake_divination_consumer(monkeypatch, fake_client_cls)

    fn = build_provider_fn()
    assert callable(fn), "build_provider_fn 成功路径应返回 callable"
    fake_client_cls.assert_not_called()


def test_provider_fn_calls_analyze_with_domain_and_payload(monkeypatch, tmp_path):
    """返回的 provider_fn(domain, payload) 实际调用 client.analyze(domain, payload)。"""
    fake_instance = MagicMock()
    fake_instance.analyze.return_value = {"domain": "bazi", "conclusions": []}
    fake_client_cls = MagicMock(return_value=fake_instance)

    monkeypatch.setenv(ENV_ENGINE_PATH, str(tmp_path))
    _install_fake_divination_consumer(monkeypatch, fake_client_cls)

    fn = build_provider_fn()
    assert callable(fn)

    payload = {"year_gan": "甲", "year_zhi": "子", "day_gan": "丙"}
    result = fn("bazi", payload)

    fake_client_cls.assert_called_once_with()
    fake_instance.analyze.assert_called_once_with("bazi", payload)
    assert result == {"domain": "bazi", "conclusions": []}


def test_providerfn_client_lazily_cached(monkeypatch, tmp_path):
    """provider_fn 多次调用只构造一次 DivinationClient（闭包缓存）。"""
    fake_instance = MagicMock()
    fake_instance.analyze.return_value = {"conclusions": []}
    fake_client_cls = MagicMock(return_value=fake_instance)

    monkeypatch.setenv(ENV_ENGINE_PATH, str(tmp_path))
    _install_fake_divination_consumer(monkeypatch, fake_client_cls)

    fn = build_provider_fn()
    fn("bazi", {"a": 1})
    fn("bazi", {"b": 2})
    fake_client_cls.assert_called_once_with()
    assert fake_instance.analyze.call_count == 2


def test_provider_fn_propagates_exception(monkeypatch, tmp_path):
    """client.analyze 抛异常时 provider_fn 不捕获（让 enrich() 处理）。"""
    fake_instance = MagicMock()
    fake_instance.analyze.side_effect = RuntimeError("provider exploded")
    fake_client_cls = MagicMock(return_value=fake_instance)

    monkeypatch.setenv(ENV_ENGINE_PATH, str(tmp_path))
    _install_fake_divination_consumer(monkeypatch, fake_client_cls)

    fn = build_provider_fn()
    with pytest.raises(RuntimeError, match="provider exploded"):
        fn("bazi", {"year_gan": "甲"})


# --------------------------------------------------------------------------- enrich() 旧单源兼容
def test_enrich_legacy_single_arg_provider():
    """enrich(text) 无 user 时走旧单源路径 provider_fn(domain)（P4 兼容）。"""
    provider_fn = MagicMock(return_value={
        "provenance": "unit-test",
        "symbols": [
            {
                "domain": "liuyao",
                "engine": "fake-engine",
                "content": "卦象提示：当前关系处于震荡期，宜静不宜动。",
                "confidence": 0.38,
                "provenance": "unit-test",
            }
        ],
    })

    enrichment = DivinationEnrichment(provider_fn=provider_fn, domain="liuyao")
    result = enrichment.enrich("今天和伴侣吵了一架")

    # 旧单源路径：provider_fn 只收 domain 一个参数（P4/ADR-DIV 既有约定）
    provider_fn.assert_called_once_with("liuyao")
    assert result.is_usable is True
    assert result.degraded is False
    assert len(result.symbols) == 1
    assert "震荡期" in result.symbols[0].content


def test_enrich_legacy_provider_fn_exception_degraded():
    """旧单源路径 provider_fn 抛异常时 enrich() 返回 degraded，绝不抛异常。"""
    provider_fn = MagicMock(side_effect=ConnectionError("provider down"))
    enrichment = DivinationEnrichment(provider_fn=provider_fn, domain="liuyao")

    result = enrichment.enrich("随便聊聊")

    assert result.is_usable is False
    assert result.degraded is True
    assert result.symbols == ()


def test_enrich_no_provider_fn_still_degraded():
    """provider_fn=None（P4 默认）时 enrich() 仍 degraded，行为不变。"""
    enrichment = DivinationEnrichment(provider_fn=None, domain="liuyao")
    result = enrichment.enrich("text")
    assert result.is_usable is False
    assert result.degraded is True
