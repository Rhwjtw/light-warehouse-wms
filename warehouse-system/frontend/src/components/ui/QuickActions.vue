<script setup>
import { computed } from 'vue'
import { canAccess, canOperate } from '../../permissions'
import { auth } from '../../auth'

const props = defineProps({ actions: { type: Array, default: () => [] } })
// 同一路由权限函数用于菜单与首页入口；快捷操作只是导航，不绕过页面和 API 鉴权。
const visibleActions = computed(() => props.actions.filter(action => canAccess(auth.user?.role) && (!action.permission || canOperate(auth.user?.role, action.permission))))
const groups = computed(() => ['仓库业务', '生产业务', '车间用料'].map(label => ({
  label, actions: visibleActions.value.filter(action => action.group === label),
})).filter(group => group.actions.length))
</script>

<template>
  <div class="quick-actions" aria-label="常用快捷操作">
    <section v-for="group in groups" :key="group.label" class="quick-action-group" :aria-label="group.label">
      <h3>{{ group.label }}</h3>
      <div class="quick-action-buttons" :class="{ 'quick-action-buttons--workshop': group.label === '车间用料' }">
        <router-link v-for="action in group.actions" :key="action.label" :to="action.to" :class="['quick-action', { 'quick-action--primary': action.primary }]">
          <el-icon><component :is="action.icon" /></el-icon><span>{{ action.label }}</span>
        </router-link>
      </div>
    </section>
  </div>
</template>

<style scoped>
.quick-actions { display: grid; gap: 14px; }
.quick-action-group + .quick-action-group { padding-top: 10px; border-top: 1px solid var(--wms-border); }
.quick-action-group h3 { margin: 0 0 7px; color: var(--wms-text-secondary); font-size: 12px; font-weight: 600; }
.quick-action-buttons { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.quick-action-buttons--workshop { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.quick-action { display: flex; min-width: 0; min-height: 38px; align-items: center; justify-content: center; gap: 7px; padding: 8px; border: 1px solid var(--wms-border); border-radius: 7px; color: var(--wms-text); background: var(--wms-surface); font-size: 13px; text-decoration: none; transition: border-color 150ms ease, background 150ms ease; }
.quick-action .el-icon { color: var(--wms-primary); font-size: 16px; flex-shrink: 0; }
.quick-action:hover { border-color: var(--wms-primary); background: var(--wms-primary-soft); }
.quick-action--primary { grid-column: 1 / -1; color: #fff; background: var(--wms-primary); border-color: var(--wms-primary); }
.quick-action--primary .el-icon { color: inherit; }
.quick-action--primary:hover { color: #fff; background: var(--wms-primary-hover); }
.quick-action:focus-visible { outline: 2px solid var(--wms-primary); outline-offset: 3px; }
@media (max-width: 1180px) { .quick-actions { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; } .quick-action-group + .quick-action-group { padding-top: 0; border-top: 0; } }
</style>
