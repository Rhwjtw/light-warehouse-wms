<script setup>
defineProps({ rows: { type: Array, default: () => [] } })
const rate = value => value == null ? '—' : `${Number(value).toFixed(2)}%`
</script>

<template>
  <el-table :data="rows" border stripe>
    <el-table-column prop="material_name" label="原材料" min-width="140" />
    <el-table-column v-for="[key,label] in [['planned_required_quantity','计划理论'],['issued_quantity','正常领料'],['supplemented_quantity','补料'],['returned_quantity','退料'],['final_theoretical_quantity','最终理论'],['actual_used_quantity','实际用量']]" :key="key" :label="label" min-width="125">
      <template #default="{row}">{{ row[key] ?? '—' }} {{ row.base_unit }}</template>
    </el-table-column>
    <el-table-column label="损耗" min-width="170"><template #default="{row}">{{ row.loss_quantity }} {{ row.base_unit }} <el-tag v-if="Number(row.loss_quantity) < 0" type="warning" size="small">节余 / 负损耗</el-tag></template></el-table-column>
    <el-table-column label="损耗率" min-width="110"><template #default="{row}">{{ rate(row.loss_rate) }}</template></el-table-column>
  </el-table>
</template>
