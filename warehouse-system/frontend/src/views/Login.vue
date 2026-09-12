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
  <div class="login-page">
    <div class="login-intro"><span class="brand-icon">仓</span><h1>轻仓 WMS</h1><p>仓库与车间，共用一份清楚的账。</p><div class="login-points">原材料与成品 · 批次与有效期 · 生产领料</div><small>第二阶段：账户与基础资料已开放</small></div>
    <el-card shadow="never" class="login-card"><h2>登录工作台</h2><p>请使用管理员为你创建的账户</p><el-form label-position="top" @submit.prevent="submit"><el-form-item label="用户名"><el-input v-model="form.username" autocomplete="username" maxlength="64" placeholder="请输入用户名" /></el-form-item><el-form-item label="密码"><el-input v-model="form.password" type="password" show-password autocomplete="current-password" maxlength="128" placeholder="请输入密码" /></el-form-item><el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="error-alert" /><el-button type="primary" native-type="submit" :loading="loading" class="login-submit">登录</el-button></el-form><p class="login-help">首次账户见项目 .runtime/phase-2-accounts.txt。<br>连续 5 次密码错误将暂时锁定 15 分钟。</p></el-card>
  </div>
</template>
