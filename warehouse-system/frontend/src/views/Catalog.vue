<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { auth } from '../auth'

const route = useRoute()
const resource = computed(() => route.meta.resource)
const admin = computed(() => auth.user?.role === 'admin')
const isUser = computed(() => resource.value === 'users')
const code = { key: 'code', label: '编码', required: true, max: 40, pattern: /^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$/, hint: '字母、数字、点、横线或下划线' }
const name = { key: 'name', label: '名称', required: true, max: 120 }
const unit = options => ({ key: 'unit', label: '单位', required: true, options: options.map(value => ({ label: value, value })), initial: options[0] })
const quantity = { key: 'min_stock', label: '最低库存预警值', initial: '0', required: true, pattern: /^\d{1,14}(\.\d{1,6})?$/, hint: '非负数，最多六位小数；这是预警阈值，不是当前库存' }
const configs = {
  materials: [code, name, unit(['kg','g','L','mL','个']), { key: 'specification', label: '规格', max: 200 }, quantity],
  products: [code, name, unit(['盒','瓶','支','套','个']), { key: 'specification', label: '规格', max: 200 }, quantity],
  suppliers: [code, name, { key: 'contact', label: '联系人', max: 80 }, { key: 'phone', label: '联系电话', max: 40 }],
  locations: [code, name],
}
const roles = ref([])
const fields = computed(() => isUser.value ? [
  { key: 'username', label: '用户名', required: true, max: 64, pattern: /^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$/, hint: '3～64 位字母、数字、点、横线或下划线' },
  { key: 'display_name', label: '姓名', required: true, max: 80 },
  { key: 'role_id', label: '角色', required: true, options: roles.value.map(r => ({ label: r.name, value: r.id })), initial: roles.value.find(r => r.code === 'warehouse')?.id },
] : configs[resource.value] || [])
const form = reactive({}), formRef = ref(), dialog = ref(false), mode = ref('create'), saving = ref(false), formError = ref('')
const rows = ref([]), total = ref(0), page = ref(1), pageSize = ref(20), query = ref(''), status = ref('all'), loading = ref(false), loadError = ref('')
const rules = computed(() => Object.fromEntries([...fields.value.map(f => [f.key, [{ required: !!f.required, message: `请填写${f.label}`, trigger: 'blur' }, ...(f.pattern ? [{ pattern: f.pattern, message: f.hint, trigger: 'blur' }] : [])]]), ['password', [{ required: true, min: 10, max: 128, message: '密码长度为 10～128 位', trigger: 'blur' }]]]))
let generation = 0
async function load() {
  const ticket = ++generation
  loading.value = true; loadError.value = ''
  try {
    const response = await api.get(`/${resource.value}`, { params: { q: query.value, page: page.value, page_size: pageSize.value, ...(status.value !== 'all' ? { is_active: status.value === 'active' } : {}) } })
    if (ticket !== generation) return
    rows.value = response.data.items; total.value = response.data.total
    if (!rows.value.length && page.value > 1) { page.value = Math.max(1, Math.ceil(total.value / pageSize.value)); await load() }
  } catch (e) { if (ticket === generation) { rows.value = []; total.value = 0; loadError.value = e.message } }
  finally { if (ticket === generation) loading.value = false }
}
function search() { page.value = 1; load() }
async function open(nextMode, row) {
  formError.value = ''
  const current = resource.value
  try {
    const data = row ? (await api.get(`/${current}/${row.id}`)).data : {}
    if (current !== resource.value) return
    Object.keys(form).forEach(key => delete form[key])
    Object.assign(form, Object.fromEntries(fields.value.map(f => [f.key, f.initial ?? ''])), { is_active: true, notes: '', ...data, password: '' })
    mode.value = nextMode; dialog.value = true
  } catch (e) { ElMessage.error(e.message) }
}
async function save() {
  if (saving.value || !await formRef.value.validate().catch(() => false)) return
  saving.value = true; formError.value = ''
  try {
    if (mode.value === 'password') await api.post(`/users/${form.id}/password`, { new_password: form.password })
    else {
      const payload = Object.fromEntries(fields.value.filter(f => !(isUser.value && form.id && f.key === 'username')).map(f => [f.key, typeof form[f.key] === 'string' ? form[f.key].trim() : form[f.key]]))
      payload.is_active = form.is_active
      if (!isUser.value) payload.notes = form.notes || null
      else if (!form.id) payload.password = form.password
      if (form.id) await api.put(`/${resource.value}/${form.id}`, payload)
      else await api.post(`/${resource.value}`, payload)
    }
    if (isUser.value && form.id === auth.user.id) auth.user = (await api.get('/auth/me')).data
    dialog.value = false; form.password = ''; ElMessage.success('保存成功'); await load()
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function remove(row) {
  try {
    await ElMessageBox.confirm(`确认软删除“${row.name || row.username}”？编码/用户名会保留，已有业务引用时请使用停用。`, '删除确认', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' })
    await api.delete(`/${resource.value}/${row.id}`); ElMessage.success('已软删除'); await load()
  } catch (e) { if (e instanceof Error) ElMessage.error(e.message) }
}
watch(resource, async () => {
  generation++; dialog.value = false; query.value = ''; status.value = 'all'; page.value = 1; rows.value = []
  if (isUser.value) { try { roles.value = (await api.get('/roles')).data } catch (e) { ElMessage.error(e.message) } }
  await load()
}, { immediate: true })
</script>

<template>
  <div class="page-heading"><div><h1>{{ route.meta.title }}</h1><p>{{ admin ? '维护基础信息，已有业务引用的档案请使用停用。' : '当前角色可查看资料，修改请联系管理员。' }}</p></div><el-button v-if="admin" type="primary" @click="open('create')">新增{{ isUser ? '用户' : route.meta.title.replace('档案', '') }}</el-button></div>
  <el-card shadow="never">
    <el-form inline class="search-bar" @submit.prevent="search"><el-form-item label="搜索"><el-input v-model="query" :placeholder="isUser ? '用户名 / 姓名' : '编码 / 名称，支持模糊搜索'" clearable maxlength="120" @clear="search" /></el-form-item><el-form-item label="状态"><el-select v-model="status" style="width: 120px" @change="search"><el-option label="全部" value="all" /><el-option label="启用" value="active" /><el-option label="停用" value="inactive" /></el-select></el-form-item><el-form-item><el-button type="primary" native-type="submit">查询</el-button><el-button @click="query = ''; status = 'all'; search()">重置</el-button></el-form-item></el-form>
    <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon />
    <el-table v-loading="loading" :data="rows" stripe row-key="id" empty-text="暂无资料，管理员可点击右上角新增">
      <el-table-column v-for="field in fields" :key="field.key" :prop="field.key === 'role_id' ? 'role_name' : field.key" :label="field.label" :min-width="field.key === 'unit' ? 75 : 145" show-overflow-tooltip />
      <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '停用' }}</el-tag></template></el-table-column>
      <el-table-column label="操作" :width="admin ? isUser ? 280 : 190 : 85" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="open('view', row)">查看</el-button><el-button v-if="admin" link type="primary" @click="open('edit', row)">编辑</el-button><el-button v-if="admin && isUser && row.id !== auth.user.id" link type="warning" @click="open('password', row)">重置密码</el-button><el-button v-if="admin && (!isUser || row.id !== auth.user.id)" link type="danger" @click="remove(row)">删除</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[10,20,50,100]" layout="total, sizes, prev, pager, next" class="pagination" @current-change="load" @size-change="search" />
  </el-card>
  <el-dialog v-model="dialog" :title="mode === 'password' ? `重置 ${form.username} 的密码` : `${{ create: '新增', edit: '编辑', view: '查看' }[mode]}${route.meta.title}`" width="600px" destroy-on-close :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @closed="form.password = ''">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="125px" :disabled="mode === 'view' || saving" @submit.prevent="save">
      <template v-if="mode !== 'password'">
        <el-form-item v-for="field in fields" :key="field.key" :label="field.label" :prop="field.key">
          <el-select v-if="field.options" v-model="form[field.key]" :disabled="isUser && field.key === 'role_id' && form.id === auth.user.id"><el-option v-for="option in field.options" :key="option.value" :label="option.label" :value="option.value" /></el-select>
          <el-input v-else v-model="form[field.key]" :maxlength="field.max" :disabled="field.key === 'username' && !!form.id" />
          <div v-if="field.hint" class="field-help">{{ field.hint }}</div>
        </el-form-item>
        <el-form-item label="状态"><el-switch v-model="form.is_active" :disabled="isUser && form.id === auth.user.id" active-text="启用" inactive-text="停用" /></el-form-item>
        <el-form-item v-if="!isUser" label="备注"><el-input v-model="form.notes" type="textarea" :rows="3" maxlength="2000" show-word-limit /></el-form-item>
      </template>
      <el-form-item v-if="isUser && (mode === 'create' || mode === 'password')" label="新密码" prop="password"><el-input v-model="form.password" type="password" show-password autocomplete="new-password" maxlength="128" placeholder="10～128 位" /></el-form-item>
    </el-form>
    <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon />
    <template #footer><el-button :disabled="saving" @click="dialog = false">{{ mode === 'view' ? '关闭' : '取消' }}</el-button><el-button v-if="mode !== 'view'" type="primary" :loading="saving" @click="save">保存</el-button></template>
  </el-dialog>
</template>
