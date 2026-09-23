<script setup>
defineProps({
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  kind: { type: String, default: 'default' },
  bodyClass: { type: String, default: '' },
})
</script>

<template>
  <el-card shadow="never" :class="['panel-card', `panel-card--${kind}`]" :body-class="bodyClass">
    <template v-if="title || description || $slots.actions" #header>
      <div class="panel-header">
        <div>
          <div v-if="title" class="panel-title">{{ title }}</div>
          <p v-if="description" class="panel-description">{{ description }}</p>
        </div>
        <div v-if="$slots.actions" class="panel-actions"><slot name="actions" /></div>
      </div>
    </template>
    <slot />
    <slot name="footer" />
  </el-card>
</template>
