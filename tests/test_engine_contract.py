# -*- coding: utf-8 -*-
"""arbitrate() 真实引擎契约测试（R2）。

本套件直接 `from backend.engine_client import arbitrate, is_engine_available`，
**禁止 mock**。仅在 XINJING_ENGINE_PATH 指向一个真实、可导入的
xinjing-relationship-engine 仓库时运行；否则整个模块自动 skip。

CI 用法：
- test-real-engine job（checkout 真实引擎）：pytest tests/test_engine_contract.py -v
- test-degraded job（/nonexistent-engine-path）：本模块自动 skip，不影响降级路径覆盖

覆盖契约：
  (a) is_engine_available() 在真实路径下返回 True
  (b) 正常输入返回字段完整性（is_crisis/matched_terms/target_level/intervention/hotline/is_symbolic_annotation）
  (c) 正常输入 target_level != SAFE_DEGRADED（证明走了真实引擎路径）
  (d) 危机关键词 "我想自杀" → is_crisis=True 且 target_level == "CRISIS"
  (e) target_level 取值域 ⊆ {CRISIS, SAFETY_PLAN, STABILIZE, REPAIR, SYMBOLIC, SAFE_DEGRADED}
  (f) 非危机输入 target_level ∈ {SAFETY_PLAN, STABILIZE, REPAIR, SYMBOLIC}
"""
import os
import sys

import pytest

# 确保仓库根在 sys.path（tests/ 与 backend/ 同级）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.engine_client import arbitrate, is_engine_available  # noqa: E402

# 模块级开关：引擎不可用时整个契约套件 skip（降级 job 不会误跑）
pytestmark = [
    pytest.mark.engine_contract,
    pytest.mark.skipif(
        not is_engine_available(),
        reason="XINJING_ENGINE_PATH 未指向真实可导入的 xinjing-relationship-engine，契约测试跳过",
    ),
]

# arbitrate() 返回 target_level 合法取值域
ALLOWED_TARGET_LEVELS = {
    "CRISIS", "SAFETY_PLAN", "STABILIZE", "REPAIR", "SYMBOLIC", "SAFE_DEGRADED",
}
# 非危机路径下 Meta-Arbiter 可能路由到的循证层级（CRISIS 由 crisis_scan 单独拦截）
NON_CRISIS_TARGET_LEVELS = {"SAFETY_PLAN", "STABILIZE", "REPAIR", "SYMBOLIC"}

# 必含字段及其期望类型
REQUIRED_FIELDS = {
    "is_crisis": bool,
    "matched_terms": list,
    "target_level": str,
    "intervention": str,
    "hotline": (str, type(None)),
    "is_symbolic_annotation": bool,
}


def test_a_engine_available_true():
    """(a) 真实路径下 is_engine_available() 必须为 True。"""
    assert is_engine_available() is True, (
        "引擎应已加载；若失败请检查 XINJING_ENGINE_PATH 是否指向含 engine/ 包的仓库根"
    )


def test_b_normal_input_field_completeness():
    """(b) 正常输入返回必含字段齐全且类型正确。"""
    result = arbitrate("今天天气不错，心情也可以", user_id="contract-user-normal")

    for field, expected_type in REQUIRED_FIELDS.items():
        assert field in result, f"arbitrate() 返回缺少必含字段: {field}"
        assert isinstance(result[field], expected_type), (
            f"字段 {field} 类型错误: 期望 {expected_type}, 实际 {type(result[field])}"
        )


def test_c_normal_input_not_safe_degraded():
    """(c) 正常输入 target_level 不得为 SAFE_DEGRADED（证明走了真实引擎而非降级路径）。"""
    result = arbitrate("今天和朋友聊得很开心", user_id="contract-user-calm")
    assert result["target_level"] != "SAFE_DEGRADED", (
        f"正常输入不应降级为 SAFE_DEGRADED；实际返回: {result}"
    )
    # 降级路径会带 error 字段；正常路径不应有
    assert "error" not in result, f"正常路径不应携带 error 字段；实际: {result}"


def test_d_crisis_keyword_triggers_crisis():
    """(d) 危机关键词 "我想自杀" 必须触发 is_crisis=True 且 target_level == CRISIS。"""
    result = arbitrate("我想自杀，活着没意义", user_id="contract-user-crisis")

    assert result["is_crisis"] is True, f"危机输入应判定 is_crisis=True；实际: {result}"
    assert result["target_level"] == "CRISIS", (
        f"危机输入 target_level 必须为 CRISIS；实际: {result['target_level']}"
    )
    assert isinstance(result["matched_terms"], list) and len(result["matched_terms"]) > 0, (
        "危机命中应至少返回一个 matched_terms"
    )
    # 危机路径必须给出热线
    assert result["hotline"] is not None, "危机路径 hotline 不得为 None"
    assert result["intervention"], "危机路径 intervention 不得为空"


def test_e_target_level_domain_is_valid():
    """(e) 任意输入的 target_level 取值必须在合法枚举域内。"""
    samples = [
        "今天天气不错",
        "我和伴侣吵了一架",
        "最近总是失眠，情绪低落",
        "我想自杀",
        "你好",
    ]
    for text in samples:
        result = arbitrate(text, user_id="contract-user-domain")
        assert result["target_level"] in ALLOWED_TARGET_LEVELS, (
            f"输入 {text!r} 的 target_level={result['target_level']!r} 越界；"
            f"合法域={ALLOWED_TARGET_LEVELS}"
        )


def test_f_non_crisis_routes_to_evidence_level():
    """(f) 非危机输入经 MetaArbiter 路由后 target_level ∈ 循证层级集合。

    本地验证：RiskAssessment 默认中性值 → evaluate_risk 返回 REPAIR。
    这里不写死 REPAIR，只断言落在 {SAFETY_PLAN, STABILIZE, REPAIR, SYMBOLIC} 内，
    以免未来引擎调参后误报。
    """
    result = arbitrate("我和对象因为钱的事闹矛盾了，想沟通一下",
                       user_id="contract-user-relational")
    assert result["is_crisis"] is False, f"该输入不应是危机；实际: {result}"
    assert result["target_level"] in NON_CRISIS_TARGET_LEVELS, (
        f"非危机输入 target_level={result['target_level']!r} 不在循证层级 "
        f"{NON_CRISIS_TARGET_LEVELS} 内"
    )
