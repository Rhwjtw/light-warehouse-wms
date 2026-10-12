import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { mobileLoginTarget, mobileRoutes, visibleMobileActions } from '../src/mobile/routes.js'
import { canOperate } from '../src/permissions.js'

function component(name, dependencies = {}) {
  const descriptor = parse(readFileSync(new URL(`../src/mobile/${name}.vue`, import.meta.url), 'utf8')).descriptor
  let body = compileScript(descriptor, { id: name, inlineTemplate: true, genDefaultAs: 'component' }).content
  body = body.replace(/import\s*\{([^}]+)\}\s*from\s*['"]vue['"]/g, (_, bindings) => `const {${bindings.replace(/\bas\b/g, ':')}} = Vue`)
    .replace(/^import .*$/gm, '').replace('export default', 'const component =')
  return new Function('Vue', ...Object.keys(dependencies), body + '\nreturn component')(Vue, ...Object.values(dependencies))
}
const icon = { render: () => Vue.h('svg') }
const render = (Component, props, slots) => renderToString(Vue.createSSRApp({ render: () => Vue.h(Component, props, slots) }))

test('new inbound category preserves login redirects, original receipt URLs and existing role permissions', () => {
  const children = mobileRoutes.find(row => row.path === '/m').children
  assert.ok(children.find(row => row.path === 'jobs/inbound').meta.mobileTask)
  for (const path of ['/m/jobs/inbound', '/m/jobs/outbound', '/m/jobs/receipt']) assert.equal(mobileLoginTarget(path), path)
  for (const role of ['admin', 'warehouse', 'workshop', 'workshop_director', 'accountant']) {
    const actions = visibleMobileActions(role, canOperate)
    if (['admin', 'warehouse'].includes(role)) assert.equal(actions.find(row => row.key === 'receipt').path, '/m/jobs/inbound')
    else assert.deepEqual(actions.map(row => row.key), ['inventory'])
  }
})
test('task heading distinguishes success, submission and unknown results while retaining read failure alerts', async () => {
  const Heading = component('MobileTaskHeading', { CircleCheck: icon, Warning: icon })
  for (const [status, text] of [['succeeded', '原料入库成功'], ['submitting', '正在提交'], ['uncertain', '结果待核实']]) {
    const html = await render(Heading, { title: '原料入库', task: { status }, step: 'result', error: '成功、待刷新' })
    assert.ok(html.includes(text)); assert.ok(html.includes('不能重复提交') || html.includes('勿重复提交'))
    assert.ok(html.includes('role="alert"')); assert.ok(html.includes('成功、待刷新'))
  }
  const html = await render(Heading, { task: { status: 'draft' }, step: 'confirm', allowed: false })
  assert.match(html, /aria-current="step"[^>]*>.*?<b>2<\/b>核对/)
  assert.ok(html.includes('无此作业的操作权限'))
})
test('record fields keep decimal precision, unit and exact identity available in collapsed details', async () => {
  const { quantityText, scopeLabel, kindLabels, receiptStatuses } = await import('../src/mobile/readQueries.js')
  const Fields = component('MobileReadFields', { quantityText, scopeLabel, kindLabels, receiptStatuses })
  const html = await render(Fields, { record: { id: 41, receipt_movement_id: 75, received_quantity: '99999999999999.000001', unit: '桶', batch_no: '同批号', supplier_name: '供应商甲' } })
  assert.ok(html.includes('99999999999999.000001 桶')); assert.ok(html.includes('本笔入库'))
  assert.ok(html.includes('同批号')); assert.ok(html.includes('供应商甲'))
  assert.match(html, /<details[^>]*><summary>记录标识<\/summary>.*?入库明细ID.*?75/)
  assert.ok(!html.includes('<details open'))
})
test('shared selector preserves row anchors, loading and error states and caller-specific search limits', async () => {
  const Choices = component('MobileChoices', { ArrowRight: icon, Search: icon })
  const props = { title: '库位', search: '同名', maxlength: 80, items: [{ id: 9 }], total: 1, page: 1, pages: 2, prefix: 's4b-location-' }
  const html = await render(Choices, props, { default: ({ row }) => Vue.h('h3', `记录${row.id}`) })
  assert.ok(html.includes('maxlength="80"')); assert.ok(html.includes('id="s4b-location-9"')); assert.ok(html.includes('记录9'))
  assert.ok(html.includes('data-mobile-list-ready')); assert.ok(html.includes('上一页'))
  const loading = await render(Choices, { ...props, loading: true })
  assert.ok(loading.includes('正在查询')); assert.ok(!loading.includes('选择此记录'))
  const failed = await render(Choices, { ...props, error: '网络不可用' })
  assert.ok(failed.includes('role="alert"')); assert.ok(failed.includes('重试查询')); assert.ok(!failed.includes('选择此记录'))
})
