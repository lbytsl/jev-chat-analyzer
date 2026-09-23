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
# 依赖与运行：后端用 uv（清单 pyproject.toml、锁文件 uv.lock），前端用 pnpm。
# 前端跑 Vite dev server（pnpm run dev，127.0.0.1:5173）直接吃源码 —— 改完前端刷新即见，
# 不用每次 build；页面从 5173 打开，接口按 client.js 的规则显式打到 127.0.0.1:8767
# （后端 CORS / origin 白名单已放行本机任意端口）。后端 API 仍在 8767。
# 找不到 pnpm 时退回「构建产物 + 后端托管」的老路子：页面开 8767，仍需要 dist。
#
# uv / pnpm 都不在双击时的默认 PATH 里（uv 在 ~/.local/bin，pnpm 由 nvm 管理），
# 下面会显式补 PATH 并尝试加载 nvm。
cd "$(dirname "$0")" || exit 1

PORT=8767                       # 后端：API（顺带托管 frontend/dist 作为兜底页面）
URL="http://127.0.0.1:$PORT"
DEV_PORT=5173                   # 前端 Vite dev server
PAGE_URL="http://127.0.0.1:$DEV_PORT"

# 双击打开时 Terminal 给的 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin，
# 不含 Homebrew、uv（~/.local/bin）与 nvm 的目录 —— 会选到系统自带的 Python 3.9，
# 也找不到 uv / pnpm。先补回来。
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# nvm 管的 node/pnpm 同样不在默认 PATH 里，加载 nvm 才找得到（没装 nvm 就跳过，不影响后端）。
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1
fi

# 打开的是前端页面：dev 模式开 5173，兜底模式开 8767（看 PAGE_URL）。
open_url() {
  if [ -d "/Applications/Google Chrome.app" ]; then
    open -a "Google Chrome" "$PAGE_URL" 2>/dev/null || open "$PAGE_URL"
  else
    open "$PAGE_URL"
  fi
}

# 用法：listening_on <端口>；不传端口时看后端端口。
listening_on() {
  lsof -nP -iTCP:${1:-$PORT} -sTCP:LISTEN >/dev/null 2>&1
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

# 前端：优先 dev server（pnpm run dev）直接吃源码，改动刷新即见；pnpm 不在时退回构建产物。
DEV_MODE=0
if command -v pnpm >/dev/null 2>&1; then
  DEV_MODE=1
  if [ ! -d "frontend/node_modules" ]; then
    echo "前端依赖还没装，正在 pnpm install（首次会稍慢）…"
    ( cd frontend && pnpm install ) || echo "⚠  pnpm install 没成功，dev server 可能起不来。"
  fi
else
  echo "⚠  没找到 pnpm，改用构建产物（页面在 ${URL}）。"
  echo "   装好 Node.js 后执行一次：cd frontend && pnpm install"
  PAGE_URL="$URL"
  if [ ! -f "frontend/dist/index.html" ]; then
    echo "   且 frontend/dist 不存在（首次 clone）——页面会返回 503，请先装 Node 并构建。"
  fi
  echo ""
fi

V=$(versions)
CUR=$(printf '%s\n' "$V" | sed -n 1p)
RUN=$(printf '%s\n' "$V" | sed -n 2p)

# ---------- 后端（8767）：已在跑就复用，版本不符 / 代码更新则问要不要重启 ----------
BACKEND_READY=0
if listening_on "$PORT"; then
  # 「代码比进程新」比版本号可靠：开发期改了 .py 不一定会动 VERSION。
  NEWER_REASON=""
  SRC=$(newest_source_change)
  STARTED=$(process_started_at)
  if [ -n "$SRC" ] && [ -n "$STARTED" ] && [ "$SRC" -gt "$STARTED" ]; then
    NEWER_REASON="代码比正在跑的进程新（源码 $(date -r "$SRC" '+%m-%d %H:%M')，进程启动于 $(date -r "$STARTED" '+%m-%d %H:%M')）"
  fi
  if [ "$RUN" = "$CUR" ] && [ -z "$NEWER_REASON" ]; then
    echo "端口 ${PORT} 上已经有一个 ${CUR} 的后端在跑，复用它。"
    BACKEND_READY=1
  else
    echo "⚠  端口 ${PORT} 被占着，但跑的不是当前代码："
    if [ "$RUN" != "$CUR" ]; then
      echo "     正在跑的是「${RUN}」，当前代码是「${CUR}」。"
    fi
    if [ -n "$NEWER_REASON" ]; then
      echo "     ${NEWER_REASON}。"
    fi
    echo "     直接复用会看到旧接口，最近的改动不会生效。"
    printf '   要停掉它、重新起一个吗？[回车=停掉重启 / n=就复用旧的] '
    read -r _ans
    case "$_ans" in
      n|N)
        echo "   保持不动，复用现有后端。"
        BACKEND_READY=1
        ;;
      *)
        OLD_PID=$(lsof -nP -iTCP:$PORT -sTCP:LISTEN -t | head -1)
        if [ -n "$OLD_PID" ]; then
          kill "$OLD_PID" 2>/dev/null
          sleep 1
          kill -9 "$OLD_PID" 2>/dev/null
          echo "   已停掉旧进程 ${OLD_PID}。"
        fi
        if listening_on "$PORT"; then
          echo "✗ 还是停不掉。手动查一下是谁占着：lsof -nP -iTCP:${PORT} -sTCP:LISTEN"
          printf '按回车键关闭窗口… '
          read -r _pause
          exit 1
        fi
        ;;
    esac
  fi
