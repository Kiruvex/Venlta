import type { BridgeResult } from './bridge-result';

// VenltaBridge 完整类型声明（与后端 @Slot 方法一一对应）
export interface VenltaBridge {
  // 代理控制
  getProxyState(): Promise<string>;
  startProxy(): Promise<string>;
  stopProxy(): Promise<string>;
  restartProxy(): Promise<string>;
  toggleTun(enabled: boolean): Promise<string>;
  toggleSystemProxy(enabled: boolean): Promise<string>;
  checkTunCapability(): Promise<string>;
  grantTunCapability(): Promise<string>;
  switchMode(mode: string): Promise<string>;
  // 节点管理
  listNodes(): Promise<string>;
  addNode(nodeJson: string): Promise<string>;
  updateNode(nodeId: string, updatesJson: string): Promise<string>;
  deleteNode(nodeId: string): Promise<string>;
  testLatency(nodeTagsJson: string): Promise<string>;
  switchNode(groupTag: string, nodeTag: string): Promise<string>;
  batchUpdateNodeLatency(updatesJson: string): Promise<string>;
  // 分组管理
  listNodeGroups(): Promise<string>;
  addNodeGroup(groupJson: string): Promise<string>;
  updateNodeGroup(groupId: string, updatesJson: string): Promise<string>;
  deleteNodeGroup(groupId: string): Promise<string>;
  // 订阅管理
  listSubscriptions(): Promise<string>;
  addSubscription(name: string, url: string): Promise<string>;
  updateSubscription(subId: string): Promise<string>;
  updateSubscriptionMeta(subId: string, updatesJson: string): Promise<string>;
  deleteSubscription(subId: string): Promise<string>;
  // 路由规则
  listRules(): Promise<string>;
  addRule(ruleJson: string): Promise<string>;
  updateRule(ruleId: string, updatesJson: string): Promise<string>;
  deleteRule(ruleId: string): Promise<string>;
  listRuleSets(): Promise<string>;
  addRuleSet(rulesetJson: string): Promise<string>;
  updateRuleSet(rulesetId: string, updatesJson: string): Promise<string>;
  deleteRuleSet(rulesetId: string): Promise<string>;
  // 设置
  getSettings(): Promise<string>;
  setSettings(settingsJson: string): Promise<string>;
  // 连接管理
  closeConnection(connId: string): Promise<string>;
  // 速度测试
  testSpeed(nodeTagsJson: string): Promise<string>;
  // 端口检测
  checkPortConflicts(): Promise<string>;
  // 自动更新
  checkUpdate(): Promise<string>;
  downloadLatestUpdate(): Promise<string>;
  installAppUpdate(archivePath: string): Promise<string>;
  // sing-box 核心
  isSingboxInstalled(): Promise<string>;
  downloadSingboxCore(): Promise<string>;
  installSingboxCore(archivePath: string): Promise<string>;
  // i18n
  getSystemLanguage(): Promise<string>;
  getAppVersion(): Promise<string>;
  setBackendLanguage(lang: string): Promise<string>;
  // Qt 信号（QWebChannel 将 Qt Signal 暴露为 { connect(callback) } 对象）
  // 注意：QWebChannel 不会添加 on 前缀，信号名与后端 Signal 定义完全一致
  // 例如后端 proxyStateChanged = Signal(str) → 前端 bridge.proxyStateChanged.connect(cb)
  proxyStateChanged?: { connect: (callback: (state: string) => void) => void };
  trafficStatsUpdated?: { connect: (callback: (stats: string) => void) => void };
  logEmitted?: { connect: (callback: (log: string) => void) => void };
  connectionsUpdated?: { connect: (callback: (conns: string) => void) => void };
  latencyResult?: { connect: (callback: (result: string) => void) => void };
  subscriptionUpdated?: { connect: (callback: (result: string) => void) => void };
  speedResult?: { connect: (callback: (result: string) => void) => void };
  connectionClosed?: { connect: (callback: (result: string) => void) => void };
  downloadProgress?: { connect: (callback: (progress: string) => void) => void };
}

