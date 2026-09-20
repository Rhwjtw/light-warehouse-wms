<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
const form = reactive({ urgent_expiry_days: null, warning_expiry_days: null })
const loading = ref(false), saving = ref(false), error = ref(''), ready = ref(false)
async function load() {
  loading.value = true; error.value = ''
  try { Object.assign(form, (await api.get('/settings/expiry')).data); ready.value = true }
  catch (e) { error.value = e.message } finally { loading.value = false }
}
async function save() {
  if (!(form.urgent_expiry_days > 0 && form.warning_expiry_days > form.urgent_expiry_days)) { error.value = '严重临期必须大于 0，一般临期必须大于严重临期'; return }
  saving.value = true; error.value = ''
  try { Object.assign(form, (await api.put('/settings/expiry', form)).data); ElMessage.success('设置已保存，预警和看板立即使用新阈值') }
  catch (e) { error.value = e.message } finally { saving.value = false }
}
onMounted(load)
</script>
<template>
  <div class="page-heading"><div><h1>基础设置</h1><p>调整有效期预警天数，所有预警页面与首页同步生效。</p></div><el-button @click="load" :loading="loading">刷新</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <el-card v-loading="loading" shadow="never"><el-form label-width="140px" :disabled="saving || !ready" @submit.prevent="save">
    <el-form-item label="严重临期天数"><el-input-number v-model="form.urgent_expiry_days" :min="1" :max="36500" :precision="0" /> 天</el-form-item>
    <el-form-item label="一般临期天数"><el-input-number v-model="form.warning_expiry_days" :min="1" :max="36500" :precision="0" /> 天</el-form-item>
    <el-form-item><el-button native-type="submit" type="primary" :loading="saving">保存设置</el-button></el-form-item>
  </el-form></el-card>
</template>
