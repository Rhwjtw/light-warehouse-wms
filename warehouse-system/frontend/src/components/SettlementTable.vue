<script setup>
import { formatQuantity } from '../quantity'
import EmptyState from './ui/EmptyState.vue'
import StatusBadge from './ui/StatusBadge.vue'
defineProps({ rows: { type: Array, default: () => [] } })
// 百分比只在界面保留两位；结算和持久化的 Decimal 精度由现有后端负责。
const rate = value => value == null ? '—' : `${Number(value).toFixed(2)}%`
</script>

<template>
  <el-table :data="rows" border stripe>
    <el-table-column prop="material_name" label="原材料" min-width="140" />
    <el-table-column v-for="[key,label] in [['planned_required_quantity','计划理论'],['issued_quantity','正常领料'],['supplemented_quantity','补料'],['returned_quantity','退料'],['final_theoretical_quantity','最终理论'],['actual_used_quantity','实际用量']]" :key="key" :label="label" min-width="125">
      <template #default="{row}"><span class="data-value">{{ formatQuantity(row[key]) }} {{ row.base_unit }}</span></template>
    </el-table-column>
    <el-table-column label="差异 / 损耗数量" min-width="185"><template #default="{row}"><span class="data-value">{{ formatQuantity(row.loss_quantity) }} {{ row.base_unit }}</span> <StatusBadge v-if="Number(row.loss_quantity) < 0" tone="warning" label="节余 / 负损耗" /></template></el-table-column>
    <el-table-column label="损耗率" min-width="110"><template #default="{row}"><span class="data-value">{{ rate(row.loss_rate) }}</span></template></el-table-column>
    <template #empty><EmptyState title="暂无结算明细" description="完成结算预览后将在这里显示理论与实际用量。" /></template>
  </el-table>
</template>
