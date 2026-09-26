# -*- coding: utf-8 -*-
"""LLM 调用封装：OpenAI 兼容接口，未配置时用模拟回复并标注。"""
import json
import logging
from typing import List, Dict, Optional

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

logger = logging.getLogger(__name__)

_llm_client = None
_llm_enabled = bool(LLM_API_KEY and LLM_BASE_URL and LLM_MODEL)

if _llm_enabled:
    try:
        from openai import OpenAI
        _llm_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        logger.info("LLM 客户端已初始化: model=%s", LLM_MODEL)
    except Exception as e:
        logger.warning("LLM 客户端初始化失败: %s", e)
        _llm_client = None
        _llm_enabled = False


# ---- 人格配置（Soul 层） ----
SOUL_PERSONAS = {
    "warm": {
        "name": "温暖陪伴型",
        "system_prompt": (
            "你是心镜，一个温暖、共情、有耐心的情绪陪伴者。"
            "你的语气柔软温和，像一个可靠的朋友。"
            "你会先倾听和接纳情绪，再轻轻引导。"
            "不要急于给建议，先让用户感到被理解。"
            "回复简洁温暖，不要长篇大论。"
        ),
    },
    "rational": {
        "name": "理性分析型",
        "system_prompt": (
            "你是心镜，一个理性、客观、善于分析的情绪咨询师。"
            "你基于心理学原理（依恋理论、Gottman 方法、认知行为疗法）分析问题。"
            "你会帮助用户识别情绪模式、认知偏差和关系动力。"
            "回复结构清晰，有分析有洞察，但保持尊重和关怀。"
        ),
    },
    "humor": {
        "name": "幽默开导型",
        "system_prompt": (
            "你是心镜，一个幽默、乐观、有点毒舌但善良的朋友。"
            "你用轻松幽默的方式帮助用户换个角度看问题。"
            "你不会嘲笑用户的痛苦，而是用幽默化解沉重。"
            "在严肃话题上保持分寸，该认真时认真。"
        ),
    },
}


def chat_completion(messages: List[Dict[str, str]], persona: str = "warm") -> str:
    """调用 LLM 生成回复。未配置时返回模拟回复并标注。"""
    if not _llm_enabled or _llm_client is None:
        return _mock_reply(messages, persona)

    try:
        system_msg = SOUL_PERSONAS.get(persona, SOUL_PERSONAS["warm"])["system_prompt"]
        full_messages = [{"role": "system", "content": system_msg}] + messages
        resp = _llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=full_messages,
            temperature=0.7,
            max_tokens=500,
        )
        return resp.choices[0].message.content or "（无回复内容）"
    except Exception as e:
        logger.error("LLM 调用失败: %s", e)
        return _mock_reply(messages, persona)


def extract_memories(user_message: str, ai_reply: str) -> List[Dict]:
    """从对话中提取结构化记忆。LLM 不可用时用规则提取。"""
    if not _llm_enabled or _llm_client is None:
        return _rule_based_memory_extract(user_message, ai_reply)

    try:
        prompt = (
            "从以下用户对话中提取值得记住的结构化记忆条目。"
            "只提取重要性≥3的条目（重要的人际关系事件、情绪模式、关键事实）。\n"
            "返回 JSON 数组，每个元素包含: content(记忆内容), emotion(情绪标签), "
            "importance(1-5), entities(相关人物列表)。\n"
            "如果没有值得记住的，返回空数组 []。\n\n"
            f"用户说: {user_message}\nAI回复: {ai_reply}\n\n"
            "JSON 输出:"
        )
        resp = _llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content or "{}"
        data = json.loads(text)
        if isinstance(data, list):
            return data
        # 可能是 {"memories": [...]}
        return data.get("memories", [])
    except Exception as e:
        logger.error("记忆提取 LLM 调用失败: %s", e)
        return _rule_based_memory_extract(user_message, ai_reply)


def generate_report_insights(recent_diaries: List[Dict], recent_memories: List[Dict]) -> str:
    """生成周报复盘洞察。LLM 不可用时用模板。"""
    if not _llm_enabled or _llm_client is None:
        return _mock_report_insights(recent_diaries)

    try:
        prompt = (
            "基于以下用户本周的情绪日记和记忆条目，写一段关系模式洞察（150字以内）。"
            "聚焦情绪模式、触发因素、积极变化。语气温暖有洞察。\n\n"
            f"日记: {json.dumps(recent_diaries, ensure_ascii=False)}\n"
            f"记忆: {json.dumps(recent_memories, ensure_ascii=False)}"
        )
        resp = _llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=250,
        )
        return resp.choices[0].message.content or "本周情绪有波动，继续记录有助于觉察。"
    except Exception as e:
        logger.error("报告洞察生成失败: %s", e)
        return _mock_report_insights(recent_diaries)


def couple_moderator_prompt(user_a_msg: str, user_b_msg: str, context: str = "") -> str:
    """双人会话 AI 调解者回复。"""
    if not _llm_enabled or _llm_client is None:
        return "（AI 调解者：我听到了双方的声音。也许可以试着先理解对方的感受，再表达自己的需求。——模拟回复，未配置 LLM）"

    try:
        system = (
            "你是心镜的双人关系调解者，基于 Gottman 方法和依恋理论。"
            "你的角色是中立的 facilitator，不站队。"
            "你帮助双方听到彼此的核心需求和恐惧，而不是争论对错。"
            "使用非暴力沟通（观察-感受-需要-请求）框架。"
            "回复简洁温和，引导双方互相理解。"
        )
        prompt = f"背景: {context}\n\n用户A说: {user_a_msg}\n用户B说: {user_b_msg}\n\n作为中立调解者，你如何回应？"
        resp = _llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=300,
        )
        return resp.choices[0].message.content or "让我们试着理解彼此。"
    except Exception as e:
        logger.error("双人调解 LLM 失败: %s", e)
        return "（AI 调解者：我听到了你们的声音。——模拟回复）"


