<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { api } from '../api'
import DataPanel from '../components/ui/DataPanel.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import StatusBadge from '../components/ui/StatusBadge.vue'
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
  <PageHeader eyebrow="系统管理" title="基础设置" description="调整有效期预警区间，保存后首页与预警列表同步使用。">
    <template #actions><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button></template>
  </PageHeader>
  <div v-if="error" class="feedback-stack"><el-alert :title="error" type="error" :closable="false" show-icon /></div>
  <div class="settings-layout">
    <DataPanel title="有效期预警" description="按剩余天数分为严重临期与一般临期，到期当天仍视为有效。">
      <div class="settings-group-heading"><span>预警区间</span><StatusBadge tone="success" label="保存后立即生效" /></div>
      <el-form v-loading="loading" label-position="top" :disabled="saving || !ready" class="settings-form" @submit.prevent="save">
        <div class="settings-field">
          <el-form-item label="严重临期天数"><el-input-number v-model="form.urgent_expiry_days" :min="1" :max="36500" :precision="0" /> <span class="settings-unit">天</span></el-form-item>
          <p>剩余天数在此范围内的批次显示为严重临期。</p>
        </div>
        <div class="settings-field">
          <el-form-item label="一般临期天数"><el-input-number v-model="form.warning_expiry_days" :min="1" :max="36500" :precision="0" /> <span class="settings-unit">天</span></el-form-item>
          <p>超过严重临期、但仍在此范围内的批次显示为一般临期。</p>
        </div>
        <div class="settings-actions"><el-button native-type="submit" type="primary" :loading="saving" :disabled="!ready">{{ saving ? '正在保存' : '保存设置' }}</el-button><span>严重临期须大于 0，且小于一般临期天数。</span></div>
      </el-form>
    </DataPanel>
    <DataPanel title="影响范围" description="这些提示用于解释现有规则，不改变库存或历史记录。">
      <div class="settings-impact"><strong>有效期预警与首页</strong><p>保存后，预警列表和首页临期统计会按新天数重新查询。</p></div>
      <div class="settings-impact"><strong>最低库存提醒</strong><p>最低库存阈值仍在原材料档案中维护，此处不修改库存余额或该阈值。</p></div>
    </DataPanel>
  </div>
</template>

<style scoped>
.settings-layout { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(280px, 0.85fr); gap: 18px; align-items: start; }
.settings-layout :deep(.panel-card + .panel-card) { margin-top: 0; }
.settings-group-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 18px; font-size: 13px; font-weight: 650; }
.settings-form { display: grid; gap: 6px; }
.settings-field { padding: 16px 0; border-top: 1px solid var(--wms-border); }
.settings-field :deep(.el-form-item) { margin-bottom: 6px; }
.settings-field p, .settings-actions span, .settings-impact p { margin: 0; color: var(--wms-text-secondary); font-size: 12px; line-height: 1.6; }
.settings-unit { margin-left: 10px; color: var(--wms-text-secondary); }
.settings-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding-top: 15px; border-top: 1px solid var(--wms-border); }
.settings-impact + .settings-impact { margin-top: 18px; padding-top: 18px; border-top: 1px solid var(--wms-border); }
.settings-impact strong { display: block; margin-bottom: 6px; font-size: 13px; }
@media (max-width: 1180px) { .settings-layout { grid-template-columns: 1fr; } }
</style>
