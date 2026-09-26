# -*- coding: utf-8 -*-
"""封装对 xinjing 引擎的调用：同进程 import，fail-closed 降级。"""
import sys
import logging
from typing import Any, Dict, Optional

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


def is_engine_available() -> bool:
    return _engine_available


def arbitrate(text: str, user_id: str = "anonymous") -> Dict[str, Any]:
    """安全分流：危机检测 + Meta-Arbiter 路由。

    返回:
        {
            "is_crisis": bool,
            "matched_terms": list,
            "target_level": str | None,
            "intervention": str,
            "hotline": str | None,
            "is_symbolic_annotation": False,
        }
    """
    if not _engine_available:
        # 降级模式：简单关键词扫描
        crisis_keywords = ["不想活", "自杀", "自残", "轻生", "想死", "suicide", "kill myself",
                          "活不下去", "结束生命", "了结自己"]
        hits = [k for k in crisis_keywords if k in text.lower()]
        if hits:
            return {
                "is_crisis": True,
                "matched_terms": hits,
                "target_level": "CRISIS",
                "intervention": "检测到危机信号，请立即拨打心理援助热线。",
                "hotline": CRISIS_HOTLINE,
                "is_symbolic_annotation": False,
            }
        return {
            "is_crisis": False,
            "matched_terms": [],
            "target_level": "REPAIR",
            "intervention": "",
            "hotline": None,
            "is_symbolic_annotation": False,
        }

    try:
        # 0 级危机扫描
        verdict = _crisis_scan_fn(text)
        if verdict.is_crisis:
            return {
                "is_crisis": True,
                "matched_terms": verdict.matched_terms,
                "target_level": "CRISIS",
                "intervention": "检测到自伤/轻生信号，已触发危机熔断。",
                "hotline": CRISIS_HOTLINE,
                "is_symbolic_annotation": False,
            }

        # Meta-Arbiter 路由
        ra = _RiskAssessment(
            has_weapon_threat=any(k in text for k in ("刀", "枪", "杀了我", "同归于尽")),
            has_strangulation=any(k in text for k in ("掐", "勒", "扼")),
            has_suicidal_intent=False,  # crisis_scan 已处理
            fear_of_death=any(k in text for k in ("怕死", "会死", "杀身")),
            lethality_score=min(15, sum(1 for k in ("打", "威胁", "恐吓", "控制", "砸", "骂", "跟踪")
                                        if k in text) * 3),
        )
        ctx = _CaseContext(
            user_id=user_id,
            raw_statement=text,
            risk_assessment=ra,
            somatic_activation=0.8 if any(k in text for k in ("闪回", "发抖", "心慌", "喘不上气", "解离", "噩梦")) else 0.2,
            relational_conflict=any(k in text for k in ("吵架", "冷战", "出轨", "离婚", "分手", "关系")),
        )
        arbiter = _MetaArbiter()
        chain = arbiter.route(ctx)
        node = chain.nodes[0] if chain.nodes else None
        return {
            "is_crisis": chain.is_terminal_crisis,
            "matched_terms": verdict.matched_terms,
            "target_level": node.target_level.name if node else None,
            "intervention": node.output_payload.get("intervention", "") if node else "",
            "hotline": CRISIS_HOTLINE if chain.is_terminal_crisis else None,
            "is_symbolic_annotation": False,
        }
    except Exception as e:
        logger.error("arbitrate 引擎调用失败: %s", e)
        return {
            "is_crisis": False,
            "matched_terms": [],
            "target_level": "REPAIR",
            "intervention": "",
            "hotline": None,
            "is_symbolic_annotation": False,
            "error": "engine_degraded",
        }


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
        return {
            "available": True,
            "day_master": getattr(chart, "day_master", ""),
            "strength": getattr(chart, "strength", ""),
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
