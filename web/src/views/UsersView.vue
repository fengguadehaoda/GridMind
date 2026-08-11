<!--
  web/src/views/UsersView.vue · V1.8.0 用户管理页（T05）
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  架构 auth-architecture §1.4 + PRD §六 6.3 + US-6（AC6-1~6-5）：

  - 用户表格：用户名 / 角色 / 状态 / 最近登录 / 创建时间 / 操作；
  - 新建用户对话框：用户名、邮箱（可选）、初始密码、角色下拉、禁用开关；
  - 编辑：改角色 / 禁用开关 / 重置密码（密码策略前端轻校验，后端兜底）；
  - 新建后提示「用户需在首次登录时修改密码」；
  - 仅 admin 路由（/admin/users，meta.roles=['admin']）可访问，后端
    require_role(ADMIN) 兜底；
  - register-rbac T4：外层 el-tabs——Tab1「用户列表」（现有内容整体搬入，
    零逻辑改动）、Tab2「权限矩阵」（只读 RbacMatrixTable，数据仅来自后端）。
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-->
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { createUser, fetchUsers, updateUser } from '../api/auth'
import type { Role, UserSummary } from '../types'
import RbacMatrixTable from '../components/controls/RbacMatrixTable.vue'

/** 外层 Tab：用户列表 / 权限矩阵（register-rbac T4） */
const activeTab = ref<'users' | 'matrix'>('users')

/** 角色下拉（与后端 5 角色枚举一致） */
const ROLE_OPTIONS: Array<{ value: Role; label: string; type: 'primary' | 'success' | 'warning' | 'info' | 'danger' }> = [
  { value: 'dispatcher', label: '调度员', type: 'primary' },
  { value: 'operator', label: '运维', type: 'success' },
  { value: 'kb_admin', label: '知识管理员', type: 'warning' },
  { value: 'auditor', label: '审计', type: 'info' },
  { value: 'admin', label: '管理员', type: 'danger' },
]

function roleLabel(role: Role): string {
  return ROLE_OPTIONS.find((r) => r.value === role)?.label ?? role
}

function roleType(role: Role): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return ROLE_OPTIONS.find((r) => r.value === role)?.type ?? 'info'
}

// ── 列表 ──
const users = ref<UserSummary[]>([])
const total = ref(0)
const loading = ref(false)
const search = ref('')
const roleFilter = ref<'' | Role>('')
const disabledFilter = ref<'' | '0' | '1'>('')

async function load(): Promise<void> {
  loading.value = true
  try {
    const resp = await fetchUsers({
      role: roleFilter.value || undefined,
      disabled: disabledFilter.value === '' ? undefined : Number(disabledFilter.value),
      q: search.value || undefined,
      page: 1,
      page_size: 200,
    })
    users.value = resp.users
    total.value = resp.total
  } catch (err) {
    ElMessage.error(
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        '加载用户列表失败',
    )
  } finally {
    loading.value = false
  }
}

// ── 新建 / 编辑对话框 ──
const dialogVisible = ref(false)
const editing = ref<UserSummary | null>(null)
const submitting = ref(false)
const form = reactive({
  username: '',
  email: '',
  password: '',
  role: 'dispatcher' as Role,
  disabled: false,
})

function openCreate(): void {
  editing.value = null
  Object.assign(form, {
    username: '',
    email: '',
    password: '',
    role: 'dispatcher',
    disabled: false,
  })
  dialogVisible.value = true
}

function openEdit(user: UserSummary): void {
  editing.value = user
  Object.assign(form, {
    username: user.username,
    email: user.email ?? '',
    password: '',
    role: user.role,
    disabled: !!user.disabled,
  })
  dialogVisible.value = true
}

async function onSubmit(): Promise<void> {
  // 前端轻校验（后端 _validate_password 兜底：≥8 位 + 数字 + 字母）
  const password = form.password.trim()
  if (!editing.value) {
    if (!form.username.trim()) {
      ElMessage.warning('请输入用户名')
      return
    }
    if (!password || password.length < 8 || !/\d/.test(password) || !/[A-Za-z]/.test(password)) {
      ElMessage.warning('初始密码需至少 8 位，且包含数字和字母')
      return
    }
  } else if (password && (password.length < 8 || !/\d/.test(password) || !/[A-Za-z]/.test(password))) {
    ElMessage.warning('新密码需至少 8 位，且包含数字和字母')
    return
  }

  submitting.value = true
  try {
    if (editing.value) {
      const payload: Record<string, unknown> = {}
      if (form.role !== editing.value.role) payload.role = form.role
      if (!!form.disabled !== !!editing.value.disabled) payload.disabled = form.disabled ? 1 : 0
      if (password) payload.password = password
      await updateUser(editing.value.id, payload)
      ElMessage.success('用户已更新')
    } else {
      await createUser({
        username: form.username.trim(),
        email: form.email.trim() || null,
        password,
        role: form.role,
      })
      ElMessage.success('用户已创建，需在首次登录时修改密码')
    }
    dialogVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        '保存失败',
    )
  } finally {
    submitting.value = false
  }
}

