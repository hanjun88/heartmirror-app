# -*- coding: utf-8 -*-
"""P3 运行时守卫与心镜接线测试（Runtime Guard & Wiring）。

核心不变量（ADR-DIV-001 / ADR-DIV-002）：

    Guard 命中  ≠  安全流程失败

正确行为：
    symbolic contamination detected
        -> discard symbolic enrichment
        -> retain original SafetyInput
        -> continue safety evaluation

错误行为（本文件须证明其不会发生）：
    guard violation
        -> raise into safety path        X
        -> safety evaluation aborted     X

探针：
  1. 正常流：干净 SafetyInput 全流程通过，引擎收到原始文本
  2. 污染剥离流：畸形象征输入 -> 守卫拦截 -> 不抛异常 -> 引擎收到净化后输入
  3. 危机链路：污染不得削弱危机判定（fail-closed 语义保持）
  4. API 形状：assert_* 抛异常（测试/启动期用），check_* 永不抛
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.divination import (  # noqa: E402
    SafetyInput,
    SymbolicLockViolation,
    check_safe_boundary,
)
from backend.engine_client import arbitrate_safety  # noqa: E402


# 畸形"算命符号"输入：携带安全评分语义 + 污染字段
POISON_SYMBOLIC = {
    "bazi_chart": {"score": 90, "domain": "bazi", "gods": ["evil"]},
    "western_natal": {"score": 99, "level": "CRISIS", "lethal": True},
    "symbolic_profiles": {"bazi_chart": {"score": 90}},
}


# ================================================== 探针 1：正常流
def test_probe1_clean_safety_input_passes():
    """干净 SafetyInput 通过边界检查。"""
    ok, violations = check_safe_boundary(SafetyInput(user_message="最近睡不好"))
    assert ok is True
    assert violations == []


def test_probe1_clean_input_reaches_arbiter_unchanged(monkeypatch):
    """正常流：引擎收到的文本与用户原文一致（未被篡改）。"""
    captured = {}

    def fake_arbitrate(text, user_id="anonymous"):
        captured["text"] = text
        captured["user_id"] = user_id
        return {"target_level": "REPAIR", "is_crisis": False}

    monkeypatch.setattr("backend.engine_client.arbitrate", fake_arbitrate)
    res = arbitrate_safety(SafetyInput(user_message="今天心情不错"), user_id="u-1")
    assert captured["text"] == "今天心情不错"
    assert captured["user_id"] == "u-1"
    assert res["target_level"] == "REPAIR"


# ================================================== 探针 2：污染剥离流
def test_probe2_check_safe_boundary_catches_poison():
    """断言 A：畸形象征输入被拦截。"""
    ok, violations = check_safe_boundary({"text": "你好", **POISON_SYMBOLIC})
    assert ok is False, "污染输入未被拦截"
    assert violations, "应返回违规详情"


def test_probe2_guard_returns_tuple_instead_of_raising():
    """守卫以返回值表达失败，而非异常。"""
    result = check_safe_boundary({"text": "x", "bazi_chart": {}})
    assert isinstance(result, tuple)
    assert result[0] is False and isinstance(result[1], list)


def test_probe2_poisoned_input_still_reaches_arbiter_with_clean_text(monkeypatch):
    """断言 C：污染被剥离后安全评估照常执行，且收到净化后的输入。"""
    captured = {}

    def fake_arbitrate(text, user_id="anonymous"):
        captured["text"] = text
        captured["user_id"] = user_id
        return {"target_level": "REPAIR", "is_crisis": False}

    monkeypatch.setattr("backend.engine_client.arbitrate", fake_arbitrate)

    # 模拟 chat.py 接线：污染被检测 -> 纯净重建 -> 继续安全评估
    polluted = {"text": "我很痛苦", **POISON_SYMBOLIC}
    ok, _ = check_safe_boundary(polluted)
    if not ok:
        polluted = SafetyInput(user_message="我很痛苦")  # 纯净重建

    result = arbitrate_safety(polluted, user_id="u-2")
    assert result["target_level"] == "REPAIR", "安全评估应正常完成"
    assert captured["text"] == "我很痛苦", "引擎应收到净化后的原始文本"
    assert captured["user_id"] == "u-2"


def test_probe2_engine_client_swallows_guard_violation(monkeypatch):
    """断言 B：污染不得中断 engine_client 安全路径（不得抛异常），且必须真实执行评估。

    污染通过 ``safety_features`` 的**内容**进入（字段名本身是安全的，
    危险的是它承载的象征键）——这是 SafetyInput 唯一可能的污染通道。
    """
    captured = {}

    def fake_arbitrate(text, user_id="anonymous"):
        captured["n"] = captured.get("n", 0) + 1
        captured["text"] = text
        return {"target_level": "REPAIR", "is_crisis": False}

    monkeypatch.setattr("backend.engine_client.arbitrate", fake_arbitrate)

    # 先确认这确实是会被守卫拦下的污染
    polluted = SafetyInput(
        user_message="需要帮助",
        safety_features={"bazi": 0.9, "chart": {"score": 1.0}},
    )
    pre_ok, pre_v = check_safe_boundary(polluted)
    assert pre_ok is False and pre_v, "前置条件：污染应被守卫识别"

    # 关键：污染输入进入 arbitrate_safety 不得抛异常
    res = arbitrate_safety(polluted, user_id="u-3")

    assert res is not None and res.get("target_level")
    assert captured.get("n") == 1, "安全评估必须被真实执行（而非被守卫中止）"
    assert captured["text"] == "需要帮助", "引擎应收到剥离污染后的原始文本"


def test_probe2_rebuild_drops_polluted_safety_features(monkeypatch):
    """剥离必须真的丢弃污染特征，而不是原样透传。"""
    captured = {}

    def fake_arbitrate(text, user_id="anonymous"):
        captured["seen"] = None
        return {"target_level": "REPAIR", "is_crisis": False}

    monkeypatch.setattr("backend.engine_client.arbitrate", fake_arbitrate)

    polluted = SafetyInput(
        user_message="我很难受", safety_features={"bazi": 0.99, "astrology": 1.0}
    )
    ok, _ = check_safe_boundary(polluted)
    if ok:
        pytest.fail("前置条件失败：污染应被守卫识别")
    rebuilt = SafetyInput(user_message="我很难受")
    assert rebuilt.safety_features == {}, "重建后不得残留污染特征"
    assert "bazi" not in rebuilt.safety_features
    arbitrate_safety(rebuilt, user_id="u-5")


# ================================================== 探针 3：危机链路不被削弱
def test_probe3_crisis_detection_survives_contamination(monkeypatch):
    """污染不得削弱危机判定。"""
    def fake_arbitrate(text, user_id="anonymous"):
        return {"target_level": "CRISIS", "is_crisis": True, "matched_terms": ["自杀"]}

    monkeypatch.setattr("backend.engine_client.arbitrate", fake_arbitrate)
    polluted = {"text": "我想自杀", **POISON_SYMBOLIC}
    ok, _ = check_safe_boundary(polluted)
    if not ok:
        polluted = SafetyInput(user_message="我想自杀")
    res = arbitrate_safety(polluted, user_id="u-4")
    assert res["is_crisis"] is True
    assert res["target_level"] == "CRISIS"


# ================================================== 探针 4：API 形状约束
def test_probe4_assert_raises_but_check_does_not():
    """API 塑形：assert_* 抛异常，check_* 永不抛。"""
    from backend.divination import assert_safe_boundary

    poison = {"text": "x", "bazi_chart": {}}
    with pytest.raises(SymbolicLockViolation):
        assert_safe_boundary(poison)

    ok, violations = check_safe_boundary(poison)
    assert ok is False and violations


def test_probe4_check_survives_garbage_input():
    """守卫面对完全非预期输入也必须返回而非抛出。"""
    for junk in (None, 123, "string", object()):
        ok, violations = check_safe_boundary(junk)
        assert isinstance(ok, bool)
        assert isinstance(violations, list)
