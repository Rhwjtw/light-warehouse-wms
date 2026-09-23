<script setup>
import { computed } from 'vue'
import StatusBadge from './ui/StatusBadge.vue'

const props = defineProps({ order: { type: Object, required: true } })
const records = computed(() => props.order.material_records || [])
const count = kind => records.value.filter(row => row.kind === kind).length
// 流程卡只解释已保存的事实；补料和退料是可选业务，不把“未发生”误写为待完成。
const steps = computed(() => [
  { label: '生产单', value: `${props.order.planned_quantity} ${props.order.finished_product_unit}`, hint: '计划生产', tone: 'success' },
  { label: '领料', value: `${count('issue')} 笔记录`, hint: count('issue') ? '已登记' : '未发生', tone: count('issue') ? 'success' : 'info' },
  { label: '补料', value: `${count('supplement')} 笔记录`, hint: count('supplement') ? '已登记' : '未发生', tone: count('supplement') ? 'warning' : 'info' },
  { label: '退料', value: `${count('return')} 笔记录`, hint: count('return') ? '已登记' : '未发生', tone: count('return') ? 'primary' : 'info' },
  { label: '实际产量', value: props.order.actual_quantity == null ? '未登记' : `${props.order.actual_quantity} ${props.order.finished_product_unit}`, hint: props.order.actual_quantity == null ? '等待登记' : '已登记', tone: props.order.actual_quantity == null ? 'info' : 'success' },
  { label: '完工结算', value: ['completed', 'received'].includes(props.order.status) ? '已完成' : props.order.status === 'cancelled' ? '已取消' : '未完成', hint: ['completed', 'received'].includes(props.order.status) ? '成品已入库' : props.order.status === 'cancelled' ? '已取消' : '完成时重新核算', tone: ['completed', 'received'].includes(props.order.status) ? 'success' : 'info' },
])
</script>

<template>
  <div class="production-flow" aria-label="生产进度">
    <div v-for="(step, index) in steps" :key="step.label" class="flow-step">
      <span class="flow-index">{{ index + 1 }}</span>
      <div class="flow-copy"><strong>{{ step.label }}</strong><span class="flow-value">{{ step.value }}</span></div>
      <StatusBadge :tone="step.tone" :label="step.hint" class="flow-status" />
    </div>
  </div>
</template>

<style scoped>
.production-flow { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 0 0 17px; }
.flow-step { display: flex; min-width: 0; align-items: flex-start; gap: 10px; padding: 12px; border: 1px solid var(--wms-border); border-radius: 12px; background: #fff; }
.flow-index { display: grid; width: 24px; height: 24px; flex: 0 0 auto; place-items: center; border-radius: 8px; color: var(--wms-primary); background: var(--wms-primary-soft); font-size: 11px; font-weight: 700; }
.flow-copy { display: flex; min-width: 0; flex: 1; flex-direction: column; gap: 3px; }
.flow-copy strong { font-size: 12px; font-weight: 650; }
.flow-value { overflow: hidden; font-size: 14px; font-variant-numeric: tabular-nums; font-weight: 690; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 1100px) { .production-flow { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
