@echo off
chcp 65001 >nul 2>&1
REM 恋爱·职场聊天神器 —— Windows 一键启动（双击运行）。
REM 后端 API 在 127.0.0.1:8767，前端 dev server 在 127.0.0.1:5173，自动打开页面。
cd /d "%~dp0"

set "PORT=8767"
set "DEV_PORT=5173"
set "URL=http://127.0.0.1:%PORT%"
set "PAGE_URL=http://127.0.0.1:%DEV_PORT%"
set "FD=%~dp0frontend"

echo 恋爱·职场聊天神器 启动中…

REM ---------- 后端依赖：uv 同步 ----------
where uv >nul 2>&1
if %errorlevel%==0 (
  echo 同步后端依赖（uv）…
  uv sync --no-dev
) else (
  echo [警告] 未找到 uv，跳过依赖同步。若 .venv 不存在请先安装 uv（winget install astral-sh.uv 或 pip install uv）。
)

REM ---------- 解释器：优先用 .venv ----------
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

REM ---------- 起后端（新开一个窗口，/k 保持）----------
echo 后端启动中 → %URL%
start "jev-backend" cmd /k "%PY% -m app serve"

REM ---------- 前端：优先 dev server ----------
where pnpm >nul 2>&1
if %errorlevel%==0 (
  if not exist "%FD%\node_modules" (
    echo 前端依赖还没装，正在 pnpm install（首次会稍慢）…
    pushd "%FD%"
    call pnpm install
    popd
  )
  echo 前端 dev server 启动中 → %PAGE_URL%
  start "jev-frontend" cmd /k " cd /d %FD% && pnpm run dev"
  timeout /t 6 >nul
  start "" "%PAGE_URL%"
) else (
  echo [警告] 未找到 pnpm，改用构建产物（页面在 %URL%）。请先装 Node.js 与 pnpm（npm i -g pnpm）。
  timeout /t 4 >nul
  start "" "%URL%"
)

echo.
echo 页面      → %PAGE_URL%
echo 接口文档  → %URL%/docs
echo 关闭「jev-backend」「jev-frontend」两个窗口即可停止服务。
echo 按任意键关闭此窗口（后台服务继续运行）。
pause >nul
exit /b
