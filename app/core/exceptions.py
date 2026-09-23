"""领域异常。

分成两类，处理方式完全不同：

- `InvalidRequest`（用户/前端的问题）→ 400，消息直接回显给用户；
- Jev 相关异常 → 502/503/500，按状态码给出可操作的提示（见 main.py 的异常处理器）。

原单文件版直接抛 `ValueError`，这里换成显式异常类型，语义更清楚且不影响 400 的响应体。
"""
from __future__ import annotations


class InvalidRequest(ValueError):
    """入参不合法（含业务校验失败）。响应 400，`str(exc)` 即给用户的提示。"""


class NotFoundError(Exception):
    """按 id 找不到资源（会话等）。响应 404，`str(exc)` 即给用户的提示。"""


class JevConnectionError(Exception):
    """Jev 上游暂时不可连接；与 Key、参数或模型输出错误分开处理。"""


class JevConfigurationError(Exception):
    """本地缺少 Jev 配置。"""


class JevResponseError(Exception):
    """Jev 已响应，但响应结构不符合当前集成约定。"""


class JevAPIError(Exception):
    """Jev 返回了明确的 HTTP 错误。"""

    def __init__(self, status: int, detail: str = ''):
        super().__init__(f'Jev HTTP {status}')
        self.status = status
        self.detail = detail


class GeneralLLMError(Exception):
    """生成层（DeepSeek 等）调用失败；与 Jev 分类错误分开，避免拖垮整次分析。"""
