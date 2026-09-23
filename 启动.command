#!/bin/bash
# 恋爱·职场聊天神器 —— 双击启动本地服务并打开页面。
#
# 本脚本要同时兼容 bash 和 zsh（双击 .command 时 Terminal 可能用 zsh 执行），
# 所以只用两者都支持的写法，有两条硬规矩：
#   1) 不要用 read -p —— zsh 里 -p 是协进程，不是提示语；用 printf + read -r。
#   2) 变量后面紧跟中文/全角字符时必须写 ${VAR} —— bash 会把多字节字符的首字节
#      吞进变量名，导致变量变空、后面的字变成乱码。zsh 无此问题，但两边都要过。
#
# 2026-09-23 补的两条，针对的是同一个坑：端口被占时不能无条件复用旧服务。
#   1) 以前只要端口有人就直接开页面，结果常常打开的是上一版代码的旧服务 —— 看着像
#      "启动了"，改动却完全没生效。现在会比对版本，不一样就问要不要停掉重起。
#   2) 只比对 VERSION 也不够：开发期改了代码通常不会改版本号，照样漏检。所以再加一道
#      「app/ 下最新的 .py 是否比进程启动时间还新」——不依赖人工记得改版本号。
#
# 依赖与构建：后端用 uv（清单 pyproject.toml、锁文件 uv.lock），前端用 pnpm。
# 这两个可执行文件都不在双击时的默认 PATH 里（uv 装在 ~/.local/bin，pnpm 由 nvm 管理），
# 所以下面会显式补 PATH 并尝试加载 nvm。
cd "$(dirname "$0")" || exit 1

PORT=8767
URL="http://127.0.0.1:$PORT"

# 双击打开时 Terminal 给的 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin，
# 不含 Homebrew、uv（~/.local/bin）与 nvm 的目录 —— 会选到系统自带的 Python 3.9，
# 也找不到 uv / pnpm。先补回来。
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# nvm 管的 node/pnpm 同样不在默认 PATH 里，加载 nvm 才找得到（没装 nvm 就跳过，不影响后端）。
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1
fi

open_url() {
  if [ -d "/Applications/Google Chrome.app" ]; then
    open -a "Google Chrome" "$URL" 2>/dev/null || open "$URL"
  else
    open "$URL"
  fi
}

listening() {
  lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1
}

# 读两个版本号：第一行 = 当前代码里的 VERSION，第二行 = 端口上真正在跑的版本。
# 探测不到就给 '?'（端口没起、或被别的服务占着都是这个结果）。
versions() {
  "${PY}" - "$PORT" <<'PYEOF'
import json, re, sys, urllib.request
port = sys.argv[1]
try:
    src = open('app/core/config.py', encoding='utf-8').read()
    hit = re.search(r"^VERSION\s*=\s*['\"]([^'\"]+)", src, re.M)
    cur = hit.group(1) if hit else '?'
except Exception:
    cur = '?'
run = '?'
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:%s/health' % port, timeout=3) as resp:
        run = json.loads(resp.read().decode('utf-8', 'replace')).get('version', '?')
except Exception:
    pass
print(cur)
print(run)
PYEOF
}

# 端口上那个进程的启动时间（epoch 秒）；拿不到就输出空。
process_started_at() {
  local pid started
  pid=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t 2>/dev/null | head -1)
  [ -n "$pid" ] || return 0
  # ps 的 lstart 形如「Wed Sep 23 21:30:12 2026」；日小于 10 时会有多余空格，先压平。
  started=$(ps -o lstart= -p "$pid" 2>/dev/null | tr -s ' ' | sed 's/^ //; s/ $//')
  [ -n "$started" ] || return 0
  date -j -f '%a %b %d %T %Y' "$started" '+%s' 2>/dev/null
}

# app/ 下最新的 .py 改动时间（epoch 秒）——用来判断「代码是不是比进程新」。
newest_source_change() {
  find app -type f -name '*.py' -exec stat -f '%m' {} + 2>/dev/null | sort -rn | head -1
}

# 后端依赖交给 uv：清单在 pyproject.toml、锁文件是 uv.lock，uv sync 自己创建并同步 .venv，
# 不用先手动建虚拟环境。带 --no-dev：启动服务用不到 pytest / ruff，少装几个包。
PY=""
if command -v uv >/dev/null 2>&1; then
  echo "同步后端依赖（uv）…"
  uv sync --no-dev --quiet || echo "⚠  uv sync 没成功（离线？），继续用现有环境试试。"
fi
if [ -x ".venv/bin/python" ]; then
  PY="$(pwd)/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY=$(command -v python3)
  if ! "${PY}" -c 'import fastapi' >/dev/null 2>&1; then
    echo "✗ 没有可用的运行环境。先安装 uv 再同步依赖："
    echo "    brew install uv && uv sync"
    printf '按回车键关闭窗口… '
    read -r _pause
    exit 1
  fi
