# -*- coding: utf-8 -*-
"""占星推演接入层（ADR-DIV-001 / ADR-DIV-002）。

安全定位：**enrichment，非 safety dependency**。

* ADR-DIV-001 Divination Failure Isolation —— Divination 故障不阻断安全流程
* ADR-DIV-002 Symbolic Data Isolation  —— 类型层隔离（主）+ 运行时断言（纵深）
* SAFETY_AUTHORITY = Meta-Arbiter only   —— 本包不参与任何安全裁决
"""
from .enrichment import (
    SECURITY_EVENT_CONTRACT_MISMATCH,
    SECURITY_EVENT_MALFORMED,
    SECURITY_EVENT_PROVIDER_UNAVAILABLE,
    DivinationEnrichment,
)
from .guards import (
    assert_no_divination_provenance,
    assert_no_symbolic_fields,
    assert_safe_boundary,
)
from .types import (
    FORBIDDEN_SAFETY_FIELDS,
    SYMBOLIC_CONFIDENCE_MAX,
    SYMBOLIC_CONFIDENCE_MIN,
    DivinationResult,
    SafetyInput,
    SymbolicAnnotation,
    SymbolicLockViolation,
)

__all__ = [
    "DivinationEnrichment",
    "DivinationResult",
    "SafetyInput",
    "SymbolicAnnotation",
    "SymbolicLockViolation",
    "SYMBOLIC_CONFIDENCE_MIN",
    "SYMBOLIC_CONFIDENCE_MAX",
    "FORBIDDEN_SAFETY_FIELDS",
    "SECURITY_EVENT_PROVIDER_UNAVAILABLE",
    "SECURITY_EVENT_CONTRACT_MISMATCH",
    "SECURITY_EVENT_MALFORMED",
    "assert_no_symbolic_fields",
    "assert_no_divination_provenance",
    "assert_safe_boundary",
]
