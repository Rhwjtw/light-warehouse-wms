<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Box, Document, House, SetUp, User } from '@element-plus/icons-vue'
import { mobileNavigation } from './routes'
import { api } from '../api'
import { auth } from '../auth'
import { mobileParentLocation, returnToParent } from './navigation'
import { mobileRevision, mobileStore, pendingMobileTasks, touchMobile } from './session'
import { attachMobileLifecycle } from './lifecycle'
import { receiptTaskPath } from './receipt'
import { putawayTaskPath } from './putaway'
import './mobile.css'
const route = useRoute(), router = useRouter(), viewport = ref({ inset: 0, keyboard: false }), recovery = ref('')
const parent = computed(() => {
  mobileRevision.value
  if (route.meta.mobileList && route.query.panel === 'filter') {
    const query = { ...route.query }; delete query.panel
    return router.resolve({ path: route.path, query }).fullPath
  }
  return mobileParentLocation(route, mobileStore()?.get(route.params.jobId))
})
const pending = computed(() => { mobileRevision.value; return pendingMobileTasks() })
const icons = { Box, Document, House, SetUp, User }
let cleanup, resuming = false
async function resume() {
  if (resuming) return
  resuming = true; recovery.value = '正在恢复登录与页面状态…'
  try {
    const user = (await api.get('/auth/me')).data
    if (user.id !== auth.user?.id) throw new Error('账号已变化，请重新登录')
    auth.user = user; touchMobile()
    recovery.value = '已恢复登录；正在重新读取，作业状态保留，不会自动提交。'
    window.dispatchEvent(new Event('wms-mobile-resume'))
  } catch (e) { recovery.value = `恢复核实失败：${e.message}。不会自动提交。` }
  finally { resuming = false }
}
onMounted(() => {
  mobileStore()
  cleanup = attachMobileLifecycle({ win: window, doc: document, suspend: () => { mobileStore()?.suspend(); touchMobile() }, resume, stopResources: () => window.dispatchEvent(new Event('wms-mobile-stop-resources')), viewportChanged: value => { viewport.value = value } })
})
onBeforeUnmount(() => cleanup?.())
</script>

<template>
  <div :class="['mobile-root', { 'mobile-task': route.meta.mobileTask, 'mobile-keyboard': viewport.keyboard }]" :style="{ '--mobile-keyboard-inset': `${viewport.inset}px` }">
    <header class="mobile-header">
      <button v-if="route.meta.mobileParent" class="mobile-back" aria-label="返回" @click="returnToParent(router, parent)"><ArrowLeft /><span>返回</span></button>
      <span v-else class="mobile-brand" aria-hidden="true">仓</span>
      <h1>{{ route.meta.title }}</h1><span class="mobile-header-tag">轻仓 WMS</span>
    </header>
    <main id="mobile-content" class="mobile-content"><p v-if="recovery" class="mobile-warning" role="status">{{ recovery }}</p><router-link v-for="item in pending" :key="item.id" :to="item.kind === 'material-receipt' ? receiptTaskPath(item, 'result') : item.kind === 'material-putaway' ? putawayTaskPath(item, 'result') : '/m/state-lab'" class="mobile-warning mobile-text-link">{{ item.kind === 'material-receipt' ? '原料入库' : item.kind === 'material-putaway' ? '原料上架' : 'S2演练' }}处理中/待核实：先核对，不能重交</router-link><router-view /></main>
    <nav v-if="!route.meta.mobileTask" class="mobile-nav" aria-label="手机主导航">
      <router-link v-for="item in mobileNavigation" :key="item.path" :to="item.path" :class="{ active: route.meta.mobileTab === item.path }" :aria-current="route.meta.mobileTab === item.path ? 'page' : undefined">
        <component :is="icons[item.icon]" /><span>{{ item.label }}</span>
      </router-link>
    </nav>
  </div>
</template>
