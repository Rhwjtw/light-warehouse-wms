import { safeReadOrigin } from './readQueries.js'
import { receiptOrigin, receiptTaskPath } from './receipt.js'
import { putawayOrigin, putawayTaskPath } from './putaway.js'

export function prototypeListLocation(value) {
  if (typeof value !== 'string' || value.includes('\\')) return '/m/state-lab'
  try {
    const url = new URL(value, 'https://wms.invalid')
    if (url.origin !== 'https://wms.invalid' || url.pathname !== '/m/state-lab') return '/m/state-lab'
    return url.pathname + url.search
  } catch { return '/m/state-lab' }
}

export function mobileParentLocation(route, task) {
  if (route.meta.mobilePutawayTask) {
    if (['confirm', 'choose'].includes(route.params.step) && task && ['draft', 'rejected'].includes(task.status)) return putawayTaskPath(task)
    return putawayOrigin(task?.origin || route.query.from)
  }
  if (route.meta.mobileReceiptTask) {
    if (['confirm', 'choose'].includes(route.params.step) && task && ['draft', 'rejected'].includes(task.status)) return receiptTaskPath(task)
    return receiptOrigin(task?.origin || route.query.from)
  }
  if (route.meta.mobileAsyncList && route.query.from && route.path !== '/m/locations') return safeReadOrigin(route.query.from)
  if (route.meta.mobileReadDetail) return safeReadOrigin(route.query.from, '/m/records')
  if (route.path === '/m/locations') return safeReadOrigin(route.query.from)
  if (route.meta.mobileLabTask) {
    if (['confirm', 'context'].includes(route.params.step) && task && ['draft', 'rejected'].includes(task.status)) return `/m/state-lab/${task.id}/form`
    return prototypeListLocation(task?.origin)
  }
  return route.meta.mobileParent || '/m'
}

export function returnToParent(router, target) {
  // 正常层级使用已有上一条历史，深链接或非父级历史则替换到父级，不堆积返回记录。
  if (router.options.history.state.back === target) router.back()
  else router.replace(target)
}

export function completedTaskLocation(route, task) {
  // 将过时历史条目替换为结果；不新增历史条目，连续后退最终仍可离开任务。
  return task?.status === 'succeeded' && route.params.step !== 'result'
    ? { path: task.kind === 'material-receipt' ? receiptTaskPath(task, 'result') : task.kind === 'material-putaway' ? putawayTaskPath(task, 'result') : `/m/state-lab/${task.id}/result`, replace: true }
    : null
}

export function listQuery(query) {
  const q = typeof query.q === 'string' ? query.q.slice(0, 80) : ''
  const group = ['all', 'A', 'B'].includes(query.group) ? query.group : 'all'
  const raw = Number(query.page)
  return { q, group, page: Number.isInteger(raw) && raw > 0 ? Math.min(raw, 100) : 1 }
}
