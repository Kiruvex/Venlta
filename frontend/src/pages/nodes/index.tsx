import { useSignal } from '@preact/signals';
import { useEffect } from 'preact/hooks';
import { useTranslation } from '../../i18n/useTranslation';
import { nodeStore } from '../../stores/nodeStore';
import { toastStore } from '../../stores/toastStore';
import { callBridge } from '../../lib/api';
import { Button } from '../../components/Button';
import { Switch } from '../../components/Switch';
import { Modal } from '../../components/Modal';
import { Input } from '../../components/Input';
import { formatLatency, formatSpeed } from '../../lib/format';

const VALID_PROTOCOLS = ['vmess', 'vless', 'trojan', 'shadowsocks', 'hysteria2', 'wireguard', 'tuic'] as const;

const PROTOCOL_COLORS: Record<string, string> = {
  vmess: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  vless: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300',
  trojan: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
  shadowsocks: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  hysteria2: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  wireguard: 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
  tuic: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
};

export function NodesPage() {
  const { t } = useTranslation();
  const selectedGroup = useSignal<string | null>(null);
  const showAddModal = useSignal(false);
  const newNodeName = useSignal('');
  const newNodeAddress = useSignal('');
  const newNodePort = useSignal('443');
  const newNodeProtocol = useSignal<'vmess' | 'vless' | 'trojan' | 'shadowsocks' | 'hysteria2' | 'wireguard' | 'tuic'>('vmess');
  const newNodeUuid = useSignal('');
  const newNodePassword = useSignal('');
  const newNodeMethod = useSignal('aes-256-gcm');
  const newNodePrivateKey = useSignal('');
  const newNodePeerPublicKey = useSignal('');
  const newNodeReserved = useSignal('');
  const newNodeLocalAddress = useSignal('');
  const newNodeTls = useSignal('none');
  const newNodeSni = useSignal('');
  const newNodeRealityPublicKey = useSignal('');
  const newNodeRealityShortId = useSignal('');
  const newNodeTransport = useSignal('tcp');
  const newNodeWsPath = useSignal('/');
  const newNodeWsHost = useSignal('');
  const newNodeGrpcServiceName = useSignal('');
  const newNodeCongestionControl = useSignal('bbr');
  const newNodeUdpRelayMode = useSignal('native');
  const newNodeHeartbeat = useSignal('10s');
  const newNodeAlpn = useSignal('');
  const newNodeDisableSni = useSignal(false);
  const newNodeMuxEnabled = useSignal(false);
  const newNodeMuxProtocol = useSignal('h2mux');
  const newNodeMuxMaxStreams = useSignal('8');
  const newNodeMuxPadding = useSignal(false);
  const newNodeBrutalEnabled = useSignal(false);
  const newNodeBrutalSpeed = useSignal('100');
  const newNodeUtlsFingerprint = useSignal('');
  const editingGroup = useSignal<{id: string; name: string} | null>(null);
  const showGroupModal = useSignal(false);
  const groupModalMode = useSignal<'add' | 'rename'>('add');
  const groupModalName = useSignal('');
  const showDeleteConfirm = useSignal(false);
  const deleteTargetId = useSignal('');
  const deleteTargetType = useSignal<'group' | 'node'>('group');
  const newSubName = useSignal('');
  const newSubUrl = useSignal('');
  const editingSubId = useSignal<string | null>(null);
  const newSubAutoUpdate = useSignal(false);
  const newSubUpdateInterval = useSignal('0');
  const editingSubAutoUpdate = useSignal(false);
  const editingSubUpdateInterval = useSignal('0');
  const selectedNodeIds = useSignal<Set<string>>(new Set());
  // 异步操作 loading 状态 — 延迟/速度测试使用 nodeStore 全局状态（跨组件共享，信号回调清除）
  const isBatchOperating = useSignal(false);
  const searchQuery = useSignal('');
  const protocolFilter = useSignal('');

  useEffect(() => {
    nodeStore.fetchNodes();
    nodeStore.fetchGroups();
    nodeStore.fetchSubscriptions();
  }, []);

  const nodes = nodeStore.nodes.value;
  const groups = nodeStore.groups.value;
  const subscriptions = nodeStore.subscriptions.value;

  // 多级过滤：分组 → 协议 → 搜索
  // selectedGroup: null=未分组(无groupId), '__all__'=全部
  let filteredNodes = selectedGroup.value === '__all__'
    ? nodes
    : selectedGroup.value
    ? nodes.filter(n => n.groupId === selectedGroup.value)
    : nodes.filter(n => !n.groupId);
  if (protocolFilter.value) {
    filteredNodes = filteredNodes.filter(n => n.protocol === protocolFilter.value);
  }
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase();
    filteredNodes = filteredNodes.filter(n =>
      n.name.toLowerCase().includes(q) ||
      n.address.toLowerCase().includes(q) ||
      n.protocol.toLowerCase().includes(q)
    );
  }

  const allFilteredIds = filteredNodes.map(n => n.id);
  const selectedCount = allFilteredIds.filter(id => selectedNodeIds.value.has(id)).length;
  const isAllSelected = allFilteredIds.length > 0 && selectedCount === allFilteredIds.length;
  const isIndeterminate = selectedCount > 0 && selectedCount < allFilteredIds.length;

  const handleToggleSelectAll = () => {
    if (isAllSelected) {
      const newSet = new Set(selectedNodeIds.value);
      allFilteredIds.forEach(id => newSet.delete(id));
      selectedNodeIds.value = newSet;
    } else {
      const newSet = new Set(selectedNodeIds.value);
      allFilteredIds.forEach(id => newSet.add(id));
      selectedNodeIds.value = newSet;
    }
  };

  const handleToggleSelectNode = (id: string) => {
    const newSet = new Set(selectedNodeIds.value);
    if (newSet.has(id)) { newSet.delete(id); } else { newSet.add(id); }
    selectedNodeIds.value = newSet;
  };

  const handleEnableSelected = async () => {
    const ids = [...selectedNodeIds.value];
    if (ids.length === 0) return;
    isBatchOperating.value = true;
    try {
      for (const id of ids) { await callBridge('updateNode', id, JSON.stringify({ isEnabled: true })); }
      selectedNodeIds.value = new Set();
      await nodeStore.fetchNodes();
    } finally {
      isBatchOperating.value = false;
    }
  };

  const handleDisableSelected = async () => {
    const ids = [...selectedNodeIds.value];
    if (ids.length === 0) return;
    isBatchOperating.value = true;
    try {
      for (const id of ids) { await callBridge('updateNode', id, JSON.stringify({ isEnabled: false })); }
      selectedNodeIds.value = new Set();
      await nodeStore.fetchNodes();
    } finally {
      isBatchOperating.value = false;
    }
  };

  const handleDeleteSelected = async () => {
    const ids = [...selectedNodeIds.value];
    if (ids.length === 0) return;
    isBatchOperating.value = true;
    try {
      for (const id of ids) { await callBridge('deleteNode', id); }
      selectedNodeIds.value = new Set();
      await nodeStore.fetchNodes();
    } finally {
      isBatchOperating.value = false;
    }
  };

  const handleTestLatency = async () => {
    const tags = filteredNodes.map(n => n.tag);
    if (tags.length === 0) return;
    // 设置全局 loading 状态，不在此处清除——由信号回调追踪完成
    nodeStore.startLatencyTest(tags.length, false);
    toastStore.info(t('nodes.testing_group_latency', { count: tags.length }) ?? `Testing ${tags.length} nodes...`);
    const result = await callBridge('testLatency', JSON.stringify(tags));
    if (!result.ok) {
      toastStore.error(result.error?.message ?? t('common.error_test_latency'));
      // bridge 调用失败时立即清除（不会有后续信号）
      nodeStore.forceFinishLatencyTest();
    }
  };

  const handleAddGroup = async () => { showGroupModal.value = true; groupModalMode.value = 'add'; groupModalName.value = ''; };

  const handleGroupModalConfirm = async () => {
    const name = groupModalName.value.trim();
    if (!name) return;
    if (groupModalMode.value === 'add') {
      const result = await nodeStore.addGroup(name);
      if (!result.ok) { toastStore.error(result.error?.message ?? t('nodes.add_group_failed')); return; }
    } else if (groupModalMode.value === 'rename' && editingGroup.value) {
      const result = await callBridge('updateNodeGroup', editingGroup.value.id, JSON.stringify({ name }));
      if (!result.ok) { toastStore.error(result.error?.message ?? t('nodes.edit_group_failed')); return; }
      await nodeStore.fetchGroups();
    }
    showGroupModal.value = false;
  };

  const handleRenameGroup = async (groupId: string, currentName: string) => {
    editingGroup.value = {id: groupId, name: currentName};
    groupModalMode.value = 'rename';
    groupModalName.value = currentName;
    showGroupModal.value = true;
  };

  const handleDeleteGroup = async (groupId: string) => {
    showDeleteConfirm.value = true;
    deleteTargetId.value = groupId;
    deleteTargetType.value = 'group';
  };

  const handleConfirmDelete = async () => {
    if (deleteTargetType.value === 'group') {
      await nodeStore.deleteGroup(deleteTargetId.value);
      if (selectedGroup.value === deleteTargetId.value) selectedGroup.value = null;
    } else if (deleteTargetType.value === 'node') {
      await nodeStore.deleteNode(deleteTargetId.value);
    }
    showDeleteConfirm.value = false;
  };

  const handleToggleNode = async (nodeId: string, enabled: boolean) => {
    const result = await callBridge('updateNode', nodeId, JSON.stringify({ isEnabled: enabled }));
    if (!result.ok) toastStore.error(result.error?.message ?? t('common.operation_failed'));
    await nodeStore.fetchNodes();
  };

  const handleDeleteNode = async (nodeId: string) => {
    deleteTargetId.value = nodeId;
    deleteTargetType.value = 'node';
    showDeleteConfirm.value = true;
  };

  const handleAddNode = async () => {
    const name = newNodeName.value.trim();
    const address = newNodeAddress.value.trim();
    const port = parseInt(newNodePort.value);
    const protocol = newNodeProtocol.value;
    if (!name || !address) { toastStore.error(t('validation.required_fields')); return; }
    if (isNaN(port) || port <= 0 || port > 65535) { toastStore.error(t('validation.port_range')); return; }
    if (!VALID_PROTOCOLS.includes(protocol as any)) { toastStore.error(t('validation.invalid_protocol')); return; }
    let config: Record<string, any> = {};
    if (protocol === 'vmess' || protocol === 'vless') {
      if (!newNodeUuid.value.trim()) { toastStore.error(t('validation.required_fields')); return; }
      config = { uuid: newNodeUuid.value.trim() };
      if (newNodeTls.value === 'tls') { config.tls = true; if (newNodeSni.value.trim()) config.sni = newNodeSni.value.trim(); }
      else if (newNodeTls.value === 'reality' && protocol === 'vless') { config.tls = true; config.reality = true; if (newNodeSni.value.trim()) config.sni = newNodeSni.value.trim(); if (newNodeRealityPublicKey.value.trim()) config.realityPublicKey = newNodeRealityPublicKey.value.trim(); if (newNodeRealityShortId.value.trim()) config.realityShortId = newNodeRealityShortId.value.trim(); }
      config.network = newNodeTransport.value;
      if (newNodeTransport.value === 'ws') { config.wsPath = newNodeWsPath.value || '/'; if (newNodeWsHost.value.trim()) config.wsHeaders = { Host: newNodeWsHost.value.trim() }; }
      else if (newNodeTransport.value === 'grpc') { config.grpcServiceName = newNodeGrpcServiceName.value.trim(); }
    } else if (protocol === 'trojan' || protocol === 'hysteria2') {
      if (!newNodePassword.value.trim()) { toastStore.error(t('validation.required_fields')); return; }
      config = { password: newNodePassword.value.trim() };
      if (protocol === 'hysteria2') config.tls = true;
    } else if (protocol === 'shadowsocks') {
      if (!newNodePassword.value.trim()) { toastStore.error(t('validation.required_fields')); return; }
      config = { method: newNodeMethod.value, password: newNodePassword.value.trim() };
    } else if (protocol === 'wireguard') {
      if (!newNodePrivateKey.value.trim() || !newNodePeerPublicKey.value.trim()) { toastStore.error(t('validation.required_fields')); return; }
      const localAddrInput = newNodeLocalAddress.value.trim();
      const localAddresses = localAddrInput ? localAddrInput.split(',').map(s => s.trim()).filter(Boolean) : [];
      const cidrRegex = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\/\d{1,3}$/;
      if (localAddresses.length > 0 && !localAddresses.every(a => cidrRegex.test(a))) { toastStore.error(t('validation.invalid_cidr') ?? 'Invalid CIDR format'); return; }
      config = { privateKey: newNodePrivateKey.value.trim(), peerPublicKey: newNodePeerPublicKey.value.trim(), reserved: newNodeReserved.value.trim() ? newNodeReserved.value.trim().split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n)) : [], localAddress: localAddresses };
    } else if (protocol === 'tuic') {
      if (!newNodeUuid.value.trim() || !newNodePassword.value.trim()) { toastStore.error(t('validation.required_fields')); return; }
      config = {
        uuid: newNodeUuid.value.trim(),
        password: newNodePassword.value.trim(),
        tls: true,
        sni: newNodeSni.value.trim() || undefined,
        congestionControl: newNodeCongestionControl.value,
        udpRelayMode: newNodeUdpRelayMode.value,
        heartbeat: newNodeHeartbeat.value.trim() || undefined,
        alpn: newNodeAlpn.value.trim() || undefined,
        disableSni: newNodeDisableSni.value || undefined,
      };
    }
    // Mux/Brutal config (applies to all protocols except wireguard)
    if (newNodeMuxEnabled.value && newNodeProtocol.value !== 'wireguard') {
      config.muxEnabled = true;
      config.muxProtocol = newNodeMuxProtocol.value;
      config.muxMaxStreams = parseInt(newNodeMuxMaxStreams.value) || 8;
      config.muxPadding = newNodeMuxPadding.value;
      if (newNodeBrutalEnabled.value) {
        config.brutalEnabled = true;
        config.brutalSpeed = parseInt(newNodeBrutalSpeed.value) || 100;
      }
    }
    // Per-node uTLS fingerprint (overrides global setting)
    if (newNodeUtlsFingerprint.value) {
      config.utlsFingerprint = newNodeUtlsFingerprint.value;
    }
    try {
      const result = await nodeStore.addNode({ name, address, port, protocol, tag: `${protocol}-${(typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 14)}`).replace(/-/g, '').slice(0, 16)}`, groupId: selectedGroup.value, config });
      if (!result.ok) { toastStore.error(result.error?.message ?? t('nodes.add_node_failed')); return; }
      showAddModal.value = false;
      newNodeName.value = ''; newNodeAddress.value = ''; newNodePort.value = '443'; newNodeProtocol.value = 'vmess'; newNodeUuid.value = ''; newNodePassword.value = ''; newNodeMethod.value = 'aes-256-gcm'; newNodePrivateKey.value = ''; newNodePeerPublicKey.value = ''; newNodeTls.value = 'none'; newNodeSni.value = ''; newNodeRealityPublicKey.value = ''; newNodeRealityShortId.value = ''; newNodeTransport.value = 'tcp'; newNodeWsPath.value = '/'; newNodeWsHost.value = ''; newNodeGrpcServiceName.value = ''; newNodeReserved.value = ''; newNodeLocalAddress.value = ''; newNodeCongestionControl.value = 'bbr'; newNodeUdpRelayMode.value = 'native'; newNodeHeartbeat.value = '10s'; newNodeAlpn.value = ''; newNodeDisableSni.value = false; newNodeMuxEnabled.value = false; newNodeMuxProtocol.value = 'h2mux'; newNodeMuxMaxStreams.value = '8'; newNodeMuxPadding.value = false; newNodeBrutalEnabled.value = false; newNodeBrutalSpeed.value = '100'; newNodeUtlsFingerprint.value = '';
    } catch (e) { console.warn('[addNode]', e); toastStore.error(t('nodes.add_node_failed')); }
  };

  // 获取当前分组下的节点数（用于侧栏显示）
  const getNodeCountForGroup = (groupId: string | null) => {
    if (groupId === '__all__') return nodes.length;
    if (!groupId) return nodes.filter(n => !n.groupId).length;
    return nodes.filter(n => n.groupId === groupId).length;
  };

  return (
    <div class="h-screen overflow-hidden flex flex-col p-6 gap-4">
      {/* ─── 顶部工具栏（固定） ─── */}
      <div class="flex items-center gap-3 shrink-0">
        {/* 搜索框 */}
        <div class="relative flex-1 max-w-md">
          <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
          <input
            type="text"
            value={searchQuery.value}
            onInput={(e: any) => { searchQuery.value = e.target.value; }}
            placeholder={t('nodes.search_placeholder') ?? 'Search nodes...'}
            class="w-full pl-9 pr-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-sm text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all"
          />
        </div>
        {/* 协议过滤 */}
        <select
          value={protocolFilter.value}
          onChange={(e: any) => { protocolFilter.value = e.target.value; }}
          class="px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-sm text-gray-700 dark:text-gray-300 focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all"
        >
          <option value="">{t('nodes.all_protocols') ?? 'All Protocols'}</option>
          {VALID_PROTOCOLS.map(p => <option key={p} value={p}>{p.toUpperCase()}</option>)}
        </select>
        <div class="flex-1" />
        {/* 节点数 */}
        <span class="text-sm text-gray-500 dark:text-gray-400 whitespace-nowrap">{filteredNodes.length} / {nodes.length}</span>
        {/* 延迟测试按钮（统一：测试当前筛选的节点延迟） */}
        <button class="p-2 rounded-lg text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700/50 transition-colors disabled:opacity-40" disabled={nodeStore.isTestingLatency.value} onClick={handleTestLatency} title={t('nodes.test_latency')}>
          {nodeStore.isTestingLatency.value
            ? <div class="w-4 h-4 border-2 border-gray-400 border-t-gray-700 rounded-full animate-spin" />
            : <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg>
          }
        </button>
        <div class="w-px h-5 bg-gray-200 dark:bg-gray-700" />
        <Button variant="secondary" size="sm" onClick={handleAddGroup}>{t('nodes.add_group')}</Button>
        <Button variant="primary" size="sm" onClick={() => { showAddModal.value = true; }}>
          <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
          {t('nodes.add_node')}
        </Button>
      </div>

      <div class="flex gap-4 min-h-0 flex-1">
        {/* ─── 侧栏：分组 + 订阅（区域滚动） ─── */}
        <div class="w-56 shrink-0 space-y-3 overflow-y-auto">
          {/* 分组 */}
          <div class="bg-white dark:bg-gray-800/90 rounded-xl shadow-sm ring-1 ring-gray-100 dark:ring-gray-700/50 overflow-hidden">
            <div class="px-3 py-2.5 border-b border-gray-100 dark:border-gray-700/50">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">{t('nodes.groups') ?? 'Groups'}</h3>
            </div>
            <div class="p-1.5 space-y-0.5">
              <button
                class={`w-full text-left px-3 py-1.5 rounded-lg text-sm transition-all duration-150 flex items-center gap-2
                  ${selectedGroup.value === '__all__' ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 font-medium' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50'}`}
                onClick={() => { selectedGroup.value = '__all__'; }}
              >
                <svg class="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /></svg>
                <span class="flex-1">{t('nodes.all_nodes') ?? 'All'}</span>
                <span class="text-xs text-gray-400 dark:text-gray-500">{getNodeCountForGroup('__all__')}</span>
              </button>
              <button
                class={`w-full text-left px-3 py-1.5 rounded-lg text-sm transition-all duration-150 flex items-center gap-2
                  ${!selectedGroup.value ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 font-medium' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50'}`}
                onClick={() => { selectedGroup.value = null; }}
              >
                <svg class="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="16" /></svg>
                <span class="flex-1">{t('nodes.no_group')}</span>
                <span class="text-xs text-gray-400 dark:text-gray-500">{getNodeCountForGroup(null)}</span>
              </button>
              {groups.map(g => (
                <div key={g.id} class="flex items-center group">
                  <button
                    class={`flex-1 text-left px-3 py-1.5 rounded-lg text-sm transition-all duration-150 flex items-center gap-2
                      ${selectedGroup.value === g.id ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 font-medium' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50'}`}
                    onClick={() => { selectedGroup.value = g.id; }}
                  >
                    <svg class="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></svg>
                    <span class="flex-1 truncate">{g.name}</span>
                    <span class="text-xs text-gray-400 dark:text-gray-500">{getNodeCountForGroup(g.id)}</span>
                  </button>
                  <div class="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity pr-1">
                    <button class="p-0.5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700" onClick={() => handleRenameGroup(g.id, g.name)}>
                      <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
                    </button>
                    <button class="p-0.5 rounded text-gray-400 hover:text-red-500 dark:hover:text-red-400 hover:bg-gray-100 dark:hover:bg-gray-700" onClick={() => handleDeleteGroup(g.id)}>
                      <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* 订阅 */}
          <div class="bg-white dark:bg-gray-800/90 rounded-xl shadow-sm ring-1 ring-gray-100 dark:ring-gray-700/50 overflow-hidden">
            <div class="px-3 py-2.5 border-b border-gray-100 dark:border-gray-700/50 flex items-center justify-between">
              <h3 class="text-xs font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">{t('nodes.subscriptions')}</h3>
            </div>
            <div class="p-1.5 space-y-0.5">
              {subscriptions.length === 0 ? (
                <p class="text-xs text-gray-400 dark:text-gray-500 text-center py-3">{t('nodes.no_subscriptions')}</p>
              ) : subscriptions.map(sub => (
                <div key={sub.id} class="flex items-center group px-2 py-1.5 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors">
                  <div class="flex-1 min-w-0">
                    <p class="text-sm text-gray-700 dark:text-gray-300 truncate">{sub.name}</p>
                    <p class="text-[10px] text-gray-400 dark:text-gray-500">
                      {sub.nodeCount ?? 0} {t('nodes.nodes_count')}
                      {sub.autoUpdate && <span class="ml-1 text-green-500">Auto</span>}
                    </p>
                  </div>
                  <div class="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button class="p-1 rounded text-gray-400 hover:text-green-600 dark:hover:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/20" disabled={nodeStore.updatingSubId.value === sub.id} onClick={async () => {
                      nodeStore.startSubUpdate(sub.id);
                      const result = await callBridge('updateSubscription', sub.id);
                      if (!result.ok) { toastStore.error(result.error?.message ?? t('nodes.subscription_update_failed')); nodeStore.finishSubUpdate(); }
                    }} title={t('nodes.refresh_subscription')}>
                      {nodeStore.updatingSubId.value === sub.id
                        ? <div class="w-3 h-3 border-2 border-gray-400 border-t-green-600 rounded-full animate-spin" />
                        : <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" /></svg>
                      }
                    </button>
                    <button class="p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700" onClick={() => { editingSubId.value = sub.id; newSubName.value = sub.name; newSubUrl.value = sub.url; editingSubAutoUpdate.value = !!sub.autoUpdate; editingSubUpdateInterval.value = String(sub.updateInterval ?? 0); }} title="Edit">
                      <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
                    </button>
                    <button class="p-1 rounded text-gray-400 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20" onClick={async () => {
                      const result = await callBridge('deleteSubscription', sub.id);
                      if (result.ok) { await nodeStore.fetchSubscriptions(); await nodeStore.fetchNodes(); }
                      else toastStore.error(result.error?.message ?? t('common.operation_failed'));
                    }} title={t('action.delete')}>
                      <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
                    </button>
                  </div>
                </div>
              ))}
              {/* 添加/编辑订阅表单 */}
              <div class="pt-1.5 mt-1 border-t border-gray-100 dark:border-gray-700/50 space-y-1.5">
                <input value={newSubName.value} onInput={(e: any) => { newSubName.value = e.target.value; }} placeholder={t('nodes.subscription_name')} class="w-full px-2.5 py-1.5 text-xs rounded-md border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:ring-1 focus:ring-green-500/30 focus:border-green-500" />
                <input value={newSubUrl.value} onInput={(e: any) => { newSubUrl.value = e.target.value; }} placeholder={t('nodes.subscription_url')} class="w-full px-2.5 py-1.5 text-xs rounded-md border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:ring-1 focus:ring-green-500/30 focus:border-green-500" />
                <div class="flex items-center gap-2">
                  <label class="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
                    <input type="checkbox" checked={editingSubId.value ? editingSubAutoUpdate.value : newSubAutoUpdate.value} onChange={(e: any) => { if (editingSubId.value) editingSubAutoUpdate.value = e.target.checked; else newSubAutoUpdate.value = e.target.checked; }} class="h-3 w-3 rounded border-gray-300 text-green-600 dark:border-gray-600 dark:bg-gray-700" />
                    {t('nodes.auto_update')}
                  </label>
                </div>
                <div class="flex gap-1.5">
                  {editingSubId.value && (
                    <button class="px-2 py-1 text-xs rounded-md text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700" onClick={() => { editingSubId.value = null; newSubName.value = ''; newSubUrl.value = ''; newSubAutoUpdate.value = false; newSubUpdateInterval.value = '0'; }}>{t('action.cancel')}</button>
                  )}
                  <button class="flex-1 px-2 py-1 text-xs rounded-md bg-green-600 hover:bg-green-700 text-white font-medium transition-colors disabled:opacity-40" onClick={async () => {
                    const name = newSubName.value.trim();
                    const url = newSubUrl.value.trim();
                    if (!name || !url) { toastStore.error(t('validation.required_fields')); return; }
                    if (editingSubId.value) {
                      const autoUpdate = editingSubAutoUpdate.value;
                      const updateInterval = parseInt(editingSubUpdateInterval.value) || 0;
                      const result = await callBridge('updateSubscriptionMeta', editingSubId.value, JSON.stringify({ name, url, autoUpdate, updateInterval }));
                      if (result.ok) { editingSubId.value = null; newSubName.value = ''; newSubUrl.value = ''; newSubAutoUpdate.value = false; newSubUpdateInterval.value = '0'; await nodeStore.fetchSubscriptions(); }
                      else { toastStore.error(result.error?.message ?? t('nodes.subscription_update_failed')); }
                    } else {
                      const result = await callBridge('addSubscription', name, url);
                      if (result.ok) {
                        const autoUpdate = newSubAutoUpdate.value;
                        const updateInterval = parseInt(newSubUpdateInterval.value) || 0;
                        if (autoUpdate || updateInterval > 0) {
                          const subResult = await callBridge('listSubscriptions');
                          if (subResult.ok && subResult.data) {
                            const newSub = (subResult.data as any[]).find((s: any) => s.name === name && s.url === url);
                            if (newSub) { await callBridge('updateSubscriptionMeta', newSub.id, JSON.stringify({ autoUpdate, updateInterval })); }
                          }
                        }
                        newSubName.value = ''; newSubUrl.value = ''; newSubAutoUpdate.value = false; newSubUpdateInterval.value = '0';
                        await nodeStore.fetchSubscriptions();
                      }
                      else toastStore.error(result.error?.message ?? t('nodes.subscription_update_failed'));
                    }
                  }}>{editingSubId.value ? t('action.save') : t('nodes.add_subscription')}</button>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ─── 节点列表（区域滚动） ─── */}
        <div class="flex-1 min-w-0 overflow-y-auto">
          <div class="bg-white dark:bg-gray-800/90 rounded-xl shadow-sm ring-1 ring-gray-100 dark:ring-gray-700/50 overflow-hidden">
            {nodeStore.loading.value ? (
              <div class="text-center py-12 text-gray-500 dark:text-gray-400">
                <div class="w-6 h-6 border-2 border-gray-300 border-t-green-600 rounded-full animate-spin mx-auto mb-3" />
                {t('common.loading')}
              </div>
            ) : filteredNodes.length === 0 ? (
              <div class="text-center py-16">
                <svg class="w-14 h-14 mx-auto text-gray-200 dark:text-gray-700 mb-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><circle cx="12" cy="12" r="10" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /></svg>
                <p class="text-gray-400 dark:text-gray-500 text-sm">
                  {searchQuery.value || protocolFilter.value ? (t('nodes.no_matching_nodes') ?? 'No matching nodes') : t('nodes.no_nodes')}
                </p>
              </div>
            ) : (
              <div>
                {/* 全选栏 */}
                <div class="flex items-center gap-3 px-4 py-2.5 border-b border-gray-100 dark:border-gray-700/50 bg-gray-50/50 dark:bg-gray-800/50">
                  <input type="checkbox" checked={isAllSelected} ref={(el) => { if (el) el.indeterminate = isIndeterminate; }} onChange={handleToggleSelectAll} class="h-4 w-4 rounded border-gray-300 text-green-600 focus:ring-green-500 dark:border-gray-600 dark:bg-gray-700 cursor-pointer" />
                  <span class="text-xs text-gray-500 dark:text-gray-400">{t('nodes.select_all')}</span>
                  {selectedNodeIds.value.size > 0 && (
                    <>
                      <div class="w-px h-4 bg-gray-200 dark:bg-gray-700" />
                      <span class="text-xs text-green-600 dark:text-green-400 font-medium">{selectedNodeIds.value.size} selected</span>
                      <div class="flex gap-1">
                        <button class="px-2 py-0.5 text-xs rounded bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 hover:bg-green-100 dark:hover:bg-green-900/30 disabled:opacity-40 transition-colors" disabled={isBatchOperating.value} onClick={handleEnableSelected}>{t('nodes.enable_selected')}</button>
                        <button class="px-2 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-40 transition-colors" disabled={isBatchOperating.value} onClick={handleDisableSelected}>{t('nodes.disable_selected')}</button>
                        <button class="px-2 py-0.5 text-xs rounded bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-300 hover:bg-red-100 dark:hover:bg-red-900/30 disabled:opacity-40 transition-colors" disabled={isBatchOperating.value} onClick={handleDeleteSelected}>{t('nodes.delete_selected')}</button>
                        <button class="px-2 py-0.5 text-xs rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors" onClick={() => { selectedNodeIds.value = new Set(); }}>✕</button>
                      </div>
                    </>
                  )}
                </div>

                {/* 节点行列表 */}
                <div class="divide-y divide-gray-50 dark:divide-gray-700/30">
                  {filteredNodes.map(node => (
                    <div key={node.id} class={`flex items-center gap-3 px-4 py-2.5 transition-all duration-150 group
                      ${node.isEnabled
                        ? 'hover:bg-gray-50 dark:hover:bg-gray-700/20'
                        : 'opacity-50 hover:opacity-75'}`}>
                      <input type="checkbox" checked={selectedNodeIds.value.has(node.id)} onChange={() => handleToggleSelectNode(node.id)} class="h-3.5 w-3.5 rounded border-gray-300 text-green-600 focus:ring-green-500 dark:border-gray-600 dark:bg-gray-700 cursor-pointer shrink-0" />
                      <Switch checked={node.isEnabled} onChange={(v) => handleToggleNode(node.id, v)} />
                      <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2">
                          <p class="font-medium text-sm text-gray-900 dark:text-gray-100 truncate">{node.name}</p>
                          <span class={`shrink-0 px-1.5 py-0.5 text-[10px] rounded font-semibold uppercase ${PROTOCOL_COLORS[node.protocol] || 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'}`}>{node.protocol}</span>
                        </div>
                        <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5 font-mono truncate">{node.address}:{node.port}</p>
                      </div>
                      <div class="flex items-center gap-2.5 shrink-0">
                        <span class={`text-xs font-medium ${node.latency != null && node.latency >= 0 ? (node.latency < 200 ? 'text-green-600 dark:text-green-400' : node.latency < 500 ? 'text-amber-600 dark:text-amber-400' : 'text-red-500 dark:text-red-400') : 'text-gray-400 dark:text-gray-500'}`}>{formatLatency(node.latency)}</span>
                        {node.speed != null && node.speed > 0 && (
                          <span class="text-xs text-green-600 dark:text-green-400 font-medium">{formatSpeed(node.speed)}</span>
                        )}
                        <button class="p-1 rounded text-gray-300 hover:text-red-500 dark:text-gray-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 opacity-0 group-hover:opacity-100 transition-all" onClick={() => handleDeleteNode(node.id)}>
                          <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 添加节点 Modal */}
      <Modal title={t('nodes.add_node')} open={showAddModal.value} onClose={() => { showAddModal.value = false; }} onConfirm={handleAddNode}>
        <div class="space-y-3">
          <div>
            <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.name')}</label>
            <Input value={newNodeName.value} onInput={(e: any) => { newNodeName.value = e.target.value; }} placeholder="Node Name" />
          </div>
          <div>
            <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.address')}</label>
            <Input value={newNodeAddress.value} onInput={(e: any) => { newNodeAddress.value = e.target.value; }} placeholder="server.example.com" />
          </div>
          <div class="flex gap-3">
            <div class="flex-1">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.port')}</label>
              <Input value={newNodePort.value} onInput={(e: any) => { newNodePort.value = e.target.value; }} placeholder="443" />
            </div>
            <div class="flex-1">
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.protocol')}</label>
              <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={newNodeProtocol.value} onChange={(e: any) => { newNodeProtocol.value = e.target.value; }}>
                {VALID_PROTOCOLS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          </div>
          {(newNodeProtocol.value === 'vmess' || newNodeProtocol.value === 'vless') && (
            <div class="space-y-3">
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.uuid')}</label>
                <Input value={newNodeUuid.value} onInput={(e: any) => { newNodeUuid.value = e.target.value; }} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
              </div>
              <div class="flex gap-3">
                <div class="flex-1">
                  <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.tls')}</label>
                  <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={newNodeTls.value} onChange={(e: any) => { newNodeTls.value = e.target.value; }}>
                    <option value="none">None</option>
                    <option value="tls">TLS</option>
                    {newNodeProtocol.value === 'vless' && <option value="reality">Reality</option>}
                  </select>
                </div>
                <div class="flex-1">
                  <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.transport')}</label>
                  <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={newNodeTransport.value} onChange={(e: any) => { newNodeTransport.value = e.target.value; }}>
                    <option value="tcp">TCP</option>
                    <option value="ws">WebSocket</option>
                    <option value="grpc">gRPC</option>
                  </select>
                </div>
              </div>
              {newNodeTls.value !== 'none' && (
                <div>
                  <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.sni')}</label>
                  <Input value={newNodeSni.value} onInput={(e: any) => { newNodeSni.value = e.target.value; }} placeholder="example.com" />
                </div>
              )}
              {newNodeTls.value === 'reality' && (
                <div class="space-y-3">
                  <div>
                    <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.reality_public_key')}</label>
                    <Input value={newNodeRealityPublicKey.value} onInput={(e: any) => { newNodeRealityPublicKey.value = e.target.value; }} placeholder="base64-encoded public key" />
                  </div>
                  <div>
                    <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.reality_short_id')}</label>
                    <Input value={newNodeRealityShortId.value} onInput={(e: any) => { newNodeRealityShortId.value = e.target.value; }} placeholder="hex short ID" />
                  </div>
                </div>
              )}
              {newNodeTransport.value === 'ws' && (
                <div class="space-y-3">
                  <div>
                    <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.ws_path')}</label>
                    <Input value={newNodeWsPath.value} onInput={(e: any) => { newNodeWsPath.value = e.target.value; }} placeholder="/" />
                  </div>
                  <div>
                    <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.ws_host')}</label>
                    <Input value={newNodeWsHost.value} onInput={(e: any) => { newNodeWsHost.value = e.target.value; }} placeholder="example.com" />
                  </div>
                </div>
              )}
              {newNodeTransport.value === 'grpc' && (
                <div>
                  <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.grpc_service_name')}</label>
                  <Input value={newNodeGrpcServiceName.value} onInput={(e: any) => { newNodeGrpcServiceName.value = e.target.value; }} placeholder="service_name" />
                </div>
              )}
            </div>
          )}
          {(newNodeProtocol.value === 'trojan' || newNodeProtocol.value === 'hysteria2' || newNodeProtocol.value === 'shadowsocks') && (
            <div>
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.password')}</label>
              <Input value={newNodePassword.value} onInput={(e: any) => { newNodePassword.value = e.target.value; }} placeholder={t('nodes.password')} />
            </div>
          )}
          {newNodeProtocol.value === 'shadowsocks' && (
            <div>
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.method')}</label>
              <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={newNodeMethod.value} onChange={(e: any) => { newNodeMethod.value = e.target.value; }}>
                {['aes-256-gcm', 'aes-128-gcm', 'chacha20-ietf-poly1305', 'xchacha20-ietf-poly1305', '2022-blake3-aes-128-gcm', '2022-blake3-aes-256-gcm'].map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
          )}
          {newNodeProtocol.value === 'wireguard' && (
            <div class="space-y-3">
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.private_key')}</label>
                <Input value={newNodePrivateKey.value} onInput={(e: any) => { newNodePrivateKey.value = e.target.value; }} placeholder="base64-encoded private key" />
              </div>
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.peer_public_key')}</label>
                <Input value={newNodePeerPublicKey.value} onInput={(e: any) => { newNodePeerPublicKey.value = e.target.value; }} placeholder="base64-encoded peer public key" />
              </div>
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.reserved')}</label>
                <Input value={newNodeReserved.value} onInput={(e: any) => { newNodeReserved.value = e.target.value; }} placeholder="e.g. 16,28,144 (comma-separated, optional)" />
              </div>
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.local_address')}</label>
                <Input value={newNodeLocalAddress.value} onInput={(e: any) => { newNodeLocalAddress.value = e.target.value; }} placeholder="172.19.0.1/30 (comma-separated)" />
              </div>
            </div>
          )}
          {newNodeProtocol.value === 'tuic' && (
            <div class="space-y-3">
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.uuid')}</label>
                <Input value={newNodeUuid.value} onInput={(e: any) => { newNodeUuid.value = e.target.value; }} placeholder="UUID" />
              </div>
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.password')}</label>
                <Input value={newNodePassword.value} onInput={(e: any) => { newNodePassword.value = e.target.value; }} placeholder="Password" />
              </div>
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.sni')}</label>
                <Input value={newNodeSni.value} onInput={(e: any) => { newNodeSni.value = e.target.value; }} placeholder="SNI" />
              </div>
              <div class="flex items-center gap-2">
                <Switch checked={newNodeDisableSni.value} onChange={(v: boolean) => { newNodeDisableSni.value = v; }} />
                <label class="text-sm text-gray-700 dark:text-gray-300">{t('nodes.disable_sni') ?? 'Disable SNI'}</label>
              </div>
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.congestion_control')}</label>
                <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={newNodeCongestionControl.value} onChange={(e: any) => { newNodeCongestionControl.value = e.target.value; }}>
                  <option value="cubic">cubic</option>
                  <option value="new_reno">new_reno</option>
                  <option value="bbr">bbr</option>
                </select>
              </div>
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.udp_relay_mode')}</label>
                <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={newNodeUdpRelayMode.value} onChange={(e: any) => { newNodeUdpRelayMode.value = e.target.value; }}>
                  <option value="native">native</option>
                  <option value="quic">quic</option>
                </select>
              </div>
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">ALPN</label>
                <Input value={newNodeAlpn.value} onInput={(e: any) => { newNodeAlpn.value = e.target.value; }} placeholder="h3 (comma-separated, optional)" />
              </div>
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.heartbeat') ?? 'Heartbeat'}</label>
                <Input value={newNodeHeartbeat.value} onInput={(e: any) => { newNodeHeartbeat.value = e.target.value; }} placeholder="10s" />
              </div>
            </div>
          )}
          {/* Mux / Brutal (all protocols except wireguard) */}
          {newNodeProtocol.value !== 'wireguard' && (
            <div class="space-y-3 pt-3 border-t border-gray-100 dark:border-gray-700/50">
              <div class="flex items-center gap-2">
                <Switch checked={newNodeMuxEnabled.value} onChange={(v: boolean) => { newNodeMuxEnabled.value = v; }} />
                <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{t('nodes.mux')}</label>
              </div>
              {newNodeMuxEnabled.value && (
                <div class="space-y-3 pl-4 border-l-2 border-green-200 dark:border-green-800/40">
                  <div>
                    <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.mux_protocol')}</label>
                    <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={newNodeMuxProtocol.value} onChange={(e: any) => { newNodeMuxProtocol.value = e.target.value; }}>
                      <option value="h2mux">h2mux</option>
                      <option value="smux">smux</option>
                      <option value="yamux">yamux</option>
                    </select>
                  </div>
                  <div>
                    <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.mux_max_streams')}</label>
                    <Input value={newNodeMuxMaxStreams.value} onInput={(e: any) => { newNodeMuxMaxStreams.value = e.target.value; }} placeholder="8" />
                  </div>
                  <div class="flex items-center gap-2">
                    <Switch checked={newNodeMuxPadding.value} onChange={(v: boolean) => { newNodeMuxPadding.value = v; }} />
                    <label class="text-sm text-gray-700 dark:text-gray-300">{t('nodes.mux_padding')}</label>
                  </div>
                  <div class="flex items-center gap-2">
                    <Switch checked={newNodeBrutalEnabled.value} onChange={(v: boolean) => { newNodeBrutalEnabled.value = v; }} />
                    <label class="text-sm font-medium text-gray-700 dark:text-gray-300">{t('nodes.brutal')}</label>
                  </div>
                  {newNodeBrutalEnabled.value && (
                    <div>
                      <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.brutal_speed')}</label>
                      <Input value={newNodeBrutalSpeed.value} onInput={(e: any) => { newNodeBrutalSpeed.value = e.target.value; }} placeholder="100" />
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          {/* Per-node uTLS fingerprint */}
          {(newNodeProtocol.value === 'vmess' || newNodeProtocol.value === 'vless' || newNodeProtocol.value === 'trojan' || newNodeProtocol.value === 'hysteria2' || newNodeProtocol.value === 'tuic') && (
            <div class="space-y-3 pt-3 border-t border-gray-100 dark:border-gray-700/50">
              <div>
                <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.utls_fingerprint')}</label>
                <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={newNodeUtlsFingerprint.value} onChange={(e: any) => { newNodeUtlsFingerprint.value = e.target.value; }}>
                  <option value="">{t('settings.utls_fingerprint_auto')}</option>
                  <option value="chrome">chrome</option>
                  <option value="firefox">firefox</option>
                  <option value="edge">edge</option>
                  <option value="safari">safari</option>
                  <option value="ios">ios</option>
                  <option value="android">android</option>
                  <option value="random">random</option>
                  <option value="randomized">randomized</option>
                </select>
              </div>
            </div>
          )}
        </div>
      </Modal>

      {/* 分组 Modal */}
      <Modal title={groupModalMode.value === 'add' ? t('nodes.add_group') : t('nodes.edit_group')} open={showGroupModal.value} onClose={() => { showGroupModal.value = false; }} onConfirm={handleGroupModalConfirm}>
        <div>
          <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('nodes.group_name')}</label>
          <Input value={groupModalName.value} onInput={(e: any) => { groupModalName.value = e.target.value; }} placeholder={t('nodes.group_name')} />
        </div>
      </Modal>

      {/* 删除确认 Modal */}
      <Modal title={t('action.delete')} open={showDeleteConfirm.value} onClose={() => { showDeleteConfirm.value = false; }} onConfirm={handleConfirmDelete}>
        <div class="flex items-center gap-3 p-3 bg-red-50 dark:bg-red-900/20 rounded-lg border border-red-100 dark:border-red-800/30">
          <svg class="w-5 h-5 text-red-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" /></svg>
          <p class="text-sm text-red-700 dark:text-red-300">{deleteTargetType.value === 'group' ? t('nodes.confirm_delete_group') : t('nodes.confirm_delete_node')}</p>
        </div>
      </Modal>
    </div>
  );
}
