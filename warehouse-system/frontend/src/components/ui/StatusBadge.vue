<script setup>
defineProps({
  tone: { type: String, default: 'info' },
  label: { type: String, required: true },
})
</script>

<template><span :class="['status-badge', `status-badge--${tone}`]">{{ label }}</span></template>
