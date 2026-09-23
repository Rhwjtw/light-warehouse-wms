<script setup>
defineProps({ rows: { type: Number, default: 6 } })
</script>

<template>
  <div class="table-skeleton" aria-label="正在加载列表">
    <div v-for="row in rows" :key="row" class="table-skeleton-row">
      <el-skeleton-item v-for="column in 5" :key="column" variant="text" />
    </div>
  </div>
</template>
