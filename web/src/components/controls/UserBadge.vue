<!--
  web/src/components/controls/UserBadge.vue · M-5 用户 + 角色徽标（T05）
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  架构 session-mgmt-architecture §1.4 + PRD US-3 / AC3-1：
    - Header 右侧显示「用户名 + 角色徽标」（如 `张三 · 调度员`）
    - 数据源：base64url 解码 JWT `role` claim（方案 A，Q4）
    - 解析失败 / 缺失 / dev token → 默认「调度员」（fail-closed 展示层，
      与后端 get_role 默认一致；AC3-5 dev 模式行为一致）
    - 角色文案 / 颜色映射（5 角色区分，低优先级视觉）
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-->
<script setup lang="ts">
import { computed } from 'vue'
import { User } from '@element-plus/icons-vue'
import { getJwtDisplayName, getJwtRole, type JwtRole } from '../../composables/useJwtAuth'

/** 角色 → 展示文案 / Element tag type（视觉区分） */
const ROLE_META: Record<JwtRole, { label: string; type: 'primary' | 'success' | 'warning' | 'info' | 'danger' }> = {
  dispatcher: { label: '调度员', type: 'primary' },
  operator: { label: '运维', type: 'success' },
  kb_admin: { label: '知识管理员', type: 'warning' },
  auditor: { label: '审计', type: 'info' },
  admin: { label: '管理员', type: 'danger' },
}

const displayName = computed<string>(() => getJwtDisplayName())
const role = computed<JwtRole>(() => getJwtRole())
const roleMeta = computed(() => ROLE_META[role.value] ?? ROLE_META.dispatcher)
</script>

<template>
  <div class="user-badge" data-test="user-badge">
    <el-icon class="user-badge__icon" :size="16"><User /></el-icon>
    <span class="user-badge__name" :title="displayName">{{ displayName }}</span>
    <el-tag
      size="small"
      :type="roleMeta.type"
      effect="dark"
      class="user-badge__role"
      data-test="user-badge-role"
    >
      {{ roleMeta.label }}
    </el-tag>
  </div>
</template>

<style scoped lang="scss">
.user-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 4px var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: 999px;
  background: var(--bg-card);
  white-space: nowrap;
}

.user-badge__icon {
  color: var(--brand-primary);
  flex-shrink: 0;
}

.user-badge__name {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-badge__role {
  flex-shrink: 0;
}
</style>
