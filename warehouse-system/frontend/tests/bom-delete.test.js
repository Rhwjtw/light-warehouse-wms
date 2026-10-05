import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { computed, reactive, ref } from 'vue'
import { canOperate } from '../src/permissions.js'

const descriptor = parse(readFileSync(new URL('../src/views/Boms.vue', import.meta.url), 'utf8')).descriptor
const body = descriptor.scriptSetup.content.replace(/^import .*$/gm, '') + '\nreturn {remove,rows,page,total,deletingId,previewResult,loadError}'
function component(api, confirm, role = 'admin') {
  const messages = []
  const state = new Function('computed', 'reactive', 'ref', 'onMounted', 'auth', 'canOperate', 'api', 'ElMessage', 'ElMessageBox', body)(
    computed, reactive, ref, () => {}, {user:{role}}, canOperate, api,
    {success: message => messages.push(message), error: message => messages.push(message)}, {confirm},
  )
  return {state, messages}
}
const row = {id:9, product_name:'测试成品', version:2, status:'active'}

test('delete confirms active impact, prevents duplicates and repairs empty last page', async () => {
  const calls = []
  let confirmText, release
  const {state} = component({async delete(url) {calls.push(url)}, async get(url, config) {
    calls.push(config.params.page); return {data:{items:[{id:8}],total:20}}
  }}, text => {confirmText=text; return new Promise(resolve => {release=resolve})})
  state.rows.value=[row]; state.page.value=2; state.previewResult.value={bom_id:9}
  const pending = state.remove(row)
  await state.remove(row)
  assert.equal(state.deletingId.value,9); assert.deepEqual(calls,[])
  assert.match(confirmText,/测试成品.*V2/); assert.match(confirmText,/正在启用/)
  release(); await pending
  assert.deepEqual(calls,['/boms/9',1]); assert.equal(state.page.value,1)
  assert.equal(state.previewResult.value,null); assert.equal(state.deletingId.value,null)
})
test('cancel and close never delete or report errors', async () => {
  for (const action of ['cancel','close']) {
    const {state,messages} = component({delete(){assert.fail('cancelled delete')}}, () => Promise.reject(action))
    await state.remove(row); assert.deepEqual(messages,[]); assert.equal(state.deletingId.value,null)
  }
})
test('server reference rejection preserves list, page and preview', async () => {
  const {state,messages} = component({async delete(){throw new Error('该BOM已被生产单引用，请使用停用，不能删除')}}, async () => {})
  state.rows.value=[row]; state.page.value=2; state.previewResult.value={bom_id:9}
  await state.remove(row)
  assert.equal(state.page.value,2); assert.deepEqual(state.rows.value,[row])
  assert.equal(state.previewResult.value.bom_id,9); assert.match(messages[0],/停用/)
  assert.equal(state.deletingId.value,null)
})
test('read-only roles cannot initiate delete, write roles use existing BOM permission', async () => {
  for (const role of ['warehouse','workshop',null]) {
    const {state} = component({delete(){assert.fail('read-only delete')}}, () => assert.fail('read-only confirm'),role)
    await state.remove(row)
  }
  for (const role of ['admin','workshop_director','accountant']) assert.equal(canOperate(role,'bom.write'),true)
  assert.match(descriptor.template.content,/v-if="canWrite"[^>]*type="danger"[^>]*@click="remove\(row\)"/)
})
