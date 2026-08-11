<!--
  web/src/views/LoginView.vue · V1.8.0 真实登录页（T05）
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  架构 auth-architecture §1.4 + PRD §六 6.1 + US-1/US-2/US-8：

  - 居中卡片、Logo、用户名/密码输入（回车提交、密码显隐）、错误提示条、
    登录主按钮（loading 防重复提交）、无注册入口（仅管理员创建）；
  - 登录成功：must_change_password=1 → 同页改密面板（改密后清标记）；
    90 天过期（/auth/me password_expiring）→ 一次性 ElMessage 提醒；
  - redirect 回跳（守卫记录 / ?redirect= 参数），无 redirect 回首页。
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-->
<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Hide, Lock, User, View } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
import { changePassword } from '../api/auth'
import LogoHorizontal from '../components/brand/LogoHorizontal.vue'

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

const form = reactive({ username: '', password: '' })
const showPassword = ref(false)
const loading = ref(false)
const errorMsg = ref('')

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

      <p class="login-footnote">登录即代表同意安全策略；账号由管理员创建</p>
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
