# -*- coding: utf-8 -*-
"""ADR-DIV-001 / ADR-DIV-002 接入层测试。

覆盖：
  A. 类型层隔离（主防线）
  B. Symbolic Lock 运行时守卫（纵深防御）
  C. Divination Failure Isolation（NON_BLOCKING）
  D. 对抗场景：象征数据无法影响安全裁决

本文件不含真实 HTTP / 引擎调用——推演层为注入的可控 provider。
真实链路覆盖见 P5（CI 真实 Engine + Provider integration）。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.divination import (  # noqa: E402
    SECURITY_EVENT_CONTRACT_MISMATCH,
    SECURITY_EVENT_MALFORMED,
    SECURITY_EVENT_PROVIDER_UNAVAILABLE,
    SYMBOLIC_CONFIDENCE_MAX,
    SYMBOLIC_CONFIDENCE_MIN,
    DivinationEnrichment,
    DivinationResult,
    SafetyInput,
    SymbolicAnnotation,
    SymbolicLockViolation,
    assert_no_divination_provenance,
    assert_no_symbolic_fields,
    assert_safe_boundary,
)


# ====================================================================== A. 类型层
def test_safety_input_structurally_excludes_divination_fields():
    """ADR-DIV-002 主防线：SafetyInput 结构上不含任何 divination/symbolic 字段。"""
    names = {n.lower() for n in SafetyInput.__dataclass_fields__}
    forbidden = {
        "divination", "divination_result", "symbolic", "symbolic_profiles",
        "bazi", "ziwei", "astrology", "vedic", "liuyao", "natal", "chart",
    }
    assert not (names & forbidden), f"SafetyInput 出现象征字段: {names & forbidden}"


def test_safety_input_rejects_symbolic_kwarg_at_construction():
    """类型层是硬边界：构造 SafetyInput 时无法传入象征字段。"""
    with pytest.raises(TypeError):
        SafetyInput(user_message="hi", divination={"bazi": "..."})  # type: ignore[call-arg]


def test_engine_kwargs_are_whitelist_only():
    """通往引擎调用的出口是白名单，象征数据无缝隙可钻。"""
    kwargs = SafetyInput(
        user_message="今天很焦虑",
        conversation_context="近期在处理关系冲突",
        safety_features={"sleep_hours": 4.0},
    ).to_engine_kwargs()
    assert set(kwargs) <= {
        "text", "conversation_context", "behavioral_signals", "safety_features"
    }
    forbidden = {"divination", "symbolic", "bazi", "ziwei", "astrology", "chart"}
    assert not (set(kwargs) & forbidden)


def test_symbolic_confidence_clamped_into_lock_range():
    """象征置信度构造时即被夹到硬锁区间，调用方无法传入越界值。"""
    too_high = SymbolicAnnotation(domain="bazi", engine="e", content="c", confidence=0.97)
    too_low = SymbolicAnnotation(domain="bazi", engine="e", content="c", confidence=0.05)
    assert too_high.confidence == SYMBOLIC_CONFIDENCE_MAX
    assert too_low.confidence == SYMBOLIC_CONFIDENCE_MIN
    assert SYMBOLIC_CONFIDENCE_MAX < 0.5, "象征置信绝不应达到安全判定所需强度"


def test_symbolic_confidence_rejects_garbage():
    ok = SymbolicAnnotation(domain="bazi", engine="e", content="c", confidence="abc")  # type: ignore[arg-type]
    assert SYMBOLIC_CONFIDENCE_MIN <= ok.confidence <= SYMBOLIC_CONFIDENCE_MAX


# ====================================================================== B. 运行时守卫
def test_guard_blocks_symbolic_field_in_plain_dict():
    """即使绕过类型系统用 dict 注入，守卫仍会拦下。"""
    with pytest.raises(SymbolicLockViolation) as ei:
        assert_no_symbolic_fields({"text": "hi", "bazi_chart": {}})
    assert "ADR-DIV-002" in str(ei.value)


def test_guard_blocks_nested_symbolic_field():
    with pytest.raises(SymbolicLockViolation):
        assert_no_symbolic_fields({"meta": {"nested": {"symbolic_profiles": {}}}})


def test_guard_blocks_disguised_metadata_field():
    """metadata 是最容易被当成旁路的参数名，必须显式禁止。"""
    with pytest.raises(SymbolicLockViolation):
        assert_no_symbolic_fields({"text": "hi", "metadata": {"bazi": "..."}})


def test_guard_blocks_provenance_even_when_field_name_is_clean():
    """字段名伪装成 innocuous，但值含 divination 溯源键 -> 仍应拦下。"""
    with pytest.raises(SymbolicLockViolation):
        assert_no_divination_provenance({"text": "hi", "extra": {"content_sha256": "ab"}})


def test_guard_passes_clean_safety_input():
    assert_safe_boundary(SafetyInput(user_message="我最近睡眠很差"))


def test_guard_passes_clean_engine_kwargs():
    assert_safe_boundary(
        SafetyInput(user_message="焦虑", safety_features={"sleep_hours": 3.0})
    )


# ====================================================================== C. Failure Isolation
def test_provider_unavailable_is_non_blocking():
    """ADR-DIV-001：Provider 不可用 -> 降级无侧注，不抛异常。"""
    events = []

    def boom(_domain):
        raise ConnectionError("provider unreachable")

    res = DivinationEnrichment(boom, security_sink=lambda e, d: events.append(e)).enrich("焦虑")
    assert isinstance(res, DivinationResult)
    assert res.degraded is True
    assert res.is_usable is False
    assert events == [SECURITY_EVENT_PROVIDER_UNAVAILABLE]


def test_contract_mismatch_is_non_blocking_to_safety():
    """契约不一致（供应链安全事件）-> 拒绝侧注 + 告警，安全流程不受影响。"""
    events = []

    class ManifestValidationError(Exception):
        pass

    def boom(_domain):
        raise ManifestValidationError("content_sha256 不匹配")

    res = DivinationEnrichment(boom, security_sink=lambda e, d: events.append(e)).enrich("x")
    assert res.degraded is True
    assert events == [SECURITY_EVENT_CONTRACT_MISMATCH]


def test_malformed_result_is_non_blocking():
    events = []
    res = DivinationEnrichment(
        lambda _d: "not-a-mapping",  # type: ignore[return-value]
        security_sink=lambda e, d: events.append(e),
    ).enrich("x")
    assert res.degraded is True
    assert events == [SECURITY_EVENT_MALFORMED]


def test_missing_provider_config_is_degraded_not_error():
    res = DivinationEnrichment(None).enrich("x")
    assert res.degraded is True and res.reason


def test_security_sink_failure_does_not_escape():
    """诊断链路自身故障不得外溢到安全路径。"""

    def bad_sink(_e, _d):
        raise RuntimeError("sink down")

    def boom(_d):
        raise ConnectionError("down")

    res = DivinationEnrichment(boom, security_sink=bad_sink).enrich("x")
    assert res.degraded is True


# ====================================================================== D. 对抗场景
def test_provider_cannot_smuggle_safety_score_through_symbolic_layer():
    """Provider 即使在象征层夹带 score/level/risk，也必须被丢弃。"""
    res = DivinationEnrichment(lambda _d: {
        "symbols": [
            {"domain": "bazi", "engine": "e", "content": "官鬼爻旺", "confidence": 0.38},
            {"domain": "bazi", "engine": "e", "content": "高分", "score": 0.99},
            {"domain": "bazi", "engine": "e", "content": "高危", "riskLevel": "CRISIS"},
        ]
    }).enrich("x")
    assert len(res.symbols) == 1
    assert res.symbols[0].content == "官鬼爻旺"


def test_annotation_declares_it_is_not_a_safety_verdict():
    """侧注必须自我声明非判定依据，避免被下游当作证据使用。"""
    res = DivinationEnrichment(lambda _d: {
        "symbols": [{"domain": "bazi", "engine": "e", "content": "官鬼爻旺相", "confidence": 0.38}]
    }).enrich("焦虑")
    assert res.is_usable
    rendered = DivinationEnrichment.render_annotation(res)
    assert "非安全判定依据" in rendered
    # 渲染文本若携带 divination 溯源，不得通过 provenance 边界
    with pytest.raises(SymbolicLockViolation):
        assert_no_divination_provenance({"text": rendered, "content_sha256": "x"})


def test_symbolic_lock_violation_does_not_stop_safety():
    """触发 SymbolicLockViolation 后，安全流程必须仍可继续。"""
    recovered = None
    try:
        assert_no_symbolic_fields({"text": "hi", "bazi_chart": {}})
    except SymbolicLockViolation:
        # 捕获后，用**原始**安全输入继续——不中断安全引擎
        recovered = SafetyInput(user_message="hi")
    assert recovered is not None
    assert_safe_boundary(recovered)


def test_degraded_result_renders_empty():
    res = DivinationEnrichment(lambda _d: (_ for _ in ()).throw(ConnectionError())).enrich("x")
    assert DivinationEnrichment.render_annotation(res) == ""


def test_successful_enrichment_produces_clamped_annotation():
    res = DivinationEnrichment(lambda _d: {
        "symbols": [{"domain": "liuyao", "engine": "liuyao-v1", "content": "六爻见官鬼",
                     "confidence": 0.88, "provenance": "22880a4"}],
        "provenance": "22880a4",
    }).enrich("最近很焦虑")
    assert res.is_usable
    assert res.symbols[0].confidence == SYMBOLIC_CONFIDENCE_MAX  # 0.88 被夹到 0.40
    assert "六爻见官鬼" in DivinationEnrichment.render_annotation(res)