fi

SERVER_PID=""
if [ "$BACKEND_READY" -eq 0 ]; then
  echo "恋爱·职场聊天神器 ${CUR} 后端启动中 → ${URL}"
  echo "解释器：${PY}（$("${PY}" --version 2>&1)）"
  "${PY}" -m app serve &
  SERVER_PID=$!

  # 等端口真的监听起来再继续；最多等 5 秒。
  i=0
  while [ $i -lt 25 ]; do
    listening_on "$PORT" && break
    kill -0 "$SERVER_PID" 2>/dev/null || break   # 进程已经挂了，不必再等
    sleep 0.2
    i=$((i + 1))
  done
  if ! listening_on "$PORT"; then
    echo ""
    echo "✗ 后端没能在 127.0.0.1:${PORT} 上起来，页面不打开。上面的报错就是原因。"
    echo "  排查端口占用：lsof -nP -iTCP:${PORT} -sTCP:LISTEN"
    kill "$SERVER_PID" 2>/dev/null
    printf '按回车键关闭窗口… '
    read -r _pause
    exit 1
  fi
fi

# ---------- 前端 dev server（5173）：已在跑就复用，否则用 pnpm run dev 起一个 ----------
DEV_PID=""
if [ "$DEV_MODE" -eq 1 ]; then
  if listening_on "$DEV_PORT"; then
    echo "端口 ${DEV_PORT} 上已经有一个 dev server 在跑，复用它（改完前端刷新即见）。"
  else
    echo "前端 dev server 启动中 → ${PAGE_URL}"
    # 端口/主机/strictPort 都写在 frontend/vite.config.js 里。
    # （pnpm 会把 `--` 原样透传给 vite，命令行再补参数反而失效，所以这里只跑干净的 dev。）
    ( cd frontend && exec pnpm run dev ) &
    DEV_PID=$!

    i=0
    while [ $i -lt 75 ]; do
      listening_on "$DEV_PORT" && break
      kill -0 "$DEV_PID" 2>/dev/null || break   # dev server 起不来就别干等了
      sleep 0.2
      i=$((i + 1))
    done
    if ! listening_on "$DEV_PORT"; then
      echo ""
      echo "✗ dev server 没能在 127.0.0.1:${DEV_PORT} 上起来（端口被占？依赖没装？）。"
      echo "   可手动排查：cd frontend && pnpm run dev"
      [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
      printf '按回车键关闭窗口… '
      read -r _pause
      exit 1
    fi
  fi
fi

echo ""
echo "页面      → ${PAGE_URL}"
echo "接口文档  → ${URL}/docs"
echo "按 Ctrl+C 停止服务。"
echo ""
open_url

# Ctrl+C（SIGINT）会发给整个前台进程组，我们自己起的后端与 dev server 都会收到。
# 这里再补一次显式清理，保证异常退出时不留下孤儿进程；复用的旧服务不归我们管，不动。
cleanup() {
  if [ -n "$DEV_PID" ]; then
    pkill -P "$DEV_PID" 2>/dev/null
    kill "$DEV_PID" 2>/dev/null
  fi
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
}
trap 'echo; echo "服务已停止。"; cleanup; exit 0' INT TERM
wait
