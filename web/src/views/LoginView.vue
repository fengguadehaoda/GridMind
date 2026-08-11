<!--
  web/src/views/LoginView.vue · V1.8.0 真实登录页（T05）+ 开放注册（register-rbac T4）
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  架构 auth-architecture §1.4 + PRD §六 6.1 + US-1/US-2/US-8 +
  register-rbac §1.4 F04（登录/注册同页 Tab 切换，拍板 2）：

  - 居中卡片、Logo、`el-tabs`（登录/注册）切换，各自表单状态保留；
  - 登录表单：用户名/密码（回车提交、密码显隐）、错误提示条、loading 防重复；
  - 注册表单：用户名/密码/确认密码/邮箱（可选）+ 前端校验（用户名非空、
    小写规则、密码 ≥8 位含数字+字母、**两次密码一致本地拦截**、邮箱轻校验）
    + loading 防重复 + 错误条（409/422/429 明确文案，**不清空已填用户名**）；
  - 注册成功 → `authStore.register`（注册即登录）→ `router.replace(redirectTarget)`
    （与登录一致）；底部小字「注册即登录；默认角色为调度员；密码至少 8 位
    且包含数字和字母」；
  - 登录成功：must_change_password=1 → 同页改密面板（改密后清标记）；
    90 天过期（/auth/me password_expiring）→ 一次性 ElMessage 提醒；
  - redirect 回跳（守卫记录 / ?redirect= 参数），无 redirect 回首页。
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-->
<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Hide, Lock, Message, User, View } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
import { changePassword } from '../api/auth'
import LogoHorizontal from '../components/brand/LogoHorizontal.vue'

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

/** 登录 / 注册 Tab（register-rbac T4，拍板 2） */
const activeTab = ref<'login' | 'register'>('login')

const form = reactive({ username: '', password: '' })
const showPassword = ref(false)
const loading = ref(false)
const errorMsg = ref('')

/** 注册表单（用户名/密码/确认密码/邮箱可选） */
const registerForm = reactive({
  username: '',
  password: '',
  confirmPassword: '',
  email: '',
})
const registerLoading = ref(false)
const registerError = ref('')

/** 注册错误技术详情（仅 dev 渲染）：status + url + method，辅助定位"打到哪个后端" */
interface RegisterErrorInfo {
  message: string
  status: number | null
  url: string
  method: string
}
const registerErrorInfo = ref<RegisterErrorInfo | null>(null)
/** 是否 dev 构建（技术详情折叠区仅 dev 下渲染） */
const isDev = import.meta.env.DEV

/** 技术详情折叠区展示文本（status + url + method，精确换行不依赖模板空白压缩） */
const registerErrorDetailText = computed<string>(() => {
  const info = registerErrorInfo.value
  if (!info) return ''
  return [
    `status : ${info.status ?? '-'}`,
    `url    : ${info.url}`,
    `method : ${info.method}`,
  ].join('\n')
})

/** 登录成功后回跳目标（?redirect= 优先，否则守卫记录，最后首页） */
const redirectTarget = computed<string>(() => {
  if (typeof route.query.redirect === 'string' && route.query.redirect.length > 0) {
    return route.query.redirect
  }
  const recorded = authStore.consumeRedirect()
  return recorded || '/'
})

// ── 首次登录强制改密面板（must_change_password=1）──
const mustChange = ref(false)
const pwdForm = reactive({ newPassword: '', confirmPassword: '' })
const pwdLoading = ref(false)
const pwdError = ref('')
const showPwd = ref(false)

