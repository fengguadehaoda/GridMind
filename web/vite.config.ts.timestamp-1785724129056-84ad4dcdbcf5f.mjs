// vite.config.ts
import { defineConfig } from "file:///F:/GridOpsAgent/web/node_modules/vite/dist/node/index.js";
import vue from "file:///F:/GridOpsAgent/web/node_modules/@vitejs/plugin-vue/dist/index.mjs";
import { fileURLToPath, URL } from "node:url";
var __vite_injected_original_import_meta_url = "file:///F:/GridOpsAgent/web/vite.config.ts";
var vite_config_default = defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", __vite_injected_original_import_meta_url))
    }
  },
  css: {
    preprocessorOptions: {
      scss: {
        // 注入共享 SCSS 变量（每个 .scss 文件都自动可用）
        additionalData: `@use "@/styles/tokens.shared.scss" as *;`,
        // sass 新版默认使用 legacy API，避免 `@use` 重复注入警告
        api: "modern-compiler"
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:9900",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, "")
      }
    }
  },
  build: {
    assetsInlineLimit: 4096
    // < 4KB SVG/PNG 内联
  }
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcudHMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCJGOlxcXFxHcmlkT3BzQWdlbnRcXFxcd2ViXCI7Y29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2ZpbGVuYW1lID0gXCJGOlxcXFxHcmlkT3BzQWdlbnRcXFxcd2ViXFxcXHZpdGUuY29uZmlnLnRzXCI7Y29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2ltcG9ydF9tZXRhX3VybCA9IFwiZmlsZTovLy9GOi9HcmlkT3BzQWdlbnQvd2ViL3ZpdGUuY29uZmlnLnRzXCI7Ly8gXHUyNTAwXHUyNTAwXHUyNTAwIFZpdGUgXHU5MTREXHU3RjZFIFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFx1MjUwMFxuLy8gMS4gU0NTUyBcdTUxNjhcdTVDNDBcdTZDRThcdTUxNjUgdG9rZW5zLnNoYXJlZFx1RkYwOFx1N0VDNFx1NEVGNlx1NTE4NVx1NTNFRlx1NzZGNFx1NjNBNVx1NzUyOCB2YXIoKVx1RkYwOVxuLy8gMi4gQC8qIFx1OERFRlx1NUY4NFx1NTIyQlx1NTQwRCBcdTIxOTIgc3JjLypcbi8vIDMuIGFzc2V0c0lubGluZUxpbWl0OiA0S0JcdUZGMDhcdThCQTlcdTVDMEYgU1ZHIGlubGluZSBcdTUyMzAgSFRNTFx1RkYwOVxuXG5pbXBvcnQgeyBkZWZpbmVDb25maWcgfSBmcm9tICd2aXRlJ1xuaW1wb3J0IHZ1ZSBmcm9tICdAdml0ZWpzL3BsdWdpbi12dWUnXG5pbXBvcnQgeyBmaWxlVVJMVG9QYXRoLCBVUkwgfSBmcm9tICdub2RlOnVybCdcblxuZXhwb3J0IGRlZmF1bHQgZGVmaW5lQ29uZmlnKHtcbiAgcGx1Z2luczogW3Z1ZSgpXSxcbiAgcmVzb2x2ZToge1xuICAgIGFsaWFzOiB7XG4gICAgICAnQCc6IGZpbGVVUkxUb1BhdGgobmV3IFVSTCgnLi9zcmMnLCBpbXBvcnQubWV0YS51cmwpKSxcbiAgICB9LFxuICB9LFxuICBjc3M6IHtcbiAgICBwcmVwcm9jZXNzb3JPcHRpb25zOiB7XG4gICAgICBzY3NzOiB7XG4gICAgICAgIC8vIFx1NkNFOFx1NTE2NVx1NTE3MVx1NEVBQiBTQ1NTIFx1NTNEOFx1OTFDRlx1RkYwOFx1NkJDRlx1NEUyQSAuc2NzcyBcdTY1ODdcdTRFRjZcdTkwRkRcdTgxRUFcdTUyQThcdTUzRUZcdTc1MjhcdUZGMDlcbiAgICAgICAgYWRkaXRpb25hbERhdGE6IGBAdXNlIFwiQC9zdHlsZXMvdG9rZW5zLnNoYXJlZC5zY3NzXCIgYXMgKjtgLFxuICAgICAgICAvLyBzYXNzIFx1NjVCMFx1NzI0OFx1OUVEOFx1OEJBNFx1NEY3Rlx1NzUyOCBsZWdhY3kgQVBJXHVGRjBDXHU5MDdGXHU1MTREIGBAdXNlYCBcdTkxQ0RcdTU5MERcdTZDRThcdTUxNjVcdThCNjZcdTU0NEFcbiAgICAgICAgYXBpOiAnbW9kZXJuLWNvbXBpbGVyJyxcbiAgICAgIH0sXG4gICAgfSxcbiAgfSxcbiAgc2VydmVyOiB7XG4gICAgcG9ydDogNTE3MyxcbiAgICBwcm94eToge1xuICAgICAgJy9hcGknOiB7XG4gICAgICAgIHRhcmdldDogJ2h0dHA6Ly9sb2NhbGhvc3Q6OTkwMCcsXG4gICAgICAgIGNoYW5nZU9yaWdpbjogdHJ1ZSxcbiAgICAgICAgcmV3cml0ZTogKHBhdGgpID0+IHBhdGgucmVwbGFjZSgvXlxcL2FwaS8sICcnKSxcbiAgICAgIH0sXG4gICAgfSxcbiAgfSxcbiAgYnVpbGQ6IHtcbiAgICBhc3NldHNJbmxpbmVMaW1pdDogNDA5NiwgLy8gPCA0S0IgU1ZHL1BORyBcdTUxODVcdTgwNTRcbiAgfSxcbn0pXG4iXSwKICAibWFwcGluZ3MiOiAiO0FBS0EsU0FBUyxvQkFBb0I7QUFDN0IsT0FBTyxTQUFTO0FBQ2hCLFNBQVMsZUFBZSxXQUFXO0FBUGdILElBQU0sMkNBQTJDO0FBU3BNLElBQU8sc0JBQVEsYUFBYTtBQUFBLEVBQzFCLFNBQVMsQ0FBQyxJQUFJLENBQUM7QUFBQSxFQUNmLFNBQVM7QUFBQSxJQUNQLE9BQU87QUFBQSxNQUNMLEtBQUssY0FBYyxJQUFJLElBQUksU0FBUyx3Q0FBZSxDQUFDO0FBQUEsSUFDdEQ7QUFBQSxFQUNGO0FBQUEsRUFDQSxLQUFLO0FBQUEsSUFDSCxxQkFBcUI7QUFBQSxNQUNuQixNQUFNO0FBQUE7QUFBQSxRQUVKLGdCQUFnQjtBQUFBO0FBQUEsUUFFaEIsS0FBSztBQUFBLE1BQ1A7QUFBQSxJQUNGO0FBQUEsRUFDRjtBQUFBLEVBQ0EsUUFBUTtBQUFBLElBQ04sTUFBTTtBQUFBLElBQ04sT0FBTztBQUFBLE1BQ0wsUUFBUTtBQUFBLFFBQ04sUUFBUTtBQUFBLFFBQ1IsY0FBYztBQUFBLFFBQ2QsU0FBUyxDQUFDLFNBQVMsS0FBSyxRQUFRLFVBQVUsRUFBRTtBQUFBLE1BQzlDO0FBQUEsSUFDRjtBQUFBLEVBQ0Y7QUFBQSxFQUNBLE9BQU87QUFBQSxJQUNMLG1CQUFtQjtBQUFBO0FBQUEsRUFDckI7QUFDRixDQUFDOyIsCiAgIm5hbWVzIjogW10KfQo=
