<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login } from '../auth'
const route = useRoute(), router = useRouter()
const form = reactive({ username: '', password: '' })
const loading = ref(false), error = ref('')
async function submit() {
  if (loading.value) return
  error.value = ''
  if (!form.username.trim() || !form.password) { error.value = '请输入用户名和密码'; return }
  loading.value = true
  try {
    await login(form.username.trim(), form.password)
    const next = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/') && !route.query.redirect.startsWith('//') ? route.query.redirect : '/'
    await router.replace(next)
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
</script>
<template>
  <main class="login-page">
    <section class="login-shell" aria-labelledby="login-title">
      <div class="login-brand"><span class="brand-icon" aria-hidden="true">仓</span><span>轻仓 WMS</span></div>
      <div class="login-heading"><p class="login-eyebrow">仓库与生产工作台</p><h1 id="login-title">欢迎登录</h1><p>使用管理员创建的账户登录工作台。</p></div>
      <el-form label-position="top" class="login-form" @submit.prevent="submit">
        <el-form-item label="用户名"><el-input v-model="form.username" autocomplete="username" maxlength="64" placeholder="请输入用户名" :disabled="loading" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" type="password" show-password autocomplete="current-password" maxlength="128" placeholder="请输入密码" :disabled="loading" /></el-form-item>
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="login-error" />
        <el-button type="primary" native-type="submit" :loading="loading" class="login-submit">{{ loading ? '正在登录' : '登录' }}</el-button>
      </el-form>
      <p class="login-help">忘记密码请联系管理员。连续 5 次密码错误将暂时锁定 15 分钟。</p>
    </section>
    <p class="login-footer">轻仓 WMS · 仓库与生产领料系统</p>
  </main>
</template>

<style scoped>
.login-page { min-height: 100vh; display: grid; place-items: center; padding: 42px 24px 68px; background: var(--wms-bg); position: relative; }
.login-shell { width: min(100%, 430px); padding: 36px 38px 30px; border: 1px solid var(--wms-border); border-radius: var(--wms-radius-dialog); background: var(--wms-surface); box-shadow: 0 18px 55px rgba(15, 23, 42, 0.07); }
.login-brand { display: flex; align-items: center; gap: 12px; font-size: 15px; font-weight: 700; letter-spacing: -0.01em; }
.login-brand .brand-icon { flex: 0 0 auto; width: 42px; height: 42px; font-size: 20px; }
.login-heading { margin: 38px 0 27px; }
.login-eyebrow { margin: 0 0 8px; color: var(--wms-primary); font-size: 12px; font-weight: 650; }
.login-heading h1 { margin: 0 0 9px; font-size: 28px; font-weight: 700; letter-spacing: -0.025em; }
.login-heading p:last-child { margin: 0; color: var(--wms-text-secondary); font-size: 13px; line-height: 1.65; }
.login-form :deep(.el-form-item) { margin-bottom: 19px; }
.login-form :deep(.el-form-item__label) { padding-bottom: 7px; }
.login-form :deep(.el-input__wrapper) { min-height: 42px; }
.login-error { margin: 0 0 18px; }
.login-submit { width: 100%; min-height: 43px; margin-top: 3px; }
.login-help { margin: 22px 0 0; color: var(--wms-text-tertiary); font-size: 11px; line-height: 1.65; }
.login-footer { position: absolute; bottom: 22px; left: 24px; right: 24px; margin: 0; color: var(--wms-text-tertiary); font-size: 11px; text-align: center; }
@media (max-width: 520px) { .login-shell { padding: 30px 24px 26px; } }
</style>
