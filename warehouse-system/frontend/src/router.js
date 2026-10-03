import { createRouter, createWebHistory } from 'vue-router'
import { canAccess } from './permissions'
import { auth, restoreAuth } from './auth'

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
  routes: [
    { path: '/login', component: Login, meta: { title: '登录', public: true } },
    { path: '/', component: Dashboard, meta: { title: '首页' } },
    ...menuGroups.flatMap(group => group.items.map(([path, label]) => ({
      path: `/${path}`, component: path === 'about' ? About : path === 'traceability' ? Traceability : path === 'expiry' ? Expiry : path === 'settings' ? Settings : path === 'logs' ? OperationLogs : path === 'reversals' ? Reversals : path === 'losses' ? Losses : path === 'boms' ? Boms : ['orders','issues','supplements','returns'].includes(path) ? ProductionOrders : ['finished-receipts','finished-outbounds','finished-stock'].includes(path) ? FinishedInventory : ['material-putaways','material-location-stock','material-transfers','material-counts'].includes(path) ? MaterialLocations : ['material-receipts','material-outbounds','material-stock','batches','material-transactions'].includes(path) ? MaterialInventory : path === 'users' ? Users : ['materials', 'products', 'suppliers', 'locations'].includes(path) ? Catalog : Dashboard,
      meta: { title: label, group: group.label, resource: path },
    }))),
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
router.beforeEach(async to => {
  // 页面守卫确认登录态；写入权限由页面按钮和服务端接口分别检查。
  if (to.meta.public) return true
  try {
    if (!await restoreAuth()) return { path: '/login', query: { redirect: to.fullPath } }
    if (!canAccess(auth.user.role, to.meta.resource)) return '/'
  } catch { return { path: '/login', query: { redirect: to.fullPath } } }
})
window.addEventListener('wms-auth-expired', () => { if (router.currentRoute.value.path !== '/login') router.replace('/login') })
export default router
