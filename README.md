# 心镜 HeartMirror - 应用层 MVP

情绪陪伴与关系认知应用，基于 xinjing-relationship-engine 引擎构建。

## 快速开始

### 安装依赖

```bash
cd heartmirror-app
pip install -r backend/requirements.txt
```

### 配置环境变量（可选）

```bash
export LLM_API_KEY="your-api-key"
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_MODEL="gpt-4o-mini"
export DATABASE_URL="sqlite:///./heartmirror.db"
export JWT_SECRET="your-secret-key"
```

未配置 LLM 时，应用自动使用模拟回复（会标注"模拟回复"），核心功能不受影响。

### 启动

```bash
uvicorn backend.main:app --reload --port 8000
```

打开浏览器访问 http://localhost:8000

### 运行测试

```bash
pytest tests/ -v
```

## API 列表

### 认证
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /auth/register | 注册（邮箱+密码+昵称+年龄） |
| POST | /auth/login | 登录，返回 JWT |
| GET | /auth/me | 获取当前用户资料 |
| PUT | /auth/me | 更新用户资料 |
| DELETE | /auth/me | 删除账户（GDPR） |

### 情绪日记
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /diary | 记录情绪 |
| GET | /diary | 列表（分页/日期/标签过滤） |
| GET | /diary/trends | 情绪趋势统计 |

### 记忆系统
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /memory | 记忆列表（可搜索） |
| POST | /memory | 手动添加记忆 |
| PUT | /memory/{id} | 编辑/置顶记忆 |
| DELETE | /memory/{id} | 删除记忆 |
| POST | /memory/feedback | 用户反馈（点赞/点踩） |

### AI 对话
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /chat | 发送消息，返回 AI 回复 |
| GET | /chat/history | 对话历史 |

### 报告
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /report/weekly | 本周情绪复盘 |
| GET | /report/share-card | 分享卡片（SVG） |

### 双人配对
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /couple/invite | 创建邀请码 |
| POST | /couple/join | 通过邀请码加入 |
| GET | /couple/status | 配对状态 |
| POST | /couple/session | 创建联合会话 |
| POST | /couple/session/{id}/message | 会话中发消息 |
| POST | /couple/session/{id}/end | 结束会话生成共识卡 |

### 测评
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /assessment/questions/{type} | 获取题目 |
| POST | /assessment/{type}/submit | 提交答案（attachment/love_language/conflict） |

### 合规
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /compliance/status | 合规状态 |
| POST | /compliance/tick | 上报使用时长 |
| GET | /notifications | 通知列表 |
| POST | /notifications/{id}/read | 标记已读 |

## 技术架构

- **后端**: FastAPI + SQLAlchemy + SQLite
- **前端**: 单文件 HTML + Tailwind CSS + Chart.js
- **引擎**: xinjing-relationship-engine（同进程 import）
- **LLM**: OpenAI 兼容接口（未配置时降级为模拟回复）

## 安全约束

- 密码 bcrypt 哈希存储
- JWT 双 token（access 15min + refresh 7d）
- 危机熔断硬约束：命中自伤关键词直接返回 12356 热线，不走 LLM
- 象征层（命理）结果置信度硬锁 [0.36, 0.40]，标注 is_symbolic_annotation=True
- SQL 注入防护：全部使用 ORM
- XSS 防护：前端文本转义
- 未成年人模式：<18岁限制功能 + 每日1小时上限
- AI 身份披露：所有回复标注"（AI 生成，仅供参考）"
