// 主屏幕仅提供在线网页入口，不注册 service worker 或缓存库存请求。
export function configureMobileHomeScreen(doc, mobile) {
  for (const node of doc.head.querySelectorAll('[data-wms-mobile-home]')) node.remove()
  if (!mobile) return
  const entries = [
    ['link', { rel: 'manifest', href: '/mobile.webmanifest' }],
    ['link', { rel: 'apple-touch-icon', sizes: '180x180', href: '/mobile-icons/apple-touch-icon.png' }],
    ['meta', { name: 'theme-color', content: '#2563eb' }],
    ['meta', { name: 'mobile-web-app-capable', content: 'yes' }],
    ['meta', { name: 'apple-mobile-web-app-capable', content: 'yes' }],
    ['meta', { name: 'apple-mobile-web-app-title', content: '轻仓 WMS' }],
    ['meta', { name: 'apple-mobile-web-app-status-bar-style', content: 'default' }],
  ]
  for (const [tag, attrs] of entries) {
    const node = doc.createElement(tag)
    node.setAttribute('data-wms-mobile-home', '')
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value)
    doc.head.appendChild(node)
  }
}
