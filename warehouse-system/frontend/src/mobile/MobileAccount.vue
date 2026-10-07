<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { auth, logout } from '../auth'
import { SYSTEM_INFO } from '../config/system'
import { clearMobileSession, pendingMobileTasks } from './session'
const router = useRouter(), signingOut = ref(false), error = ref(''), confirming = ref(false)
async function signOut() {
  if (signingOut.value) return
  signingOut.value = true; error.value = ''
  clearMobileSession(auth.user?.id)
  try { await logout(); await router.replace('/m/login') }
  catch (e) {
    // 既有logout始终清本地认证；网络失败不能声称服务器已撤销所有旧会话。
    confirming.value = false
    error.value = `本机登录已清除，服务器退出结果未确认：${e.message}。请重新登录后再次退出以撤销旧会话。`
  } finally { signingOut.value = false }
}
</script>

<template>
  <section class="mobile-card"><h2>当前账号</h2><dl class="mobile-details"><div><dt>姓名</dt><dd>{{ auth.user?.display_name || '已退出' }}</dd></div><div><dt>用户名</dt><dd>{{ auth.user?.username || '—' }}</dd></div><div><dt>角色</dt><dd>{{ auth.user?.role_name || '—' }}</dd></div></dl><router-link v-if="auth.user" to="/m/account/password" class="mobile-button">修改密码</router-link></section>
  <section class="mobile-card"><h2>关于系统</h2><p>{{ SYSTEM_INFO.name }} · {{ SYSTEM_INFO.version }}</p><p class="mobile-muted mobile-small">{{ SYSTEM_INFO.nameEn }}<br />Developed by {{ SYSTEM_INFO.developer }}</p><router-link to="/" class="mobile-button">进入电脑版</router-link></section>
  <p v-if="error" class="mobile-error" role="alert">{{ error }}</p>
  <router-link v-if="!auth.user" to="/m/login" class="mobile-button primary">重新登录</router-link>
  <section v-else class="mobile-card"><p class="mobile-muted mobile-small">退出将撤销当前账户在所有设备上的旧会话，并清理本账号手机草稿。</p><div v-if="confirming"><p v-if="pendingMobileTasks().length" class="mobile-warning">存在处理中/待核实的演练任务。建议先核对；退出会清理本机上下文，不能据此认为服务端操作已取消。</p><router-link v-if="pendingMobileTasks().length" to="/m/state-lab" class="mobile-button">先去核对</router-link><p>确认退出当前账户？</p><button class="mobile-button danger" :disabled="signingOut" @click="signOut">{{ signingOut ? '正在退出…' : '确认退出' }}</button><button class="mobile-button" :disabled="signingOut" @click="confirming = false">取消</button></div><button v-else class="mobile-button danger" @click="confirming = true">退出登录</button></section>
</template>
