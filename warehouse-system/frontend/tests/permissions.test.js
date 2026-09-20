import test from 'node:test'
import assert from 'node:assert/strict'
import { canAccess, canIssue, canAdjustProduction } from '../src/permissions.js'

test('管理员可访问全部业务和系统页', () => {
  for (const page of ['','materials','material-receipts','material-outbounds','products','boms','orders','issues','supplements','returns','losses','expiry','users','suppliers','locations','settings','logs']) assert.equal(canAccess('admin',page),true)
})
test('仓库页面与系统管理隔离', () => {
  for (const page of ['users','settings','logs']) assert.equal(canAccess('warehouse',page),false)
  for (const page of ['','materials','products','orders','issues','supplements','returns','expiry','material-transactions']) assert.equal(canAccess('warehouse',page),true)
})
test('车间可处理补退和产量，不能普通库存记账或领料', () => {
  for (const page of ['material-receipts','material-outbounds','finished-receipts','finished-outbounds','issues','users','settings','logs']) assert.equal(canAccess('workshop',page),false)
  for (const page of ['','orders','supplements','returns','losses']) assert.equal(canAccess('workshop',page),true)
  assert.equal(canIssue('workshop'),false)
  assert.equal(canAdjustProduction('workshop'),true)
})
test('未知角色和未登录用户拒绝访问', () => {
  for (const role of [undefined,null,'','other']) assert.equal(canAccess(role,'orders'),false)
})
