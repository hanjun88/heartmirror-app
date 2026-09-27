# -*- coding: utf-8 -*-
"""封装对 xinjing 引擎的调用：同进程 import，fail-closed 降级。

安全决策单一权威原则：
- 正常路径下，危机与风险判断统一由 xinjing engine 的 crisis_scan + Meta-Arbiter 完成，
  应用层不再自行构造关键词风险信号（has_weapon_threat / lethality_score 等）。
- 仅当引擎不可用（加载失败 / 调用抛异常）时，才触发独立硬规则危机扫描作为最后防线。
- 引擎异常且硬规则未命中时，返回 SAFE_DEGRADED（安全降级），绝不允许进入普通 LLM 自由对话。
"""
import sys
import logging
from typing import Any, Dict, List, Optional

from .config import ENGINE_REPO_PATH, SYMBOLIC_CONFIDENCE_LOCK, CRISIS_HOTLINE

logger = logging.getLogger(__name__)

# 将引擎仓库加入 sys.path
if ENGINE_REPO_PATH not in sys.path:
    sys.path.insert(0, ENGINE_REPO_PATH)

_engine_available = False
_crisis_scan_fn = None
_MetaArbiter = None
_RiskAssessment = None
_CaseContext = None
_compute_natal_fn = None
_compute_bazi_fn = None
_compute_vedic_fn = None
_compute_ziwei_fn = None

try:
    from engine.astrology_engine import crisis_scan as _cs, CrisisStop
    from engine.meta_arbiter import MetaArbiter as _MA, CaseContext as _CC, RiskAssessment as _RA
    from engine.astrology_engine import compute_natal as _cn
    from engine.bazi_engine import compute_bazi as _cb
    from engine.vedic_engine import compute_vedic as _cv
    from engine.ziwei_engine import compute_ziwei as _cz

    _crisis_scan_fn = _cs
    _MetaArbiter = _MA
    _CaseContext = _CC
    _RiskAssessment = _RA
    _compute_natal_fn = _cn
    _compute_bazi_fn = _cb
    _compute_vedic_fn = _cv
    _compute_ziwei_fn = _cz
    _engine_available = True
    logger.info("xinjing 引擎加载成功")
except Exception as e:
    logger.warning("xinjing 引擎加载失败，将使用降级模式: %s", e)
    _engine_available = False


# ---- 独立硬规则危机扫描（最后防线，仅引擎不可用时使用） ----
_HARD_RISK_KEYWORDS = [
    "自杀", "自伤", "想死", "活不下去", "不想活", "结束生命",
    "杀了", "伤害自己", "跳楼", "割腕", "吃药过量", "抑郁到极点", "没有出路",
]


def _hard_rule_crisis_scan(text: str) -> List[str]:
    """关键词级硬规则危机扫描。

    注意：此函数仅作为引擎不可用时的最后防线，不参与正常路径的安全决策。
    """
    lowered = (text or "").lower()
    return [k for k in _HARD_RISK_KEYWORDS if k.lower() in lowered]


def is_engine_available() -> bool:
    return _engine_available


def _crisis_result(hits: List[str], intervention: str, error: Optional[str] = None) -> Dict[str, Any]:
    """构造危机命中返回。"""
    result: Dict[str, Any] = {
        "is_crisis": True,
        "matched_terms": hits,
        "target_level": "CRISIS",
        "intervention": intervention,
        "hotline": CRISIS_HOTLINE,
        "is_symbolic_annotation": False,
    }
    if error:
        result["error"] = error
    return result


def _safe_degraded_result(error: str) -> Dict[str, Any]:
    """构造安全降级返回：不进入普通 LLM 自由对话。"""
    return {
        "is_crisis": False,
        "matched_terms": [],
        "target_level": "SAFE_DEGRADED",
        "intervention": "",
        "hotline": CRISIS_HOTLINE,
        "is_symbolic_annotation": False,
        "error": error,
    }


def arbitrate(text: str, user_id: str = "anonymous") -> Dict[str, Any]:
    """安全分流：危机检测 + Meta-Arbiter 路由（fail-closed）。

    返回 target_level 可能取值：
        - "CRISIS"        危机，必须熔断并给出热线
        - "SAFE_DEGRADED" 引擎不可用且硬规则未命中，应用层不得进入普通 LLM 自由对话
        - "REPAIR"/"STABILIZE"/"SAFETY_PLAN" 等引擎正常路由结果
    """
    # 引擎整体加载失败：直接走最后防线
    if not _engine_available:
        hits = _hard_rule_crisis_scan(text)
        if hits:
            return _crisis_result(
                hits,
                "检测到危机信号（硬规则兜底），请立即拨打心理援助热线。",
                error="engine_unavailable_hard_rule_triggered",
            )
        return _safe_degraded_result("engine_unavailable")

    try:
        # 0 级危机扫描（引擎权威判断）
        verdict = _crisis_scan_fn(text)
        if verdict.is_crisis:
            return _crisis_result(
                list(verdict.matched_terms),
                "检测到自伤/轻生信号，已触发危机熔断。",
            )

        # Meta-Arbiter 路由（单一权威）。
        # F2：应用层不再自行注入关键词风险信号，RiskAssessment 使用引擎默认中性值，
        # 风险判断完全由 Meta-Arbiter 基于 raw_statement 完成。
        ra = _RiskAssessment()
        ctx = _CaseContext(
            user_id=user_id,
            raw_statement=text,
            risk_assessment=ra,
        )
        arbiter = _MetaArbiter()
        chain = arbiter.route(ctx)
        node = chain.nodes[0] if chain.nodes else None
        return {
            "is_crisis": chain.is_terminal_crisis,
            "matched_terms": list(verdict.matched_terms),
            "target_level": node.target_level.name if node else None,
            "intervention": node.output_payload.get("intervention", "") if node else "",
            "hotline": CRISIS_HOTLINE if chain.is_terminal_crisis else None,
            "is_symbolic_annotation": False,
        }
    except Exception as e:
        # P0-B：引擎调用异常时绝不能 fail-open 进入 REPAIR。
        # 先做硬规则危机扫描；命中则 CRISIS，否则 SAFE_DEGRADED。
        logger.error("arbitrate 引擎调用失败，执行 fail-closed 降级: %s", e)
        hits = _hard_rule_crisis_scan(text)
        if hits:
            return _crisis_result(
                hits,
                "检测到危机信号（引擎异常时硬规则兜底），请立即拨打心理援助热线。",
                error="engine_unavailable_hard_rule_triggered",
            )
        return _safe_degraded_result("engine_unavailable")


