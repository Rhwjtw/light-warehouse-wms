<script setup>
import { Box } from '@element-plus/icons-vue'

defineProps({
  title: { type: String, default: '暂无数据' },
  description: { type: String, default: '当前条件下没有可显示的记录。' },
})
</script>

<template>
  <div class="empty-state">
    <div>
      <span class="empty-state-icon"><el-icon><Box /></el-icon></span>
      <strong>{{ title }}</strong>
      <p>{{ description }}</p>
      <div v-if="$slots.default" class="empty-state-actions"><slot /></div>
    </div>
  </div>
</template>
