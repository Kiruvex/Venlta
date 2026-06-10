import { useSignal } from '@preact/signals';
import { useEffect } from 'preact/hooks';
import { useTranslation } from '../../i18n/useTranslation';
import { nodeStore } from '../../stores/nodeStore';
import { proxyStore } from '../../stores/proxyStore';
import { toastStore } from '../../stores/toastStore';
import { callBridge } from '../../lib/api';
import { Card } from '../../components/Card';
import { Button } from '../../components/Button';
import { Switch } from '../../components/Switch';
import { Modal } from '../../components/Modal';
import { Input } from '../../components/Input';
import { formatLatency, formatSpeed } from '../../lib/format';

const VALID_PROTOCOLS = ['vmess', 'vless', 'trojan', 'shadowsocks', 'hysteria2', 'wireguard'] as const;

const PROTOCOL_COLORS: Record<string, string> = {
  vmess: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  vless: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300',
  trojan: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
  shadowsocks: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  hysteria2: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  wireguard: 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300',
};

export function NodesPage() {
  const { t } = useTranslation();
  const selectedGroup = useSignal<string | null>(null);
  const showAddModal = useSignal(false);
  const newNodeName = useSignal('');
  const newNodeAddress = useSignal('');
  const newNodePort = useSignal('443');
  const newNodeProtocol = useSignal<'vmess' | 'vless' | 'trojan' | 'shadowsocks' | 'hysteria2' | 'wireguard'>('vmess');
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
  const editingGroup = useSignal<{id: string; name: string} | null>(null);
  const showGroupModal = useSignal(false);
  const groupModalMode = useSignal<'add' | 'rename'>('add');
  const groupModalName = useSignal('');
  const showDeleteConfirm = useSignal(false);
  const deleteTargetId = useSignal('');
  const deleteTargetType = useSignal<'group' | 'node'>('group');
  const newSubName = useSignal('');
  const newSubUrl = useSignal('');
  const updatingSubId = useSignal<string | null>(null);
  const editingSubId = useSignal<string | null>(null);
  const selectedNodeIds = useSignal<Set<string>>(new Set());

  useEffect(() => {
    nodeStore.fetchNodes();
    nodeStore.fetchGroups();
    nodeStore.fetchSubscriptions();
  }, []);

  const nodes = nodeStore.nodes.value;
  const groups = nodeStore.groups.value;
  const filteredNodes = selectedGroup.value
    ? nodes.filter(n => n.groupId === selectedGroup.value)
    : nodes;

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
    for (const id of ids) { await callBridge('updateNode', id, JSON.stringify({ isEnabled: true })); }
    selectedNodeIds.value = new Set();
    await nodeStore.fetchNodes();
  };

  const handleDisableSelected = async () => {
    const ids = [...selectedNodeIds.value];
    for (const id of ids) { await callBridge('updateNode', id, JSON.stringify({ isEnabled: false })); }
    selectedNodeIds.value = new Set();
    await nodeStore.fetchNodes();
  };

  const handleDeleteSelected = async () => {
    const ids = [...selectedNodeIds.value];
    for (const id of ids) { await callBridge('deleteNode', id); }
    selectedNodeIds.value = new Set();
    await nodeStore.fetchNodes();
  };

  const handleTestLatency = async () => {
    const tags = filteredNodes.map(n => n.tag);
    if (tags.length === 0) return;
    toastStore.info(t('nodes.testing_group_latency', { count: tags.length }) ?? `Testing ${tags.length} nodes...`);
    const result = await callBridge('testLatency', JSON.stringify(tags));
    if (!result.ok) toastStore.error(result.error?.message ?? t('common.error_test_latency'));
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
    }
    try {
      const result = await nodeStore.addNode({ name, address, port, protocol, tag: `${protocol}-${(typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 14)}`).replace(/-/g, '').slice(0, 16)}`, groupId: selectedGroup.value, config });
      if (!result.ok) { toastStore.error(result.error?.message ?? t('nodes.add_node_failed')); return; }
      showAddModal.value = false;
      newNodeName.value = ''; newNodeAddress.value = ''; newNodePort.value = '443'; newNodeProtocol.value = 'vmess'; newNodeUuid.value = ''; newNodePassword.value = ''; newNodeMethod.value = 'aes-256-gcm'; newNodePrivateKey.value = ''; newNodePeerPublicKey.value = ''; newNodeTls.value = 'none'; newNodeSni.value = ''; newNodeRealityPublicKey.value = ''; newNodeRealityShortId.value = ''; newNodeTransport.value = 'tcp'; newNodeWsPath.value = '/'; newNodeWsHost.value = ''; newNodeGrpcServiceName.value = ''; newNodeReserved.value = ''; newNodeLocalAddress.value = '';
    } catch (e) { console.warn('[addNode]', e); toastStore.error(t('nodes.add_node_failed')); }
  };

  return (
    <div class="p-6 space-y-5">
      {/* 页面标题 + 操作按钮 */}
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-xl font-bold text-gray-900 dark:text-gray-100">{t('nodes.title')}</h2>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{filteredNodes.length} {t('nodes.nodes_count')}</p>
        </div>
        <div class="flex gap-2">
          <Button variant="ghost" size="sm" onClick={handleTestLatency}>
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg>
            {t('nodes.test_latency')}
          </Button>
          <Button variant="ghost" size="sm" onClick={async () => {
            const allTags = nodeStore.nodes.value.map(n => n.tag);
            if (allTags.length === 0) return;
            const result = await callBridge('testLatency', JSON.stringify(allTags));
            if (!result.ok) toastStore.error(result.error?.message ?? t('common.error_test_latency'));
          }}>{t('nodes.test_all_latency')}</Button>
          <Button variant="ghost" size="sm" onClick={async () => {
            if (!proxyStore.state.value.isRunning) { toastStore.warning(t('nodes.speed_test_proxy_required') ?? 'Start proxy before speed test'); return; }
            const tags = filteredNodes.filter(n => n.isEnabled).map(n => n.tag);
            if (tags.length === 0) { toastStore.info(t('nodes.no_enabled_nodes') ?? 'No enabled nodes'); return; }
            const result = await callBridge('testSpeed', JSON.stringify(tags));
            if (!result.ok) toastStore.error(result.error?.message ?? t('nodes.test_speed_failed') ?? 'Speed test failed');
          }}>{t('nodes.test_speed')}</Button>
          <Button variant="secondary" size="sm" onClick={handleAddGroup}>{t('nodes.add_group')}</Button>
          <Button variant="primary" size="sm" onClick={() => { showAddModal.value = true; }}>
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
            {t('nodes.add_node')}
          </Button>
        </div>
      </div>

      <div class="flex gap-5">
        {/* 分组侧栏 */}
        <div class="w-52 shrink-0">
          <Card>
            <div class="space-y-0.5">
              <button
                class={`w-full text-left px-3 py-2 rounded-lg text-sm transition-all duration-150 flex items-center gap-2
                  ${!selectedGroup.value ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 font-medium' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50'}`}
                onClick={() => { selectedGroup.value = null; }}
              >
                <svg class="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /></svg>
                {t('nodes.no_group')}
              </button>
              {groups.map(g => (
                <div key={g.id} class="flex items-center group">
                  <button
                    class={`flex-1 text-left px-3 py-2 rounded-lg text-sm transition-all duration-150 flex items-center gap-2
                      ${selectedGroup.value === g.id ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 font-medium' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50'}`}
                    onClick={() => { selectedGroup.value = g.id; }}
                  >
                    <svg class="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></svg>
                    <span class="truncate">{g.name}</span>
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
          </Card>
        </div>

        {/* 节点列表 */}
        <div class="flex-1">
          <Card>
            {nodeStore.loading.value ? (
              <div class="text-center py-8 text-gray-500 dark:text-gray-400">{t('common.loading')}</div>
            ) : filteredNodes.length === 0 ? (
              <div class="text-center py-12">
                <svg class="w-12 h-12 mx-auto text-gray-300 dark:text-gray-600 mb-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /></svg>
                <p class="text-gray-400 dark:text-gray-500 text-sm">{t('nodes.no_nodes')}</p>
              </div>
            ) : (
              <div class="space-y-1.5">
                {/* 全选栏 */}
                <div class="flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-50 dark:bg-gray-700/30 border border-gray-100 dark:border-gray-700/50">
                  <input type="checkbox" checked={isAllSelected} ref={(el) => { if (el) el.indeterminate = isIndeterminate; }} onChange={handleToggleSelectAll} class="h-4 w-4 rounded border-gray-300 text-green-600 focus:ring-green-500 dark:border-gray-600 dark:bg-gray-700 cursor-pointer" />
                  <span class="text-sm text-gray-600 dark:text-gray-300">{t('nodes.select_all')}</span>
                </div>

                {/* 批量操作工具栏 */}
                {selectedNodeIds.value.size > 0 && (
                  <div class="flex items-center gap-2 px-3 py-2 bg-green-50 dark:bg-green-900/20 rounded-lg border border-green-100 dark:border-green-800/30 animate-fade-in">
                    <span class="text-sm text-green-700 dark:text-green-300 font-medium">{selectedNodeIds.value.size} selected</span>
                    <div class="w-px h-4 bg-green-200 dark:bg-green-800" />
                    <Button variant="ghost" size="sm" onClick={handleEnableSelected}>{t('nodes.enable_selected')}</Button>
                    <Button variant="ghost" size="sm" onClick={handleDisableSelected}>{t('nodes.disable_selected')}</Button>
                    <Button variant="ghost" size="sm" onClick={handleDeleteSelected}>{t('nodes.delete_selected')}</Button>
                    <Button variant="ghost" size="sm" onClick={() => { selectedNodeIds.value = new Set(); }}>{t('action.cancel')}</Button>
                  </div>
                )}

                {filteredNodes.map(node => (
                  <div key={node.id} class={`flex items-center justify-between px-3.5 py-2.5 rounded-xl transition-all duration-150 group
                    ${node.isEnabled
                      ? 'bg-gray-50 dark:bg-gray-700/30 hover:bg-gray-100 dark:hover:bg-gray-700/50 border border-gray-100 dark:border-gray-700/50'
                      : 'hover:bg-gray-50 dark:hover:bg-gray-700/20 border border-transparent opacity-60'}`}>
                    <div class="flex items-center gap-3">
                      <input type="checkbox" checked={selectedNodeIds.value.has(node.id)} onChange={() => handleToggleSelectNode(node.id)} class="h-4 w-4 rounded border-gray-300 text-green-600 focus:ring-green-500 dark:border-gray-600 dark:bg-gray-700 cursor-pointer" />
                      <Switch checked={node.isEnabled} onChange={(v) => handleToggleNode(node.id, v)} />
                      <div>
                        <div class="flex items-center gap-2">
                          <p class="font-medium text-sm text-gray-900 dark:text-gray-100">{node.name}</p>
                          <span class={`px-1.5 py-0.5 text-[10px] rounded-md font-semibold uppercase ${PROTOCOL_COLORS[node.protocol] || 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'}`}>{node.protocol}</span>
                        </div>
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 font-mono">{node.address}:{node.port}</p>
                      </div>
                    </div>
                    <div class="flex items-center gap-3">
                      <span class="text-xs text-gray-500 dark:text-gray-400">{formatLatency(node.latency)}</span>
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
            )}
          </Card>
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

      {/* 订阅管理 */}
      <Card title={t('nodes.subscriptions')}>
        <div class="space-y-3">
          {nodeStore.subscriptions.value.length === 0 ? (
            <div class="text-center py-6">
              <svg class="w-10 h-10 mx-auto text-gray-300 dark:text-gray-600 mb-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" /><polyline points="22,6 12,13 2,6" /></svg>
              <p class="text-gray-400 dark:text-gray-500 text-sm">{t('nodes.no_subscriptions')}</p>
            </div>
          ) : (
            <div class="space-y-1.5">
              {nodeStore.subscriptions.value.map(sub => (
                <div key={sub.id} class="flex items-center justify-between px-3.5 py-2.5 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors group border border-transparent hover:border-gray-100 dark:hover:border-gray-700/50">
                  <div>
                    <p class="font-medium text-sm text-gray-900 dark:text-gray-100">{sub.name}</p>
                    <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                      {sub.nodeCount ?? 0} {t('nodes.nodes_count')} · {sub.lastUpdate ? new Date(sub.lastUpdate).toLocaleDateString() : t('nodes.never_updated')}
                      {sub.autoUpdate && <span class="ml-1.5 text-green-500 dark:text-green-400">Auto</span>}
                    </p>
                  </div>
                  <div class="flex items-center gap-2">
                    <Button variant="secondary" size="sm" disabled={updatingSubId.value === sub.id} onClick={async () => {
                      updatingSubId.value = sub.id;
                      try { const result = await callBridge('updateSubscription', sub.id); if (!result.ok) toastStore.error(result.error?.message ?? t('nodes.subscription_update_failed')); }
                      finally { updatingSubId.value = null; }
                    }}>{updatingSubId.value === sub.id ? '...' : t('nodes.refresh_subscription')}</Button>
                    <button class="p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 opacity-0 group-hover:opacity-100 transition-all" onClick={() => { editingSubId.value = sub.id; newSubName.value = sub.name; newSubUrl.value = sub.url; }}>
                      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
                    </button>
                    <button class="p-1 rounded text-gray-400 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 opacity-0 group-hover:opacity-100 transition-all" onClick={async () => {
                      const result = await callBridge('deleteSubscription', sub.id);
                      if (result.ok) { await nodeStore.fetchSubscriptions(); await nodeStore.fetchNodes(); }
                      else toastStore.error(result.error?.message ?? t('common.operation_failed'));
                    }}>
                      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div class="flex gap-2 pt-2 border-t border-gray-100 dark:border-gray-700/50">
            <Input value={newSubName.value} onInput={(e: any) => { newSubName.value = e.target.value; }} placeholder={t('nodes.subscription_name')} class="flex-1" />
            <Input value={newSubUrl.value} onInput={(e: any) => { newSubUrl.value = e.target.value; }} placeholder={t('nodes.subscription_url')} class="flex-1" />
            {editingSubId.value && (
              <Button variant="ghost" size="sm" onClick={() => { editingSubId.value = null; newSubName.value = ''; newSubUrl.value = ''; }}>{t('action.cancel')}</Button>
            )}
            <Button variant="primary" size="sm" onClick={async () => {
              const name = newSubName.value.trim();
              const url = newSubUrl.value.trim();
              if (!name || !url) { toastStore.error(t('validation.required_fields')); return; }
              if (editingSubId.value) {
                const result = await callBridge('updateSubscriptionMeta', editingSubId.value, JSON.stringify({ name, url }));
                if (result.ok) { editingSubId.value = null; newSubName.value = ''; newSubUrl.value = ''; await nodeStore.fetchSubscriptions(); }
                else { toastStore.error(result.error?.message ?? t('nodes.subscription_update_failed')); }
              } else {
                const result = await callBridge('addSubscription', name, url);
                if (result.ok) { newSubName.value = ''; newSubUrl.value = ''; await nodeStore.fetchSubscriptions(); }
                else toastStore.error(result.error?.message ?? t('nodes.subscription_update_failed'));
              }
            }}>{editingSubId.value ? t('action.save') : t('nodes.add_subscription')}</Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
