<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login } from '../auth'
import { SYSTEM_INFO } from '../config/system'
import { mobileLoginTarget } from './routes'
import './mobile.css'
import './mobile-refresh.css'
const route = useRoute(), router = useRouter()
const form = reactive({ username: '', password: '' }), loading = ref(false), error = ref('')
async function submit() {
  if (loading.value) return
  error.value = ''
  if (!form.username.trim() || !form.password) { error.value = '请输入用户名和密码'; return }
  loading.value = true
  try {
    await login(form.username.trim(), form.password)
    form.password = ''
    await router.replace(mobileLoginTarget(route.query.redirect))
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
</script>

<template>
  <main class="mobile-root mobile-login">
    <section class="mobile-card" aria-labelledby="mobile-login-title">
      <div class="mobile-login-brand"><span class="mobile-brand" aria-hidden="true">仓</span><strong>{{ SYSTEM_INFO.shortName }}</strong><span class="mobile-pill">手机版</span></div>
      <h1 id="mobile-login-title">欢迎登录</h1><p class="mobile-muted">登录后开始今天的仓库作业。</p>
      <p v-if="route.query.status === 'password-changed'" class="mobile-success" role="status">密码已修改，请使用新密码重新登录。</p>
      <form class="mobile-form" @submit.prevent="submit">
        <label>用户名<input v-model="form.username" name="username" autocomplete="username" autocapitalize="none" :spellcheck="false" maxlength="64" :disabled="loading" placeholder="请输入用户名" /></label>
        <label>密码<input v-model="form.password" name="password" type="password" autocomplete="current-password" maxlength="128" :disabled="loading" placeholder="请输入密码" /></label>
        <p v-if="error" class="mobile-error" role="alert">{{ error }}</p>
        <button type="submit" class="mobile-button primary" :disabled="loading">{{ loading ? '正在登录…' : '登录' }}</button>
      </form>
      <p class="mobile-muted mobile-small">忘记密码请联系管理员。连续 5 次密码错误将暂时锁定 15 分钟。</p>
    </section>
    <router-link to="/login" class="mobile-text-link">进入电脑版登录</router-link>
    <footer class="mobile-muted mobile-small">{{ SYSTEM_INFO.nameEn }} · {{ SYSTEM_INFO.version }}<br />Developed by {{ SYSTEM_INFO.developer }}</footer>
  </main>
</template>
