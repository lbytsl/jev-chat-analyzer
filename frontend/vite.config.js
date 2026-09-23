import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 构建产物由 FastAPI 托管：GET / → dist/index.html，GET /assets/* → dist/assets/*。
// 所以 base 用绝对路径 '/'，不要改成 './'（页面只在服务里打开，不支持 file:// 双击）。
export default defineConfig({
  plugins: [vue()],
  base: '/',
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    // 固定 127.0.0.1:5173 且不自动顺延：启动脚本按这个端口判活，被占了就该显式报错，
    // 而不是悄悄换到 5174（那样脚本会以为没起来）。pnpm run dev 直接读这份配置。
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // 开发模式下前端直连后端（后端 CORS 已允许 localhost / 127.0.0.1 任意端口），
    // 所以这里不配 proxy：同一条代码路径在开发与生产下都成立。
    open: false,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
  },
})
