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
JWT_SECRET = os.getenv("JWT_SECRET", "heartmirror-dev-secret-change-in-production")
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

# 前端静态文件目录
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
