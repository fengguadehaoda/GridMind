<!--
  web/src/components/controls/RbacMatrixTable.vue · 权限矩阵可视化（register-rbac T4）
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  架构 register-rbac §1.4 F06 + PRD §六 6.2 / US-3 / US-4：

  - 数据**只来自** `GET /rbac/matrix`（后端权威定义序列化），前端**零硬编码**
    权限布尔值（`ROLE_OPTIONS` 仅用于用户管理角色下拉，非矩阵数据源）；
  - 7 行（端点类别）× 5 列（角色）矩阵：✓(绿)/✗(灰)；scope 角标
    （own→「本人」/all→「全部」，来自后端 scope 字段）；
  - 行头悬浮显示 `categories[].endpoints`；点击行/列头高亮 + 说明卡
    （`roles[].description` / `categories[].description`）；
  - 加载态 `v-loading`；失败 → 错误态 + 「重试」按钮（不渲染伪造矩阵）；
  - **纯只读**：无任何勾选/编辑交互——即使显示 ✓ 实际访问仍由后端
    `require_role` / `verify_*` 判定（前端不承担安全边界，PRD AC5-3）。
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-->
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { fetchRbacMatrix } from '../../api/auth'
import type { RbacCategoryMeta, RbacMatrixResponse, RbacRoleMeta, Role } from '../../types'

const matrix = ref<RbacMatrixResponse | null>(null)
const loading = ref(false)
const error = ref('')

/** 点击行/列头高亮状态 */
const highlight = reactive<{ role: Role | null; category: string | null }>({
  role: null,
  category: null,
})

/** 高亮说明卡内容（title/description/endpoints 可选） */
const highlightInfo = ref<{ title: string; description: string; endpoints?: string } | null>(null)

/** 加载矩阵（onMounted / 重试共用） */
async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    matrix.value = await fetchRbacMatrix()
  } catch (err) {
    matrix.value = null
    error.value =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
      '权限矩阵加载失败'
  } finally {
    loading.value = false
  }
}

/** 重试按钮 */
async function retry(): Promise<void> {
  await load()
}

/** scope 角标：own→本人 / all→全部；无该类别 scope → 纯 ✓ */
function scopeOf(categoryKey: string, roleKey: Role): 'own' | 'all' | undefined {
  return matrix.value?.scope?.[categoryKey]?.[roleKey]
}

/** 点击角色列头：高亮该列 + 展示角色说明卡（再点一次取消高亮） */
function selectRole(role: RbacRoleMeta): void {
  if (highlight.role === role.key) {
    highlight.role = null
    highlightInfo.value = null
    return
  }
  highlight.role = role.key
  highlight.category = null
  highlightInfo.value = { title: role.label, description: role.description }
}

/** 点击端点类别行头：高亮该行 + 展示类别说明卡（再点一次取消高亮） */
function selectCategory(cat: RbacCategoryMeta): void {
  if (highlight.category === cat.key) {
    highlight.category = null
    highlightInfo.value = null
    return
  }
  highlight.category = cat.key
  highlight.role = null
  highlightInfo.value = {
    title: cat.label,
    description: cat.description,
    endpoints: cat.endpoints.join('、'),
  }
}

onMounted(load)
</script>

<template>
  <div class="rbac-matrix" data-test="rbac-matrix">
    <!-- 错误态 + 重试（不渲染伪造矩阵） -->
    <div v-if="error" class="rbac-error" data-test="rbac-matrix-error">
      <el-alert :title="error" type="error" show-icon :closable="false" class="rbac-error-alert" />
      <el-button type="primary" plain data-test="rbac-matrix-retry" @click="retry">
        重试
      </el-button>
    </div>

    <div v-else v-loading="loading" class="rbac-matrix-body">
      <table v-if="matrix" class="rbac-table" data-test="rbac-matrix-table">
        <thead>
          <tr>
            <th class="rbac-corner">端点类别</th>
            <th
              v-for="role in matrix.roles"
              :key="role.key"
              class="rbac-role-head"
              :class="{ 'is-highlight': highlight.role === role.key }"
              data-test="rbac-role-head"
              @click="selectRole(role)"
            >
              {{ role.label }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="cat in matrix.categories"
            :key="cat.key"
            class="rbac-row"
            :class="{ 'is-highlight': highlight.category === cat.key }"
          >
            <th class="rbac-cat-head" data-test="rbac-cat-head" @click="selectCategory(cat)">
              <el-tooltip
                :content="cat.endpoints.join('、')"
                placement="top"
                :show-after="200"
                popper-class="rbac-endpoints-tooltip"
              >
                <span class="rbac-cat-label">{{ cat.label }}</span>
              </el-tooltip>
            </th>
            <td v-for="role in matrix.roles" :key="role.key" class="rbac-cell">
              <span
                v-if="matrix.matrix[role.key]?.[cat.key]"
                class="rbac-mark is-allowed"
                data-test="rbac-cell-allowed"
              >
                <template v-if="scopeOf(cat.key, role.key) === 'own'">✓(本人)</template>
                <template v-else-if="scopeOf(cat.key, role.key) === 'all'">✓(全部)</template>
                <template v-else>✓</template>
              </span>
              <span v-else class="rbac-mark is-denied" data-test="rbac-cell-denied">✗</span>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 说明卡 / 提示 -->
      <div v-if="highlightInfo" class="rbac-highlight-card" data-test="rbac-highlight-card">
        <h4 class="rbac-highlight-title">{{ highlightInfo.title }}</h4>
        <p class="rbac-highlight-desc">{{ highlightInfo.description }}</p>
        <p v-if="highlightInfo.endpoints" class="rbac-highlight-endpoints">
          {{ highlightInfo.endpoints }}
        </p>
      </div>
      <p v-else class="rbac-hint">点击角色名或端点类别查看说明</p>

      <p class="rbac-source">
        数据来源：GET /rbac/matrix（后端权威，只读展示；生成于 {{ matrix?.generated_at ?? '—' }}）
      </p>
    </div>
  </div>
</template>

<style scoped lang="scss">
.rbac-matrix {
  width: 100%;
}

.rbac-error {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-4);
}