/** 提交登录（回车 / 按钮均走此） */
async function onSubmit(): Promise<void> {
  const username = form.username.trim()
  if (!username || !form.password) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  errorMsg.value = ''
  loading.value = true
  try {
    await authStore.login(username, form.password)
    if (authStore.user?.must_change_password) {
      mustChange.value = true
      return
    }
    ElMessage.success('登录成功')
    // 90 天过期提醒（一次性；不强制）
    if (authStore.user) {
      void maybeWarnPasswordExpiry()
    }
    void router.replace(redirectTarget.value)
  } catch (err) {
    errorMsg.value =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
      '登录失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

/** 登录成功后拉取 /auth/me 校验会话 + 一次性过期提醒 */
async function maybeWarnPasswordExpiry(): Promise<void> {
  try {
    await authStore.fetchMe()
    if (authStore.user?.password_expiring) {
      ElMessage.warning('密码即将过期，请尽快修改')
    }
  } catch {
    // /auth/me 失败不阻断登录体验（dev 占位 / 网络抖动）
  }
}

/** 从 axios 错误中提取可观测元信息（status/url/method；网络错误无 response 时回退 config） */
function extractErrorMeta(err: unknown): { status: number | null; url: string; method: string } {
  const e = err as {
    response?: { status?: number; config?: { url?: string; method?: string; baseURL?: string } }
    config?: { url?: string; method?: string; baseURL?: string }
  }
  const status = e?.response?.status ?? null
  const cfg = e?.response?.config ?? e?.config
  const path = cfg?.url ?? '/auth/register'
  const baseURL = cfg?.baseURL ?? ''
  const method = cfg?.method ? String(cfg.method).toUpperCase() : 'POST'
  const url = baseURL ? `${String(baseURL).replace(/\/$/, '')}${path}` : path
  return { status, url, method }
}

/**
 * 注册错误文案分类映射（可观测性改造：404/网络错误等秒定位根因）。
 * 返回 { message, status, url, method }——message 用于错误条展示，
 * status/url/method 供 dev 技术详情折叠区渲染。
 */
function registerErrorMessage(err: unknown): RegisterErrorInfo {
  const { status, url, method } = extractErrorMeta(err)
  const e = err as {
    code?: string
    message?: string
    response?: { data?: { detail?: string } }
  }

  // 网络层错误（无 HTTP 响应）：连接拒绝 / 超时 / Vite 代理不可达
  if (!e.response) {
    const code = e.code ?? ''
    const msg = e.message ?? ''
    if (code === 'ECONNABORTED' || code === 'ERR_NETWORK' || /network|timeout/i.test(msg)) {
      return {
        message: `无法连接到后端（${url}），请确认 uvicorn 是否在运行`,
        status,
        url,
        method,
      }
    }
  }

  let message: string
  switch (status) {
    case 404:
      message =
        '后端无注册端点 /auth/register（可能后端未重启或端口错配），请检查后端是否含最新版'
      break
    case 401:
      message = '需要管理员配置（生产模式要求登录的接口）'
      break
    case 409:
      message = '用户名已被占用'
      break
    case 422:
      message = '密码 ≥8 位且包含数字和字母；用户名仅支持小写字母数字_-.（1-64 位）'
      break
    case 429:
      message = '请求过于频繁，请稍后再试'
      break
    default: {
      const detail = e.response?.data?.detail
      message = detail || '注册失败，请稍后重试'
    }
  }
  return { message, status, url, method }
}

/** 提交注册（回车 / 按钮均走此；前端校验本地拦截，后端兜底） */
async function onSubmitRegister(): Promise<void> {
  const username = registerForm.username.trim()
  const password = registerForm.password
  const email = registerForm.email.trim()

  registerError.value = ''
  registerErrorInfo.value = null
  // 前端轻校验（后端 _validate_password 兜底：≥8 位 + 数字 + 字母）
  if (!username) {
    registerError.value = '请输入用户名'
    return
  }
  if (!/^[a-z0-9_.-]{1,64}$/.test(username)) {
    registerError.value = '用户名仅支持小写字母、数字、_ - .（1-64 位）'
    return
  }
  if (!password || password.length < 8 || !/\d/.test(password) || !/[A-Za-z]/.test(password)) {
    registerError.value = '密码需至少 8 位，且包含数字和字母'
    return
  }
  // AC1-5：两次密码不一致 → 本地拦截，不提交
  if (password !== registerForm.confirmPassword) {
    registerError.value = '两次输入的密码不一致'
    return
  }
  // 邮箱可选；填了才轻校验基础格式（P1-4 后端严格校验未启用，前端先拦）
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    registerError.value = '邮箱格式不正确'
    return
  }

  registerLoading.value = true
  try {
    await authStore.register(username, password, email || undefined)
    ElMessage.success('注册成功')
    void router.replace(redirectTarget.value)
  } catch (err) {
    // AC1-7：失败仅错误提示条，**不清空已填用户名**；同时记录技术详情（dev 展示）
    registerErrorInfo.value = registerErrorMessage(err)
    registerError.value = registerErrorInfo.value.message
  } finally {
    registerLoading.value = false
  }
}

/** 首次登录改密提交（改密后撤销全部 refresh；当前 access 仍有效） */
async function onSubmitPassword(): Promise<void> {
  if (!pwdForm.newPassword || pwdForm.newPassword !== pwdForm.confirmPassword) {
    pwdError.value = '两次输入的新密码不一致'
    return
  }
  pwdError.value = ''
  pwdLoading.value = true
  try {
    await changePassword({ old_password: form.password, new_password: pwdForm.newPassword })
    await authStore.fetchMe()
    ElMessage.success('密码已修改，请继续使用')
    mustChange.value = false
    void router.replace(redirectTarget.value)
  } catch (err) {
    pwdError.value =
      (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
      '修改密码失败，请重试'
  } finally {
    pwdLoading.value = false
  }
}
</script>

<template>
  <div class="login-view" data-test="login-view">
    <div class="login-card">
      <div class="login-brand">
        <LogoHorizontal :size="48" />
      </div>
      <h1 class="login-title">灵枢电网 · GridMind</h1>
      <p class="login-subtitle">请登录后继续使用</p>

      <!-- 登录 / 注册 Tab 切换（拍板 2：同页 Tab，各自表单状态保留） -->
      <el-tabs v-model="activeTab" class="login-tabs" data-test="login-tabs">
        <!-- 登录 Tab -->
        <el-tab-pane label="登录" name="login">
          <!-- 错误提示条（统一文案，不暴露账号存在性） -->
          <el-alert
            v-if="errorMsg"
            :title="errorMsg"
            type="error"
            show-icon
            :closable="false"
            class="login-error"
            data-test="login-error"
          />

          <el-form label-position="top" class="login-form" @submit.prevent="onSubmit">
            <el-form-item label="用户名">
              <el-input
                v-model="form.username"
                placeholder="请输入用户名"
                :prefix-icon="User"
                data-test="login-username"
                @keyup.enter="onSubmit"
              />
            </el-form-item>
            <el-form-item label="密码">
              <el-input
                v-model="form.password"
                type="password"
                placeholder="请输入密码"
                :prefix-icon="Lock"
                :show-password="true"
                data-test="login-password"
                @keyup.enter="onSubmit"
              />
            </el-form-item>
            <el-button
              type="primary"
              class="login-submit"
              :loading="loading"
              data-test="login-submit"
              native-type="submit"
            >
              登 录
            </el-button>
          </el-form>

          <p class="login-footnote">
            没有账号？
            <el-link type="primary" :underline="false" data-test="login-to-register" @click="activeTab = 'register'">
              立即注册
            </el-link>
          </p>
        </el-tab-pane>

        <!-- 注册 Tab（开放注册：默认角色 dispatcher，注册即登录） -->
        <el-tab-pane label="注册" name="register">
          <!-- 错误提示条（409/422/429 明确文案；失败不清空已填用户名） -->
          <el-alert
            v-if="registerError"
            :title="registerError"
            type="error"
            show-icon
            :closable="false"
            class="login-error"
            data-test="register-error"
          />
          <!-- 技术详情（仅 dev）：status + url + method，辅助定位"打到哪个后端" -->
          <details
            v-if="isDev && registerErrorInfo"
            class="register-error-details"
            data-test="register-error-details"
          >
            <summary>技术详情（仅 dev）</summary>
            <pre class="register-error-meta">{{ registerErrorDetailText }}</pre>
          </details>

          <el-form label-position="top" class="login-form" @submit.prevent="onSubmitRegister">
            <el-form-item label="用户名">
              <el-input
                v-model="registerForm.username"
                placeholder="小写字母/数字/_ - .（1-64 位）"
                :prefix-icon="User"
                data-test="register-username"
                @keyup.enter="onSubmitRegister"
              />
            </el-form-item>
            <el-form-item label="密码">
              <el-input
                v-model="registerForm.password"
                type="password"
                placeholder="至少 8 位，含数字和字母"
                :prefix-icon="Lock"
                :show-password="true"
                data-test="register-password"
                @keyup.enter="onSubmitRegister"
              />
            </el-form-item>
            <el-form-item label="确认密码">
              <el-input
                v-model="registerForm.confirmPassword"
                type="password"
                placeholder="请再次输入密码"
                :prefix-icon="Lock"
                :show-password="true"
                data-test="register-confirm-password"
                @keyup.enter="onSubmitRegister"
              />
            </el-form-item>
            <el-form-item label="邮箱（可选）">
              <el-input
                v-model="registerForm.email"
                placeholder="user@example.com"
                :prefix-icon="Message"
                data-test="register-email"
                @keyup.enter="onSubmitRegister"
              />
            </el-form-item>
            <el-button
              type="primary"
              class="login-submit"
              :loading="registerLoading"
              data-test="register-submit"
              native-type="submit"
            >
              注 册
            </el-button>
          </el-form>

          <p class="login-footnote">注册即登录；默认角色为调度员；密码至少 8 位且包含数字和字母</p>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 首次登录强制改密弹层 -->
    <el-dialog
      v-model="mustChange"
      title="首次登录 · 请修改密码"
      width="420px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="false"
      append-to-body
      data-test="login-must-change-dialog"
    >
      <el-alert
        v-if="pwdError"
        :title="pwdError"
        type="error"
        show-icon
        :closable="false"
        class="login-error"
      />
      <el-form label-position="top" class="login-form" @submit.prevent="onSubmitPassword">
        <el-form-item label="新密码（至少 8 位，含数字和字母）">
          <el-input
            v-model="pwdForm.newPassword"
            type="password"
            :show-password="true"
            placeholder="请输入新密码"
            data-test="login-new-password"
            @keyup.enter="onSubmitPassword"
          />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input
            v-model="pwdForm.confirmPassword"
            type="password"
            :show-password="true"
            placeholder="请再次输入新密码"
            data-test="login-confirm-password"
            @keyup.enter="onSubmitPassword"
          />
        </el-form-item>
        <el-button
          type="primary"
          class="login-submit"
          :loading="pwdLoading"
          native-type="submit"
          data-test="login-change-submit"
        >
          确认修改
        </el-button>
      </el-form>
    </el-dialog>
  </div>
</template>

<style scoped lang="scss">
.login-view {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(1200px 600px at 50% -10%, var(--brand-primary-soft, rgba(97, 92, 237, 0.12)), transparent),
    var(--bg-base);
  padding: var(--space-6);
}

.login-card {
  width: 100%;
  max-width: 400px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg, 16px);
  box-shadow: var(--shadow-md, 0 4px 24px rgba(0, 0, 0, 0.12));
  padding: var(--space-8);
}

.login-brand {
  display: flex;
  justify-content: center;
  margin-bottom: var(--space-4);
}

.login-title {
  font-family: var(--font-cn);
  font-size: var(--fs-xl, 22px);
  font-weight: var(--fw-bold);
  color: var(--text-primary);
  text-align: center;
  margin: 0;
}

.login-subtitle {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  text-align: center;
  margin: var(--space-2) 0 var(--space-6);
}

.login-error {
  margin-bottom: var(--space-4);
}

.register-error-details {
  margin: calc(-1 * var(--space-2)) 0 var(--space-4);
  font-family: var(--font-mono, 'SFMono-Regular', Consolas, 'Courier New', monospace);
  font-size: var(--fs-xs, 12px);
  color: var(--text-tertiary, #999);

  summary {
    cursor: pointer;
    user-select: none;
  }
}

.register-error-meta {
  margin: var(--space-2) 0 0;
  padding: var(--space-2) var(--space-3);
  background: var(--bg-base);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-sm, 6px);
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.5;
}

.login-tabs {
  margin-bottom: var(--space-2);

  :deep(.el-tabs__nav-wrap::after) {
    height: 1px;
  }
}

.login-form {
  width: 100%;
}

.login-submit {
  width: 100%;
  margin-top: var(--space-4);
  letter-spacing: 0.2em;
  font-family: var(--font-cn);
  font-weight: var(--fw-semibold);
}

.login-footnote {
  font-family: var(--font-cn);
  font-size: var(--fs-xs, 12px);
  color: var(--text-tertiary, #999);
  text-align: center;
  margin: var(--space-6) 0 0;
}
</style>
