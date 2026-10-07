export const mobileNavigation = [
  { path: '/m', label: '工作台', icon: 'House' },
  { path: '/m/inventory', label: '库存', icon: 'Box' },
  { path: '/m/jobs', label: '作业', icon: 'SetUp' },
  { path: '/m/records', label: '记录', icon: 'Document' },
  { path: '/m/account', label: '我的', icon: 'User' },
]

export const mobileActions = [
  { key: 'inventory', path: '/m/inventory', label: '查库存', stage: 'S3', icon: 'Search' },
  { key: 'receipt', path: '/m/jobs/receipt', label: '入库', stage: 'S4 / S5', icon: 'Download', permissions: ['material.inventory.write', 'finished.inventory.write'] },
  { key: 'outbound', path: '/m/jobs/outbound', label: '出库', stage: 'S5', icon: 'Upload', permissions: ['material.inventory.write', 'finished.inventory.write'] },
  { key: 'putaway', path: '/m/jobs/putaway', label: '上架', stage: 'S4', icon: 'Box', permissions: ['material.inventory.write'] },
  { key: 'transfer', path: '/m/jobs/transfer', label: '移库', stage: 'S6', icon: 'Switch', permissions: ['material.inventory.write'] },
  { key: 'count', path: '/m/jobs/count', label: '盘点', stage: 'S6', icon: 'List', permissions: ['material.inventory.write'] },
]

export function visibleMobileActions(role, canOperate) {
  return mobileActions.filter(action => !action.permissions || action.permissions.some(permission => canOperate(role, permission)))
}

// 只提供手机内回跳；不让登录参数跳到站外、电脑页面或登录自身。
export function mobileLoginTarget(value) {
  if (typeof value !== 'string' || value.includes('\\') || /[\u0000-\u001f]/.test(value)) return '/m'
  const path = value.split(/[?#]/)[0]
  const paths = [...mobileNavigation.map(item => item.path), '/m/account/password', ...mobileActions.map(item => item.path)]
  return paths.includes(path) ? value : '/m'
}

export function loginLocation(route) {
  return { path: route.meta.mobile ? '/m/login' : '/login', query: { redirect: route.fullPath } }
}

const pending = () => import('./MobilePending.vue')
export const mobileRoutes = [
  { path: '/m/login', component: () => import('./MobileLogin.vue'), meta: { title: '手机登录', public: true, mobile: true } },
  {
    path: '/m', component: () => import('./MobileLayout.vue'), meta: { mobile: true },
    children: [
      { path: '', component: () => import('./MobileHome.vue'), meta: { title: '工作台', mobileTab: '/m' } },
      { path: 'inventory', component: pending, meta: { title: '库存', mobileTab: '/m/inventory', mobileParent: '/m', stage: 'S3' } },
      { path: 'jobs', component: pending, meta: { title: '作业', mobileTab: '/m/jobs', mobileParent: '/m', stage: 'S4～S6' } },
      { path: 'records', component: pending, meta: { title: '记录', mobileTab: '/m/records', mobileParent: '/m', stage: 'S3', pendingLabel: '单据与流水查询' } },
      { path: 'account', component: () => import('./MobileAccount.vue'), meta: { title: '我的', mobileTab: '/m/account', mobileParent: '/m' } },
      { path: 'account/password', component: () => import('./MobilePassword.vue'), meta: { title: '修改密码', mobileParent: '/m/account', mobileTask: true } },
      ...mobileActions.filter(item => item.key !== 'inventory').map(item => ({
        path: `jobs/${item.key}`, component: pending,
        meta: { title: item.label, mobileParent: '/m/jobs', mobileTask: true, stage: item.stage },
      })),
      { path: ':pathMatch(.*)*', redirect: '/m' },
    ],
  },
]