def arbitrate_safety(safety_input, user_id: str = "anonymous") -> Dict[str, Any]:
    """安全评估的**显式 SafetyInput 入口**（P3 接线点）。

    与 :func:`arbitrate` 的区别：后者接受裸 ``text``，调用方可能夹带任意数据；
    本函数只接受 :class:`SafetyInput`，并在组装发往引擎的载荷前强制通过
    Symbolic Lock 边界检查。

    ADR-DIV-001 不变量：**守卫命中 ≠ 安全流程失败**。
    检测到象征污染时——剥离污染、以原始用户文本重建纯净 SafetyInput、
    记录审计日志，然后**继续**正常安全裁决。绝不抛异常中断危机判定链路。

    ``arbitrate()`` 保持原签名不变（向后兼容既有调用方与测试）。
    """
    from .divination import SafetyInput, check_safe_boundary

    passed, violations = check_safe_boundary(safety_input)
    if not passed:
        logger.warning(
            "[SYMBOLIC_LOCK_VIOLATION] 剥离象征数据后继续安全评估: user=%s violations=%s",
            user_id, violations,
        )
        # 剥离：以原始用户文本重建纯净输入（不中断安全流程）
        safety_input = SafetyInput(
            user_message=getattr(safety_input, "user_message", "") or ""
        )

    return arbitrate(safety_input.user_message, user_id=user_id)


def natal(dt_utc: str, lat: float, lon: float) -> Dict[str, Any]:
    """西方本命盘（象征层，低置信标注）。"""
    if not _engine_available or not _compute_natal_fn:
        return {"available": False, "disclaimer": "引擎不可用",
                "is_symbolic_annotation": True, "confidence": 0.38}
    try:
        chart = _compute_natal_fn(dt_utc, lat, lon)
        bodies = [{"name": b.name.value, "sign": b.sign.value, "degree": round(b.degree_in_sign, 2)}
                  for b in chart.bodies]
        return {
            "available": True,
            "datetime_utc": dt_utc,
            "bodies": bodies,
            "angles": chart.angles,
            "is_symbolic_annotation": True,
            "confidence": 0.38,
            "disclaimer": "西方星盘为象征层旁注，置信度 [0.36, 0.40]，不构成科学结论。",
        }
    except Exception as e:
        logger.error("natal 引擎调用失败: %s", e)
        return {"available": False, "error": str(e),
                "is_symbolic_annotation": True, "confidence": 0.38}


def bazi(dt_local: str, sex: str = "男") -> Dict[str, Any]:
    """八字排盘（象征层）。"""
    if not _engine_available or not _compute_bazi_fn:
        return {"available": False, "disclaimer": "引擎不可用",
                "is_symbolic_annotation": True, "confidence": 0.39}
    try:
        chart = _compute_bazi_fn(dt_local, sex=sex)
        # P5a：返回四柱干支（year/month/day/hour 的 gan+zhi），供心镜映射到
        # Provider BaziInput 契约。公历→干支换算完全由引擎仓 bazi_engine(lunar-python)
        # 完成，心镜不自行排盘。
        def _pillar(p) -> Dict[str, str]:
            return {
                "gan": str(getattr(p, "gan", "") or ""),
                "zhi": str(getattr(p, "zhi", "") or ""),
            }
        return {
            "available": True,
            "day_master": getattr(chart, "day_master", ""),
            "day_master_wx": getattr(chart, "day_master_wx", ""),
            "strength": getattr(chart, "strength", ""),
            "pillars": {
                "year": _pillar(getattr(chart, "year", None)),
                "month": _pillar(getattr(chart, "month", None)),
                "day": _pillar(getattr(chart, "day", None)),
                "hour": _pillar(getattr(chart, "hour", None)),
            },
            "is_symbolic_annotation": True,
            "confidence": 0.39,
            "disclaimer": "八字为象征层旁注，置信度 [0.36, 0.40]，不构成科学结论。",
        }
    except Exception as e:
        logger.error("bazi 引擎调用失败: %s", e)
        return {"available": False, "error": str(e),
                "is_symbolic_annotation": True, "confidence": 0.39}


def four_engine_composite(dt1: str, lat1: float, lon1: float,
                          dt2: str, lat2: float, lon2: str) -> Dict[str, Any]:
    """四引擎合盘（简化版，象征层全量标注）。"""
    result = {"is_symbolic_annotation": True, "engines": {}}

    n1 = natal(dt1, lat1, lon1)
    result["engines"]["western_natal"] = n1

    b = bazi(dt1)
    result["engines"]["bazi"] = b

    lo, hi = SYMBOLIC_CONFIDENCE_LOCK
    result["confidence_range"] = [lo, hi]
    result["disclaimer"] = "四引擎合盘均为象征层旁注，置信度 [0.36, 0.40]，不参与循证决策。"
    return result
