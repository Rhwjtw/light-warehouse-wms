import test from 'node:test'
import assert from 'node:assert/strict'
import { parseQr, qrText, qrInstance, readQrObject } from '../src/mobile/qr.js'
import { makeLabel } from '../src/mobile/labels.js'
import { mobileLoginTarget } from '../src/mobile/routes.js'
const instance = 'a'.repeat(32)
const row = { id: 7, receipt_movement_id: 19, batch_id: 30, material_id: 1, supplier_id: 2, unit: 'kg', document_no: 'MR-7', status: 'partial', pending_quantity: '0.000001', material_name: '同名物料', material_code: 'M1', batch_no: '重复批号', supplier_name: '供应商', production_date: '2026-01-01', expiry_date: '2027-01-01' }
test('strict v1 type, instance, canonical INTEGER identity and no external link execution', () => {
  const text=qrText('receipt', row, instance)
  assert.deepEqual(parseQr(text,'receipt',instance),{type:'receipt',instance,id:'7',movementId:'19'})
  assert.equal(mobileLoginTarget('/m/labels'),'/m/labels')
  for(const invalid of [text+' ',text+':1',text.replace(':7:',':07:'),text.replace(':7:',':0:'),text.replace(':19',':2147483648'),text.replace('v1','v2'),text.replace(instance,'b'.repeat(32)),text.replace('receipt','location'),'https://evil.invalid']) assert.throws(()=>parseQr(invalid,'receipt',instance))
  for(const config of ['',undefined,'a'.repeat(31),'A'.repeat(32)]) assert.throws(()=>qrInstance(config))
  assert.throws(()=>parseQr(qrText('location',{id:1},instance),'receipt',instance),/货物/)
})
test('read only exact document+movement, current status and catalog; invalid labels cause zero API calls', async () => {
  let calls=[]
  const api={get:async path=>{calls.push(path);return {data:path.includes('material-receipts')?row:path.includes('materials')?{id:1,unit:'kg',is_active:true}:{id:2,is_active:true}}}}
  assert.equal((await readQrObject(api,qrText('receipt',row,instance),'receipt',instance)).id,7)
  assert.equal(calls.length,3)
  calls=[]; await assert.rejects(readQrObject(api,'https://evil.invalid','receipt',instance)); assert.equal(calls.length,0)
  for(const bad of [{...row,id:8},{...row,receipt_movement_id:20},{...row,status:'reversed'},{...row,pending_quantity:'0'},{...row,status:'shelved'}]) await assert.rejects(readQrObject({get:async()=>({data:bad})},qrText('receipt',row,instance),'receipt',instance))
  await assert.rejects(readQrObject({get:async path=>({data:path.includes('material-receipts')?row:{id:1,is_active:false,unit:'kg'}})},qrText('receipt',row,instance),'receipt',instance),/失效/)
})
test('label generation maps real identities, encodes no quantity or credential; name changes keep stable location code', async () => {
  const api={get:async path=>({data:path.includes('material-receipts')?row:path.includes('materials')?{id:1,unit:'kg',is_active:true}:{id:2,is_active:true}})}
  const label=await makeLabel(api,'receipt',7,19,instance)
  assert.match(label.image,/^data:image\/png;base64,/)
  assert.ok(label.lines.includes('单ID 7 / 明细 19')); assert.ok(label.lines.includes('单位 kg'))
  assert.equal(label.text,`wmsqr:v1:${instance}:receipt:7:19`)
  const two=qrText('receipt',{...row,id:8,receipt_movement_id:20},instance);assert.notEqual(label.text,two)
  const first=await makeLabel({get:async()=>({data:{id:3,name:'同名库位',is_active:true}})},'location',3,'',instance)
  const renamed=await makeLabel({get:async()=>({data:{id:3,name:'改名库位',is_active:true}})},'location',3,'',instance)
  assert.equal(first.text,renamed.text);assert.notDeepEqual(first.lines,renamed.lines)
  assert.notEqual(first.text,qrText('location',{id:4,name:'同名库位'},instance))
  await assert.rejects(makeLabel({get:async()=>({data:{id:3,is_active:false}})},'location',3,'',instance))
})
