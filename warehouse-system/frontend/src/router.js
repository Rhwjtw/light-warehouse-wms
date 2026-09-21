import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from './views/Dashboard.vue'
import Expiry from './views/Expiry.vue'
import Settings from './views/Settings.vue'
import OperationLogs from './views/OperationLogs.vue'
import { canAccess } from './permissions'
import Catalog from './views/Catalog.vue'
import Users from './views/Users.vue'
import Login from './views/Login.vue'
import MaterialInventory from './views/MaterialInventory.vue'
import FinishedInventory from './views/FinishedInventory.vue'
import Boms from './views/Boms.vue'
import ProductionOrders from './views/ProductionOrders.vue'
import Losses from './views/Losses.vue'
import { auth, restoreAuth } from './auth'

export const menuGroups = [
  { label: '原材料管理', items: [['materials', '原材料档案'], ['material-receipts', '原材料入库'], ['material-outbounds', '原材料出库'], ['material-stock', '原材料库存'], ['batches', '批次库存'], ['material-transactions', '库存流水']] },
  { label: '成品管理', items: [['products', '成品档案'], ['finished-receipts', '成品入库'], ['finished-outbounds', '成品出库'], ['finished-stock', '成品库存']] },
  { label: '生产管理', items: [['boms', 'BOM 配方'], ['orders', '生产单'], ['issues', '车间领料'], ['supplements', '车间补料'], ['returns', '车间退料'], ['losses', '损耗统计']] },
  { label: '预警中心', items: [['expiry', '有效期预警']] },
  { label: '系统管理', items: [['users', '用户管理'], ['suppliers', '供应商'], ['locations', '库位'], ['settings', '基础设置'], ['logs', '操作日志']] },
]

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: Login, meta: { title: '登录', public: true } },
    { path: '/', component: Dashboard, meta: { title: '首页' } },
    ...menuGroups.flatMap(group => group.items.map(([path, label]) => ({
      path: `/${path}`, component: path === 'expiry' ? Expiry : path === 'settings' ? Settings : path === 'logs' ? OperationLogs : path === 'losses' ? Losses : path === 'boms' ? Boms : ['orders','issues','supplements','returns'].includes(path) ? ProductionOrders : ['finished-receipts','finished-outbounds','finished-stock'].includes(path) ? FinishedInventory : ['material-receipts','material-outbounds','material-stock','batches','material-transactions'].includes(path) ? MaterialInventory : path === 'users' ? Users : ['materials', 'products', 'suppliers', 'locations'].includes(path) ? Catalog : Dashboard,
      meta: { title: label, group: group.label, resource: path, admin: ['users', 'settings', 'logs'].includes(path) },
    }))),
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
router.beforeEach(async to => {
  // 页面守卫先恢复服务端确认过的登录态，再按角色过滤路由；它不是后端鉴权的替代品。
  if (to.meta.public) return true
  try {
    if (!await restoreAuth()) return { path: '/login', query: { redirect: to.fullPath } }
    if (!canAccess(auth.user.role, to.meta.resource)) return '/'
  } catch { return { path: '/login', query: { redirect: to.fullPath } } }
})
window.addEventListener('wms-auth-expired', () => { if (router.currentRoute.value.path !== '/login') router.replace('/login') })
export default router
