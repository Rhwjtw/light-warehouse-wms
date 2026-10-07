import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { computed, reactive, ref } from 'vue'
import { canAccess, canOperate } from '../src/permissions.js'
import { loginLocation, mobileLoginTarget, mobileRoutes, visibleMobileActions } from '../src/mobile/routes.js'

function component(name, dependencies, exported) {
  const descriptor = parse(readFileSync(new URL(`../src/mobile/${name}.vue`, import.meta.url), 'utf8')).descriptor
  const body = descriptor.scriptSetup.content.replace(/^import .*$/gm, '') + `\nreturn {${exported}}`
  return new Function(...Object.keys(dependencies), body)(...Object.values(dependencies))
}
const base = { computed, reactive, ref, onMounted() {}, onBeforeUnmount() {}, Box: null, Download: null, List: null, Search: null, Switch: null, Upload: null }

test('mobile deep links and expiry return to mobile login; desktop keeps its login', () => {
  assert.deepEqual(loginLocation({meta:{mobile:true},fullPath:'/m/account?source=home'}), {path:'/m/login',query:{redirect:'/m/account?source=home'}})
  assert.equal(loginLocation({meta:{},fullPath:'/orders'}).path,'/login')
  for (const target of ['https://other.test', '//other.test', '/m/login', '/m/accounting', '/orders', '/m/unknown', '/m/\\other', null, ['x']]) assert.equal(mobileLoginTarget(target),'/m')
  assert.equal(mobileLoginTarget('/m/jobs/putaway?receipt=8'),'/m/jobs/putaway?receipt=8')
  assert.equal(mobileLoginTarget('/m/account/password'),'/m/account/password')
})

test('five roles read mobile pages; only existing stock permissions show write shortcuts', () => {
  const roles = ['admin','warehouse','workshop_director','workshop','accountant']
  for (const role of roles) {
    assert.equal(canAccess(role),true)
    assert.equal(visibleMobileActions(role,canOperate).length, ['admin','warehouse'].includes(role) ? 6 : 1)
  }
  const parent = mobileRoutes.find(route => route.path === '/m')
  assert.equal(parent.children.find(route=>route.path==='account/password').meta.mobileTask,true)
  for (const path of ['inventory','jobs','records']) assert.ok(parent.children.find(route=>route.path===path).meta.stage)
})

test('mobile login validates fields, prevents duplicate submission, and safely restores destination', async () => {
  const calls = [], redirects = []
  let release
  const state = component('MobileLogin', {...base, useRoute:()=>({query:{redirect:'/m/records'}}), useRouter:()=>({replace:async next=>redirects.push(next)}),
    login:(...args)=>{calls.push(args);return new Promise(resolve=>{release=resolve})}, mobileLoginTarget}, 'submit,form,loading,error')
  await state.submit(); assert.equal(calls.length,0); assert.match(state.error.value,/用户名/)
  state.form.username=' warehouse ';state.form.password='private-test'
  const request=state.submit(); await state.submit()
  assert.equal(calls.length,1); assert.equal(state.loading.value,true)
  assert.deepEqual(calls[0],['warehouse','private-test'])
  release();await request
  assert.deepEqual(redirects,['/m/records']); assert.equal(state.form.password,'')
})

test('mobile login failure preserves username, shows reason and releases busy state', async () => {
  const state=component('MobileLogin',{...base,useRoute:()=>({query:{}}),useRouter:()=>({replace(){assert.fail('failed login redirect')}}),login:async()=>{throw new Error('账号暂时锁定')},mobileLoginTarget},'submit,form,loading,error')
  Object.assign(state.form,{username:'warehouse',password:'test'})
  await state.submit();assert.equal(state.form.username,'warehouse');assert.match(state.error.value,/锁定/);assert.equal(state.loading.value,false)
})

test('workbench loads real source quantities unchanged, separates failures, and clears stale success', async () => {
  let failing=false, calls=0
  const rows=Array.from({length:5},(_,id)=>({id,change_quantity:'99999999999999.000001',unit:'自定义'}))
  const state=component('MobileHome',{...base,auth:{user:{role:'admin'}},visibleMobileActions,canOperate,
    api:{async get(url){calls++; if(failing&&url.endsWith('summary'))throw new Error('读取失败'); return {data:url.endsWith('summary')?{expired_batches:2}:{items:rows}}}}},'load,summary,transactions,loading,summaryError,recordsError')
  await state.load();assert.equal(state.summary.value.expired_batches,2);assert.equal(state.transactions.value.length,3)
  assert.equal(state.transactions.value[0].change_quantity,rows[0].change_quantity)
  failing=true;await state.load();assert.equal(state.summary.value,null);assert.match(state.summaryError.value,/失败/);assert.equal(state.recordsError.value,'')
  state.loading.value=true;await state.load();assert.equal(calls,4)
})

test('logout calls original account-wide logout once and never reports network failure as successful revocation', async () => {
  let release, count=0
  const redirects=[]
  const state=component('MobileAccount',{...base,useRouter:()=>({replace:async path=>redirects.push(path)}),logout:()=>{count++;return new Promise(resolve=>{release=resolve})}},'signOut,signingOut,error')
  const pending=state.signOut();await state.signOut();assert.equal(count,1);release();await pending
  assert.deepEqual(redirects,['/m/login'])
  const failed=component('MobileAccount',{...base,useRouter:()=>({replace(){assert.fail('network failure success')}}),logout:async()=>{throw new Error('断网')}},'signOut,error')
  await failed.signOut();assert.match(failed.error.value,/服务器退出结果未确认/)
})

test('password change validates confirmation and clears local session only after backend success', async () => {
  let fail=true, cleared=0, calls=0
  const redirects=[]
  const state=component('MobilePassword',{...base,useRouter:()=>({replace:async path=>redirects.push(path)}),clearAuth:()=>cleared++,api:{async post(url,body){calls++;assert.equal(url,'/auth/password');assert.equal(body.new_password,'new-test-password');if(fail)throw new Error('原密码不正确')}}},'save,form,saving,error')
  await state.save();assert.equal(calls,0)
  Object.assign(state.form,{current_password:'old-test-password',new_password:'new-test-password',confirm:'mismatch'})
  await state.save();assert.equal(calls,0)
  state.form.confirm='new-test-password';await state.save();assert.equal(cleared,0);assert.match(state.error.value,/原密码/)
  fail=false;await state.save();assert.equal(cleared,1);assert.deepEqual(redirects,[{path:'/m/login',query:{redirect:'/m/account',status:'password-changed'}}]);assert.equal(state.form.current_password,'');assert.equal(state.saving.value,false)
})
