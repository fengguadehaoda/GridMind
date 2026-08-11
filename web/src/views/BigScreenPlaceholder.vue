<script setup lang="ts">
/**
 * BigScreenPlaceholder · 大屏模式占位页（V1.7.0 F-1 仅接口预留，不实现大屏 UI）
 *
 * 用途：访问 ``/bigscreen`` 得到明确的「开发中」占位页，不出现 404 / 白屏
 * （PRD US-4.2）。
 *
 * 扩展点已就绪（PRD US-4.1/4.3）：
 * - ``DisplayMode`` 联合类型含 ``'bigscreen'``；
 * - ``displayStore.isBigScreen`` getter 可读；
 * - tokens 含 ``$bp-bigscreen`` / ``--bp-bigscreen`` 断点 token。
 *
 * 后续实现大屏 UI 时，直接替换本占位页内容即可，无需改路由入口。
 */
import { useRouter } from 'vue-router'
import { useDisplayStore } from '../stores/display'

const router = useRouter()
const displayStore = useDisplayStore()

function goBack() {
  void router.push('/')
}
</script>

<template>
  <div class="bigscreen-placeholder">
    <div class="bigscreen-placeholder__card">
      <div class="bigscreen-placeholder__badge">BIG SCREEN</div>
      <h1 class="bigscreen-placeholder__title">大屏模式（开发中）</h1>
      <ul class="bigscreen-placeholder__list">
        <li>扩展点已预留：DisplayMode = 'bigscreen'</li>
        <li>isBigScreen / 断点 token 已就绪</li>
        <li>本页仅接口占位，完整大屏 UI 将在后续批次实现</li>
      </ul>
      <button
        type="button"
        class="bigscreen-placeholder__back"
        @click="goBack"
      >
        ← 返回工作台
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.bigscreen-placeholder {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--gm-bg, #0b0e1a);
  padding: 24px;
}

.bigscreen-placeholder__card {
  max-width: 520px;
  width: 100%;
  padding: 40px 36px;
  border-radius: 16px;
  background: var(--gm-bg-elev-2, rgba(20, 20, 30, 0.98));
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.12));
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
  text-align: center;
}

.bigscreen-placeholder__badge {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--gm-accent, #615ced);
  border: 1px solid var(--gm-accent, #615ced);
  margin-bottom: 16px;
}

.bigscreen-placeholder__title {
  font-size: 22px;
  font-weight: 600;
  color: var(--gm-text-primary, #e5e7eb);
  margin: 0 0 16px;
}

.bigscreen-placeholder__list {
  list-style: none;
  padding: 0;
  margin: 0 0 24px;
  color: var(--gm-text-secondary, #9ca3af);
  font-size: 13px;
  line-height: 1.9;
}

.bigscreen-placeholder__back {
  padding: 10px 22px;
  border-radius: 999px;
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.14));
  background: transparent;
  color: var(--gm-text-primary, #e5e7eb);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.18s ease;
}

.bigscreen-placeholder__back:hover {
  border-color: var(--gm-accent, #615ced);
  color: var(--gm-accent, #615ced);
}
</style>
