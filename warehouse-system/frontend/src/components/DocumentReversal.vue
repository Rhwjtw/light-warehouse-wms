<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { auth } from '../auth'
import { canOperate } from '../permissions'

const props = defineProps({ modelValue: Boolean, documentId: Number })
const emit = defineEmits(['update:modelValue', 'changed'])
const open = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const detail = ref(null), preview = ref(null), reason = ref(''), error = ref(''), loading = ref(false), saving = ref(false)
const canReverse = computed(() => canOperate(auth.user?.role, 'stock.reversal.write'))
let generation = 0, requestId = '', requestReason = ''
const labels = { material_outbound: '原材料普通出库', finished_outbound: '成品普通出库', reversal: '冲销单' }
const stateLabel = computed(() => detail.value?.status === 'reversed' ? '已冲销' : detail.value?.status === 'reversal' ? '冲销单' : '正常')
function time(value) { return value ? new Date(value).toLocaleString('zh-CN', { timeZone: auth.user?.business_timezone || 'Asia/Shanghai', hour12: false }) : '—' }
async function load(id) {
  const ticket = ++generation
  loading.value = true; error.value = ''; detail.value = null; preview.value = null; reason.value = ''
  requestId = ''; requestReason = ''
  try {
    const result = await api.get(`/stock-documents/${id}`)
    if (ticket !== generation) return
    detail.value = result.data
    if (result.data.status === 'normal' && ['material_outbound', 'finished_outbound'].includes(result.data.kind)) {
      const next = await api.get(`/stock-documents/${id}/reversal-preview`)
      if (ticket === generation) preview.value = next.data
    }
  } catch (e) { if (ticket === generation) error.value = e.message }
  finally { if (ticket === generation) loading.value = false }
}
watch(() => [props.modelValue, props.documentId], ([visible, id]) => {
  if (visible && id) load(id)
  else generation++
})
async function execute() {
  if (!preview.value?.eligible || saving.value) return
  const clean = reason.value.trim()
  if (!clean) { error.value = '请填写冲销原因'; return }
  try {
    await ElMessageBox.confirm(`确认冲销单据 ${detail.value.document_no}？将产生上方显示的反向库存流水。`, '二次确认', {
      confirmButtonText: '确认执行冲销', cancelButtonText: '返回检查', type: 'warning', distinguishCancelAndClose: true,
    })
  } catch { return }
  if (!requestId || requestReason !== clean) { requestId = crypto.randomUUID(); requestReason = clean }
  saving.value = true; error.value = ''
  try {
    const result = await api.post(`/stock-documents/${detail.value.id}/reverse`, {
      request_id: requestId, reason: clean,
    })
    ElMessage.success(`冲销单 ${result.data.document_no} 已记账`)
    emit('changed'); await load(detail.value.id)
  } catch (e) {
    error.value = e.message + (e.status ? '' : '；网络中断时可用相同原因重试，请先核对冲销记录。')
  } finally { saving.value = false }
}
</script>

<template>
  <el-dialog v-model="open" title="库存单据详情" width="760px" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <div v-loading="loading">
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon style="margin-bottom: 16px" />
      <template v-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="单据号">{{ detail.document_no }}</el-descriptions-item>
          <el-descriptions-item label="状态"><el-tag :type="detail.status === 'reversed' ? 'danger' : 'info'">{{ stateLabel }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="业务类型">{{ labels[detail.kind] || detail.kind }}</el-descriptions-item>
          <el-descriptions-item label="操作人">{{ detail.operator_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="操作时间">{{ time(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="关联单号">{{ detail.reference_no || '—' }}</el-descriptions-item>
          <el-descriptions-item label="备注" :span="2">{{ detail.notes || '—' }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.reversal_reason" label="冲销原因" :span="2">{{ detail.reversal_reason }}</el-descriptions-item>
        </el-descriptions>
        <p v-if="detail.original">原单：<el-button link type="primary" @click="load(detail.original.id)">{{ detail.original.document_no }}</el-button></p>
        <p v-if="detail.reversal">冲销单：<el-button link type="primary" @click="load(detail.reversal.id)">{{ detail.reversal.document_no }}</el-button> · {{ detail.reversal.reason }} · {{ time(detail.reversal.created_at) }}</p>
        <h4>库存流水</h4>
        <el-table :data="detail.transactions" border size="small">
          <el-table-column prop="item_name" label="物料/成品" min-width="130" />
          <el-table-column prop="batch_no" label="原料批次" min-width="110" />
          <el-table-column prop="source_location_name" label="原库位" min-width="100" />
          <el-table-column prop="target_location_name" label="目标库位" min-width="100" />
          <el-table-column prop="before_quantity" label="变动前" min-width="95" />
          <el-table-column prop="change_quantity" label="变动量" min-width="95" />
          <el-table-column prop="after_quantity" label="变动后" min-width="95" />
        </el-table>
        <template v-if="preview">
          <h4>冲销预览</h4>
          <el-alert :title="preview.message" :type="preview.eligible ? 'info' : 'warning'" :closable="false" show-icon />
          <p v-for="row in preview.changes" :key="row.batch_id">{{ row.item_name }} {{ row.batch_no || '' }} {{ row.location_name || '' }}：{{ row.before_quantity }} + {{ row.change_quantity }} = {{ row.after_quantity }} {{ row.unit }}</p>
          <el-form v-if="preview.eligible && canReverse" label-position="top" @submit.prevent="execute">
            <el-form-item label="冲销原因（必填）" required><el-input v-model="reason" type="textarea" :rows="2" maxlength="2000" show-word-limit placeholder="说明原单错误及修正依据" /></el-form-item>
            <el-button type="danger" :loading="saving" native-type="submit">作废 / 冲销</el-button>
          </el-form>
        </template>
      </template>
    </div>
  </el-dialog>
</template>