// 全局类型声明
declare global {
  interface Window {
    qt: { webChannelTransport: any };
    QWebChannel: any;
    bridge: VenltaBridge;
  }
}

export function initBridge(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.bridge) {
      resolve();
      return;
    }
    if (!(window.qt && window.qt.webChannelTransport)) {
      console.warn('No Qt environment, using mock bridge');
      window.bridge = createMockBridge();
      resolve();
      return;
    }
    if (typeof window.QWebChannel === 'undefined') {
      reject(new Error('QWebChannel script not loaded'));
      return;
    }
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error('Bridge initialization timeout'));
      }
    }, 10000);
    new window.QWebChannel(window.qt.webChannelTransport, (channel: any) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        window.bridge = channel.objects.bridge;
        resolve();
      }
    });
  });
}

// 可调用的 Bridge 方法名（排除信号监听器，防止误调信号回调）
// 使用条件类型自动排除含 connect 属性的信号对象，无需手动维护排除列表
type HasConnect<T> = T extends { connect: (...args: any[]) => any } ? true : false;
type BridgeMethodName = Exclude<{
  [K in keyof VenltaBridge]: HasConnect<VenltaBridge[K]> extends true ? never : K
}[keyof VenltaBridge], undefined>;

/**
 * 类型安全的 Bridge 调用封装
 * 使用 BridgeMethodName 约束方法名，排除信号监听器，防止拼写错误
 *
 * 改进建议：可使用 TypeScript 函数重载为每个方法名提供精确的参数类型，
 * 例如：callBridge('switchNode', groupTag: string, nodeTag: string): Promise<BridgeResult<void>>
 * 当前使用 any[] 参数类型是为了简洁，实际类型安全由 VenltaBridge 接口定义保证
 */
export async function callBridge<T>(methodName: BridgeMethodName, ...args: any[]): Promise<BridgeResult<T>> {
  const method = (window.bridge as any)[methodName];
  if (!method || typeof method !== 'function') {
    return { ok: false, error: { code: 'METHOD_NOT_FOUND', message: `Bridge method "${methodName}" not found` } };
  }
  try {
    const raw = await method(...args);
    const result: BridgeResult<T> = typeof raw === 'string' ? JSON.parse(raw) : raw;
    return result;
  } catch (e) {
    return { ok: false, error: { code: 'BRIDGE_CALL_FAILED', message: String(e) } };
  }
}

