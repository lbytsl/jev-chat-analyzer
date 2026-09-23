"""读写 .env：只动目标键，注释、顺序、其它行原样保留。

为什么不去维护第二份配置（比如 var/settings.json）：`.env` 已经是唯一事实来源，
用户也会手改它。界面保存时做「按行合并」最不容易出现两边打架的情况。
"""
from __future__ import annotations

import re
from pathlib import Path

_LINE_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$')


def read_env(path: Path) -> dict[str, str]:
    """读成 {KEY: value}；值去掉首尾空格与成对引号（与 pydantic-settings 的解析保持一致）。"""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = _LINE_RE.match(line)
        if match:
            result[match.group(1)] = _unquote(match.group(2).strip())
    return result


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def format_value(value: str) -> str:
    """含空格或 # 的值加引号，其它保持裸值（尽量少改动用户文件的样子）。"""
    text = str(value)
    if text != text.strip() or any(ch in text for ch in ' #"\'') or text == '':
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return text


def update_env(path: Path, updates: dict[str, str]) -> list[str]:
    """把 updates 合并进 .env，返回真正被更新的键。

    - 已存在的键：原地替换那一行（保留它在文件里的位置）；
    - 不存在的键：追加到文件末尾；
    - 注释、空行、别的键一个都不动。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    pending = {key: value for key, value in updates.items() if value is not None}
    changed: list[str] = []
    output: list[str] = []
    for line in lines:
        match = _LINE_RE.match(line)
        key = match.group(1) if match else None
        if key is not None and key in pending:
            value = str(pending.pop(key))
            new_line = key + '=' + format_value(value)
            output.append(new_line)
            if new_line != line:
                changed.append(key)
            continue
        output.append(line)
    for key, value in pending.items():
        output.append(key + '=' + format_value(value))
        changed.append(key)
    path.write_text('\n'.join(output).rstrip('\n') + '\n', encoding='utf-8')
    return changed