.rbac-error-alert {
  width: 100%;
}

.rbac-matrix-body {
  min-height: 260px;
}

.rbac-table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-cn);
  font-size: var(--fs-sm, 13px);

  th,
  td {
    border: 1px solid var(--border-default, #e4e7ed);
    padding: var(--space-2) var(--space-3);
    text-align: center;
  }

  .rbac-corner {
    background: var(--bg-muted, #f5f7fa);
    color: var(--text-secondary);
    font-weight: var(--fw-semibold);
    text-align: left;
    white-space: nowrap;
  }

  .rbac-role-head {
    background: var(--bg-muted, #f5f7fa);
    color: var(--text-primary);
    font-weight: var(--fw-semibold);
    cursor: pointer;
    user-select: none;
    transition: background-color 0.2s;

    &:hover {
      background: var(--brand-primary-soft, rgba(97, 92, 237, 0.12));
    }

    &.is-highlight {
      background: var(--brand-primary-soft, rgba(97, 92, 237, 0.2));
      color: var(--brand-primary, #615ced);
    }
  }

  .rbac-cat-head {
    background: var(--bg-muted, #f5f7fa);
    color: var(--text-primary);
    font-weight: var(--fw-semibold);
    text-align: left;
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
    transition: background-color 0.2s;

    &:hover {
      background: var(--brand-primary-soft, rgba(97, 92, 237, 0.12));
    }
  }

  .rbac-cat-label {
    border-bottom: 1px dashed var(--text-tertiary, #999);
  }

  tr.is-highlight {
    .rbac-cat-head {
      background: var(--brand-primary-soft, rgba(97, 92, 237, 0.2));
      color: var(--brand-primary, #615ced);
    }

    td {
      background: var(--brand-primary-soft, rgba(97, 92, 237, 0.06));
    }
  }

  .rbac-cell {
    .rbac-mark {
      font-weight: var(--fw-semibold);
      font-family: var(--font-mono, ui-monospace, monospace);

      &.is-allowed {
        color: var(--success, #67c23a);
      }

      &.is-denied {
        color: var(--text-tertiary, #999);
      }
    }
  }
}

.rbac-highlight-card {
  margin-top: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--brand-primary-soft, rgba(97, 92, 237, 0.35));
  background: var(--brand-primary-soft, rgba(97, 92, 237, 0.08));
  border-radius: var(--radius-md, 8px);
}

.rbac-highlight-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm, 13px);
  font-weight: var(--fw-bold);
  color: var(--text-primary);
  margin: 0 0 var(--space-1);
}

.rbac-highlight-desc {
  font-family: var(--font-cn);
  font-size: var(--fs-sm, 13px);
  color: var(--text-secondary);
  margin: 0;
}

.rbac-highlight-endpoints {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--fs-xs, 12px);
  color: var(--text-tertiary, #999);
  margin: var(--space-1) 0 0;
  word-break: break-all;
}

.rbac-hint {
  font-family: var(--font-cn);
  font-size: var(--fs-sm, 13px);
  color: var(--text-tertiary, #999);
  margin: var(--space-4) 0 0;
}

.rbac-source {
  font-family: var(--font-cn);
  font-size: var(--fs-xs, 12px);
  color: var(--text-tertiary, #999);
  margin: var(--space-2) 0 0;
}
</style>
