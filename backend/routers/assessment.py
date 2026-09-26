# -*- coding: utf-8 -*-
"""关系测评路由：依恋类型/爱语/冲突风格。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, AssessmentResult
from ..schemas import AssessmentSubmit, AssessmentResultOut
from .auth import get_current_user

router = APIRouter(prefix="/assessment", tags=["assessment"])

# ---- 测评题库（简化版） ----

ATTACHMENT_QUESTIONS = [
    "我担心自己不够值得被爱",
    "我倾向于在关系中保持独立，不依赖对方",
    "我容易担心伴侣是否真的爱我",
    "我觉得向别人敞开心扉会让我不舒服",
    "我常常担心伴侣会离开我",
    "亲密关系让我感到有点不安",
    "我很容易与他人建立亲密感",
    "当关系变得太近时我会想后退",
    "我经常担心伴侣不想和我在一起",
    "我发现依赖别人让我感到自在",
]

LOVE_LANGUAGE_QUESTIONS = [
    "当伴侣认真听我说话时，我感到被爱",
    "收到手写的卡片或礼物让我很开心",
    "伴侣主动帮我做事时，我感到被在乎",
    "拥抱和身体接触让我感到安心",
    "伴侣花时间单独陪我时最幸福",
    "一句真诚的赞美比礼物更让我开心",
    "我喜欢收到出乎意料的小礼物",
    "伴侣帮我分担家务让我感到被爱",
    "牵手、亲吻让我感到亲密",
    "即使忙碌，伴侣也会抽出时间陪我",
    "行动比言语更能表达爱",
    "精心挑选的礼物让我觉得被重视",
    "伴侣为我做事时我感到被爱",
    "身体接触让我感到被接纳",
    "高质量的陪伴时间让我感到幸福",
]

CONFLICT_QUESTIONS = [
    "冲突时我会坚持自己的立场，争取胜利",
    "冲突时我会努力找到双方都满意的方案",
    "冲突时我会各让一步，达成妥协",
    "冲突时我会尽量回避，避免矛盾激化",
    "冲突时我会优先考虑对方的需求",
    "我会用数据和逻辑在争论中获胜",
    "我相信双赢是可能的",
    "我宁愿放弃自己的部分需求也不想争吵",
]


@router.get("/questions/{assessment_type}")
def get_questions(assessment_type: str):
    """获取测评题目。"""
    if assessment_type == "attachment":
        return {"type": "attachment", "title": "依恋类型测试", "questions": ATTACHMENT_QUESTIONS}
    elif assessment_type == "love_language":
        return {"type": "love_language", "title": "五种爱语测试", "questions": LOVE_LANGUAGE_QUESTIONS}
    elif assessment_type == "conflict":
        return {"type": "conflict", "title": "冲突风格测试", "questions": CONFLICT_QUESTIONS}
    raise HTTPException(status_code=404, detail="未知测评类型")


@router.post("/{assessment_type}/submit", response_model=AssessmentResultOut)
def submit_assessment(assessment_type: str, body: AssessmentSubmit,
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    answers = body.answers  # 每题 1-5 分（非常不同意~非常同意）

    if assessment_type == "attachment":
        result = _score_attachment(answers)
        user.attachment_type = result["type"]
    elif assessment_type == "love_language":
        result = _score_love_language(answers)
        user.love_language = str(result["ranking"])
    elif assessment_type == "conflict":
        result = _score_conflict(answers)
        user.conflict_style = result["style"]
    else:
        raise HTTPException(status_code=404, detail="未知测评类型")

    # 保存结果
    ar = AssessmentResult(
        user_id=user.id,
        assessment_type=assessment_type,
        result_data=result,
    )
    db.add(ar)
    db.commit()
    db.refresh(ar)

    return AssessmentResultOut(
        assessment_type=assessment_type,
        result=result,
        created_at=ar.created_at,
    )


def _score_attachment(answers: list[int]) -> dict:
    """简化依恋类型评分。"""
    # 焦虑维度: Q1,Q3,Q5,Q9 (0-indexed: 0,2,4,8)
    # 回避维度: Q2,Q4,Q6,Q8 (0-indexed: 1,3,5,7)
    anxiety = sum(answers[i] for i in [0, 2, 4, 8]) / 4.0
    avoidance = sum(answers[i] for i in [1, 3, 5, 7]) / 4.0

    if anxiety < 3 and avoidance < 3:
        atype = "安全型"
    elif anxiety >= 3 and avoidance < 3:
        atype = "焦虑型"
    elif anxiety < 3 and avoidance >= 3:
        atype = "回避型"
    else:
        atype = "恐惧型"

    return {
        "type": atype,
        "anxiety_score": round(anxiety, 1),
        "avoidance_score": round(avoidance, 1),
        "description": {
            "安全型": "你对亲密关系感到安心，能够信任他人也保持独立。",
            "焦虑型": "你对关系有些担忧，渴望亲密但害怕被抛弃。",
            "回避型": "你重视独立，在关系过于亲密时会感到不适。",
            "恐惧型": "你既渴望亲密又害怕受伤，在靠近与退缩间摇摆。",
        }[atype],
    }


def _score_love_language(answers: list[int]) -> dict:
    """5种爱语评分：肯定的言语/接受礼物/服务的行动/身体接触/精心的时刻。"""
    # 每题对应一种爱语（3题一种，共15题）
    categories = ["肯定的言语", "接受礼物", "服务的行动", "身体接触", "精心的时刻"]
    scores = {}
    for i, cat in enumerate(categories):
        # 每个类别 3 题
        indices = [i * 3, i * 3 + 1, i * 3 + 2]
        scores[cat] = sum(answers[j] for j in indices if j < len(answers)) / 3.0

    ranking = sorted(scores.items(), key=lambda x: -x[1])
    return {
        "primary": ranking[0][0],
        "ranking": [{"language": k, "score": round(v, 1)} for k, v in ranking],
    }


def _score_conflict(answers: list[int]) -> dict:
    """冲突风格：竞争/合作/妥协/回避/迁就。"""
    # Q: 1=竞争, 2=合作, 3=妥协, 4=回避, 5=迁就, 6=竞争, 7=合作, 8=迁就
    categories = {
        "竞争": [0, 5],
        "合作": [1, 6],
        "妥协": [2],
        "回避": [3],
        "迁就": [4, 7],
    }
    scores = {}
    for cat, indices in categories.items():
        scores[cat] = sum(answers[i] for i in indices if i < len(answers)) / len(indices)

    top = max(scores.items(), key=lambda x: x[1])
    return {
        "style": top[0],
        "scores": {k: round(v, 1) for k, v in scores.items()},
    }
