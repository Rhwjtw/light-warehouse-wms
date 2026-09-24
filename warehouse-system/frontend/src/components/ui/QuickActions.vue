<script setup>
import { computed } from 'vue'
import { canAccess, canOperate } from '../../permissions'
import { auth } from '../../auth'

const props = defineProps({ actions: { type: Array, default: () => [] } })
// 同一路由权限函数用于菜单与首页入口；快捷操作只是导航，不绕过页面和 API 鉴权。
const visibleActions = computed(() => props.actions.filter(action => canAccess(auth.user?.role) && (!action.permission || canOperate(auth.user?.role, action.permission))))
</script>

<template>
  <div class="quick-actions" aria-label="常用快捷操作">
    <router-link v-for="action in visibleActions" :key="`${action.resource}-${action.label}`" :to="action.to" class="quick-action">
      <span class="quick-action-icon"><el-icon><component :is="action.icon" /></el-icon></span>
      <span class="quick-action-copy"><strong>{{ action.label }}</strong><small>{{ action.description }}</small></span>
      <span class="quick-action-arrow" aria-hidden="true">›</span>
    </router-link>
  </div>
</template>

<style scoped>
.quick-actions { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.quick-action { display: flex; min-width: 0; min-height: 62px; align-items: center; gap: 11px; padding: 10px 12px; border: 1px solid var(--wms-border); border-radius: 12px; color: var(--wms-text); background: #fff; text-decoration: none; transition: border-color 150ms ease, background 150ms ease, transform 150ms var(--wms-ease); }
.quick-action:hover { transform: translateY(-2px); border-color: rgba(22,119,232,.24); background: #f8fbff; }
.quick-action:active { transform: scale(.985); }
.quick-action-icon { display: grid; width: 34px; height: 34px; flex: 0 0 auto; place-items: center; border-radius: 10px; color: var(--wms-primary); background: var(--wms-primary-soft); font-size: 17px; }
.quick-action-copy { display: flex; min-width: 0; flex: 1; flex-direction: column; gap: 2px; }
.quick-action-copy strong { overflow: hidden; font-size: 13px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.quick-action-copy small { overflow: hidden; color: var(--wms-text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.quick-action-arrow { color: #a5afba; font-size: 19px; }
@media (max-width: 1450px) { .quick-actions { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 1100px) { .quick-actions { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
