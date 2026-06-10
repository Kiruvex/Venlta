// QWebChannel 桥接脚本
// Qt 环境下由 PySide6 注入（qrc:///qtwebchannel/qwebchannel.js）
// 开发模式下此处提供空实现，initBridge() 检测到无 Qt 环境时使用 mock bridge

// 如果已在 Qt 环境中加载，不需要此文件
if (typeof window.QWebChannel === 'undefined') {
  console.log('[qwebchannel.js] Running in development mode (no Qt environment)');
  // 开发模式下不定义 QWebChannel，由 initBridge() 检测并降级到 mock bridge
}
