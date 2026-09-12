<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { auth, clearAuth, logout } from '../auth'
import { api } from '../api'
const router = useRouter(), dialog = ref(false), saving = ref(false), error = ref('')
const form = reactive({ current_password: '', new_password: '', confirm: '' })
function reset() { Object.assign(form, { current_password: '', new_password: '', confirm: '' }); error.value = '' }
async function signOut() { try { await logout() } catch (e) { ElMessage.warning(e.message) } finally { router.replace('/login') } }
async function save() {
  if (saving.value) return
  if (!form.current_password || form.new_password.length < 10 || form.new_password.length > 128 || form.new_password !== form.confirm) { error.value = '请填写原密码，新密码需 10～128 位且两次输入一致'; return }
  saving.value = true; error.value = ''
  try {
    await api.post('/auth/password', { current_password: form.current_password, new_password: form.new_password })
    dialog.value = false; clearAuth(); ElMessage.success('密码已修改，请重新登录'); router.replace('/login')
  } catch (e) { error.value = e.message } finally { saving.value = false }
}
</script>
<template>
  <div class="account-bar"><el-tag type="info" effect="plain">{{ auth.user?.role_name }}</el-tag><span>{{ auth.user?.display_name }}</span><el-button link type="primary" @click="reset(); dialog = true">修改密码</el-button><el-button link @click="signOut">退出</el-button></div>
  <el-dialog v-model="dialog" title="修改我的密码" width="480px" :close-on-click-modal="false" :show-close="!saving" :close-on-press-escape="!saving" @closed="reset">
    <el-form label-width="95px" :disabled="saving" @submit.prevent="save"><el-form-item label="原密码"><el-input v-model="form.current_password" type="password" show-password autocomplete="current-password" maxlength="128" /></el-form-item><el-form-item label="新密码"><el-input v-model="form.new_password" type="password" show-password autocomplete="new-password" maxlength="128" /></el-form-item><el-form-item label="确认新密码"><el-input v-model="form.confirm" type="password" show-password autocomplete="new-password" maxlength="128" /></el-form-item></el-form><el-alert v-if="error" :title="error" type="error" :closable="false" /><template #footer><el-button :disabled="saving" @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">修改并重新登录</el-button></template>
  </el-dialog>
</template>
