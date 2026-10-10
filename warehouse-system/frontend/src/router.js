import { createRouter, createWebHistory } from 'vue-router'
import { nextTick } from 'vue'
import { canAccess } from './permissions'
import { auth, restoreAuth } from './auth'
import { mobileRoutes, loginLocation } from './mobile/routes'
import { mobileStore, touchMobile } from './mobile/session'
import { completedTaskLocation } from './mobile/navigation'
import { configureMobileHomeScreen } from './mobile/homeScreen'

const Dashboard = () => import('./views/Dashboard.vue')
const Expiry = () => import('./views/Expiry.vue')
const Settings = () => import('./views/Settings.vue')
const About = () => import('./views/About.vue')
const OperationLogs = () => import('./views/OperationLogs.vue')
const Catalog = () => import('./views/Catalog.vue')
const Users = () => import('./views/Users.vue')
const Login = () => import('./views/Login.vue')
const MaterialInventory = () => import('./views/MaterialInventory.vue')
const MaterialLocations = () => import('./views/MaterialLocations.vue')
const FinishedInventory = () => import('./views/FinishedInventory.vue')
const Boms = () => import('./views/Boms.vue')
const ProductionOrders = () => import('./views/ProductionOrders.vue')
const Losses = () => import('./views/Losses.vue')
const Reversals = () => import('./views/Reversals.vue')
const Traceability = () => import('./views/Traceability.vue')

export const menuGroups = [
  { label: '原材料管理', items: [['materials', '原材料档案'], ['material-receipts', '原材料入库'], ['material-putaways', '原材料上架'], ['material-outbounds', '原材料出库'], ['material-stock', '原材料库存'], ['material-location-stock', '库位库存'], ['batches', '批次库存'], ['material-transfers', '原材料移库'], ['material-counts', '库存盘点'], ['material-transactions', '库存流水']] },
  { label: '成品管理', items: [['products', '成品档案'], ['finished-receipts', '成品入库'], ['finished-outbounds', '成品出库'], ['finished-stock', '成品库存']] },
  { label: '生产管理', items: [['boms', 'BOM 配方'], ['orders', '生产单'], ['issues', '车间领料'], ['supplements', '车间补料'], ['returns', '车间退料'], ['losses', '损耗统计']] },
  { label: '预警中心', items: [['expiry', '有效期预警']] },
  { label: '批次追溯', items: [['traceability', '批次追溯']] },
  { label: '系统管理', items: [['users', '用户管理'], ['suppliers', '供应商'], ['locations', '库位'], ['settings', '基础设置'], ['about', '关于系统'], ['logs', '操作日志'], ['reversals', '冲销记录']] },
]

const router = createRouter({
  history: createWebHistory(),
  async scrollBehavior(to, from, saved) {
    if (!to.meta.mobile) return saved || false
    // S3远程数据由列表组件在渲染完成后恢复；此处不能对空DOM提前滚动。
    if (to.meta.mobileAsyncList || ((to.meta.mobileReceiptTask || to.meta.mobilePutawayTask || to.meta.mobileStockTask || to.meta.mobileLocationTask) && to.params.step === 'choose')) return false
    await nextTick()
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    if (to.meta.mobileList) {
      const position = mobileStore()?.list(to.fullPath)
      const anchor = position?.anchor && document.getElementById(position.anchor)
      if (anchor) return { el: anchor, top: 80 }
      if (position?.top) return { top: position.top }
    }
    return saved || { top: 0 }
  },
  routes: [
    ...mobileRoutes,
    { path: '/login', component: Login, meta: { title: '登录', public: true } },
    { path: '/', component: Dashboard, meta: { title: '首页' } },
    ...menuGroups.flatMap(group => group.items.map(([path, label]) => ({
      path: `/${path}`, component: path === 'about' ? About : path === 'traceability' ? Traceability : path === 'expiry' ? Expiry : path === 'settings' ? Settings : path === 'logs' ? OperationLogs : path === 'reversals' ? Reversals : path === 'losses' ? Losses : path === 'boms' ? Boms : ['orders','issues','supplements','returns'].includes(path) ? ProductionOrders : ['finished-receipts','finished-outbounds','finished-stock'].includes(path) ? FinishedInventory : ['material-putaways','material-location-stock','material-transfers','material-counts'].includes(path) ? MaterialLocations : ['material-receipts','material-outbounds','material-stock','batches','material-transactions'].includes(path) ? MaterialInventory : path === 'users' ? Users : ['materials', 'products', 'suppliers', 'locations'].includes(path) ? Catalog : Dashboard,
      meta: { title: label, group: group.label, resource: path },
    }))),
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
router.beforeEach(async (to, from) => {
  if (from.meta.mobile && from.fullPath !== to.fullPath) window.dispatchEvent(new Event('wms-mobile-stop-resources'))
  if ((from.meta.mobileList || ((from.meta.mobileReceiptTask || from.meta.mobilePutawayTask || from.meta.mobileStockTask || from.meta.mobileLocationTask) && from.params.step === 'choose')) && (!from.meta.mobileAsyncList && !from.meta.mobileReceiptTask && !from.meta.mobilePutawayTask && !from.meta.mobileStockTask && !from.meta.mobileLocationTask || document.querySelector('[data-mobile-list-ready]'))) {
    const anchor = [...document.querySelectorAll('[data-mobile-anchor]')].find(element => element.getBoundingClientRect().bottom > 80)
    mobileStore()?.saveList(from.fullPath, { top: window.scrollY, anchor: anchor?.id })
  }
  if (from.fullPath !== to.fullPath && (from.meta.mobileLabTask || from.meta.mobileReceiptTask || from.meta.mobilePutawayTask || from.meta.mobileStockTask || from.meta.mobileLocationTask)) { mobileStore()?.suspend(); touchMobile() }
  // 页面守卫确认登录态；写入权限由页面按钮和服务端接口分别检查。
  if (to.meta.public) return true
  try {
    if (!await restoreAuth()) return loginLocation(to)
    if (!canAccess(auth.user.role, to.meta.resource)) return '/'
    if (to.meta.mobileLabTask || to.meta.mobileReceiptTask || to.meta.mobilePutawayTask || to.meta.mobileStockTask || to.meta.mobileLocationTask) {
      const task = mobileStore()?.get(to.params.jobId)
      const completed = completedTaskLocation(to, task)
      if (completed) return completed
    }
  } catch { return loginLocation(to) }
})
router.afterEach((to, from, failure) => {
  if (!failure) configureMobileHomeScreen(document, Boolean(to.meta.mobile))
})
window.addEventListener('wms-auth-expired', () => {
  const current = router.currentRoute.value
  if (!current.meta.public) router.replace(loginLocation(current))
})
export default router