# ---- 模拟回复（LLM 未配置时） ----
def couple_private_threeline(feelings: str) -> List[str]:
    """私下调停：基于用户私下倾诉，生成'建议对TA说的三句话'。

    使用非暴力沟通（观察-感受-需要-请求）结构。LLM 不可用时返回模板。
    """
    fallback = [
        f"我想先听你说——刚才发生的那件事，你当时是什么感受？",
        f"我承认我也有做得不好的地方，我希望我们能一起把它说开。",
        f"我在乎这段关系，我们能不能约定一个下次吵架时的暂停信号？",
    ]
    if not _llm_enabled or _llm_client is None:
        return fallback

    try:
        system = (
            "你是心镜的双人关系私下调停顾问。用户正在单独向你倾诉对伴侣的情绪。"
            "请基于非暴力沟通（观察-感受-需要-请求），生成 3 句'建议他对伴侣说的话'。"
            "要求：语气柔软、不指责、以'我'开头表达感受与需要。"
            "只返回 JSON 数组（3 个字符串），不要解释。"
        )
        resp = _llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": f"用户倾诉: {feelings}"}],
            temperature=0.6,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content or "{}"
        data = json.loads(text)
        lines = data if isinstance(data, list) else data.get("lines", data.get("suggested", []))
        lines = [str(x) for x in lines][:3]
        return lines or fallback
    except Exception as e:
        logger.error("私下调停 LLM 失败: %s", e)
        return fallback


def gottman_opening_guide() -> Dict[str, Any]:
    """Gottman 方法中立引导：联合会话开场的结构化步骤。"""
    return {
        "stage": "开场 ·  soften startup",
        "steps": [
            "第一步：由一方用'我观察到…（只说事实，不评判）'开始",
            "第二步：说出自己的感受（情绪词），而不是指责对方",
            "第三步：说出自己的需要和期待",
            "第四步：向对方提一个具体、可执行的小请求",
        ],
        "prompt": "心镜将作为中立 facilitator，不站队。请用'我观察到…我感到…因为…我希望…'的句式开始。",
    }


# ---- 模拟回复（LLM 未配置时） ----
def _mock_reply(messages: List[Dict[str, str]], persona: str) -> str:
    """生成模拟回复，明确标注。"""
    user_msg = ""
    for m in reversed(messages):
        if m["role"] == "user":
            user_msg = m["content"]
            break

    templates = {
        "warm": [
            "我听到了，你现在一定不好受。能多说一点吗？（模拟回复·未连接 LLM）",
            "谢谢你愿意和我分享。这种感觉确实很难熬。（模拟回复·未连接 LLM）",
            "我在这里陪着你。你的感受是真实的，也是重要的。（模拟回复·未连接 LLM）",
        ],
        "rational": [
            "让我们梳理一下：你提到的核心触发点是什么？这和你过去的依恋模式有关联吗？（模拟回复·未连接 LLM）",
            "从情绪调节的角度看，你目前的强度是多少？我们可以试着做一个深呼吸练习。（模拟回复·未连接 LLM）",
        ],
        "humor": [
            "哈哈，这事儿确实够让人头大的。不过话说回来，你能说出来就已经赢了一半了。（模拟回复·未连接 LLM）",
            "生活有时候就是这样，给你发一副烂牌。但咱打得漂亮不就行了？（模拟回复·未连接 LLM）",
        ],
    }
    import random
    pool = templates.get(persona, templates["warm"])
    return random.choice(pool)


def _rule_based_memory_extract(user_message: str, ai_reply: str) -> List[Dict]:
    """基于规则的记忆提取（降级方案）。"""
    results = []
    # 简单关键词触发
    emotion_keywords = {
        "开心": "快乐", "难过": "悲伤", "生气": "愤怒", "焦虑": "焦虑",
        "害怕": "恐惧", "委屈": "委屈", "失望": "失望", "孤独": "孤独",
    }
    found_emotion = None
    for kw, label in emotion_keywords.items():
        if kw in user_message:
            found_emotion = label
            break

    # 如果消息包含重要关系词
    relation_words = ["男朋友", "女朋友", "老公", "老婆", "对象", "伴侣", "他", "她", "我妈", "我爸", "老板", "同事"]
    entities = [w for w in relation_words if w in user_message]

    if found_emotion or entities:
        importance = 4 if entities else 3
        results.append({
            "content": user_message[:100],
            "emotion": found_emotion or "未标注",
            "importance": importance,
            "entities": entities,
        })
    return results


def _mock_report_insights(diaries: List[Dict]) -> str:
    """模拟报告洞察。"""
    if not diaries:
        return "本周还没有情绪记录，开始记录吧，点滴觉察都是进步。"
    avg = sum(d.get("intensity", 5) for d in diaries) / len(diaries)
    if avg >= 7:
        return "本周情绪强度偏高，可能正处于压力期。建议关注自我关怀，必要时寻求支持。"
    elif avg <= 4:
        return "本周情绪整体平稳，你做得不错。注意保持积极活动的节奏。"
    else:
        return "本周情绪有自然波动，这是正常的。觉察情绪变化就是成长的开始。"
