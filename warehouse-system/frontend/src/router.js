import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from './views/Dashboard.vue'
import ModulePlaceholder from './views/ModulePlaceholder.vue'

export const menuGroups = [
  { label: '原材料管理', items: [['materials', '原材料档案'], ['material-receipts', '原材料入库'], ['material-outbounds', '原材料出库'], ['material-stock', '原材料库存'], ['batches', '批次库存']] },
  { label: '成品管理', items: [['products', '成品档案'], ['finished-receipts', '成品入库'], ['finished-outbounds', '成品出库'], ['finished-stock', '成品库存']] },
  { label: '生产管理', items: [['boms', 'BOM 配方'], ['orders', '生产单'], ['issues', '车间领料'], ['supplements', '车间补料'], ['returns', '车间退料'], ['losses', '损耗统计']] },
  { label: '预警中心', items: [['expiry', '有效期预警']] },
  { label: '系统管理', items: [['users', '用户管理'], ['suppliers', '供应商'], ['locations', '库位'], ['settings', '基础设置'], ['logs', '操作日志']] },
]

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Dashboard, meta: { title: '首页' } },
    ...menuGroups.flatMap(group => group.items.map(([path, label]) => ({
      path: `/${path}`, component: ModulePlaceholder, meta: { title: label, group: group.label },
    }))),
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
