export interface ProxyStateType {
  isRunning: boolean;
  currentMode: 'route' | 'global' | 'direct';
  isTunEnabled: boolean;
  isSystemProxyEnabled: boolean;
  currentNode: string | null;
  currentSelectorTag: string | null;
  restartCount: number;
  lastCrashTime: string | null;
}
