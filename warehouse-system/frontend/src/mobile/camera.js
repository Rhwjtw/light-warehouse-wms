export async function qrReader() {
  const { BrowserQRCodeReader } = await import('@zxing/browser')
  return new BrowserQRCodeReader()
}
export function cameraMessage(error) {
  return ({ NotAllowedError: '相机权限未获允许，请在浏览器设置中检查；也可返回手动选择', NotFoundError: '未找到可用相机，可返回手动选择', NotReadableError: '相机被占用或无法启动，请关闭其他相机应用后重试', OverconstrainedError: '所选相机不可用，请尝试后摄或手动选择' })[error?.name] || '相机启动或识别失败，请重开相机或返回手动选择'
}
export function createQrCamera({ video, canvas, mediaDevices, secure, visible = () => true, reader = qrReader, schedule = setTimeout, cancel = clearTimeout, onState = () => {}, onText = () => {}, onError = () => {}, onDevices = () => {} }) {
  let epoch = 0, stream = null, timer = null, state = 'stopped'
  const setState = next => { state = next; onState(next) }
  function stop() {
    epoch++; if (timer != null) cancel(timer); timer = null
    stream?.getTracks().forEach(track => track.stop()); stream = null
    video.pause(); video.srcObject = null
    setState('stopped')
  }
  async function start(deviceId = '') {
    if (state !== 'stopped') return
    if (!secure || !mediaDevices?.getUserMedia) { onError('相机需要可信HTTPS及浏览器支持；可返回手动选择'); return }
    if (!visible()) return
    const ticket = ++epoch
    setState('requesting')
    try {
      const engine = await reader()
      if (ticket !== epoch || !visible()) { if (ticket === epoch) stop(); return }
      const acquired = await mediaDevices.getUserMedia({ audio: false, video: { ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } }), width: { ideal: 1280 }, height: { ideal: 720 } } })
      // Permission promises cannot be cancelled. Late grants must stop every track.
      if (ticket !== epoch || !visible()) { acquired.getTracks().forEach(track => track.stop()); if (ticket === epoch) stop(); return }
      stream = acquired; video.srcObject = acquired
      await video.play()
      if (ticket !== epoch || !visible()) { if (ticket === epoch) stop(); return }
      setState('scanning')
      mediaDevices.enumerateDevices?.().then(devices => { if (ticket === epoch) onDevices(devices.filter(device => device.kind === 'videoinput').map(device => ({ id: device.deviceId, label: device.label }))) }).catch(() => {})
      function frame() {
        if (ticket !== epoch) return
        if (!visible()) { stop(); return }
        try {
          if (video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) {
            const scale = Math.min(1, 960 / video.videoWidth)
            canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale)
            const context = canvas.getContext('2d', { willReadFrequently: true })
            if (!context) throw new Error('canvas unavailable')
            context.drawImage(video, 0, 0, canvas.width, canvas.height)
            const decoded = engine.decodeFromCanvas(canvas)
            const text = decoded.getText()
            stop(); onText(text); return
          }
        } catch (e) {
          // ZXing's stable kind survives production minification of constructor names.
          if (!['NotFoundException', 'ChecksumException', 'FormatException'].includes(e?.getKind?.() || e?.name)) { stop(); onError(cameraMessage(e)); return }
        }
        timer = schedule(frame, 200)
      }
      frame()
    } catch (e) { if (ticket === epoch) { stop(); onError(cameraMessage(e)) } }
  }
  return { start, stop, state: () => state }
}
