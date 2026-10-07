export function viewportState(viewport, innerHeight, focused) {
  const inset = viewport ? Math.max(0, innerHeight - viewport.height - viewport.offsetTop) : 0
  return { inset, keyboard: Boolean(focused && inset > 120) }
}

// 停流回调供S9接入；本阶段不获取相机。恢复回调只刷新读取/权限，不包含写入。
export function attachMobileLifecycle({ win, doc, suspend, resume, stopResources = () => {}, viewportChanged = () => {} }) {
  const hidden = () => { stopResources(); suspend() }
  const visibility = () => doc.visibilityState === 'hidden' ? hidden() : resume()
  const pageshow = event => { if (event.persisted) resume() }
  const viewport = () => {
    viewportChanged(viewportState(win.visualViewport, win.innerHeight, /^(INPUT|TEXTAREA|SELECT)$/.test(doc.activeElement?.tagName || '')))
    const field = doc.activeElement
    if (field && /^(INPUT|TEXTAREA|SELECT)$/.test(field.tagName) && win.visualViewport) {
      const box = field.getBoundingClientRect(), top = win.visualViewport.offsetTop, bottom = top + win.visualViewport.height
      if (box.bottom > bottom - 16 || box.top < top + 72) field.scrollIntoView({ block: 'center' })
    }
  }
  doc.addEventListener('visibilitychange', visibility)
  doc.addEventListener('focusin', viewport); doc.addEventListener('focusout', viewport)
  win.addEventListener('pagehide', hidden); win.addEventListener('pageshow', pageshow); win.addEventListener('online', resume)
  win.addEventListener('resize', viewport)
  win.visualViewport?.addEventListener('resize', viewport); win.visualViewport?.addEventListener('scroll', viewport)
  viewport()
  return () => {
    stopResources()
    doc.removeEventListener('visibilitychange', visibility); doc.removeEventListener('focusin', viewport); doc.removeEventListener('focusout', viewport)
    win.removeEventListener('pagehide', hidden); win.removeEventListener('pageshow', pageshow); win.removeEventListener('online', resume); win.removeEventListener('resize', viewport)
    win.visualViewport?.removeEventListener('resize', viewport); win.visualViewport?.removeEventListener('scroll', viewport)
  }
}
