"""全局配置：路径、环境变量、模型参数与阈值。

所有模块只从这里取配置，不再各自 `os.getenv`——换端点、调并发、改阈值都只改一处。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 对外暴露的版本号：/health 与响应头 X-Jev 都取它，启动脚本也靠它比对「端口上跑的是不是当前代码」。
VERSION = 'v008'

# ===== 路径 =====
BASE_DIR = Path(__file__).resolve().parents[2]          # 仓库根目录
DATA_DIR = BASE_DIR / 'data'                            # 只读数据（标签库、夹具）
VAR_DIR = BASE_DIR / 'var'                              # 运行产物（回流池、回归结果），已 gitignore
VAR_DIR.mkdir(parents=True, exist_ok=True)

ENV_PATH = BASE_DIR / '.env'
if not ENV_PATH.exists():
    # 兼容旧布局：server.py 曾在子目录、.env 放在上一级。
    ENV_PATH = BASE_DIR.parent / '.env'

# ===== 前端（Vite + Vue 3）=====
# 源码在 frontend/，构建产物在 frontend/dist/，由 FastAPI 托管（GET / 与 GET /assets/*）。
FRONTEND_DIR = BASE_DIR / 'frontend'
FRONTEND_DIST = FRONTEND_DIR / 'dist'
FRONTEND_INDEX = FRONTEND_DIST / 'index.html'
FRONTEND_ASSETS = FRONTEND_DIST / 'assets'
BUILD_HINT = '前端还没构建。请在 frontend/ 目录执行：npm install && npm run build（或直接双击项目根目录的「启动.command」）。'

INTENTS_SEED_PATH = DATA_DIR / 'intents_seed.json'
CASES_PATH = DATA_DIR / 'cases.json'
POOL_PATH = VAR_DIR / 'low_confidence_pool.json'
REGRESSION_DIR = VAR_DIR / 'regression'
# 会话库：每次导入的聊天记录都存在这里（含原文与分析结果），只在本机，var/ 已 gitignore。
SESSIONS_DB = VAR_DIR / 'sessions.db'
SESSIONS_LIST_LIMIT = 100        # 侧栏一次最多返回多少条（按最后活跃时间倒序）

# ===== 网络 =====
HOST = '127.0.0.1'          # 只绑本机，外部网络进不来
PORT = 8767
# 允许的页面来源：本机任意端口（内置预览、本地静态服务）+ file:// 打开的页面（Origin: null）。
# 收紧过的部分：仍然只绑 127.0.0.1；代价是本机任意页面都能调用本接口（会消耗 API 额度）。
ALLOWED_ORIGIN = re.compile(r'^(?:null|https?://(?:127\.0\.0\.1|localhost)(?::\d+)?)$')
ALLOWED_ORIGIN_REGEX = r'(?:null|https?://(?:127\.0\.0\.1|localhost)(?::\d+)?)'
MAX_BODY_BYTES = 300_000


class Settings(BaseSettings):
    """从 `.env` / 环境变量读取的运行配置（环境变量优先于 .env 文件）。"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    # ---------- 分类层：Jev ----------
    # BASE_URL 两种写法都支持，见 clients/jev.py 的 JevClient.endpoint：
    #   只写主机名（TypeSafe 原生网关）→ 自动补 /v1/systemone
    #   写成完整端点（OpenRouter alpha decisions）→ 原样使用
    typesafe_api_key: str = ''
    typesafe_base_url: str = 'https://api.typesafe.ai'
    typesafe_default_model: str = 'jev-latest'

    # ---------- 生成层：OpenAI 兼容端点 ----------
    deepseek_api_key: str = ''
    deepseek_base_url: str = 'https://api.deepseek.com'
    deepseek_model: str = 'deepseek-flash'
    # 推荐回复给几条（界面上可改）。提示词里的条数与输出整形都用它，改一处即可。
    gen_suggestions_count: int = 3

    # ---------- 并发度 ----------
    # 整批消息（Jev 两级路由 + 可能的生成）的并发数。Jev 有过偶发 403，保守取 4。
    jev_max_workers: int = 4
    # 生成层每条各调一次，串行时 26 条实测十几分钟；压在 6 既能把总时间压到 1 分钟内，又不打满上游限流。
    gen_max_workers: int = 6

    @property
    def jev_configured(self) -> bool:
        return bool(self.typesafe_api_key)

    @property
    def generation_configured(self) -> bool:
        return bool(self.deepseek_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例。测试可用 `get_settings.cache_clear()` 重置。"""
    return Settings()


# 推荐回复条数的允许范围：少于 2 条没有「选一个发」的意义，多于 6 条模型开始凑数。
SUGGESTIONS_MIN = 1
SUGGESTIONS_MAX = 6


def clamp_suggestions_count(value) -> int:
    """把界面/配置里的条数收进合法范围（配置是人工可改的，不能信）。"""
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = Settings.model_fields['gen_suggestions_count'].default
    return max(SUGGESTIONS_MIN, min(count, SUGGESTIONS_MAX))


# ===== 模型判定阈值（与原单文件版一致，改这里等于改判定口径） =====
INTENT_MIN = 0.45          # 意图层进入回流池的分数下限
EMOTION_MIN = 0.35         # 情绪层低于此值退到一级大类展示（"知道他不爽，但细分没把握"）
EMOTION_POOL_MIN = 0.45    # 情绪回流线：过了 UI 门控但低于此线也留档
TIE_GAP = 0.05             # 前两名差距小于此值视为平票
FAMILY_TIE_GAP = 0.12      # 一级大类平票时把第二名也带进第二级
POOL_LIMIT = 400           # 回流池上限，满了只提示不写入

# ===== 单次请求规模上限（与旧版一致） =====
MAX_TRANSCRIPT_CHARS = 50_000
MAX_MESSAGE_CHARS = 6_000
MAX_CONTEXT_CHARS = 12_000
MAX_TARGETS = 50
