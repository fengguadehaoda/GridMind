// ─── Vite 配置 ────────────────────────────
// 1. SCSS 全局注入 tokens.shared（组件内可直接用 var()）
// 2. @/* 路径别名 → src/*
// 3. assetsInlineLimit: 4KB（让小 SVG inline 到 HTML）

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        // 注入共享 SCSS 变量（每个 .scss 文件都自动可用）
        additionalData: `@use "@/styles/tokens.shared.scss" as *;`,
        // sass 新版默认使用 legacy API，避免 `@use` 重复注入警告
        api: 'modern-compiler',
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:9900',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    assetsInlineLimit: 4096, // < 4KB SVG/PNG 内联
  },
})
