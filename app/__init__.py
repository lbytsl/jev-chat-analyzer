"""恋爱·职场聊天神器 —— 本地聊天记录分析服务（FastAPI 版）。

分层约定（依赖方向自上而下，禁止反向依赖）：

    api        路由与请求校验（只做协议转换，不写业务）
    services   用例编排（分类、生成、整段/追加/补跑流水线、回流池）
    domain     纯领域逻辑（标签库、提示词、聊天记录解析），无 IO
    clients    外部依赖封装（Jev 分类网关、OpenAI 兼容生成层）
    core       配置、日志、异常等基础设施

原单文件版 `server.py` 已按此结构拆成分层后端，原单文件前端 `index.html` 已拆成
`frontend/`（Vite + Vue 3，构建产物由 GET / 与 GET /assets/* 托管）；
换框架的过程中对外 HTTP 契约与页面行为都保持不变。
"""

__all__ = ['__version__']

__version__ = 'v009'