fi
if [ -z "$PY" ]; then
  echo "✗ 没找到 python3。请先安装 Python 3，或手动运行：python3 -m app serve"
  printf '按回车键关闭窗口… '
  read -r _pause
  exit 1
fi

# 前端：源码在 frontend/，包管理用 pnpm，页面由 FastAPI 托管 frontend/dist 里的构建产物。
# dist 不存在（刚 clone、或改过前端还没重新构建）时自动构建一次，省掉手动步骤。
if [ ! -f "frontend/dist/index.html" ]; then
  if command -v pnpm >/dev/null 2>&1; then
    echo "前端还没构建，正在构建（首次会先装依赖，请稍等）…"
    if [ ! -d "frontend/node_modules" ]; then
      ( cd frontend && pnpm install ) || echo "⚠  pnpm install 没成功，构建可能会失败。"
    fi
    ( cd frontend && pnpm run build ) || echo "⚠  前端构建失败：服务照常启动，但页面打不开。"
  else
    echo "⚠  没找到 pnpm，前端页面无法构建。"
    echo "   装好 Node.js 后执行：cd frontend && pnpm install && pnpm run build"
  fi
  echo ""
fi

V=$(versions)
CUR=$(printf '%s\n' "$V" | sed -n 1p)
RUN=$(printf '%s\n' "$V" | sed -n 2p)

if listening; then
  # 「代码比进程新」比版本号可靠：开发期改了 .py 不一定会动 VERSION。
  NEWER_REASON=""
  SRC=$(newest_source_change)
  STARTED=$(process_started_at)
  if [ -n "$SRC" ] && [ -n "$STARTED" ] && [ "$SRC" -gt "$STARTED" ]; then
    NEWER_REASON="代码比正在跑的进程新（源码 $(date -r "$SRC" '+%m-%d %H:%M')，进程启动于 $(date -r "$STARTED" '+%m-%d %H:%M')）"
  fi
  if [ "$RUN" = "$CUR" ] && [ -z "$NEWER_REASON" ]; then
    echo "端口 ${PORT} 上已经有一个 ${CUR} 在跑，直接打开页面。"
    open_url
    exit 0
  fi
  echo "⚠  端口 ${PORT} 被占着，但跑的不是当前代码："
  if [ "$RUN" != "$CUR" ]; then
    echo "     正在跑的是「${RUN}」，当前代码是「${CUR}」。"
  fi
  if [ -n "$NEWER_REASON" ]; then
    echo "     ${NEWER_REASON}。"
  fi
  echo "     直接打开会看到旧页面，最近的改动不会生效。"
  printf '   要停掉它、重新起一个吗？[回车=停掉重启 / n=就用旧的] '
  read -r _ans
  case "$_ans" in
    n|N)
      echo "   保持不动，打开现有页面。"
      open_url
      exit 0
      ;;
    *)
      OLD_PID=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t | head -1)
      if [ -n "$OLD_PID" ]; then
        kill "$OLD_PID" 2>/dev/null
        sleep 1
        kill -9 "$OLD_PID" 2>/dev/null
        echo "   已停掉旧进程 ${OLD_PID}。"
      fi
      if listening; then
        echo "✗ 还是停不掉。手动查一下是谁占着：lsof -nP -iTCP:${PORT} -sTCP:LISTEN"
        printf '按回车键关闭窗口… '
        read -r _pause
        exit 1
      fi
      ;;
  esac
fi

echo "恋爱·职场聊天神器 ${CUR} 启动中 → ${URL}"
echo "解释器：${PY}（$("${PY}" --version 2>&1)）"
echo "接口文档：${URL}/docs"
echo "按 Ctrl+C 停止服务。"
echo ""

"${PY}" -m app serve &
SERVER_PID=$!

# 等端口真的监听起来再打开页面；最多等 5 秒。
i=0
while [ $i -lt 25 ]; do
  listening && break
  kill -0 "$SERVER_PID" 2>/dev/null || break   # 进程已经挂了，不必再等
  sleep 0.2
  i=$((i + 1))
done

if listening; then
  open_url
else
  echo ""
  echo "✗ 服务没能在 127.0.0.1:${PORT} 上起来，页面不打开。上面的报错就是原因。"
  echo "  排查端口占用：lsof -nP -iTCP:${PORT} -sTCP:LISTEN"
  kill "$SERVER_PID" 2>/dev/null
  printf '按回车键关闭窗口… '
  read -r _pause
  exit 1
fi

wait "$SERVER_PID"
CODE=$?

# Ctrl+C 会让子进程带 130 退出，这是正常停止，不用报警。
if [ "$CODE" -eq 0 ] || [ "$CODE" -eq 130 ]; then
  echo ""
  echo "服务已停止。"
  exit 0
fi

echo ""
echo "服务异常退出（退出码 ${CODE}）。"
printf '按回车键关闭窗口… '
read -r _pause
exit "$CODE"
