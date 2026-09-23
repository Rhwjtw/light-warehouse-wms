<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '—' },
  detail: { type: String, default: '' },
  tone: { type: String, default: 'default' },
  to: { type: String, default: '' },
})
const tag = computed(() => props.to ? 'router-link' : 'div')
</script>

<template>
  <component :is="tag" :to="to || undefined" :class="['stat-card', `stat-card--${tone}`, { 'stat-card--link': to }]">
    <span class="stat-card-label">{{ label }}</span>
    <strong class="stat-card-value">{{ value }}</strong>
    <span v-if="detail" class="stat-card-detail">{{ detail }}</span>
  </component>
</template>

<style scoped>
.stat-card { display: flex; min-width: 0; min-height: 112px; flex-direction: column; justify-content: center; padding: 17px 19px; border: 1px solid var(--wms-border); border-radius: var(--wms-radius-card); background: var(--wms-surface); box-shadow: var(--wms-shadow-card); text-decoration: none; transition: transform 160ms var(--wms-ease), box-shadow 160ms var(--wms-ease); }
.stat-card--link:hover { transform: translateY(-2px); box-shadow: var(--wms-shadow-hover); }
.stat-card-label { color: var(--wms-text-secondary); font-size: 12px; }
.stat-card-value { overflow: hidden; margin-top: 7px; color: var(--wms-text); font-size: 27px; font-variant-numeric: tabular-nums; font-weight: 690; line-height: 1.2; text-overflow: ellipsis; white-space: nowrap; }
.stat-card-detail { margin-top: 6px; color: var(--wms-text-tertiary); font-size: 11px; line-height: 1.4; }
.stat-card--warning .stat-card-value { color: var(--wms-warning); }
.stat-card--danger .stat-card-value { color: var(--wms-danger); }
.stat-card--primary .stat-card-value { color: #075fc5; }
</style>
