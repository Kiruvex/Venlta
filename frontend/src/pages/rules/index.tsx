import { useSignal } from '@preact/signals';
import { useEffect } from 'preact/hooks';
import { useTranslation } from '../../i18n/useTranslation';
import { callBridge } from '../../lib/api';
import { toastStore } from '../../stores/toastStore';
import { Card } from '../../components/Card';
import { Button } from '../../components/Button';
import { Switch } from '../../components/Switch';
import { Modal } from '../../components/Modal';
import { Input } from '../../components/Input';

const OUTBOUND_OPTIONS = ['proxy', 'direct', 'block', 'auto'] as const;
const NETWORK_OPTIONS = ['', 'tcp', 'udp'] as const;
const ACTION_OPTIONS = ['route', 'reject', 'sniff', 'resolve', 'hijack-dns'] as const;
const REJECT_METHOD_OPTIONS = ['default', 'conn-reset'] as const;
const RESOLVE_SERVER_OPTIONS = ['dns-remote', 'dns-direct', 'dns-local'] as const;

const OUTBOUND_COLORS: Record<string, string> = {
  proxy: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
  direct: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  block: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  auto: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
};

const ACTION_COLORS: Record<string, string> = {
  route: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
  reject: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  sniff: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  resolve: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  'hijack-dns': 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
};

const arrToStr = (v: string[] | string | undefined): string =>
  Array.isArray(v) ? v.join(', ') : (v || '');

const strToArr = (v: string): string[] =>
  v.split(',').map(s => s.trim()).filter(Boolean);

