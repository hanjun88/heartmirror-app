# -*- coding: utf-8 -*-
"""心镜应用 FastAPI 入口。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .database import init_db
from .config import FRONTEND_DIR
from .routers import auth, diary, memory, chat, report, couple, assessment, compliance

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动
    init_db()
    logger.info("心镜后端启动，数据库已初始化")

    # 启动主动 Agent（可选，失败不阻塞）
    scheduler = None
    try:
        from .services.proactive_service import start_scheduler
        scheduler = start_scheduler()
    except Exception as e:
        logger.warning("主动 Agent 启动失败（不影响主功能）: %s", e)

    yield

    # 关闭
    if scheduler:
        scheduler.shutdown()
    logger.info("心镜后端已关闭")


app = FastAPI(title="心镜 HeartMirror API", version="0.1.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router)
app.include_router(diary.router)
app.include_router(memory.router)
app.include_router(chat.router)
app.include_router(report.router)
app.include_router(couple.router)
app.include_router(assessment.router)
app.include_router(compliance.router)


@app.get("/")
def root():
    """返回前端单页应用。"""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "心镜 API 运行中", "docs": "/docs"}


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "heartmirror"}


# 挂载前端静态文件
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
