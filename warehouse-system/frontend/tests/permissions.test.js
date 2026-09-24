import test from 'node:test'
import assert from 'node:assert/strict'
import { canAccess, canOperate } from '../src/permissions.js'

test('管理员可访问全部业务和系统页', () => {
  for (const page of ['','materials','material-receipts','material-outbounds','products','boms','orders','issues','supplements','returns','losses','expiry','users','suppliers','locations','settings','logs']) assert.equal(canAccess('admin',page),true)
})
test('五种角色都能查看全部页面', () => {
  for (const role of ['admin','warehouse','workshop_director','workshop','accountant'])
    for (const page of ['','materials','products','orders','issues','supplements','returns','expiry','material-transactions','users','settings','logs'])
      assert.equal(canAccess(role,page),true)
})
test('操作权限区分档案、库存、生产和领补退', () => {
  assert.equal(canOperate('warehouse','material.inventory.write'),true)
  assert.equal(canOperate('warehouse','production.order.write'),false)
  assert.equal(canOperate('workshop_director','production.manage'),true)
  assert.equal(canOperate('workshop_director','material.inventory.write'),false)
  for (const code of ['workshop.pick','workshop.replenish','workshop.return']) assert.equal(canOperate('workshop',code),true)
  assert.equal(canOperate('workshop','production.order.write'),false)
  assert.equal(canOperate('workshop','bom.write'),false)
  assert.equal(canOperate('accountant','material.archive.write'),true)
  assert.equal(canOperate('accountant','material.inventory.write'),false)
  assert.equal(canOperate('accountant','finished.archive.write'),true)
  assert.equal(canOperate('accountant','finished.inventory.write'),false)
  assert.equal(canOperate('accountant','bom.write'),true)
  assert.equal(canOperate('accountant','production.order.write'),true)
  assert.equal(canOperate('accountant','production.manage'),false)
})
test('未知角色和未登录用户拒绝访问', () => {
  for (const role of [undefined,null,'','other']) assert.equal(canAccess(role,'orders'),false)
})