export function RulesPage() {
  const { t } = useTranslation();
  const rules = useSignal<any[]>([]);
  const ruleSets = useSignal<any[]>([]);
  const showAddModal = useSignal(false);
  const showEditModal = useSignal(false);
  const editingRuleId = useSignal('');

  const newRuleName = useSignal('');
  const newRuleAction = useSignal<'route' | 'reject' | 'sniff' | 'resolve' | 'hijack-dns'>('route');
  const newRuleOutbound = useSignal('proxy');
  const newRuleRejectMethod = useSignal<'default' | 'conn-reset'>('default');
  const newRuleResolveServer = useSignal('dns-direct');
  const newRuleDomain = useSignal('');
  const newRuleDomainSuffix = useSignal('');
  const newRuleDomainKeyword = useSignal('');
  const newRuleIpCidr = useSignal('');
  const newRuleSourceIpCidr = useSignal('');
  const newRuleProcessName = useSignal('');
  const newRuleProcessPath = useSignal('');
  const newRulePort = useSignal('');
  const newRuleSourcePort = useSignal('');
  const newRuleSourcePortRange = useSignal('');
  const newRuleNetwork = useSignal('');
  const newRuleRuleSetId = useSignal('');
  const newRuleInvert = useSignal(false);
  const showAddAdvanced = useSignal(false);

  const editRuleName = useSignal('');
  const editRuleAction = useSignal<'route' | 'reject' | 'sniff' | 'resolve' | 'hijack-dns'>('route');
  const editRuleOutbound = useSignal('proxy');
  const editRuleRejectMethod = useSignal<'default' | 'conn-reset'>('default');
  const editRuleResolveServer = useSignal('dns-direct');
  const editRuleDomain = useSignal('');
  const editRuleDomainSuffix = useSignal('');
  const editRuleDomainKeyword = useSignal('');
  const editRuleIpCidr = useSignal('');
  const editRuleSourceIpCidr = useSignal('');
  const editRuleProcessName = useSignal('');
  const editRuleProcessPath = useSignal('');
  const editRulePort = useSignal('');
  const editRuleSourcePort = useSignal('');
  const editRuleSourcePortRange = useSignal('');
  const editRuleNetwork = useSignal('');
  const editRuleRuleSetId = useSignal('');
  const editRuleInvert = useSignal(false);
  const showEditAdvanced = useSignal(false);

  const showAddRuleSetModal = useSignal(false);
  const newRuleSetName = useSignal('');
  const newRuleSetTag = useSignal('');
  const newRuleSetUrl = useSignal('');
  const newRuleSetFormat = useSignal<'binary' | 'source'>('binary');
  const newRuleSetType = useSignal<'remote' | 'local'>('remote');

  const fetchRules = async () => {
    const result = await callBridge<any[]>('listRules');
    if (result.ok && result.data) rules.value = result.data;
  };

  const fetchRuleSets = async () => {
    const result = await callBridge<any[]>('listRuleSets');
    if (result.ok && result.data) ruleSets.value = result.data;
  };

  useEffect(() => { fetchRules(); fetchRuleSets(); }, []);

  const handleToggle = async (ruleId: string, enabled: boolean) => {
    const result = await callBridge('updateRule', ruleId, JSON.stringify({ isEnabled: enabled }));
    if (!result.ok) toastStore.error(result.error?.message ?? t('common.operation_failed'));
    await fetchRules();
  };

  const handleDelete = async (ruleId: string) => {
    const result = await callBridge('deleteRule', ruleId);
    if (!result.ok) toastStore.error(result.error?.message ?? t('common.operation_failed'));
    await fetchRules();
  };

  const buildAdvancedFields = (domainSuffix: string, domainKeyword: string, ipCidr: string, sourceIpCidr: string, processName: string, processPath: string, port: string, sourcePort: string, sourcePortRange: string, network: string, ruleSetId: string, invert: boolean): Record<string, any> => {
    const data: Record<string, any> = {};
    const arr = strToArr;
    if (arr(domainSuffix).length) data.domainSuffix = arr(domainSuffix);
    if (arr(domainKeyword).length) data.domainKeyword = arr(domainKeyword);
    if (arr(ipCidr).length) data.ipCidr = arr(ipCidr);
    if (arr(sourceIpCidr).length) data.sourceIpCidr = arr(sourceIpCidr);
    if (arr(processName).length) data.processName = arr(processName);
    if (arr(processPath).length) data.processPath = arr(processPath);
    if (port.trim()) data.port = Number(port.trim());
    if (sourcePort.trim()) data.sourcePort = sourcePort.trim();
    if (arr(sourcePortRange).length) data.sourcePortRange = arr(sourcePortRange);
    if (network) data.network = network;
    if (ruleSetId) data.ruleSetId = ruleSetId;
    if (invert) data.invert = true;
    return data;
  };

  const resetAddFields = () => {
    newRuleName.value = ''; newRuleAction.value = 'route'; newRuleOutbound.value = 'proxy';
    newRuleRejectMethod.value = 'default'; newRuleResolveServer.value = 'dns-direct';
    newRuleDomain.value = '';
    newRuleDomainSuffix.value = ''; newRuleDomainKeyword.value = ''; newRuleIpCidr.value = '';
    newRuleSourceIpCidr.value = ''; newRuleProcessName.value = ''; newRuleProcessPath.value = '';
    newRulePort.value = ''; newRuleSourcePort.value = ''; newRuleSourcePortRange.value = '';
    newRuleNetwork.value = ''; newRuleRuleSetId.value = ''; newRuleInvert.value = false;
    showAddAdvanced.value = false;
  };

  const handleAddRule = async () => {
    const name = newRuleName.value.trim();
    const action = newRuleAction.value;
    const domain = newRuleDomain.value.trim();
    if (!action) { toastStore.error(t('validation.required_fields')); return; }
    const ruleData: Record<string, any> = { name, action, isEnabled: true };
    // route action 需要 outboundTag
    if (action === 'route') {
      ruleData.outboundTag = newRuleOutbound.value;
    }
    // reject action 可选 rejectMethod
    if (action === 'reject' && newRuleRejectMethod.value !== 'default') {
      ruleData.rejectMethod = newRuleRejectMethod.value;
    }
    // resolve action 需要 resolveServer
    if (action === 'resolve') {
      ruleData.resolveServer = newRuleResolveServer.value;
    }
    // route/reject/resolve 需要匹配条件
    if (action === 'route' || action === 'reject' || action === 'resolve') {
      if (domain) ruleData.domain = strToArr(domain);
      const advanced = buildAdvancedFields(newRuleDomainSuffix.value, newRuleDomainKeyword.value, newRuleIpCidr.value, newRuleSourceIpCidr.value, newRuleProcessName.value, newRuleProcessPath.value, newRulePort.value, newRuleSourcePort.value, newRuleSourcePortRange.value, newRuleNetwork.value, newRuleRuleSetId.value, newRuleInvert.value);
      Object.assign(ruleData, advanced);
    }
    const result = await callBridge('addRule', JSON.stringify(ruleData));
    if (result.ok) { showAddModal.value = false; resetAddFields(); await fetchRules(); }
    else { toastStore.error(result.error?.message ?? t('rules.add_rule_failed')); }
  };

  const handleEditRule = (rule: any) => {
    editingRuleId.value = rule.id;
    editRuleName.value = rule.name || '';
    editRuleAction.value = rule.action || 'route';
    editRuleOutbound.value = rule.outboundTag || 'proxy';
    editRuleRejectMethod.value = rule.rejectMethod || 'default';
    editRuleResolveServer.value = rule.resolveServer || 'dns-direct';
    editRuleDomain.value = arrToStr(rule.domain);
    editRuleDomainSuffix.value = arrToStr(rule.domainSuffix);
    editRuleDomainKeyword.value = arrToStr(rule.domainKeyword);
    editRuleIpCidr.value = arrToStr(rule.ipCidr);
    editRuleSourceIpCidr.value = arrToStr(rule.sourceIpCidr);
    editRuleProcessName.value = arrToStr(rule.processName);
    editRuleProcessPath.value = arrToStr(rule.processPath);
    editRulePort.value = rule.port != null ? String(rule.port) : '';
    editRuleSourcePort.value = rule.sourcePort || '';
    editRuleSourcePortRange.value = arrToStr(rule.sourcePortRange);
    editRuleNetwork.value = rule.network || '';
    editRuleRuleSetId.value = rule.ruleSetId || '';
    editRuleInvert.value = !!rule.invert;
    showEditAdvanced.value = false;
    showEditModal.value = true;
  };

  const handleSaveEditRule = async () => {
    const name = editRuleName.value.trim();
    const action = editRuleAction.value;
    const domain = editRuleDomain.value.trim();
    const updates: Record<string, any> = { name, action };
    if (action === 'route') {
      updates.outboundTag = editRuleOutbound.value;
    }
    if (action === 'reject') {
      updates.rejectMethod = editRuleRejectMethod.value;
    }
    if (action === 'resolve') {
      updates.resolveServer = editRuleResolveServer.value;
    }
    if (action === 'route' || action === 'reject' || action === 'resolve') {
      if (domain) updates.domain = strToArr(domain); else updates.domain = [];
      const advanced = buildAdvancedFields(editRuleDomainSuffix.value, editRuleDomainKeyword.value, editRuleIpCidr.value, editRuleSourceIpCidr.value, editRuleProcessName.value, editRuleProcessPath.value, editRulePort.value, editRuleSourcePort.value, editRuleSourcePortRange.value, editRuleNetwork.value, editRuleRuleSetId.value, editRuleInvert.value);
      Object.assign(updates, advanced);
    }
    const result = await callBridge('updateRule', editingRuleId.value, JSON.stringify(updates));
    if (result.ok) { showEditModal.value = false; await fetchRules(); }
    else { toastStore.error(result.error?.message ?? t('rules.update_rule_failed')); }
  };

  const renderAdvancedFields = (domainSuffix: { value: string }, domainKeyword: { value: string }, ipCidr: { value: string }, sourceIpCidr: { value: string }, processName: { value: string }, processPath: { value: string }, port: { value: string }, sourcePort: { value: string }, sourcePortRange: { value: string }, network: { value: string }, ruleSetId: { value: string }, invert: { value: boolean }) => {
    const input = (labelKey: string, sig: { value: string }, placeholder: string, setter: (v: string) => void) => (
      <div>
        <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t(labelKey)}</label>
        <Input value={sig.value} onInput={(e: any) => setter(e.target.value)} placeholder={placeholder} />
      </div>
    );

    return (
      <div class="space-y-3 mt-3 pt-3 border-t border-gray-200/80 dark:border-gray-600/50">
        {input('rules.domain_suffix', domainSuffix, '.example.com, .test.com', (v) => { domainSuffix.value = v; })}
        {input('rules.domain_keyword', domainKeyword, 'google, facebook', (v) => { domainKeyword.value = v; })}
        {input('rules.ip_cidr', ipCidr, '10.0.0.0/8, 192.168.0.0/16', (v) => { ipCidr.value = v; })}
        {input('rules.source_ip_cidr', sourceIpCidr, '192.168.1.0/24', (v) => { sourceIpCidr.value = v; })}
        {input('rules.process_name', processName, 'chrome, firefox', (v) => { processName.value = v; })}
        {input('rules.process_path', processPath, '/usr/bin/curl', (v) => { processPath.value = v; })}
        <div>
          <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.port')}</label>
          <Input value={port.value} onInput={(e: any) => { port.value = e.target.value; }} placeholder="80" type="number" />
        </div>
        {input('rules.source_port', sourcePort, '8080', (v) => { sourcePort.value = v; })}
        {input('rules.source_port_range', sourcePortRange, '1000-2000, 3000-4000', (v) => { sourcePortRange.value = v; })}
        <div>
          <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.network')}</label>
          <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={network.value} onChange={(e: any) => { network.value = e.target.value; }}>
            {NETWORK_OPTIONS.map(o => <option key={o} value={o}>{o || '—'}</option>)}
          </select>
        </div>
        <div>
          <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.rule_set_id')}</label>
          <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={ruleSetId.value} onChange={(e: any) => { ruleSetId.value = e.target.value; }}>
            <option value="">—</option>
            {ruleSets.value.map(rs => <option key={rs.id} value={rs.id}>{rs.name || rs.tag}</option>)}
          </select>
        </div>
        <div class="flex items-center gap-2">
          <Switch checked={invert.value} onChange={(v: boolean) => { invert.value = v; }} />
          <label class="text-sm text-gray-600 dark:text-gray-400">{t('rules.invert')}</label>
        </div>
      </div>
    );
  };

  // 渲染 action 相关的配置选项（根据 action 类型动态显示）
  const renderActionFields = (
    action: { value: string },
    outbound: { value: string },
    rejectMethod: { value: string },
    resolveServer: { value: string },
  ) => (
    <div class="space-y-3">
      <div>
        <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.action')}</label>
        <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={action.value} onChange={(e: any) => { action.value = e.target.value; }}>
          {ACTION_OPTIONS.map(a => <option key={a} value={a}>{t(`rules.action_${a.replace('-', '_')}`)}</option>)}
        </select>
      </div>
      {/* route action: 选择出站 */}
      {action.value === 'route' && (
        <div>
          <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.outbound')}</label>
          <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={outbound.value} onChange={(e: any) => { outbound.value = e.target.value; }}>
            {OUTBOUND_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
      )}
      {/* reject action: 选择拒绝方式 */}
      {action.value === 'reject' && (
        <div>
          <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.reject_method')}</label>
          <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={rejectMethod.value} onChange={(e: any) => { rejectMethod.value = e.target.value; }}>
            {REJECT_METHOD_OPTIONS.map(m => <option key={m} value={m}>{t(`rules.reject_method_${m.replace('-', '_')}`)}</option>)}
          </select>
        </div>
      )}
      {/* resolve action: 选择 DNS 服务器 */}
      {action.value === 'resolve' && (
        <div>
          <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.resolve_server')}</label>
          <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={resolveServer.value} onChange={(e: any) => { resolveServer.value = e.target.value; }}>
            {RESOLVE_SERVER_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      )}
      {/* sniff/hijack-dns action 提示信息 */}
      {action.value === 'sniff' && (
        <p class="text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-700/30 rounded-lg px-3 py-2">{t('rules.sniff_hint')}</p>
      )}
      {action.value === 'hijack-dns' && (
        <p class="text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-700/30 rounded-lg px-3 py-2">{t('rules.hijack_dns_hint')}</p>
      )}
    </div>
  );

  // 获取规则在列表中显示的标签
  const getRuleBadge = (rule: any) => {
    const action = rule.action || 'route';
    if (action === 'route') {
      return { text: rule.outboundTag || 'route', cls: OUTBOUND_COLORS[rule.outboundTag] || ACTION_COLORS.route };
    }
    return { text: t(`rules.action_${action.replace('-', '_')}`), cls: ACTION_COLORS[action] || 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400' };
  };

  return (
    <div class="p-6 space-y-5">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-xl font-bold text-gray-900 dark:text-gray-100">{t('rules.title')}</h2>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{rules.value.length} {t('rules.title').toLowerCase()}</p>
        </div>
        <Button variant="primary" size="sm" onClick={() => { showAddModal.value = true; }}>
          <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
          {t('rules.add_rule')}
        </Button>
      </div>
      <Card>
        {rules.value.length === 0 ? (
          <div class="text-center py-12">
            <svg class="w-12 h-12 mx-auto text-gray-300 dark:text-gray-600 mb-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" /></svg>
            <p class="text-gray-400 dark:text-gray-500 text-sm">{t('rules.no_rules')}</p>
          </div>
        ) : (
          <div class="space-y-1.5">
            {rules.value.map(rule => {
              const badge = getRuleBadge(rule);
              return (
                <div key={rule.id} class={`flex items-center justify-between px-3.5 py-2.5 rounded-xl transition-all duration-150 group
                  ${rule.isEnabled
                    ? 'bg-gray-50 dark:bg-gray-700/30 hover:bg-gray-100 dark:hover:bg-gray-700/50 border border-gray-100 dark:border-gray-700/50'
                    : 'hover:bg-gray-50 dark:hover:bg-gray-700/20 border border-transparent opacity-60'}`}>
                  <div class="flex items-center gap-3">
                    <Switch checked={rule.isEnabled} onChange={(v) => handleToggle(rule.id, v)} />
                    <div>
                      <div class="flex items-center gap-2">
                        <p class="font-medium text-sm text-gray-900 dark:text-gray-100">{rule.name || badge.text}</p>
                        <span class={`px-1.5 py-0.5 text-[10px] rounded-md font-semibold uppercase ${badge.cls}`}>{badge.text}</span>
                      </div>
                      {rule.domain && rule.domain.length > 0 && (
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 truncate max-w-md">{rule.domain.join(', ')}</p>
                      )}
                    </div>
                  </div>
                  <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button class="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-all" onClick={() => handleEditRule(rule)}>
                      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>
                    </button>
                    <button class="p-1.5 rounded-lg text-gray-400 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 transition-all" onClick={() => handleDelete(rule.id)}>
                      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* Add Rule Modal */}
      <Modal title={t('rules.add_rule')} open={showAddModal.value} onClose={() => { showAddModal.value = false; }} onConfirm={handleAddRule}>
        <div class="space-y-3">
          <div>
            <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.rule_name')}</label>
            <Input value={newRuleName.value} onInput={(e: any) => { newRuleName.value = e.target.value; }} placeholder={t('rules.rule_name')} />
          </div>
          {renderActionFields(newRuleAction, newRuleOutbound, newRuleRejectMethod, newRuleResolveServer)}
          {/* route/reject/resolve action 需要匹配条件 */}
          {(newRuleAction.value === 'route' || newRuleAction.value === 'reject' || newRuleAction.value === 'resolve') && (
            <div>
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.domain')}</label>
              <Input value={newRuleDomain.value} onInput={(e: any) => { newRuleDomain.value = e.target.value; }} placeholder="example.com, test.com" />
            </div>
          )}
          {(newRuleAction.value === 'route' || newRuleAction.value === 'reject' || newRuleAction.value === 'resolve') && (
            <>
              <button type="button" class="flex items-center gap-1.5 text-sm text-green-600 hover:text-green-700 dark:text-green-400 dark:hover:text-green-300 mt-1 font-medium" onClick={() => { showAddAdvanced.value = !showAddAdvanced.value; }}>
                <svg class={`w-3 h-3 transition-transform ${showAddAdvanced.value ? 'rotate-90' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6" /></svg>
                {t('rules.advanced')}
              </button>
              {showAddAdvanced.value && renderAdvancedFields(newRuleDomainSuffix, newRuleDomainKeyword, newRuleIpCidr, newRuleSourceIpCidr, newRuleProcessName, newRuleProcessPath, newRulePort, newRuleSourcePort, newRuleSourcePortRange, newRuleNetwork, newRuleRuleSetId, newRuleInvert)}
            </>
          )}
        </div>
      </Modal>

      {/* Edit Rule Modal */}
      <Modal title={t('rules.edit_rule')} open={showEditModal.value} onClose={() => { showEditModal.value = false; }} onConfirm={handleSaveEditRule}>
        <div class="space-y-3">
          <div>
            <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.rule_name')}</label>
            <Input value={editRuleName.value} onInput={(e: any) => { editRuleName.value = e.target.value; }} placeholder={t('rules.rule_name')} />
          </div>
          {renderActionFields(editRuleAction, editRuleOutbound, editRuleRejectMethod, editRuleResolveServer)}
          {(editRuleAction.value === 'route' || editRuleAction.value === 'reject' || editRuleAction.value === 'resolve') && (
            <div>
              <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.domain')}</label>
              <Input value={editRuleDomain.value} onInput={(e: any) => { editRuleDomain.value = e.target.value; }} placeholder="example.com, test.com" />
            </div>
          )}
          {(editRuleAction.value === 'route' || editRuleAction.value === 'reject' || editRuleAction.value === 'resolve') && (
            <>
              <button type="button" class="flex items-center gap-1.5 text-sm text-green-600 hover:text-green-700 dark:text-green-400 dark:hover:text-green-300 mt-1 font-medium" onClick={() => { showEditAdvanced.value = !showEditAdvanced.value; }}>
                <svg class={`w-3 h-3 transition-transform ${showEditAdvanced.value ? 'rotate-90' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6" /></svg>
                {t('rules.advanced')}
              </button>
              {showEditAdvanced.value && renderAdvancedFields(editRuleDomainSuffix, editRuleDomainKeyword, editRuleIpCidr, editRuleSourceIpCidr, editRuleProcessName, editRuleProcessPath, editRulePort, editRuleSourcePort, editRuleSourcePortRange, editRuleNetwork, editRuleRuleSetId, editRuleInvert)}
            </>
          )}
        </div>
      </Modal>

      {/* Rule Set management */}
      <Card title={t('rules.rule_set')}>
        <div class="space-y-3">
          {ruleSets.value.length === 0 ? (
            <div class="text-center py-6">
              <svg class="w-10 h-10 mx-auto text-gray-300 dark:text-gray-600 mb-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" /></svg>
              <p class="text-gray-400 dark:text-gray-500 text-sm">{t('rules.no_rule_sets')}</p>
            </div>
          ) : (
            <div class="space-y-1.5">
              {ruleSets.value.map(rs => (
                <div key={rs.id} class="flex items-center justify-between px-3.5 py-2.5 rounded-xl bg-gray-50 dark:bg-gray-700/30 hover:bg-gray-100 dark:hover:bg-gray-700/50 transition-colors border border-gray-100 dark:border-gray-700/50 group">
                  <div class="flex items-center gap-3">
                    <Switch checked={rs.isEnabled} onChange={async (v) => {
                      await callBridge('updateRuleSet', rs.id, JSON.stringify({ isEnabled: v })).then(r => { if (!r.ok) toastStore.error(r.error?.message ?? t('common.operation_failed')); });
                      await fetchRuleSets();
                    }} />
                    <div>
                      <p class="font-medium text-sm text-gray-900 dark:text-gray-100">{rs.name}</p>
                      <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{rs.tag} · <span class="uppercase">{rs.format}</span></p>
                    </div>
                  </div>
                  <button class="p-1.5 rounded-lg text-gray-400 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 opacity-0 group-hover:opacity-100 transition-all" onClick={async () => {
                    const delResult = await callBridge('deleteRuleSet', rs.id);
                    if (!delResult.ok) toastStore.error(delResult.error?.message ?? t('common.operation_failed'));
                    await fetchRuleSets();
                  }}>
                    <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                  </button>
                </div>
              ))}
            </div>
          )}
          <Button variant="secondary" size="sm" onClick={() => { showAddRuleSetModal.value = true; }}>{t('rules.add_rule_set')}</Button>
        </div>
      </Card>

      {/* Add Rule Set Modal */}
      <Modal title={t('rules.add_rule_set')} open={showAddRuleSetModal.value} onClose={() => { showAddRuleSetModal.value = false; }} onConfirm={async () => {
        const name = newRuleSetName.value.trim();
        const tag = newRuleSetTag.value.trim();
        const url = newRuleSetUrl.value.trim();
        if (!name || !tag || !url) { toastStore.error(t('validation.required_fields')); return; }
        const result = await callBridge('addRuleSet', JSON.stringify({ name, tag, url, format: newRuleSetFormat.value, type: newRuleSetType.value }));
        if (result.ok) { showAddRuleSetModal.value = false; newRuleSetName.value = ''; newRuleSetTag.value = ''; newRuleSetUrl.value = ''; newRuleSetType.value = 'remote'; await fetchRuleSets(); }
        else { toastStore.error(result.error?.message ?? t('rules.add_rule_set_failed')); }
      }}>
        <div class="space-y-3">
          <div>
            <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.rule_set_name')}</label>
            <Input value={newRuleSetName.value} onInput={(e: any) => { newRuleSetName.value = e.target.value; }} placeholder="Rule Set Name" />
          </div>
          <div>
            <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.rule_set_tag')}</label>
            <Input value={newRuleSetTag.value} onInput={(e: any) => { newRuleSetTag.value = e.target.value; }} placeholder="geoip-cn" />
          </div>
          <div>
            <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.rule_set_type')}</label>
            <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={newRuleSetType.value} onChange={(e: any) => { newRuleSetType.value = e.target.value; }}>
              <option value="remote">{t('rules.rule_set_type_remote')}</option>
              <option value="local">{t('rules.rule_set_type_local')}</option>
            </select>
          </div>
          <div>
            <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{newRuleSetType.value === 'local' ? t('rules.rule_set_path') : t('rules.url')}</label>
            <Input value={newRuleSetUrl.value} onInput={(e: any) => { newRuleSetUrl.value = e.target.value; }} placeholder={newRuleSetType.value === 'local' ? '/path/to/rule-set.srs' : 'https://example.com/rule-set.srs'} />
          </div>
          <div>
            <label class="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t('rules.rule_set_format')}</label>
            <select class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all" value={newRuleSetFormat.value} onChange={(e: any) => { newRuleSetFormat.value = e.target.value; }}>
              <option value="binary">binary</option>
              <option value="source">source</option>
            </select>
          </div>
        </div>
      </Modal>
    </div>
  );
}
