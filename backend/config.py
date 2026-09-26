# -*- coding: utf-8 -*-
"""心镜应用配置：从环境变量读取，开发有合理默认值。"""
import os
from pathlib import Path

# 引擎仓库路径（同进程 import 用）
ENGINE_REPO_PATH = os.getenv(
    "XINJING_ENGINE_PATH",
    "/home/user/.doubao/agent_mode/workspace/.sessions/38444276910206978/agents/s_000cx1kjGIg/xinjing-relationship-engine/xinjing-relationship-engine",
)

# 数据库
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./heartmirror.db")

# JWT
# 安全硬约束：JWT_SECRET 必须由环境变量显式提供，且长度 >= 32。
# 未设置或过短时应用启动直接失败，杜绝默认密钥被攻击者伪造 token。
JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET or len(JWT_SECRET) < 32:
    raise RuntimeError(
        "JWT_SECRET must be set and at least 32 characters "
        "(refuse to start with a default/short secret)."
    )
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

# LLM（OpenAI 兼容接口）
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

# 合规
DAILY_USAGE_LIMIT_SECONDS = 2 * 3600  # 成人 2 小时
MINOR_DAILY_LIMIT_SECONDS = 1 * 3600  # 未成年人 1 小时
CRISIS_HOTLINE = "12356"

# 象征层置信硬锁
SYMBOLIC_CONFIDENCE_LOCK = (0.36, 0.40)

# CORS：显式 allowlist（逗号分隔），不再使用通配符 "*"
_DEFAULT_WEB_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
WEB_ORIGINS = [
    o.strip() for o in os.getenv("WEB_ORIGINS", _DEFAULT_WEB_ORIGINS).split(",")
    if o.strip()
]

# 前端静态文件目录
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
