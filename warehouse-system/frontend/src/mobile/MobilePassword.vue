<script setup>
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { clearAuth } from '../auth'
import { auth } from '../auth'
import { clearMobileSession } from './session'
const router = useRouter(), saving = ref(false), error = ref('')
const form = reactive({ current_password: '', new_password: '', confirm: '' })
function reset() { Object.assign(form, { current_password: '', new_password: '', confirm: '' }) }
onBeforeUnmount(reset)
async function save() {
  if (saving.value) return
  error.value = ''
  if (!form.current_password || form.new_password.length < 10 || form.new_password.length > 128 || form.new_password !== form.confirm) {
    error.value = '请填写原密码，新密码需 10～128 位且两次输入一致'; return
  }
  saving.value = true
  try {
    await api.post('/auth/password', { current_password: form.current_password, new_password: form.new_password })
    reset(); clearMobileSession(auth.user?.id); clearAuth(); await router.replace({ path: '/m/login', query: { redirect: '/m/account', status: 'password-changed' } })
  } catch (e) { error.value = e.message } finally { saving.value = false }
}
</script>

<template>
  <section class="mobile-card"><p class="mobile-muted">修改后需重新登录，当前账户的所有旧会话都会失效。</p><form class="mobile-form" @submit.prevent="save"><label>原密码<input v-model="form.current_password" type="password" autocomplete="current-password" maxlength="128" :disabled="saving" /></label><label>新密码<input v-model="form.new_password" type="password" autocomplete="new-password" maxlength="128" :disabled="saving" /></label><label>确认新密码<input v-model="form.confirm" type="password" autocomplete="new-password" maxlength="128" :disabled="saving" /></label><p v-if="error" class="mobile-error" role="alert">{{ error }}</p><button type="submit" class="mobile-button primary" :disabled="saving">{{ saving ? '正在修改…' : '修改并重新登录' }}</button></form></section>
</template>
