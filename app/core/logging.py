"""日志：保持旧版终端输出格式（`[jev] ...`），便于延续既有排查习惯。"""
from __future__ import annotations

import logging
import sys

_FORMAT = '[%(name)s] %(message)s'
_TAGS = ('jev', 'gen', 'pool', 'api', 'cli')
_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """配置根 logger，重复调用只生效一次（uvicorn --reload 下会多次导入）。"""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn 自己有 access log 格式，这里不让它另起一套 handler。
    for tag in _TAGS:
        logging.getLogger(tag).propagate = True
    _configured = True


def get_logger(tag: str) -> logging.Logger:
    """tag 用固定几个：jev / gen / pool / api。"""
    configure_logging()
    return logging.getLogger(tag)
