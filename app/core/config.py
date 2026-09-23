"""全局配置：路径、环境变量、模型参数与阈值。

所有模块只从这里取配置，不再各自 `os.getenv`——换端点、调并发、改阈值都只改一处。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 对外暴露的版本号：/health 与响应头 X-Jev 都取它，启动脚本也靠它比对「端口上跑的是不是当前代码」。
VERSION = 'v009'

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


# ===== 生成层多套配置（LLM_PROFILES / LLM_ACTIVE） =====
# 生成层可以保存多套「接口地址 + 模型 + 密钥」，界面上切换用哪套。存法刻意选 .env 里的
# 单行 JSON：`.env` 仍是唯一事实来源，不额外维护第二份配置文件（理由见 core/env_file.py）。
# 没写过这两个键时（老用户、脚本、夹具）回退到 DEEPSEEK_* 三项，行为与以前完全一致。
LLM_PROFILES_KEY = 'LLM_PROFILES'
LLM_ACTIVE_KEY = 'LLM_ACTIVE'
DEFAULT_LLM_PROFILE_NAME = '默认'      # 只有 DEEPSEEK_* 时，界面上把它当成这样一套
MAX_LLM_PROFILES = 20                  # 存太多没有意义，界面也会跟着变成一长串
LLM_PROFILE_NAME_MAX = 24


@dataclass(frozen=True, slots=True)
class LLMProfile:
    """一套生成层配置。api_key 可为空 = 还没填（生成时会给出「未配置」的提示）。"""

    name: str
    base_url: str = ''
    model: str = ''
    api_key: str = ''

    def to_dict(self) -> dict:
        return {'name': self.name, 'base_url': self.base_url,
                'model': self.model, 'api_key': self.api_key}


def llm_profile_from(item: dict, name: str = '') -> LLMProfile:
    """把界面 / JSON 里的一项整成 LLMProfile（只做整形，不校验——校验在 services 里）。"""
    def text(value) -> str:
        return '' if value is None else str(value).strip()

    return LLMProfile(name=text(item.get('name')) or name,
                      base_url=text(item.get('base_url')),
                      model=text(item.get('model')),
                      api_key=text(item.get('api_key')))


def load_llm_profiles(raw) -> tuple[list[LLMProfile], str]:
    """把 LLM_PROFILES 的 JSON 文本解析成列表，返回 (列表, 出错时的一句提示)。

    解析不了时返回空列表而不是抛异常：这两个键是人工可改的，手改坏了不该让整个服务
    起不来——退回 DEEPSEEK_* 那套继续跑，界面上再用一句话提示哪里坏了。
    """
    text = (raw or '').strip()
    if not text:
        return [], ''
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], 'LLM_PROFILES 不是合法的 JSON，已忽略（正在用的是 DEEPSEEK_* 那套）。'
    if not isinstance(data, list):
        return [], 'LLM_PROFILES 应该是 JSON 数组，已忽略（正在用的是 DEEPSEEK_* 那套）。'
    profiles, seen = [], set()
    for item in data:
        if not isinstance(item, dict):
            continue
        profile = llm_profile_from(item)
        if not profile.name or profile.name in seen:
            continue                     # 没名字 / 重名的项直接跳过，不让它把整张表带坏
        seen.add(profile.name)
        profiles.append(profile)
    if not profiles:
        return [], 'LLM_PROFILES 里没有可用的配置项，已忽略（正在用的是 DEEPSEEK_* 那套）。'
    return profiles, ''


def pick_llm_profile(profiles: list[LLMProfile], active: str | None) -> LLMProfile | None:
    """按名字取「当前启用」那套；名字对不上（被改 / 被删）就退回第一套，不至于没有可用配置。"""
    if not profiles:
        return None
    wanted = (active or '').strip()
    for profile in profiles:
        if profile.name == wanted:
            return profile
    return profiles[0]


def dump_llm_profiles(profiles: list[LLMProfile]) -> str:
    """存进 .env 的单行 JSON（ensure_ascii=False：中文名字保持可读，方便手改）。"""
    return json.dumps([profile.to_dict() for profile in profiles], ensure_ascii=False)


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
    # 钉版本化 ID，不用别名 jev-latest：别名随新版本发布移动，判定口径会在
    # 我们没有任何改动的情况下变化（置信度阈值也就跟着失准）。
    typesafe_default_model: str = 'jev-1.13.0'

    # ---------- 生成层：OpenAI 兼容端点 ----------
    deepseek_api_key: str = ''
    deepseek_base_url: str = 'https://api.deepseek.com'
    deepseek_model: str = 'deepseek-flash'
    # 多套配置：界面上可以保存若干套（地址+模型+密钥）并切换启用哪套。这两个键是「原始
    # 存取」（JSON 文本 / 当前启用的名字），真正被生成链路读的始终是上面三个 deepseek_*：
    # 初始化时会被「当前启用」那套覆盖，所以 clients/general_llm.py 与生成链路不需要
    # 知道多套配置的存在（换配置 = 换 Settings 里那三个值，仅此而已）。
    llm_profiles: str = ''
    llm_active: str = ''
    # 推荐回复给几条（界面上可改）。提示词里的条数与输出整形都用它，改一处即可。
    gen_suggestions_count: int = 3

    # ---------- 并发度 ----------
    # 整批消息（Jev 两级路由 + 可能的生成）的并发数。Jev 有过偶发 403，保守取 4。
    jev_max_workers: int = 4
    # 生成层每条各调一次，串行时 26 条实测十几分钟；压在 6 既能把总时间压到 1 分钟内，又不打满上游限流。
    gen_max_workers: int = 6

    def model_post_init(self, __context, /) -> None:
        """把「当前启用」那套配置落到 deepseek_* 三个字段上。

        生成链路（clients/general_llm.py、services/generation.py）只认这三个字段，
        多套配置的切换就发生在这一处，链路本身不必有分支。
        """
        profile = pick_llm_profile(*self._profiles_and_active())
        if profile is None:
            return
        # 地址 / 模型为空时保留默认值；密钥则原样覆盖（空 = 这套还没填密钥）。
        self.deepseek_base_url = profile.base_url or self.deepseek_base_url
        self.deepseek_model = profile.model or self.deepseek_model
        self.deepseek_api_key = profile.api_key

    def _profiles_and_active(self) -> tuple[list[LLMProfile], str]:
        profiles, _ = load_llm_profiles(self.llm_profiles)
        return profiles, self.llm_active

    @property
    def llm_profile_list(self) -> list[LLMProfile]:
        """LLM_PROFILES 里真正写了的那几套（没写过就是空列表）。"""
        return load_llm_profiles(self.llm_profiles)[0]

    @property
    def llm_profiles_error(self) -> str:
        """LLM_PROFILES 手改坏了的话，这里是给界面看的一句话（正常时为空串）。"""
        return load_llm_profiles(self.llm_profiles)[1]

    @property
    def effective_llm_profiles(self) -> list[LLMProfile]:
        """界面上要展示的整张表：写过 LLM_PROFILES 就用它，否则把 DEEPSEEK_* 当成唯一那套。"""
        profiles = self.llm_profile_list
        if profiles:
            return profiles
        return [LLMProfile(name=DEFAULT_LLM_PROFILE_NAME, base_url=self.deepseek_base_url,
                           model=self.deepseek_model, api_key=self.deepseek_api_key)]

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
