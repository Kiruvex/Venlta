import { render } from 'preact';
import { App } from './app';
import { initBridge, createMockBridge } from './lib/api';
import { initI18n } from './i18n';
import '../styles/main.css';

// QWebChannel 信号防御补丁：Qt 6 的 QWebChannel JS 库在分发信号时可能对 null 参数
// 使用 spread 语法（...args），导致 "object null is not iterable" 错误。
// 此补丁直接修改 QWebChannel 原型方法，在内部处理信号前将 null args 替换为空数组，
// 防止 handleSignal 中 ...null 导致的 TypeError。
function patchQWebChannel() {
  if (typeof window.QWebChannel !== 'function') return;
  const proto = window.QWebChannel.prototype;

  // 补丁 handleSignal：在信号分发前修复 null args（核心修复点）
  // handleSignal 内部使用 ...message.args 展开参数，null 会导致 TypeError
  if (proto && typeof proto.handleSignal === 'function') {
    const origHandleSignal = proto.handleSignal;
    proto.handleSignal = function(message: any) {
      if (message && (message.args === null || message.args === undefined)) {
        message.args = [];
      }
      return origHandleSignal.call(this, message);
    };
  }

  // 补丁 handleMessage：作为额外防线，在消息解析阶段修复 null args
  // 不同 Qt 版本的内部结构可能不同，此补丁确保在最早阶段拦截
  if (proto && typeof proto.handleMessage === 'function') {
    const origHandleMessage = proto.handleMessage;
    proto.handleMessage = function(data: any) {
      try {
        const parsed = typeof data === 'string' ? JSON.parse(data) : data;
        if (parsed && parsed.type === 'signal' && (parsed.args === null || parsed.args === undefined)) {
          parsed.args = [];
          data = JSON.stringify(parsed);
        }
      } catch (_e) {
        // 非 JSON 消息或解析失败，原样传递
      }
      return origHandleMessage.call(this, data);
    };
  }
}

async function bootstrap() {
  // 初始化国际化
  try {
    await initI18n();
  } catch (e) {
    console.error('i18n initialization failed:', e);
    // 继续启动，i18n 降级为键名显示
  }
  // 应用 QWebChannel 信号防御补丁（在 initBridge 之前）
  patchQWebChannel();
  // 等待 QWebChannel Bridge 就绪
  try {
    await initBridge();
  } catch (e) {
    console.error('Bridge initialization failed:', e);
    // 超时或失败时降级为 mock bridge，避免应用完全不可用
    if (!window.bridge) {
      console.warn('Falling back to mock bridge after timeout');
      window.bridge = createMockBridge();
    }
  }
  // 渲染根组件
  const root = document.getElementById('app');
  if (root) {
    render(<App />, root);
  } else {
    console.error('Root element #app not found');
  }
}

bootstrap();