async function toggleDisabled(user: UserSummary): Promise<void> {
  try {
    await updateUser(user.id, { disabled: user.disabled ? 0 : 1 })
    ElMessage.success(user.disabled ? '已启用该用户' : '已禁用该用户')
    await load()
  } catch (err) {
    ElMessage.error(
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        '操作失败',
    )
  }
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
</script>

<template>
  <div class="users-view" data-test="users-view">
    <div class="users-header">
      <h1 class="users-title">用户管理</h1>
      <div class="users-toolbar">
        <el-input
          v-model="search"
          placeholder="搜索用户名 / 邮箱"
          :prefix-icon="Search"
          clearable
          class="users-search"
          data-test="users-search"
          @keyup.enter="load"
          @clear="load"
        />
        <el-select v-model="roleFilter" placeholder="角色" clearable class="users-filter" @change="load">
          <el-option v-for="r in ROLE_OPTIONS" :key="r.value" :label="r.label" :value="r.value" />
        </el-select>
        <el-select v-model="disabledFilter" placeholder="状态" clearable class="users-filter" @change="load">
          <el-option label="启用" value="0" />
          <el-option label="禁用" value="1" />
        </el-select>
        <el-button :icon="Refresh" circle title="刷新" data-test="users-refresh" @click="load" />
        <el-button
          type="primary"
          :icon="Plus"
          data-test="users-create"
          @click="openCreate"
        >
          新建用户
        </el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="users-tabs" data-test="users-tabs">
      <!-- Tab1：用户列表（现有表格/过滤/对话框整体搬入，零逻辑改动） -->
      <el-tab-pane label="用户列表" name="users">
        <el-table
          v-loading="loading"
          :data="users"
          border
          stripe
          class="users-table"
          data-test="users-table"
        >
          <el-table-column prop="username" label="用户名" min-width="140" />
          <el-table-column label="角色" width="130">
            <template #default="{ row }">
              <el-tag size="small" :type="roleType(row.role as Role)" effect="dark">
                {{ roleLabel(row.role as Role) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <span :class="['users-status', row.disabled ? 'is-disabled' : 'is-active']">
                ● {{ row.disabled ? '禁用' : '启用' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="最近登录" min-width="170">
            <template #default="{ row }">{{ formatTime(row.last_login_at) }}</template>
          </el-table-column>
          <el-table-column label="创建时间" min-width="170">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button size="small" text type="primary" data-test="users-edit" @click="openEdit(row)">
                编辑
              </el-button>
              <el-button
                size="small"
                text
                :type="row.disabled ? 'success' : 'danger'"
                data-test="users-toggle"
                @click="toggleDisabled(row)"
              >
                {{ row.disabled ? '启用' : '禁用' }}
              </el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无用户" />
          </template>
        </el-table>

        <div class="users-total">共 {{ total }} 个用户</div>

        <!-- 新建 / 编辑对话框 -->
        <el-dialog
          v-model="dialogVisible"
          :title="editing ? '编辑用户' : '新建用户'"
          width="440px"
          append-to-body
          data-test="users-dialog"
        >
          <el-form label-position="top" class="users-form">
            <el-form-item label="用户名">
              <el-input v-model="form.username" :disabled="!!editing" placeholder="小写字母/数字/_ - ." data-test="users-form-username" />
            </el-form-item>
            <el-form-item label="邮箱（可选）">
              <el-input v-model="form.email" placeholder="user@example.com" data-test="users-form-email" />
            </el-form-item>
            <el-form-item :label="editing ? '重置密码（留空则不修改）' : '初始密码'">
              <el-input
                v-model="form.password"
                type="password"
                :show-password="true"
                placeholder="至少 8 位，含数字和字母"
                data-test="users-form-password"
              />
            </el-form-item>
            <el-form-item label="角色">
              <el-select v-model="form.role" class="users-form-role" data-test="users-form-role">
                <el-option v-for="r in ROLE_OPTIONS" :key="r.value" :label="r.label" :value="r.value" />
              </el-select>
            </el-form-item>
            <el-form-item label="禁用账号">
              <el-switch v-model="form.disabled" data-test="users-form-disabled" />
              <span class="users-form-hint">禁用后该用户登录 / 刷新 / 查询本人信息均被拒绝</span>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="dialogVisible = false">取消</el-button>
            <el-button type="primary" :loading="submitting" data-test="users-form-submit" @click="onSubmit">
              保存
            </el-button>
          </template>
        </el-dialog>
      </el-tab-pane>

      <!-- Tab2：权限矩阵（只读；数据仅来自后端 GET /rbac/matrix） -->
      <el-tab-pane label="权限矩阵" name="matrix" lazy>
        <RbacMatrixTable />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped lang="scss">
.users-view {
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.users-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.users-title {
  font-family: var(--font-cn);
  font-size: var(--fs-lg, 18px);
  font-weight: var(--fw-bold);
  color: var(--text-primary);
  margin: 0;
}

.users-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.users-search {
  width: 220px;
}

.users-filter {
  width: 130px;
}

.users-tabs {
  width: 100%;

  :deep(.el-tabs__content) {
    padding-top: var(--space-2);
  }
}

.users-table {
  width: 100%;
}

.users-status {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);

  &.is-active {
    color: var(--success, #67c23a);
  }

  &.is-disabled {
    color: var(--danger, #f56c6c);
  }
}

.users-total {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}

.users-form-role {
  width: 100%;
}

.users-form-hint {
  font-family: var(--font-cn);
  font-size: var(--fs-xs, 12px);
  color: var(--text-tertiary, #999);
  margin-left: var(--space-2);
}
</style>