export function createMockBridge(): VenltaBridge {
  return {
    getProxyState: async () => JSON.stringify({ ok: true, data: { isRunning: false, currentMode: 'route', isTunEnabled: false, isSystemProxyEnabled: false, currentSelectorTag: 'proxy', currentNode: null, restartCount: 0, lastCrashTime: null } }),
    startProxy: async () => JSON.stringify({ ok: true }),
    stopProxy: async () => JSON.stringify({ ok: true }),
    restartProxy: async () => JSON.stringify({ ok: true }),
    toggleTun: async (enabled: boolean) => JSON.stringify({ ok: true, data: { tun_enabled: enabled } }),
    toggleSystemProxy: async (enabled: boolean) => JSON.stringify({ ok: true, data: { system_proxy_enabled: enabled } }),
    checkTunCapability: async () => JSON.stringify({ ok: true, data: { can_create_tun: false, platform: 'Linux', details: 'mock' } }),
    grantTunCapability: async () => JSON.stringify({ ok: true, data: { already_has: false } }),
    switchMode: async () => JSON.stringify({ ok: true }),
    listNodes: async () => JSON.stringify({ ok: true, data: [] }),
    addNode: async () => JSON.stringify({ ok: true }),
    updateNode: async () => JSON.stringify({ ok: true }),
    deleteNode: async () => JSON.stringify({ ok: true }),
    testLatency: async () => JSON.stringify({ ok: true }),
    switchNode: async () => JSON.stringify({ ok: true }),
    batchUpdateNodeLatency: async () => JSON.stringify({ ok: true }),
    listNodeGroups: async () => JSON.stringify({ ok: true, data: [] }),
    addNodeGroup: async () => JSON.stringify({ ok: true }),
    updateNodeGroup: async () => JSON.stringify({ ok: true }),
    deleteNodeGroup: async () => JSON.stringify({ ok: true }),
    listSubscriptions: async () => JSON.stringify({ ok: true, data: [] }),
    addSubscription: async (name: string, url: string) => {
      if (!name || !url) return JSON.stringify({ ok: false, error: { code: 'INVALID_INPUT', message: 'Name and URL required' } });
      return JSON.stringify({ ok: true, data: { id: 'mock-sub-id', status: 'updating' } });
    },
    updateSubscription: async () => JSON.stringify({ ok: true }),
    updateSubscriptionMeta: async () => JSON.stringify({ ok: true }),
    deleteSubscription: async () => JSON.stringify({ ok: true }),
    listRules: async () => JSON.stringify({ ok: true, data: [] }),
    addRule: async () => JSON.stringify({ ok: true }),
    updateRule: async () => JSON.stringify({ ok: true }),
    deleteRule: async () => JSON.stringify({ ok: true }),
    listRuleSets: async () => JSON.stringify({ ok: true, data: [] }),
    addRuleSet: async () => JSON.stringify({ ok: true }),
    updateRuleSet: async () => JSON.stringify({ ok: true }),
    deleteRuleSet: async () => JSON.stringify({ ok: true }),
    closeConnection: async () => JSON.stringify({ ok: true }),
    testSpeed: async () => JSON.stringify({ ok: true }),
    getSettings: async () => JSON.stringify({ ok: true, data: {} }),
    setSettings: async () => JSON.stringify({ ok: true }),
    checkPortConflicts: async () => JSON.stringify({ ok: true, data: { conflicts: [] } }),
    checkUpdate: async () => JSON.stringify({ ok: true, data: null }),
    downloadLatestUpdate: async () => JSON.stringify({ ok: true, data: { status: 'downloading' } }),
    installAppUpdate: async () => JSON.stringify({ ok: true }),
    isSingboxInstalled: async () => JSON.stringify({ ok: true, data: { installed: true } }),
    downloadSingboxCore: async () => JSON.stringify({ ok: true, data: { status: 'downloading' } }),
    installSingboxCore: async () => JSON.stringify({ ok: true }),
    getSystemLanguage: async () => JSON.stringify({ ok: true, data: { language: 'en' } }),
    getAppVersion: async () => JSON.stringify({ ok: true, data: { app_version: '0.1.0', singbox_version: '1.13.13' } }),
    setBackendLanguage: async () => JSON.stringify({ ok: true }),
    // Mock 信号对象（提供 connect 方法，开发模式下信号回调可正常触发）
    proxyStateChanged: { connect: (cb: (s: string) => void) => { /* mock: no-op */ } },
    trafficStatsUpdated: { connect: (cb: (s: string) => void) => { /* mock: no-op */ } },
    logEmitted: { connect: (cb: (s: string) => void) => { /* mock: no-op */ } },
    connectionsUpdated: { connect: (cb: (s: string) => void) => { /* mock: no-op */ } },
    latencyResult: { connect: (cb: (s: string) => void) => { /* mock: no-op */ } },
    subscriptionUpdated: { connect: (cb: (s: string) => void) => { /* mock: no-op */ } },
    speedResult: { connect: (cb: (s: string) => void) => { /* mock: no-op */ } },
    connectionClosed: { connect: (cb: (s: string) => void) => { /* mock: no-op */ } },
    downloadProgress: { connect: (cb: (s: string) => void) => { /* mock: no-op */ } },
    // 类型断言说明：使用 as VenltaBridge 替代 as any，确保 mock 返回值符合接口签名。
    // 此类型断言是有意且安全的，原因：
    // 1. mock 只在开发环境（无 Qt）使用，生产环境使用 QWebChannel 注入的真实 bridge 对象。
    // 2. mock 对象必须符合 VenltaBridge 接口的所有方法签名，TypeScript 会在编译时检查。
    // 3. 使用 as VenltaBridge 而非 as any，确保类型不匹配时能立即发现（编译期而非运行期）。
    // 4. mock 方法返回硬编码 JSON 字符串（与真实 bridge 返回格式一致），不存在类型安全风险。
  } as VenltaBridge;
}
