# -*- coding: utf-8 -*-
"""ADR-DIV-002：象征数据隔离（运行时断言，纵深防御）。

类型层隔离是主防线；本模块是第二道保险——即使有人用 dict / Any 绕过类型系统
把象征数据塞进安全输入，在真正进入 Meta-Arbiter 之前也会被拦下。

关键语义（ADR-DIV-001）：

    触发 SymbolicLockViolation 时，**不得**停止安全引擎。
    调用方应丢弃象征数据、用原始 SafetyInput 继续安全流程，并上报诊断。

因此本模块只做两件事：检测 + 描述违规内容，不含任何"停机"逻辑。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .types import (
    FORBIDDEN_SAFETY_FIELDS,
    SafetyInput,
    SymbolicLockViolation,
)

#: 象征数据溯源标记：出现在安全输入中即视为 provenance 泄漏
_PROVENANCE_MARKERS: tuple[str, ...] = (
    "symbolic_profiles",
    "divination_result",
    "content_sha256",
    "provider_commit",
    "western_natal",
    "bazi_chart",
    "ziwei_chart",
    "vedic_d9",
)


def _iter_keys(obj: Any, depth: int = 0) -> Iterable[str]:
    """深度遍历 dict / 模型的字段名（限深，避免循环结构）。"""
    if depth > 4:
        return
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            yield str(k)
            yield from _iter_keys(v, depth + 1)
    elif hasattr(obj, "__dataclass_fields__"):
        for name in obj.__dataclass_fields__:
            yield name
            yield from _iter_keys(getattr(obj, name, None), depth + 1)
    elif hasattr(obj, "model_fields"):  # pydantic
        for name in obj.model_fields:
            yield name


def assert_no_symbolic_fields(safety_input: Any) -> None:
    """禁止安全输入中出现任何 divination / symbolic 字段名。

    命中即 raise SymbolicLockViolation（fail-closed 于**该条数据**，
    而非停掉安全引擎）。
    """
    hits = sorted({
        k for k in _iter_keys(safety_input)
        if k.lower() in FORBIDDEN_SAFETY_FIELDS
    })
    if hits:
        raise SymbolicLockViolation(
            f"安全输入检测到象征层字段: {hits}。"
            "ADR-DIV-002 违规：DivinationResult ∉ SafetyInput。调用方应丢弃象征数据，"
            "使用原始 SafetyInput 继续安全流程，不得因此中断安全裁决。"
        )


def assert_no_divination_provenance(safety_input: Any) -> None:
    """禁止安全输入携带 divination 溯源标记。

    与字段名检查互补：即使字段名被巧妙伪装成 `meta` / `extra`，
    其**值**中若含 divination 特有的溯源键，仍会被拦下。
    """
    keys = {k.lower() for k in _iter_keys(safety_input)}
    hits = sorted(keys.intersection(_PROVENANCE_MARKERS))
    if hits:
        raise SymbolicLockViolation(
            f"安全输入检测到 divination 溯源标记: {hits}。"
            "ADR-DIV-002 违规：象征数据溯源不得进入安全裁决路径。"
        )


def assert_safe_boundary(safety_input: Any) -> None:
    """安全边界总闸：进入 Meta-Arbiter 前调用。"""
    assert_no_symbolic_fields(safety_input)
    assert_no_divination_provenance(safety_input)
    if isinstance(safety_input, SafetyInput):
        assert_no_symbolic_fields(safety_input.to_engine_kwargs())
