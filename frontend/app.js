const API = '/api';

// ==================== Auth ====================
function getToken() { return localStorage.getItem('auth_token') || ''; }
function checkAuth() { if (!getToken()) { window.location.href = '/login.html'; return false; } return true; }
function logout() { localStorage.removeItem('auth_token'); localStorage.removeItem('user_name'); window.location.href = '/login.html'; }
function showUserInfo() { const el = document.getElementById('user-info'); if (el) el.textContent = localStorage.getItem('user_name') || '用户'; }

// ==================== Utils ====================
async function api(path, opts = {}) {
    const token = getToken();
    const headers = { 'Content-Type': 'application/json', ...(token ? { 'Authorization': 'Bearer ' + token } : {}), ...opts.headers };
    const res = await fetch(API + path, { ...opts, headers });
    if (res.status === 401) { logout(); throw new Error('登录已过期'); }
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail || '请求失败'); }
    return res.json();
}

function showLoading(text = '处理中，请稍候...') {
    const el = document.getElementById('global-loading');
    const txt = document.getElementById('loading-text');
    if (txt) txt.textContent = text;
    if (el) el.classList.add('show');
}
function hideLoading() {
    const el = document.getElementById('global-loading');
    if (el) el.classList.remove('show');
}

function toast(msg, type = 'success') {
    const c = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => el.remove(), 3500);
}

function showModal(id) {
    document.getElementById(id).classList.add('show');
    if (id === 'launch-modal') { Promise.all([loadAccountOptions('launch-account'), loadInstanceTypes(), loadAmiOptions()]); }
    if (id === 'deploy-modal') { Promise.all([loadInstanceOptions('deploy-instance'), loadProjectOptions('deploy-project')]); }
}
function hideModal(id) { document.getElementById(id).classList.remove('show'); }

function stateBadge(state) {
    const map = { running: 'green', pending: 'yellow', stopped: 'red', terminated: 'gray', installing: 'blue', failed: 'red' };
    return `<span class="badge badge-${map[state] || 'gray'}">${state}</span>`;
}

function copyToClipboard(text, btn) {
    if (!text) { toast('内容为空', 'error'); return; }
    const done = () => {
        toast('已复制到剪贴板');
        if (btn) {
            const orig = btn.textContent;
            btn.classList.add('copied');
            btn.textContent = '✓';
            setTimeout(() => { btn.classList.remove('copied'); btn.textContent = orig; }, 1200);
        }
    };
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done).catch(() => {
            // Fallback to execCommand
            fallbackCopy(text); done();
        });
    } else {
        fallbackCopy(text); done();
    }
}

function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
}

function toggleSecretKey(id) {
    const el = document.getElementById('sk-' + id);
    if (!el) return;
    const full = el.dataset.full || '';
    if (!full) { toast('Secret Key 为空', 'error'); return; }
    if (el.dataset.shown === '1') {
        el.textContent = '•'.repeat(Math.min(full.length, 32));
        el.dataset.shown = '0';
    } else {
        el.textContent = full;
        el.dataset.shown = '1';
    }
}

function timeAgo(dateStr) {
    if (!dateStr || dateStr === 'None') return '';
    const d = new Date(dateStr);
    if (isNaN(d)) return '';
    const now = new Date();
    const diff = now - d;
    const days = Math.floor(diff / 86400000);
    if (days < 1) return '今天';
    if (days < 30) return days + ' 天';
    const months = Math.floor(days / 30);
    if (months < 12) return months + ' 个月';
    const years = Math.floor(months / 12);
    const rm = months % 12;
    return rm > 0 ? years + ' 年' + rm + ' 月' : years + ' 年';
}

// ==================== Navigation ====================
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
        document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        item.classList.add('active');
        document.getElementById('tab-' + item.dataset.tab).classList.add('active');
        loadTabData(item.dataset.tab);
    });
});
function loadTabData(tab) {
    const loaders = { dashboard: loadDashboard, accounts: loadAccounts, instances: loadInstances, proxies: loadProxies, projects: loadProjects, tasks: loadTasks };
    if (loaders[tab]) loaders[tab]();
}

// ==================== Dashboard ====================
async function loadDashboard() {
    try {
        const d = await api('/dashboard');
        document.getElementById('stats-grid').innerHTML = `
            <div class="stat-card"><div class="label">AWS 账号</div><div class="value blue">${d.accounts}</div></div>
            <div class="stat-card"><div class="label">总实例数</div><div class="value">${d.instances}</div></div>
            <div class="stat-card"><div class="label">运行中实例</div><div class="value green">${d.instances_running}</div></div>
            <div class="stat-card"><div class="label">活跃代理</div><div class="value yellow">${d.proxies_active}</div></div>
            <div class="stat-card"><div class="label">总任务数</div><div class="value">${d.tasks}</div></div>
            <div class="stat-card"><div class="label">运行中任务</div><div class="value green">${d.tasks_running}</div></div>`;
    } catch (e) { toast(e.message, 'error'); }
}

// ==================== Accounts (Card Layout) ====================
let accountsCache = [];
let selectedAccounts = new Set();

async function loadAccounts() {
    const grid = document.getElementById('accounts-grid');
    if (grid) grid.innerHTML = '<div class="loading-spinner"><div class="spinner"></div><span>加载账号中...</span></div>';
    try {
        accountsCache = await api('/accounts');
        updateGroupFilter();
        filterAccounts();
    } catch (e) { toast(e.message, 'error'); if (grid) grid.innerHTML = ''; }
}

function updateGroupFilter() {
    const sel = document.getElementById('account-group-filter');
    if (!sel) return;
    const groups = [...new Set(accountsCache.map(a => a.group_name).filter(Boolean))].sort();
    const cur = sel.value;
    sel.innerHTML = '<option value="">全部分组</option>' + groups.map(g => `<option value="${g}"${g === cur ? ' selected' : ''}>${g}</option>`).join('');
}

// vCPU 区间定义 (与 index.html 中下拉选项保持一致)
const VCPU_BUCKETS = [
    { key: 'unknown', label: '未检测', match: v => v == null },
    { key: '0', label: '0 (已封号/限制)', match: v => v === 0 },
    { key: '1-5', label: '1-5 vCPU (默认配额)', match: v => v >= 1 && v <= 5 },
    { key: '6-15', label: '6-15 vCPU', match: v => v >= 6 && v <= 15 },
    { key: '16-31', label: '16-31 vCPU', match: v => v >= 16 && v <= 31 },
    { key: '32-63', label: '32-63 vCPU', match: v => v >= 32 && v <= 63 },
    { key: '64-127', label: '64-127 vCPU', match: v => v >= 64 && v <= 127 },
    { key: '128-255', label: '128-255 vCPU', match: v => v >= 128 && v <= 255 },
    { key: '256-511', label: '256-511 vCPU', match: v => v >= 256 && v <= 511 },
    { key: '512+', label: '512+ vCPU', match: v => v >= 512 },
];

// 获取账号 vCPU 排序值 (max_on_demand)，用于分桶/排序
function getAccountVcpu(a) {
    const v = a?.max_on_demand;
    return (v == null || isNaN(v)) ? null : Number(v);
}

let _vcpuGroupView = false;

function toggleVcpuGroupView() {
    _vcpuGroupView = !_vcpuGroupView;
    const btn = document.getElementById('vcpu-view-btn');
    if (btn) btn.textContent = _vcpuGroupView ? '📊 vCPU 分组 (已开启)' : '📊 vCPU 分组';
    filterAccounts();
}

function filterAccounts() {
    const q = (document.getElementById('account-search')?.value || '').toLowerCase();
    const group = document.getElementById('account-group-filter')?.value || '';
    const vcpuKey = document.getElementById('account-vcpu-filter')?.value || '';
    const filtered = accountsCache.filter(a => {
        if (group && a.group_name !== group) return false;
        // vCPU 筛选
        if (vcpuKey) {
            const bucket = VCPU_BUCKETS.find(b => b.key === vcpuKey);
            const v = getAccountVcpu(a);
            if (!bucket || !bucket.match(v)) return false;
        }
        if (!q) return true;
        return (a.email || '').toLowerCase().includes(q) || (a.name || '').toLowerCase().includes(q) ||
            (a.group_name || '').toLowerCase().includes(q) || (a.note || '').toLowerCase().includes(q) ||
            (a.aws_account_id || '').includes(q) || String(a.id).includes(q);
    });
    if (_vcpuGroupView) {
        renderAccountCardsByVcpu(filtered);
    } else {
        renderAccountCards(filtered);
    }
}

function renderAccountCardsByVcpu(list) {
    const grid = document.getElementById('accounts-grid');
    if (!grid) return;
    // 按 vCPU 桶分组
    const groups = VCPU_BUCKETS.map(b => ({ ...b, items: [] }));
    for (const a of list) {
        const v = getAccountVcpu(a);
        const g = groups.find(b => b.match(v));
        if (g) g.items.push(a);
    }
    // 大组在上 (从大到小)，未检测放最后
    const ordered = [...groups].reverse();
    let html = '';
    for (const g of ordered) {
        if (!g.items.length) continue;
        html += `<div style="grid-column:1/-1;margin:10px 0 4px;padding:8px 12px;background:var(--bg2);border-left:3px solid var(--primary);border-radius:6px;font-size:13px;color:var(--text)">
            <b>${g.label}</b> <span style="color:var(--text2);font-weight:normal">· ${g.items.length} 个账号</span>
        </div>`;
        // 渲染该组内的卡片 (复用 renderAccountCards 的 HTML)
        const tmp = document.createElement('div');
        renderAccountCardsInto(tmp, g.items);
        html += tmp.innerHTML;
    }
    grid.innerHTML = html || '<div style="padding:30px;text-align:center;color:var(--text2)">无符合筛选的账号</div>';
}

function renderAccountCardsInto(container, list) {
    // 与 renderAccountCards 共享渲染逻辑，仅改变写入目标
    container.innerHTML = list.map(_renderAccountCardHtml).join('');
}

function _renderAccountCardHtml(a) {
    const displayName = a.email || a.name || a.access_key_id;
    const age = timeAgo(a.register_time || a.added_at);
    const flag = a.country_flag || '';
    const vcpuUsage = a.total_usage || 0;
    const vcpuText = a.max_on_demand ? `${vcpuUsage}/${a.max_on_demand} vCPUs` : '';
    const checked = selectedAccounts.has(a.id) ? 'checked' : '';

    // Credit 徽章 (从前端缓存读取，未查询过显示问号)
    const cred = _creditCache[a.id];
    let credBadge = '';
    if (cred) {
        if (cred.error) {
            credBadge = `<span class="acc-credit error" onclick="loadCredit(${a.id})" title="${cred.error}">💎 Credit 错误</span>`;
        } else {
            const ytd = cred.credits_used_ytd || 0;
            const cur = cred.currency || 'USD';
            const sign = cur === 'USD' ? '$' : (cur === 'CNY' ? '¥' : '');
            credBadge = `<span class="acc-credit" onclick="loadCredit(${a.id})" title="本年已抵扣 Credit (点击刷新)">💎 ${sign}${ytd.toFixed(2)} ${cur}</span>`;
        }
    } else {
        credBadge = `<span class="acc-credit unknown" onclick="loadCredit(${a.id})" title="点击查询 AWS Credit 抵扣额度">💎 Credit ?</span>`;
    }

    return `
        <div class="acc-card" data-id="${a.id}">
            <div class="acc-card-header">
                <div class="acc-card-left">
                    <input type="checkbox" class="acc-check" data-id="${a.id}" ${checked} onchange="toggleAccountSelect(${a.id}, this.checked)">
                    <span class="acc-num">#${a.id}</span>
                    <span class="acc-name" title="${displayName}">${displayName.length > 22 ? displayName.substring(0,22)+'...' : displayName}</span>
                    ${flag ? `<span class="acc-flag">${flag}</span>` : ''}
                    ${age ? `<span class="acc-age">${age}</span>` : ''}
                    ${vcpuText ? `<span class="acc-vcpu" onclick="showVcpuDetail(${a.id})" title="点击查看 vCPU 详情">⚡ ${vcpuText}</span>` : ''}
                    ${credBadge}
                    <span class="acc-instances ${(a.instance_count||0) > 0 ? 'has' : ''}" onclick="viewAccountInstances(${a.id})" title="查看该账号的实例">🖥 ${a.instance_count || 0} 实例</span>
                </div>
                <div class="acc-card-actions">
                    <button class="acc-toggle-btn" onclick="toggleCardExpand(${a.id})">▼</button>
                </div>
            </div>
            <div class="acc-card-meta">
                <div class="acc-field"><span class="acc-label">分组</span> <span class="acc-val editable" onclick="editAccountField(${a.id},'group_name','${(a.group_name||'').replace(/'/g,"\\'")}')">${a.group_name || '<i>点击设置</i>'}</span></div>
                <div class="acc-field"><span class="acc-label">备注</span> <span class="acc-val editable" onclick="editAccountField(${a.id},'note','${(a.note||'').replace(/'/g,"\\'")}')">${a.note || '<i>点击设置</i>'}</span></div>
            </div>
            <div class="acc-card-expand" id="acc-expand-${a.id}" style="display:none">
                <div class="acc-detail-row"><span>邮箱</span> <span>${a.email || '-'}</span></div>
                <div class="acc-detail-row"><span>注册时间</span> <span>${a.register_time || '-'}</span></div>
                <div class="acc-detail-row"><span>添加时间</span> <span>${a.added_at || '-'}</span></div>
                <div class="acc-detail-row"><span>ARN</span> <span class="acc-arn">${a.arn || '-'}</span></div>
                <div class="acc-detail-row">
                    <span>Access Key</span>
                    <span class="acc-arn" id="ak-${a.id}" data-full="${a.access_key_id || ''}">${a.access_key_id || '-'}</span>
                    <button class="btn btn-sm btn-secondary acc-copy-btn" onclick="copyToClipboard('${(a.access_key_id||'').replace(/'/g,"\\'")}', this)" title="复制 Access Key">📋</button>
                </div>
                <div class="acc-detail-row">
                    <span>Secret Key</span>
                    <span class="acc-arn acc-secret" id="sk-${a.id}" data-full="${a.secret_access_key || ''}" data-shown="0">${a.secret_access_key ? '••••••••••••••••••••••••••••••••' : '-'}</span>
                    <button class="btn btn-sm btn-secondary acc-copy-btn" onclick="toggleSecretKey(${a.id})" title="显示/隐藏">👁</button>
                    <button class="btn btn-sm btn-secondary acc-copy-btn" onclick="copyToClipboard('${(a.secret_access_key||'').replace(/'/g,"\\'")}', this)" title="复制 Secret Key">📋</button>
                </div>
                <div class="acc-detail-row">
                    <span>AK/SK</span>
                    <span class="acc-arn">一键复制完整凭证</span>
                    <button class="btn btn-sm btn-secondary acc-copy-btn" onclick="copyToClipboard('${(a.access_key_id||'').replace(/'/g,"\\'")} ${(a.secret_access_key||'').replace(/'/g,"\\'")}', this)" title="复制 AK SK (空格分隔)">📋 AK SK</button>
                    <button class="btn btn-sm btn-secondary acc-copy-btn" onclick='copyToClipboard(${JSON.stringify(JSON.stringify({access_key_id:a.access_key_id||"",secret_access_key:a.secret_access_key||"",region:a.default_region||"us-east-1"}))}, this)' title="复制 JSON 格式">📋 JSON</button>
                </div>
            </div>
            <div class="acc-card-footer">
                <button class="btn btn-sm btn-primary acc-main-btn" onclick="viewAccountInstances(${a.id})">🖥 EC2 实例</button>
                <button class="btn btn-sm btn-primary acc-main-btn" onclick="showLightsail(${a.id})">⛵ Lightsail 实例</button>
                <span class="acc-footer-spacer"></span>
                <div class="acc-menu-wrap" id="acc-menu-wrap-${a.id}">
                    <button class="btn btn-sm btn-secondary acc-menu-btn" onclick="toggleAccMenu(${a.id}, event)" title="更多操作">⋯</button>
                    <div class="acc-menu-popup" id="acc-menu-${a.id}">
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});editAccountInline(${a.id})">✏️ 编辑账号</div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});detectAccount(${a.id})">🔍 检测账号信息</div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});loadCredit(${a.id})">💎 刷新 Credit 余额</div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});showVcpuDetail(${a.id})">⚡ vCPU 配额</div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});showBilling(${a.id})">💰 账单费用</div>
                        <div class="acc-menu-divider"></div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});enableAllRegions(${a.id})">🌐 启用全部地区</div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});showFreeTier(${a.id})">🎁 免费套餐</div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});openIamLogin(${a.id})">👤 IAM 登录链接</div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});resetSecretKey(${a.id})">🔑 重置密钥</div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});showMfa(${a.id})">🔒 MFA 验证</div>
                        <div class="acc-menu-divider"></div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});detectAI(${a.id})">🤖 Bedrock / Claude</div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});copyKiroSubscription(${a.id})">✨ Kiro 订阅信息</div>
                        <div class="acc-menu-item" onclick="hideAccMenu(${a.id});copyClaudePlatform(${a.id})">🧠 Claude Platform 配置</div>
                        <div class="acc-menu-divider"></div>
                        <div class="acc-menu-item danger" onclick="hideAccMenu(${a.id});deleteAccount(${a.id})">🗑 删除账号</div>
                    </div>
                </div>
            </div>
        </div>`;
}


function renderAccountCards(list) {
    const grid = document.getElementById('accounts-grid');
    if (!grid) return;
    grid.innerHTML = list.map(_renderAccountCardHtml).join('');
}

function toggleCardExpand(id) {
    const el = document.getElementById('acc-expand-' + id);
    if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

// ==================== 账号卡片三点下拉菜单 ====================
function toggleAccMenu(id, ev) {
    if (ev) ev.stopPropagation();
    // 关闭其它已打开的菜单
    document.querySelectorAll('.acc-menu-popup.show').forEach(p => {
        if (p.id !== `acc-menu-${id}`) p.classList.remove('show');
    });
    const m = document.getElementById('acc-menu-' + id);
    if (m) m.classList.toggle('show');
}

function hideAccMenu(id) {
    const m = document.getElementById('acc-menu-' + id);
    if (m) m.classList.remove('show');
}

// 全局点击关闭菜单
document.addEventListener('click', () => {
    document.querySelectorAll('.acc-menu-popup.show').forEach(p => p.classList.remove('show'));
});

// ==================== AWS Credit (本年已抵扣) ====================
const _creditCache = {};   // {accountId: {credits_used_ytd, currency, error}}
let _creditLoading = new Set();

async function loadCredit(accountId) {
    if (_creditLoading.has(accountId)) return;
    _creditLoading.add(accountId);
    // 立即更新 UI 占位为加载中
    const card = document.querySelector(`.acc-card[data-id="${accountId}"]`);
    if (card) {
        const el = card.querySelector('.acc-credit');
        if (el) { el.classList.add('loading'); el.innerHTML = '💎 查询中...'; }
    }
    try {
        const res = await api(`/accounts/${accountId}/credits?_ts=${Date.now()}`);
        _creditCache[accountId] = res;
        if (!res.error) {
            toast(`Credit 查询完成: ${res.currency} ${res.credits_used_ytd?.toFixed(2) || 0}`);
        } else {
            toast(`⚠️ ${res.error}`, 'warning');
        }
    } catch (e) {
        _creditCache[accountId] = { error: e.message };
        toast(e.message, 'error');
    } finally {
        _creditLoading.delete(accountId);
        // 局部更新该卡片的 credit 徽章
        updateCreditBadge(accountId);
    }
}

function updateCreditBadge(accountId) {
    const card = document.querySelector(`.acc-card[data-id="${accountId}"]`);
    if (!card) return;
    const el = card.querySelector('.acc-credit');
    if (!el) return;
    const cred = _creditCache[accountId];
    el.classList.remove('loading', 'error', 'unknown');
    if (!cred) {
        el.classList.add('unknown');
        el.innerHTML = '💎 Credit ?';
        return;
    }
    if (cred.error) {
        el.classList.add('error');
        el.title = cred.error;
        el.innerHTML = '💎 Credit 错误';
        return;
    }
    const ytd = cred.credits_used_ytd || 0;
    const cur = cred.currency || 'USD';
    const sign = cur === 'USD' ? '$' : (cur === 'CNY' ? '¥' : '');
    el.title = `本年 (${cred.year}) 已抵扣 Credit · 近30天 ${sign}${(cred.credits_used_last_30d||0).toFixed(2)} · 点击刷新`;
    el.innerHTML = `💎 ${sign}${ytd.toFixed(2)} ${cur}`;
}

async function loadAllCredits() {
    if (!accountsCache.length) return;
    const targets = accountsCache.filter(a => !_creditCache[a.id]);
    if (!targets.length) { toast('所有账号 Credit 已查询完成'); return; }
    showLoading(`正在并发查询 ${targets.length} 个账号的 Credit...`);
    try {
        await Promise.all(targets.map(a => loadCredit(a.id)));
        toast(`Credit 查询完成 (${targets.length} 个账号)`);
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}

// ==================== 菜单中的占位/快捷功能 ====================
function enableAllRegions(id) {
    const a = accountsCache.find(x => x.id === id);
    if (!a) return;
    const url = `https://${a.aws_account_id ? a.aws_account_id + '.signin.aws.amazon.com/console' : 'console.aws.amazon.com'}/billing/home?#/account`;
    copyToClipboard(a.access_key_id || '', null);
    toast('已复制 Access Key，请在 AWS 控制台 Account → AWS Regions 启用 opt-in 区域', 'info');
    window.open('https://console.aws.amazon.com/billing/home?#/account', '_blank');
}

function showFreeTier(id) {
    // AWS 免费套餐控制台
    window.open('https://console.aws.amazon.com/billing/home#/freetier', '_blank');
    toast('已打开 AWS 免费套餐控制台 (需在新标签登录该账号)', 'info');
}

function openIamLogin(id) {
    const a = accountsCache.find(x => x.id === id);
    if (!a) return;
    if (!a.aws_account_id) { toast('账号 AWS Account ID 未知，请先点「检测账号信息」', 'error'); return; }
    const url = `https://${a.aws_account_id}.signin.aws.amazon.com/console`;
    copyToClipboard(`${a.aws_account_id}\n${a.access_key_id}\n${a.secret_access_key || ''}`, null);
    toast(`已复制账号 ID + AK/SK，IAM 登录页已打开`, 'info');
    window.open(url, '_blank');
}

function resetSecretKey(id) {
    if (!confirm('重置 Secret Key 需要在 AWS 控制台 IAM → 用户 → 安全凭证 中创建新的 Access Key 并替换。\n\n现在打开 IAM 控制台？')) return;
    window.open('https://console.aws.amazon.com/iam/home#/users', '_blank');
}

function showMfa(id) {
    if (!confirm('MFA 配置请在 AWS 控制台 IAM → 用户 → 安全凭证 启用。\n\n现在打开？')) return;
    window.open('https://console.aws.amazon.com/iam/home#/security_credentials', '_blank');
}

function copyKiroSubscription(id) {
    const a = accountsCache.find(x => x.id === id);
    if (!a) return;
    const cfg = {
        provider: 'aws-bedrock',
        access_key_id: a.access_key_id || '',
        secret_access_key: a.secret_access_key || '',
        region: a.default_region || 'us-east-1',
    };
    copyToClipboard(JSON.stringify(cfg, null, 2), null);
    toast('已复制 Kiro 订阅配置 (JSON)，可直接粘贴到 Kiro 设置', 'success');
}

function copyClaudePlatform(id) {
    const a = accountsCache.find(x => x.id === id);
    if (!a) return;
    const env = `# Claude Platform / claude-code env\nexport AWS_ACCESS_KEY_ID="${a.access_key_id || ''}"\nexport AWS_SECRET_ACCESS_KEY="${a.secret_access_key || ''}"\nexport AWS_REGION="${a.default_region || 'us-east-1'}"\nexport CLAUDE_CODE_USE_BEDROCK=1\nexport ANTHROPIC_MODEL="anthropic.claude-3-5-sonnet-20241022-v2:0"`;
    copyToClipboard(env, null);
    toast('已复制 Claude Platform / claude-code 环境变量', 'success');
}


// ==================== 账号 EC2 实例详情面板 ====================
let _acctInstAccountId = null;
let _acctInstData = [];

function viewAccountInstances(accountId) {
    _acctInstAccountId = accountId;
    const a = accountsCache.find(x => x.id === accountId);
    const name = a ? (a.email || a.name || ('#' + accountId)) : ('#' + accountId);
    const hint = document.getElementById('acct-instances-hint');
    if (hint) hint.textContent = `账号: ${name}  ·  默认区域: ${a?.default_region || '-'}  ·  仅展示该 AWS 账号下的 EC2 实例`;

    // 重置 UI
    document.getElementById('acct-inst-search').value = '';
    document.getElementById('acct-inst-state-filter').value = '';
    document.getElementById('acct-inst-region-filter').innerHTML = '<option value="">全部区域</option>';
    document.getElementById('acct-instances-summary').innerHTML = '';
    document.getElementById('acct-instances-list').innerHTML =
        '<div style="text-align:center;padding:30px;color:var(--text2)">正在加载实例...</div>';

    document.getElementById('acct-instances-modal').classList.add('show');
    loadAccountInstancesDetail();
}

async function loadAccountInstancesDetail() {
    if (!_acctInstAccountId) return;
    const body = document.getElementById('acct-instances-list');
    const summary = document.getElementById('acct-instances-summary');
    const managedOnly = document.getElementById('acct-inst-managed-only')?.checked ? '0' : '1';
    body.innerHTML = `<div style="text-align:center;padding:30px"><div class="spinner"></div><div style="margin-top:12px;color:var(--text2)">正在并发扫描所有区域 EC2 实例（约 10-30 秒）...</div></div>`;
    summary.innerHTML = '';
    try {
        const url = `/accounts/${_acctInstAccountId}/ec2-detail?all_managed=${managedOnly === '1' ? 'false' : 'true'}&_ts=${Date.now()}`;
        const res = await api(url);
        _acctInstData = res.instances || [];

        // 填充区域下拉
        const regions = [...new Set(_acctInstData.map(i => i.region))].sort();
        const regSel = document.getElementById('acct-inst-region-filter');
        const cur = regSel.value;
        regSel.innerHTML = '<option value="">全部区域 (' + _acctInstData.length + ')</option>' +
            regions.map(r => {
                const cnt = _acctInstData.filter(i => i.region === r).length;
                return `<option value="${r}" ${r === cur ? 'selected' : ''}>${r} (${cnt})</option>`;
            }).join('');

        renderAcctInstances();
    } catch (e) {
        body.innerHTML = `<div style="color:var(--red);padding:20px;text-align:center">加载失败: ${e.message}</div>`;
        toast(e.message, 'error');
    }
}

function renderAcctInstances() {
    const body = document.getElementById('acct-instances-list');
    const summary = document.getElementById('acct-instances-summary');
    if (!_acctInstData) return;

    const region = document.getElementById('acct-inst-region-filter').value;
    const state = document.getElementById('acct-inst-state-filter').value;
    const q = (document.getElementById('acct-inst-search').value || '').toLowerCase();

    let list = _acctInstData;
    if (region) list = list.filter(i => i.region === region);
    if (state) list = list.filter(i => i.state === state);
    if (q) list = list.filter(i =>
        (i.instance_id || '').toLowerCase().includes(q) ||
        (i.name || '').toLowerCase().includes(q) ||
        (i.public_ip || '').includes(q) ||
        (i.private_ip || '').includes(q) ||
        (i.public_dns || '').toLowerCase().includes(q)
    );

    // 摘要
    const states = {};
    for (const i of list) states[i.state] = (states[i.state] || 0) + 1;
    const stateBadges = Object.entries(states).map(([s, c]) => {
        const color = s === 'running' ? 'green' : (s === 'stopped' ? 'red' : (s === 'pending' ? 'yellow' : 'gray'));
        return `<span class="badge badge-${color}">${s} ${c}</span>`;
    }).join('');
    summary.innerHTML = `
        <div class="stat-card" style="padding:10px 14px;flex:0 0 auto"><div class="label">实例总数</div><div class="value blue" style="font-size:20px">${list.length}</div></div>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">${stateBadges}</div>
    `;

    if (!list.length) {
        body.innerHTML = '<div style="padding:30px;text-align:center;color:var(--text2);border:1px dashed var(--border);border-radius:8px">无符合条件的实例</div>';
        return;
    }

    body.innerHTML = list.map(_renderAcctInstanceCard).join('');
}

function _humanDuration(launchTimeIso) {
    if (!launchTimeIso) return '-';
    const t = new Date(launchTimeIso);
    if (isNaN(t)) return '-';
    let diff = (Date.now() - t.getTime()) / 1000;
    if (diff < 0) diff = 0;
    const d = Math.floor(diff / 86400);
    const h = Math.floor((diff % 86400) / 3600);
    const m = Math.floor((diff % 3600) / 60);
    if (d > 0) return `${d} 天 ${h} 小时`;
    if (h > 0) return `${h} 小时 ${m} 分钟`;
    return `${m} 分钟`;
}

function _fmtTime(iso) {
    if (!iso) return '-';
    const t = new Date(iso);
    if (isNaN(t)) return iso;
    const pad = n => String(n).padStart(2, '0');
    return `${t.getFullYear()}-${pad(t.getMonth()+1)}-${pad(t.getDate())} ${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
}

function _renderAcctInstanceCard(i) {
    const stateColor = i.state === 'running' ? 'green' : (i.state === 'stopped' ? 'red' : (i.state === 'pending' ? 'yellow' : 'gray'));
    const uptime = _humanDuration(i.launch_time);
    const launchStr = _fmtTime(i.launch_time);
    const ipBadge = i.is_static_ip ? '<span class="badge badge-blue" style="font-size:10px;margin-left:6px">EIP 静态</span>' : '<span class="badge badge-gray" style="font-size:10px;margin-left:6px">动态</span>';
    const platform = (i.platform || 'Linux/UNIX').replace(/^Linux\/UNIX$/, 'Linux/UNIX');
    const arch = i.architecture || '';
    const az = i.availability_zone || '';
    const name = i.name || i.instance_id;
    const publicIp = i.public_ip || '-';
    const privateIp = i.private_ip || '-';
    const publicDns = i.public_dns || '-';
    const privateDns = i.private_dns || '-';
    const escIid = (i.instance_id || '').replace(/'/g, "\\'");
    const escRegion = (i.region || '').replace(/'/g, "\\'");

    return `
    <div class="acc-card" style="margin-bottom:12px">
        <div class="acc-card-header">
            <div class="acc-card-left" style="flex-wrap:wrap">
                <span class="acc-num">🖥</span>
                <span class="acc-name" title="${name}"><b>${name.length > 32 ? name.substring(0,32)+'...' : name}</b></span>
                <span class="badge badge-${stateColor}">${i.state}</span>
                <span class="acc-flag">${i.region}</span>
                ${i.managed ? '<span class="badge badge-blue" style="font-size:10px">本平台</span>' : ''}
                <span class="acc-age" title="${launchStr}">⏱ ${uptime}</span>
            </div>
        </div>
        <div style="padding:8px 12px;display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:6px 14px;font-size:12px">
            <div><span style="color:var(--text2)">实例 ID</span> <code style="font-size:11px">${i.instance_id}</code>
                <button class="btn btn-sm btn-secondary acc-copy-btn" onclick="copyToClipboard('${escIid}', this)" title="复制">📋</button>
            </div>
            <div><span style="color:var(--text2)">类型</span> <b>${i.instance_type || '-'}</b></div>
            <div><span style="color:var(--text2)">公网 IP</span> <code>${publicIp}</code>${publicIp !== '-' ? ipBadge : ''}
                ${publicIp !== '-' ? `<button class="btn btn-sm btn-secondary acc-copy-btn" onclick="copyToClipboard('${publicIp}', this)">📋</button>` : ''}
            </div>
            <div><span style="color:var(--text2)">内网 IP</span> <code>${privateIp}</code></div>
            <div style="grid-column:span 2"><span style="color:var(--text2)">公网 DNS</span> <code style="font-size:11px;word-break:break-all">${publicDns}</code></div>
            <div style="grid-column:span 2"><span style="color:var(--text2)">内网 DNS</span> <code style="font-size:11px;word-break:break-all">${privateDns}</code></div>
            <div><span style="color:var(--text2)">可用区</span> ${az}</div>
            <div><span style="color:var(--text2)">架构</span> ${platform} ${arch}</div>
            <div><span style="color:var(--text2)">启动时间</span> ${launchStr}</div>
            <div><span style="color:var(--text2)">已运行</span> <b style="color:var(--green)">${uptime}</b></div>
            <div><span style="color:var(--text2)">VPC</span> <code style="font-size:11px">${i.vpc_id || '-'}</code></div>
            <div><span style="color:var(--text2)">子网</span> <code style="font-size:11px">${i.subnet_id || '-'}</code></div>
            <div><span style="color:var(--text2)">密钥对</span> ${i.key_name || '-'}</div>
            <div><span style="color:var(--text2)">监控</span> ${i.monitoring || '-'}</div>
            <div><span style="color:var(--text2)">AMI</span> <code style="font-size:11px">${i.image_id || '-'}</code></div>
        </div>
        <div class="acc-card-footer" style="flex-wrap:wrap">
            ${i.state === 'stopped' ? `<button class="btn btn-sm btn-primary" onclick="acctInstAction('${escIid}','${escRegion}','start')">▶ 启动</button>` : ''}
            ${i.state === 'running' ? `<button class="btn btn-sm btn-secondary" onclick="acctInstAction('${escIid}','${escRegion}','stop')">⏹ 停止</button>` : ''}
            ${i.state === 'running' ? `<button class="btn btn-sm btn-secondary" onclick="acctInstAction('${escIid}','${escRegion}','reboot')">🔄 重启</button>` : ''}
            ${i.state !== 'terminated' ? `<button class="btn btn-sm btn-danger" onclick="acctInstAction('${escIid}','${escRegion}','terminate')">🗑 终止</button>` : ''}
            <button class="btn btn-sm btn-secondary" onclick="copyToClipboard('${escIid}', this)" title="复制实例 ID">📋 ID</button>
            ${publicIp !== '-' ? `<button class="btn btn-sm btn-secondary" onclick="copyToClipboard('ssh -i depin-key-${escRegion}.pem ubuntu@${publicIp}', this)" title="复制 SSH 命令">SSH</button>` : ''}
        </div>
    </div>`;
}

async function acctInstAction(instanceId, region, action) {
    const labels = { start: '启动', stop: '停止', reboot: '重启', terminate: '终止' };
    if (action === 'terminate' && !confirm(`确定终止 EC2 实例 ${instanceId} (${region})？此操作不可恢复！`)) return;
    showLoading(`正在${labels[action] || action} ${instanceId}...`);
    try {
        // 通过 direct API 直接操作 (基于 account_id + instance_id + region，无需本地 Instance 记录)
        await api(`/instances/direct/${action}`, {
            method: 'POST',
            body: JSON.stringify({ account_id: _acctInstAccountId, instance_id: instanceId, region }),
        });
        toast(`${labels[action] || action}指令已发送`);
        // 等几秒让 AWS 状态变化后再刷新
        setTimeout(loadAccountInstancesDetail, 2500);
    } catch (e) {
        toast(e.message, 'error');
    } finally { hideLoading(); }
}

function toggleAccountSelect(id, checked) {
    if (checked) selectedAccounts.add(id); else selectedAccounts.delete(id);
    updateBatchBar();
}

function toggleSelectAll() {
    const all = document.querySelectorAll('.acc-check');
    const allChecked = selectedAccounts.size === accountsCache.length;
    selectedAccounts.clear();
    if (!allChecked) accountsCache.forEach(a => selectedAccounts.add(a.id));
    all.forEach(cb => cb.checked = !allChecked);
    updateBatchBar();
}

function updateBatchBar() {
    const bar = document.getElementById('batch-bar');
    if (!bar) return;
    bar.style.display = selectedAccounts.size > 0 ? 'flex' : 'none';
    const cnt = document.getElementById('batch-count');
    if (cnt) cnt.textContent = selectedAccounts.size;
}

async function batchDeleteSelected() {
    if (!selectedAccounts.size) return;
    if (!confirm(`确定删除选中的 ${selectedAccounts.size} 个账号？`)) return;
    showLoading('正在批量删除账号...');
    try {
        await api('/accounts/batch-delete', { method: 'POST', body: JSON.stringify({ ids: [...selectedAccounts] }) });
        toast(`已删除 ${selectedAccounts.size} 个账号`);
        selectedAccounts.clear();
        updateBatchBar();
        loadAccounts();
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}

async function detectAccount(id) {
    const btn = event?.target;
    if (btn) btn.classList.add('loading');
    showLoading('正在检测账号信息...');
    try {
        const res = await api(`/accounts/${id}/detect`, { method: 'POST' });
        if (res._proxy_error) {
            toast('⚠️ 代理连接失败！请检查代理设置', 'error');
        } else if (res._errors && res._errors.length > 0) {
            toast(`检测完成 (${res._errors.length}个警告): ${res.email || res.name}`, 'warning');
        } else {
            toast(`检测完成: ${res.email || res.name}`);
        }
        loadAccounts();
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); if (btn) btn.classList.remove('loading'); }
}

async function detectAllAccounts() {
    const btn = event?.target;
    if (btn) { btn.classList.add('loading'); btn.textContent = '检测中...'; }
    showLoading('正在并发检测所有账号，请耐心等待...');
    try {
        const res = await api('/accounts/detect-all', { method: 'POST' });
        toast(`检测完成: ${res.detected} 成功, ${res.errors} 失败`);
        loadAccounts();
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); if (btn) { btn.classList.remove('loading'); btn.textContent = '🔍 检测全部'; } }
}

// ==================== AI Detection (简化: 只查 us-east-1 Bedrock) ====================
let _aiAccountId = null;
let _aiRegion = 'us-east-1';
let _aiModels = [];

const AI_REGIONS = [
    { code: 'us-east-1', name: '🇺🇸 美国 弗吉尼亚 (推荐)' },
    { code: 'us-west-2', name: '🇺🇸 美国 俄勒冈' },
    { code: 'ap-northeast-1', name: '🇯🇵 日本 东京' },
    { code: 'ap-southeast-1', name: '🇸🇬 新加坡' },
    { code: 'eu-west-1', name: '🇮🇪 爱尔兰' },
    { code: 'eu-central-1', name: '🇩🇪 法兰克福' },
];

async function detectAI(id) {
    _aiAccountId = id;
    const a = accountsCache.find(x => x.id === id);
    const body = document.getElementById('ai-body');
    const modal = document.getElementById('ai-modal');
    modal.classList.add('show');
    renderAiShell(a);
    await runAiDetect();
}

function renderAiShell(account) {
    const body = document.getElementById('ai-body');
    const accName = account ? (account.email || account.name || ('#' + _aiAccountId)) : ('#' + _aiAccountId);
    body.innerHTML = `
        <div style="padding:8px 0;margin-bottom:10px;border-bottom:1px solid var(--border);color:var(--text2);font-size:13px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
            <span>账号: <b style="color:var(--text)">${accName}</b></span>
            <span style="display:flex;align-items:center;gap:6px">
                <label style="font-size:12px">区域</label>
                <select id="ai-region" onchange="onAiRegionChange()" style="padding:5px 8px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px">
                    ${AI_REGIONS.map(r => `<option value="${r.code}" ${r.code === _aiRegion ? 'selected' : ''}>${r.name} (${r.code})</option>`).join('')}
                </select>
                <button class="btn btn-sm btn-secondary" onclick="runAiDetect()">🔄 重检</button>
            </span>
        </div>
        <div id="ai-detect-area">
            <div style="text-align:center;padding:30px"><div class="spinner"></div><div style="margin-top:12px;color:var(--text2)">正在检测 ${_aiRegion} 的 Bedrock Claude 配额...</div></div>
        </div>
        <div id="ai-chat-area" style="margin-top:12px"></div>
    `;
}

function onAiRegionChange() {
    _aiRegion = document.getElementById('ai-region').value;
    runAiDetect();
}

async function runAiDetect() {
    if (!_aiAccountId) return;
    const area = document.getElementById('ai-detect-area');
    if (area) area.innerHTML = `<div style="text-align:center;padding:30px"><div class="spinner"></div><div style="margin-top:12px;color:var(--text2)">正在检测 ${_aiRegion} 的 Bedrock Claude 配额...</div></div>`;
    try {
        const res = await api(`/accounts/${_aiAccountId}/detect-ai?region=${_aiRegion}&_ts=${Date.now()}`, { method: 'POST' });
        renderAiResults(res);
        renderAiChat(res);
    } catch (e) {
        if (area) area.innerHTML = `<div style="color:var(--red);padding:20px;text-align:center">检测失败: ${e.message}</div>`;
        toast(e.message, 'error');
    }
}

function renderAiResults(data) {
    const area = document.getElementById('ai-detect-area');
    if (!area) return;
    const models = data.bedrock_models || [];
    const quotas = data.bedrock_quotas || [];
    _aiModels = models;

    let html = '';

    // 错误提示
    if (data.error) {
        html += `<div style="padding:10px 12px;background:rgba(239,68,68,0.12);border:1px solid var(--red);border-radius:8px;color:var(--red);font-size:13px;margin-bottom:10px">⚠️ ${data.error}</div>`;
    }

    // 模型
    html += `<div class="ai-section">
        <div class="ai-section-title">🧠 Bedrock Anthropic / Claude 模型 (${data.region || _aiRegion})</div>
        <div class="ai-section-body">`;
    if (models.length) {
        html += `<div style="margin-bottom:6px;color:var(--text2);font-size:12px">共 <b style="color:var(--green)">${models.length}</b> 个 Claude 模型可用</div>`;
        html += `<table class="ai-table"><thead><tr><th>模型 ID</th><th>名称</th></tr></thead><tbody>`;
        for (const m of models) {
            html += `<tr><td><code style="font-size:11px">${m.id}</code></td><td>${m.name}</td></tr>`;
        }
        html += '</tbody></table>';
    } else {
        html += `<span style="color:var(--text2)">该区域未检测到 Claude 模型 ${data.bedrock_enabled ? '（账号未申请模型访问）' : '（Bedrock 未开通或无权限）'}</span>`;
    }
    html += '</div></div>';

    // 配额
    html += `<div class="ai-section">
        <div class="ai-section-title">📊 Claude 关键配额 (Tokens / Requests)</div>
        <div class="ai-section-body">`;
    if (quotas.length) {
        html += `<table class="ai-table"><thead><tr><th>配额名称</th><th style="text-align:right">值</th></tr></thead><tbody>`;
        for (const q of quotas) {
            const val = q.value >= 1000000 ? (q.value / 1000000).toFixed(1) + 'M' : q.value >= 1000 ? (q.value / 1000).toFixed(1) + 'K' : q.value;
            html += `<tr><td style="font-size:12px">${q.name}</td><td style="text-align:right;font-weight:bold;color:var(--blue)">${val}</td></tr>`;
        }
        html += '</tbody></table>';
    } else {
        html += '<span style="color:var(--text2)">未检测到 Bedrock 配额（可能未开通）</span>';
    }
    html += '</div></div>';

    area.innerHTML = html;
}

function renderAiChat(data) {
    const area = document.getElementById('ai-chat-area');
    if (!area) return;
    const models = (data.bedrock_models || []);
    const enabled = models.length > 0;

    area.innerHTML = `
        <div class="ai-section">
            <div class="ai-section-title">💬 Claude 对话测试 (默认发送 "你好")</div>
            <div class="ai-section-body">
                <div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap;align-items:center">
                    <select id="ai-chat-model" style="flex:1;min-width:220px;padding:7px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:12px" ${enabled ? '' : 'disabled'}>
                        ${enabled
                            ? models.map((m, i) => `<option value="${m.id}" ${i === 0 ? 'selected' : ''}>${m.name}</option>`).join('')
                            : '<option value="">无可用模型</option>'}
                    </select>
                    <input type="text" id="ai-chat-prompt" value="你好" placeholder="输入要发送给 Claude 的内容" style="flex:2;min-width:200px;padding:7px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px">
                    <button class="btn btn-primary btn-sm" onclick="sendAiChat()" ${enabled ? '' : 'disabled'} style="padding:7px 14px">▶ 发送</button>
                </div>
                <div id="ai-chat-result" style="background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px;font-size:13px;line-height:1.6;color:var(--text2);min-height:60px;white-space:pre-wrap;word-break:break-word">
                    ${enabled ? '点击「发送」开始对话测试' : '该账号在此区域无可用 Claude 模型，无法测试对话'}
                </div>
            </div>
        </div>
    `;
}

async function sendAiChat() {
    if (!_aiAccountId) return;
    const modelSel = document.getElementById('ai-chat-model');
    const promptEl = document.getElementById('ai-chat-prompt');
    const resultEl = document.getElementById('ai-chat-result');
    if (!modelSel || !promptEl || !resultEl) return;
    const model_id = modelSel.value;
    const prompt = (promptEl.value || '你好').trim();
    if (!model_id) { toast('请选择模型', 'error'); return; }

    resultEl.innerHTML = '<div style="display:flex;align-items:center;gap:10px;color:var(--text2)"><div class="spinner" style="width:16px;height:16px;border-width:2px"></div>正在调用 Claude...</div>';
    try {
        const res = await api(`/accounts/${_aiAccountId}/bedrock/invoke`, {
            method: 'POST',
            body: JSON.stringify({ prompt, model_id, region: _aiRegion, max_tokens: 256 }),
        });
        if (res.ok) {
            const meta = `<div style="color:var(--text2);font-size:11px;margin-bottom:6px;border-bottom:1px solid var(--border);padding-bottom:6px">🤖 ${res.model_id} · 输入 ${res.input_tokens} tokens · 输出 ${res.output_tokens} tokens</div>`;
            resultEl.innerHTML = meta + `<div style="color:var(--text)">${escapeHtml(res.reply || '(空响应)')}</div>`;
            toast('Claude 响应成功');
        } else {
            resultEl.innerHTML = `<div style="color:var(--red)">❌ ${res.error || '调用失败'}</div>`;
            toast(res.error || '调用失败', 'error');
        }
    } catch (e) {
        resultEl.innerHTML = `<div style="color:var(--red)">❌ ${e.message}</div>`;
        toast(e.message, 'error');
    }
}

function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function showVcpuDetail(id) {
    const a = accountsCache.find(x => x.id === id);
    const modal = document.getElementById('vcpu-modal');
    const body = document.getElementById('vcpu-body');
    modal.classList.add('show');

    // 始终先显示加载动画，等后端返回最新数据
    body.innerHTML = `<div style="text-align:center;padding:30px"><div class="spinner"></div><div style="margin-top:12px;color:var(--text2)">正在扫描全部区域 vCPU 配额，可能需要 1-2 分钟...</div></div>`;

    try {
        const res = await api(`/accounts/${id}/vcpus`, { method: 'POST' });
        renderVcpuTable(res.regions);
        toast('vCPU 检测完成');
        // 更新缓存
        if (a) {
            a.vcpu_data = res.regions;
            a.total_vcpus = res.total_vcpus;
            a.max_on_demand = res.max_on_demand || 0;
            a.total_usage = res.total_usage || 0;
        }
        // 实时更新卡片上的 vCPU 标签
        const card = document.querySelector(`.acc-card[data-id="${id}"]`);
        if (card) {
            const vcpuEl = card.querySelector('.acc-vcpu');
            const usage = a ? a.total_usage : 0;
            const max = a ? a.max_on_demand : 0;
            if (vcpuEl && max) {
                vcpuEl.textContent = `⚡ ${usage}/${max} vCPUs`;
            } else if (!vcpuEl && max) {
                const left = card.querySelector('.acc-card-left');
                if (left) left.insertAdjacentHTML('beforeend', `<span class="acc-vcpu" onclick="showVcpuDetail(${id})" title="点击查看详情">⚡ ${usage}/${max} vCPUs</span>`);
            }
        }
    } catch (e) {
        body.innerHTML = `<div style="color:var(--red);padding:20px;text-align:center">获取失败: ${e.message}</div>`;
    }
}

// ==================== Billing ====================
let _billingAccountId = null;

function showBilling(id) {
    _billingAccountId = id;
    const a = accountsCache.find(x => x.id === id);
    const accName = a ? (a.email || a.name || ('#' + id)) : ('#' + id);
    const hint = document.getElementById('billing-acc-hint');
    if (hint) hint.textContent = `账号: ${accName}`;

    // 初始化年份/月份选项（默认当前年月，但上月数据更完整，默认取上月）
    const now = new Date();
    let defY = now.getFullYear();
    let defM = now.getMonth() + 1; // 1-12, 当前月
    // 默认选上个月
    let y = defY, m = defM - 1;
    if (m < 1) { m = 12; y = defY - 1; }

    const ySel = document.getElementById('billing-year');
    const mSel = document.getElementById('billing-month');
    if (ySel) {
        const years = [];
        for (let i = defY; i >= defY - 5; i--) years.push(i);
        ySel.innerHTML = years.map(yr => `<option value="${yr}" ${yr===y?'selected':''}>${yr} 年</option>`).join('');
    }
    if (mSel) {
        const months = [];
        for (let i = 1; i <= 12; i++) months.push(i);
        mSel.innerHTML = months.map(mo => `<option value="${mo}" ${mo===m?'selected':''}>${mo} 月</option>`).join('');
    }

    // 重置内容
    const body = document.getElementById('billing-body');
    if (body) body.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text2)">选择年月后点击「查询」</div>';

    document.getElementById('billing-modal').classList.add('show');
}

async function loadBilling() {
    if (!_billingAccountId) { toast('请先从账号卡片选择账号', 'error'); return; }
    const year = parseInt(document.getElementById('billing-year').value);
    const month = parseInt(document.getElementById('billing-month').value);
    const body = document.getElementById('billing-body');
    body.innerHTML = `<div style="text-align:center;padding:30px"><div class="spinner"></div><div style="margin-top:12px;color:var(--text2)">正在查询 Cost Explorer，可能需要 10-30 秒...</div></div>`;
    try {
        // 加时间戳参数 + no-cache 头，确保每次都重新查询，绕过浏览器/nginx 缓存
        const url = `/accounts/${_billingAccountId}/billing?year=${year}&month=${month}&granularity=DAILY&_ts=${Date.now()}`;
        const res = await api(url, { headers: { 'Cache-Control': 'no-cache', 'Pragma': 'no-cache' } });
        renderBilling(res, year, month);
    } catch (e) {
        body.innerHTML = `<div style="color:var(--red);padding:20px;text-align:center">查询失败: ${e.message}</div>`;
    }
}

function fmtMoney(amount, currency) {
    const n = Number(amount || 0);
    const c = currency || 'USD';
    const sign = c === 'USD' ? '$' : (c === 'CNY' ? '¥' : '');
    return `${sign}${n.toFixed(2)} ${c}`;
}

function renderBilling(data, year, month) {
    const body = document.getElementById('billing-body');
    if (!body) return;

    if (data.error) {
        body.innerHTML = `
            <div style="padding:14px;background:rgba(239,68,68,0.1);border:1px solid var(--red);border-radius:8px;color:var(--red);font-size:13px;line-height:1.6">
                <b>⚠️ 查询失败</b><br>${data.error}
            </div>`;
        return;
    }

    const period = data.period || {};
    const total = data.total || 0;
    const currency = data.currency || 'USD';
    const services = data.by_service || [];
    const regions = data.by_region || [];
    const daily = data.daily || [];

    // 汇总头
    let html = `
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px">
            <div class="stat-card" style="flex:1;min-width:160px;padding:14px">
                <div class="label">${year} 年 ${month} 月 总消费</div>
                <div class="value ${total>0?'yellow':''}" style="font-size:22px">${fmtMoney(total, currency)}</div>
            </div>
            <div class="stat-card" style="flex:1;min-width:160px;padding:14px">
                <div class="label">时间区间</div>
                <div class="value" style="font-size:14px;font-weight:600;color:var(--text2)">${period.start || '-'} ~ ${period.end || '-'}</div>
            </div>
            <div class="stat-card" style="flex:1;min-width:120px;padding:14px">
                <div class="label">服务项数</div>
                <div class="value blue" style="font-size:22px">${services.length}</div>
            </div>
        </div>`;

    // 无消费
    if (total <= 0 && !services.length) {
        html += `<div style="padding:20px;text-align:center;color:var(--text2);border:1px dashed var(--border);border-radius:8px">✅ 该月无账单消费</div>`;
        body.innerHTML = html;
        return;
    }

    // 按服务
    if (services.length) {
        html += `<div class="ai-section">
            <div class="ai-section-title">📦 按服务消费 (Top ${services.length})</div>
            <div class="ai-section-body">
                <table class="ai-table"><thead><tr><th>服务</th><th style="text-align:right">金额</th><th style="width:100px">占比</th></tr></thead><tbody>`;
        for (const s of services) {
            const pct = total > 0 ? (s.amount / total * 100) : 0;
            html += `<tr>
                <td>${s.service}</td>
                <td style="text-align:right;font-weight:600;color:var(--yellow)">${fmtMoney(s.amount, currency)}</td>
                <td>
                    <div style="background:var(--bg3);border-radius:6px;height:14px;position:relative;overflow:hidden">
                        <div style="background:var(--primary);height:100%;width:${pct.toFixed(1)}%"></div>
                    </div>
                    <span style="font-size:11px;color:var(--text2)">${pct.toFixed(1)}%</span>
                </td>
            </tr>`;
        }
        html += `</tbody></table></div></div>`;
    }

    // 按区域
    if (regions.length) {
        html += `<div class="ai-section">
            <div class="ai-section-title">🌍 按区域消费</div>
            <div class="ai-section-body">
                <table class="ai-table"><thead><tr><th>区域</th><th style="text-align:right">金额</th></tr></thead><tbody>`;
        for (const r of regions) {
            html += `<tr><td>${r.region || 'Global/-'}</td><td style="text-align:right;font-weight:600">${fmtMoney(r.amount, currency)}</td></tr>`;
        }
        html += `</tbody></table></div></div>`;
    }

    // 每日走势
    if (daily.length) {
        const maxAmt = Math.max(...daily.map(d => d.amount || 0), 0.01);
        html += `<div class="ai-section">
            <div class="ai-section-title">📈 每日走势</div>
            <div class="ai-section-body">
                <div style="display:flex;align-items:flex-end;gap:2px;height:80px;padding:4px 0;border-bottom:1px solid var(--border);margin-bottom:4px">`;
        for (const d of daily) {
            const h = maxAmt > 0 ? ((d.amount || 0) / maxAmt * 75) : 0;
            const color = d.amount > 0 ? 'var(--primary)' : 'var(--border)';
            html += `<div title="${d.date}: ${fmtMoney(d.amount, currency)}" style="flex:1;min-width:4px;background:${color};height:${h}px;border-radius:2px 2px 0 0;cursor:help"></div>`;
        }
        html += `</div>
                <div style="display:flex;justify-content:space-between;color:var(--text2);font-size:11px">
                    <span>${daily[0]?.date || ''}</span>
                    <span>共 ${daily.length} 天 · 最高 ${fmtMoney(maxAmt, currency)}</span>
                    <span>${daily[daily.length-1]?.date || ''}</span>
                </div>
            </div></div>`;
    }

    body.innerHTML = html;
}

function renderVcpuTable(regions) {
    const body = document.getElementById('vcpu-body');
    // 按 on_demand_limit 从高到低排序
    const sorted = Object.entries(regions).sort((a, b) => b[1].on_demand_limit - a[1].on_demand_limit);
    let html = `<table class="vcpu-table"><thead><tr><th>地区</th><th>On-Demand (已用/全部)</th><th>Spot (已用/全部)</th></tr></thead><tbody>`;
    for (const [region, data] of sorted) {
        const highlight = data.on_demand_limit > 5 ? ' style="color:var(--green);font-weight:bold"' : '';
        html += `<tr>
            <td>${data.display || region} (${region})</td>
            <td${highlight}>${data.on_demand_usage}/${data.on_demand_limit}</td>
            <td>${data.spot_usage}/${data.spot_limit}</td>
        </tr>`;
    }
    html += '</tbody></table>';
    body.innerHTML = html;
}

function editAccountField(id, field, currentVal) {
    const newVal = prompt(field === 'group_name' ? '设置分组名称:' : '设置备注:', currentVal || '');
    if (newVal === null) return;
    api(`/accounts/${id}`, { method: 'PUT', body: JSON.stringify({ [field]: newVal }) })
        .then(() => { toast('已更新'); loadAccounts(); })
        .catch(e => toast(e.message, 'error'));
}

function editAccountInline(id) {
    const a = accountsCache.find(x => x.id === id);
    if (!a) return;
    document.getElementById('edit-acc-id').value = id;
    document.getElementById('edit-acc-note').value = a.note || '';
    document.getElementById('edit-acc-group').value = a.group_name || '';
    showModal('edit-account-modal');
}

async function saveAccountEdit(e) {
    e.preventDefault();
    const id = document.getElementById('edit-acc-id').value;
    try {
        await api(`/accounts/${id}`, { method: 'PUT', body: JSON.stringify({
            note: document.getElementById('edit-acc-note').value,
            group_name: document.getElementById('edit-acc-group').value,
        })});
        hideModal('edit-account-modal');
        toast('已保存');
        loadAccounts();
    } catch (e) { toast(e.message, 'error'); }
}

async function createAccount(e) {
    e.preventDefault();
    showLoading('正在添加账号...');
    try {
        const data = { name: document.getElementById('acc-name').value, access_key_id: document.getElementById('acc-key').value, secret_access_key: document.getElementById('acc-secret').value, default_region: document.getElementById('acc-region').value };
        const res = await api('/accounts', { method: 'POST', body: JSON.stringify(data) });
        hideModal('account-modal');
        toast(res.verify?.valid ? '账号已添加，验证通过' : `账号已添加，验证失败: ${res.verify?.error || ''}`);
        loadAccounts();
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}

async function verifyAccount(id) {
    try {
        const res = await api(`/accounts/${id}/verify`, { method: 'POST' });
        toast(res.valid ? `验证通过 (${res.account_id})` : `验证失败: ${res.error}`, res.valid ? 'success' : 'error');
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteAccount(id) {
    if (!confirm('确定删除此账号？')) return;
    showLoading('正在删除账号...');
    try { await api(`/accounts/${id}`, { method: 'DELETE' }); toast('账号已删除'); loadAccounts(); } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}

async function batchCreateAccounts(e) {
    e.preventDefault();
    showLoading('正在批量添加账号...');
    try {
        const data = { text: document.getElementById('batch-acc-text').value, default_region: document.getElementById('batch-acc-region').value };
        const res = await api('/accounts/batch', { method: 'POST', body: JSON.stringify(data) });
        hideModal('batch-account-modal');
        toast(`批量添加: ${res.created?.length || 0} 成功, ${res.errors?.length || 0} 失败`, res.errors?.length ? 'error' : 'success');
        document.getElementById('batch-acc-text').value = '';
        loadAccounts();
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}

// ==================== Instances ====================
let selectedInstances = new Set();
let instancesCache = [];

async function loadInstances() {
    try {
        instancesCache = await api('/instances');
        selectedInstances.clear();
        updateInstanceBatchBar();
        renderInstances(instancesCache);
    } catch (e) { toast(e.message, 'error'); }
}

function filterInstances() {
    const q = (document.getElementById('instance-search')?.value || '').toLowerCase();
    if (!q) { renderInstances(instancesCache); return; }
    const filtered = instancesCache.filter(i =>
        (i.account_name || '').toLowerCase().includes(q) || (i.instance_id || '').toLowerCase().includes(q) ||
        (i.public_ip || '').includes(q) || (i.region || '').includes(q) || (i.instance_type || '').includes(q) ||
        (i.state || '').includes(q) || (i.projects || '').toLowerCase().includes(q) || String(i.id).includes(q)
    );
    renderInstances(filtered);
}

function renderInstances(list) {
    document.querySelector('#instances-table tbody').innerHTML = list.map(i => `<tr>
            <td><input type="checkbox" class="inst-check" data-id="${i.id}" onchange="toggleInstanceSelect(${i.id}, this.checked)"></td>
            <td>${i.id}</td><td>${i.account_name}</td><td><code>${i.instance_id || '-'}</code></td><td>${i.region}</td>
            <td>${i.instance_type}</td><td>${stateBadge(i.state)}</td><td>${i.public_ip || '-'}</td>
            <td>${i.projects || '-'}</td><td>${i.task_count}</td>
            <td class="action-btns">
                <button class="btn btn-sm btn-secondary" onclick="syncInstance(${i.id})">同步</button>
                ${i.state === 'stopped' ? `<button class="btn btn-sm btn-primary" onclick="startInstance(${i.id})">启动</button>` : ''}
                ${i.state === 'running' ? `<button class="btn btn-sm btn-secondary" onclick="stopInstance(${i.id})">停止</button>` : ''}
                <button class="btn btn-sm btn-danger" onclick="terminateInstance(${i.id})">终止</button>
                <button class="btn btn-sm btn-danger" onclick="deleteInstanceRecord(${i.id})">🗑</button>
            </td></tr>`).join('');
}

function toggleInstanceSelect(id, checked) {
    if (checked) selectedInstances.add(id); else selectedInstances.delete(id);
    updateInstanceBatchBar();
}
function toggleSelectAllInstances() {
    const cbs = document.querySelectorAll('.inst-check');
    const allChecked = selectedInstances.size === cbs.length && cbs.length > 0;
    selectedInstances.clear();
    cbs.forEach(cb => { cb.checked = !allChecked; if (!allChecked) selectedInstances.add(parseInt(cb.dataset.id)); });
    updateInstanceBatchBar();
}
function updateInstanceBatchBar() {
    const bar = document.getElementById('instance-batch-bar');
    if (bar) bar.style.display = selectedInstances.size > 0 ? 'flex' : 'none';
    const cnt = document.getElementById('instance-batch-count');
    if (cnt) cnt.textContent = selectedInstances.size;
}
async function batchDeleteInstances() {
    if (!selectedInstances.size) return;
    if (!confirm(`确定删除选中的 ${selectedInstances.size} 个实例记录？`)) return;
    showLoading('正在批量删除实例...');
    try {
        await api('/instances/batch-delete', { method: 'POST', body: JSON.stringify({ ids: [...selectedInstances] }) });
        toast(`已删除 ${selectedInstances.size} 个实例`);
        selectedInstances.clear();
        loadInstances();
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}
async function deleteInstanceRecord(id) {
    if (!confirm('确定删除此实例记录？')) return;
    showLoading('正在删除实例记录...');
    try { await api(`/instances/${id}`, { method: 'DELETE' }); toast('已删除'); loadInstances(); } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}
async function launchInstance(e) {
    e.preventDefault();
    showLoading('正在启动实例，请耐心等待...');
    try {
        const count = parseInt(document.getElementById('launch-count').value) || 1;
        const selectedAccounts = Array.from(document.getElementById('launch-account').selectedOptions).map(o => parseInt(o.value));
        const region = document.getElementById('launch-region').value || null;
        const instanceType = document.getElementById('launch-type').value;
        if (!selectedAccounts.length) { toast('请选择至少一个账号', 'error'); return; }
        hideModal('launch-modal');
        toast(`正在为 ${selectedAccounts.length} 个账号各启动 ${count} 个实例...`, 'info');
        let totalOk = 0, totalErr = 0;
        const volumeSize = parseInt(document.getElementById('launch-volume-size').value) || 20;
        const volumeType = document.getElementById('launch-volume-type').value || 'gp3';
        const promises = selectedAccounts.map(async (accountId) => {
            try {
                if (count > 1) {
                    const res = await api('/instances/batch-launch', { method: 'POST', body: JSON.stringify({ account_id: accountId, region, instance_type: instanceType, count, volume_size: volumeSize, volume_type: volumeType }) });
                    totalOk += res.launched?.length || 0;
                    totalErr += res.errors?.length || 0;
                } else {
                    await api('/instances/launch', { method: 'POST', body: JSON.stringify({ account_id: accountId, region, instance_type: instanceType, volume_size: volumeSize, volume_type: volumeType }) });
                    totalOk++;
                }
            } catch (err) { totalErr++; }
        });
        await Promise.all(promises);
        toast(`批量启动完成: ${totalOk} 成功, ${totalErr} 失败`);
        loadInstances();
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}
async function syncInstance(id) { showLoading('正在同步实例...'); try { const res = await api(`/instances/${id}/sync`, { method: 'POST' }); toast(`已同步: ${res.state}`); loadInstances(); } catch (e) { toast(e.message, 'error'); } finally { hideLoading(); } }
async function startInstance(id) { showLoading('正在启动实例...'); try { await api(`/instances/${id}/start`, { method: 'POST' }); toast('启动指令已发送'); loadInstances(); } catch (e) { toast(e.message, 'error'); } finally { hideLoading(); } }
async function stopInstance(id) { showLoading('正在停止实例...'); try { await api(`/instances/${id}/stop`, { method: 'POST' }); toast('停止指令已发送'); loadInstances(); } catch (e) { toast(e.message, 'error'); } finally { hideLoading(); } }
async function terminateInstance(id) { if (!confirm('确定终止？')) return; showLoading('正在终止实例...'); try { await api(`/instances/${id}/terminate`, { method: 'POST' }); toast('已终止'); loadInstances(); } catch (e) { toast(e.message, 'error'); } finally { hideLoading(); } }
async function syncAllInstances() { showLoading('正在同步所有实例...'); try { const res = await api('/instances/sync-all', { method: 'POST' }); toast(`已同步 ${res.synced} 个`); loadInstances(); } catch (e) { toast(e.message, 'error'); } finally { hideLoading(); } }

// ==================== Proxies ====================
async function loadProxies() {
    try {
        const list = await api('/proxies');
        document.querySelector('#proxies-table tbody').innerHTML = list.map(p => `<tr>
            <td>${p.id}</td><td>${p.protocol.toUpperCase()}</td><td>${p.host}</td><td>${p.port}</td><td>${p.username || '-'}</td>
            <td><span class="badge ${p.is_active ? 'badge-green' : 'badge-red'}" style="cursor:pointer" onclick="toggleProxy(${p.id})">${p.is_active ? '活跃' : '禁用'}</span></td>
            <td id="proxy-ip-${p.id}">${p.last_used_at || '-'}</td>
            <td class="action-btns">
                <button class="btn btn-sm btn-secondary" onclick="testProxy(${p.id})">🔍 测试</button>
                <button class="btn btn-sm btn-danger" onclick="deleteProxy(${p.id})">删除</button>
            </td></tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}
async function testProxy(id) {
    const el = document.getElementById('proxy-ip-' + id);
    if (el) el.innerHTML = '<span style="color:var(--yellow)">测试中...</span>';
    try {
        const res = await api(`/proxies/${id}/test`, { method: 'POST' });
        if (res.ok) {
            toast(`✅ ${res.proxy} → ${res.ip}`);
            if (el) el.innerHTML = `<span class="badge badge-green">${res.ip}</span>`;
        } else {
            toast(`❌ ${res.proxy}: ${res.error}`, 'error');
            if (el) el.innerHTML = `<span class="badge badge-red">失败</span>`;
        }
    } catch (e) { toast(e.message, 'error'); if (el) el.innerHTML = '<span class="badge badge-red">错误</span>'; }
}
async function testAllProxies() {
    showLoading('正在测试所有代理，请耐心等待...');
    try {
        const res = await api('/proxies/test-all', { method: 'POST' });
        toast(`测试完成: ${res.ok}/${res.total} 可用`);
        for (const r of res.results) {
            const el = document.getElementById('proxy-ip-' + r.id);
            if (el) el.innerHTML = r.ok ? `<span class="badge badge-green">${r.ip}</span>` : `<span class="badge badge-red">失败</span>`;
        }
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}
async function createProxy(e) { e.preventDefault(); try { const data = { protocol: document.getElementById('proxy-protocol').value, host: document.getElementById('proxy-host').value, port: parseInt(document.getElementById('proxy-port').value), username: document.getElementById('proxy-user').value || null, password: document.getElementById('proxy-pass').value || null }; await api('/proxies', { method: 'POST', body: JSON.stringify(data) }); hideModal('proxy-modal'); toast('代理已添加'); loadProxies(); } catch (e) { toast(e.message, 'error'); } }
async function toggleProxy(id) { try { const res = await api(`/proxies/${id}/toggle`, { method: 'PUT' }); toast(res.is_active ? '已启用' : '已禁用'); loadProxies(); } catch (e) { toast(e.message, 'error'); } }
async function deleteProxy(id) { if (!confirm('确定删除？')) return; showLoading('正在删除代理...'); try { await api(`/proxies/${id}`, { method: 'DELETE' }); toast('已删除'); loadProxies(); } catch (e) { toast(e.message, 'error'); } finally { hideLoading(); } }
async function batchCreateProxies(e) { e.preventDefault(); showLoading('正在批量添加代理...'); try { const data = { text: document.getElementById('batch-proxy-text').value }; const res = await api('/proxies/batch-text', { method: 'POST', body: JSON.stringify(data) }); hideModal('batch-proxy-modal'); toast(`批量添加: ${res.created} 成功`); document.getElementById('batch-proxy-text').value = ''; loadProxies(); } catch (e) { toast(e.message, 'error'); } finally { hideLoading(); } }

// ==================== Projects ====================
let projectsCache = [];
async function loadProjects() {
    try {
        projectsCache = await api('/projects');
        document.getElementById('projects-grid').innerHTML = projectsCache.map(p => `<div class="card"><h4>${p.name}</h4><p>${p.description || '无描述'}</p>${p.config_template ? `<div class="config-tags">${Object.keys(p.config_template).map(k => `<span class="config-tag">${k}</span>`).join('')}</div>` : ''}</div>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}
async function createProject(e) { e.preventDefault(); try { await api('/projects', { method: 'POST', body: JSON.stringify({ name: document.getElementById('proj-name').value, description: document.getElementById('proj-desc').value || null, install_script: document.getElementById('proj-script').value, health_check_cmd: document.getElementById('proj-health').value || null }) }); hideModal('project-modal'); toast('项目已添加'); loadProjects(); } catch (e) { toast(e.message, 'error'); } }

// ==================== Tasks ====================
let tasksCache = [];

async function loadTasks() {
    try {
        tasksCache = await api('/tasks');
        renderTasks(tasksCache);
    } catch (e) { toast(e.message, 'error'); }
}

function filterTasks() {
    const q = (document.getElementById('task-search')?.value || '').toLowerCase();
    if (!q) { renderTasks(tasksCache); return; }
    const filtered = tasksCache.filter(t =>
        (t.project_name || '').toLowerCase().includes(q) || (t.instance_ip || '').includes(q) ||
        (t.status || '').includes(q) || (t.log || '').toLowerCase().includes(q) || String(t.id).includes(q)
    );
    renderTasks(filtered);
}

function renderTasks(list) {
    document.querySelector('#tasks-table tbody').innerHTML = list.map(t => `<tr>
        <td>${t.id}</td><td>${t.project_name}</td><td>${t.instance_ip || '-'}</td><td>${stateBadge(t.status)}</td>
        <td title="${t.log || ''}">${(t.log || '-').substring(0, 40)}</td><td>${t.created_at}</td>
        <td class="action-btns">
            <button class="btn btn-sm btn-secondary" onclick="checkHealth(${t.id})">健康检查</button>
            <button class="btn btn-sm btn-danger" onclick="deleteTask(${t.id})">删除</button>
        </td></tr>`).join('');
}
async function deployTask(e) {
    e.preventDefault();
    showLoading('正在部署项目，请耐心等待...');
    try {
        const cf = {};
        document.querySelectorAll('#deploy-config-fields input').forEach(f => { if (f.value) cf[f.dataset.key] = f.value; });
        const projectId = parseInt(document.getElementById('deploy-project').value);
        const config = Object.keys(cf).length ? cf : null;
        const selected = Array.from(document.getElementById('deploy-instance').selectedOptions).map(o => parseInt(o.value));
        if (selected.length > 1) {
            const res = await api('/tasks/batch-deploy', { method: 'POST', body: JSON.stringify({ instance_ids: selected, project_id: projectId, config }) });
            hideModal('deploy-modal');
            toast(`批量部署: ${res.deployed?.length || 0} 成功, ${res.errors?.length || 0} 失败`);
        } else {
            const res = await api('/tasks/deploy', { method: 'POST', body: JSON.stringify({ instance_id: selected[0], project_id: projectId, config }) });
            hideModal('deploy-modal');
            toast(`部署: ${res.status}`);
        }
        loadTasks();
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}
async function deleteTask(id) {
    if (!confirm('确定删除此部署任务？')) return;
    showLoading('正在删除任务...');
    try { await api(`/tasks/${id}`, { method: 'DELETE' }); toast('任务已删除'); loadTasks(); } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}
async function checkHealth(id) { showLoading('正在健康检查...'); try { const res = await api(`/tasks/${id}/health`, { method: 'POST' }); toast(`健康检查: ${res.status} ${res.message || ''}`, 'info'); } catch (e) { toast(e.message, 'error'); } finally { hideLoading(); } }

// ==================== Helpers ====================
async function loadAccountOptions(sid) {
    try {
        const l = accountsCache.length ? accountsCache : await api('/accounts');
        const sel = document.getElementById(sid);
        sel.innerHTML = l.map(a => {
            const name = a.email || a.name;
            const cnt = a.instance_count || 0;
            const grp = a.group_name ? ` [${a.group_name}]` : '';
            const label = `${name} (${a.default_region})${grp} · 实例: ${cnt}`;
            const search = `${name} ${a.name || ''} ${a.default_region} ${a.group_name || ''} ${a.note || ''} ${a.aws_account_id || ''}`.toLowerCase();
            return `<option value="${a.id}" data-search="${search}" data-count="${cnt}">${label}</option>`;
        }).join('');
        // 触发搜索计数更新
        if (sid === 'launch-account') filterMultiSelect('launch-account-search', 'launch-account', 'launch-account-count');
    } catch(e){}
}
async function loadInstanceOptions(sid) {
    try {
        const l = await api('/instances');
        const sel = document.getElementById(sid);
        sel.innerHTML = l.filter(i => i.state === 'running').map(i => {
            const ip = i.public_ip || i.instance_id;
            const projs = (i.projects && i.projects !== '-') ? i.projects : '';
            const projText = projs ? ` · 已部署: ${projs}` : ' · 未部署';
            const label = `${ip} (${i.account_name} / ${i.region})${projText}`;
            const search = `${ip} ${i.instance_id || ''} ${i.account_name || ''} ${i.region || ''} ${i.instance_type || ''} ${projs}`.toLowerCase();
            return `<option value="${i.id}" data-search="${search}" data-projects="${projs}" selected>${label}</option>`;
        }).join('');
        if (sid === 'deploy-instance') filterMultiSelect('deploy-instance-search', 'deploy-instance', 'deploy-instance-count');
    } catch(e){}
}

// ==================== Searchable Multi-Select (modal) ====================
function filterMultiSelect(searchId, selectId, countId) {
    const q = (document.getElementById(searchId)?.value || '').trim().toLowerCase();
    const sel = document.getElementById(selectId);
    if (!sel) return;
    let visible = 0;
    const total = sel.options.length;
    for (const opt of sel.options) {
        const hay = (opt.dataset.search || opt.textContent || '').toLowerCase();
        const match = !q || hay.includes(q);
        opt.hidden = !match;
        // 原生 <select> 里 hidden 在部分浏览器不生效, 额外用样式
        opt.style.display = match ? '' : 'none';
        if (!match && opt.selected) opt.selected = false;
        if (match) visible++;
    }
    const cnt = document.getElementById(countId);
    if (cnt) cnt.textContent = `${visible} / ${total}`;
}

function multiSelectAll(selectId, selectVisible) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    for (const opt of sel.options) {
        if (selectVisible) {
            if (opt.style.display !== 'none' && !opt.hidden) opt.selected = true;
        } else {
            opt.selected = false;
        }
    }
    sel.dispatchEvent(new Event('change'));
}
async function loadProjectOptions(sid) { try { if (!projectsCache.length) projectsCache = await api('/projects'); document.getElementById(sid).innerHTML = projectsCache.map(p => `<option value="${p.id}">${p.name}</option>`).join(''); } catch(e){} }
async function loadProjectConfig() { const pid = document.getElementById('deploy-project').value; const c = document.getElementById('deploy-config-fields'); c.innerHTML = ''; try { const p = await api(`/projects/${pid}`); if (p.config_template) for (const [k,v] of Object.entries(p.config_template)) c.innerHTML += `<div class="form-group"><label>${k}</label><input type="text" data-key="${k}" value="${v}" placeholder="${k}"></div>`; } catch(e){} }

async function loadInstanceTypes() {
    try {
        const types = await api('/instances/types');
        const sel = document.getElementById('launch-type');
        sel.innerHTML = types.map(t => `<option value="${t.type}">${t.type} (${t.vcpu}C/${t.mem}) [${t.category}]</option>`).join('');
        refreshSearchSelect('launch-type');
    } catch(e){}
}
async function loadAmiOptions() {
    try {
        const amis = await api('/instances/amis');
        const sel = document.getElementById('launch-ami');
        if (sel) sel.innerHTML = amis.map(a => `<option value="${a.id}">${a.name}</option>`).join('');
        refreshSearchSelect('launch-ami');
    } catch(e){}
}

// ==================== Searchable Select Component ====================
function createSearchSelect(selectEl) {
    if (!selectEl || selectEl.dataset.ssInit) return;
    selectEl.dataset.ssInit = '1';
    selectEl.style.display = 'none';

    const wrap = document.createElement('div');
    wrap.className = 'search-select-wrap';
    selectEl.parentNode.insertBefore(wrap, selectEl);
    wrap.appendChild(selectEl);

    const display = document.createElement('div');
    display.className = 'ss-display';
    wrap.appendChild(display);

    const dropdown = document.createElement('div');
    dropdown.className = 'ss-dropdown';
    const search = document.createElement('input');
    search.className = 'ss-search';
    search.type = 'text';
    search.placeholder = '🔍 搜索...';
    dropdown.appendChild(search);
    const list = document.createElement('div');
    list.className = 'ss-list';
    dropdown.appendChild(list);
    wrap.appendChild(dropdown);

    function renderItems() {
        list.innerHTML = '';
        const opts = selectEl.options;
        if (!opts.length) { list.innerHTML = '<div class="ss-empty">无选项</div>'; return; }
        for (let i = 0; i < opts.length; i++) {
            const item = document.createElement('div');
            item.className = 'ss-item' + (opts[i].selected ? ' selected' : '');
            item.textContent = opts[i].textContent;
            item.dataset.idx = i;
            item.addEventListener('click', () => {
                selectEl.selectedIndex = i;
                selectEl.dispatchEvent(new Event('change'));
                updateDisplay();
                close();
            });
            list.appendChild(item);
        }
    }

    function updateDisplay() {
        const sel = selectEl.options[selectEl.selectedIndex];
        display.textContent = sel ? sel.textContent : '请选择';
    }

    function open() {
        wrap.classList.add('open');
        search.value = '';
        renderItems();
        search.focus();
    }
    function close() { wrap.classList.remove('open'); }

    display.addEventListener('click', (e) => {
        e.stopPropagation();
        if (wrap.classList.contains('open')) close(); else open();
        // close others
        document.querySelectorAll('.search-select-wrap.open').forEach(w => { if (w !== wrap) w.classList.remove('open'); });
    });

    search.addEventListener('input', () => {
        const q = search.value.toLowerCase();
        list.querySelectorAll('.ss-item').forEach(item => {
            item.classList.toggle('hidden', !item.textContent.toLowerCase().includes(q));
        });
    });
    search.addEventListener('click', e => e.stopPropagation());

    document.addEventListener('click', () => close());
    wrap.addEventListener('click', e => e.stopPropagation());

    // observe option changes
    const observer = new MutationObserver(() => { updateDisplay(); });
    observer.observe(selectEl, { childList: true, subtree: true });

    updateDisplay();
    return { refresh: () => { renderItems(); updateDisplay(); } };
}

// 对所有非 multiple 的 select 应用搜索功能
const _ssInstances = {};
function initSearchSelects() {
    document.querySelectorAll('select:not([multiple]):not([data-ss-init])').forEach(sel => {
        // 跳过太少选项的 (<=3)
        if (sel.options.length <= 3 && !sel.id.includes('type') && !sel.id.includes('region') && !sel.id.includes('ami') && !sel.id.includes('volume')) return;
        _ssInstances[sel.id] = createSearchSelect(sel);
    });
}

function refreshSearchSelect(id) {
    const sel = document.getElementById(id);
    if (!sel) return;
    if (!sel.dataset.ssInit) {
        _ssInstances[id] = createSearchSelect(sel);
    } else if (_ssInstances[id]) {
        _ssInstances[id].refresh();
    }
    // 更新 display text
    const wrap = sel.closest('.search-select-wrap');
    if (wrap) {
        const display = wrap.querySelector('.ss-display');
        const opt = sel.options[sel.selectedIndex];
        if (display && opt) display.textContent = opt.textContent;
    }
}

// ==================== Lightsail (光帆) ====================
let _lsAccountId = null;
let _lsBlueprintsAll = [];   // 完整蓝图列表
let _lsBundlesAll = [];      // 完整套餐列表
let _lsRegions = [];         // 区域列表
let _lsInstanceData = [];    // 当前账号的全部实例（前端过滤用）

async function showLightsail(accountId) {
    _lsAccountId = accountId;
    const a = accountsCache.find(x => x.id === accountId);
    const accName = a ? (a.email || a.name || ('#' + accountId)) : ('#' + accountId);
    const hint = document.getElementById('lightsail-acc-hint');
    if (hint) hint.textContent = `账号: ${accName}  ·  默认区域: ${a?.default_region || '-'}  ·  仅展示该 AWS 账号下的 Lightsail 实例`;

    // 重置 UI
    const searchEl = document.getElementById('ls-search');
    if (searchEl) searchEl.value = '';
    const stateEl = document.getElementById('ls-state-filter');
    if (stateEl) stateEl.value = '';
    document.getElementById('ls-region-filter').innerHTML = '<option value="">全部区域 (扫描所有)</option>';
    document.getElementById('lightsail-summary').innerHTML = '';
    document.getElementById('lightsail-list').innerHTML =
        '<div style="text-align:center;padding:30px;color:var(--text2)">正在加载 Lightsail 实例...</div>';

    document.getElementById('lightsail-modal').classList.add('show');
    loadLightsailInstances();
}

async function loadLightsailInstances() {
    if (!_lsAccountId) return;
    const body = document.getElementById('lightsail-list');
    const summary = document.getElementById('lightsail-summary');
    body.innerHTML = `<div style="text-align:center;padding:30px"><div class="spinner"></div><div style="margin-top:12px;color:var(--text2)">正在并发扫描所有 Lightsail 区域（约 10-20 秒）...</div></div>`;
    summary.innerHTML = '';
    try {
        // 扫描所有区域，前端做区域筛选 (与 EC2 详情一致)
        const url = `/lightsail/instances?account_id=${_lsAccountId}&_ts=${Date.now()}`;
        const res = await api(url);
        _lsInstanceData = res.instances || [];

        // 填充区域下拉
        const regions = [...new Set(_lsInstanceData.map(i => i.region))].sort();
        const regSel = document.getElementById('ls-region-filter');
        const cur = regSel.value;
        regSel.innerHTML = '<option value="">全部区域 (' + _lsInstanceData.length + ')</option>' +
            regions.map(r => {
                const cnt = _lsInstanceData.filter(i => i.region === r).length;
                return `<option value="${r}" ${r === cur ? 'selected' : ''}>${r} (${cnt})</option>`;
            }).join('');

        renderLightsailInstances();
    } catch (e) {
        body.innerHTML = `<div style="color:var(--red);padding:20px;text-align:center">获取失败: ${e.message}</div>`;
        toast(e.message, 'error');
    }
}

function renderLightsailInstances() {
    const body = document.getElementById('lightsail-list');
    const summary = document.getElementById('lightsail-summary');
    if (!_lsInstanceData) return;

    const region = document.getElementById('ls-region-filter')?.value || '';
    const state = document.getElementById('ls-state-filter')?.value || '';
    const q = (document.getElementById('ls-search')?.value || '').toLowerCase();

    let list = _lsInstanceData;
    if (region) list = list.filter(i => i.region === region);
    if (state) list = list.filter(i => i.state === state);
    if (q) list = list.filter(i =>
        (i.name || '').toLowerCase().includes(q) ||
        (i.public_ip || '').includes(q) ||
        (i.private_ip || '').includes(q) ||
        (i.blueprint_name || '').toLowerCase().includes(q) ||
        (i.blueprint_id || '').toLowerCase().includes(q) ||
        (i.bundle_id || '').toLowerCase().includes(q)
    );

    // 摘要徽章
    const states = {};
    for (const i of list) states[i.state] = (states[i.state] || 0) + 1;
    const stateBadges = Object.entries(states).map(([s, c]) => {
        const color = s === 'running' ? 'green' : (s === 'stopped' ? 'red' : (s === 'pending' ? 'yellow' : 'gray'));
        return `<span class="badge badge-${color}">${s} ${c}</span>`;
    }).join('');
    summary.innerHTML = `
        <div class="stat-card" style="padding:10px 14px;flex:0 0 auto"><div class="label">Lightsail 实例</div><div class="value blue" style="font-size:20px">${list.length}</div></div>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">${stateBadges}</div>
    `;

    if (!list.length) {
        body.innerHTML = '<div style="padding:30px;text-align:center;color:var(--text2);border:1px dashed var(--border);border-radius:8px">该账号下没有 Lightsail 实例。点击右上角「+ 创建 Lightsail 实例」开始开机。</div>';
        return;
    }

    body.innerHTML = list.map(_renderLsInstanceCard).join('');
}

function _renderLsInstanceCard(i) {
    const stateColor = i.state === 'running' ? 'green' : (i.state === 'stopped' ? 'red' : (i.state === 'pending' ? 'yellow' : 'gray'));
    const uptime = _humanDuration(i.created_at);
    const createdStr = _fmtTime(i.created_at);
    const safeName = (i.name || '').replace(/'/g, "\\'");
    const region = i.region;
    const ipBadge = i.is_static_ip
        ? '<span class="badge badge-blue" style="font-size:10px;margin-left:6px">静态 IP</span>'
        : (i.public_ip ? '<span class="badge badge-gray" style="font-size:10px;margin-left:6px">动态</span>' : '');
    const sshUser = i.ssh_username || 'ubuntu';
    const publicIp = i.public_ip || '-';
    const privateIp = i.private_ip || '-';

    return `
    <div class="acc-card" style="margin-bottom:12px">
        <div class="acc-card-header">
            <div class="acc-card-left" style="flex-wrap:wrap">
                <span class="acc-num">⛵</span>
                <span class="acc-name" title="${i.name}"><b>${i.name.length > 32 ? i.name.substring(0,32)+'...' : i.name}</b></span>
                <span class="badge badge-${stateColor}">${i.state}</span>
                <span class="acc-flag">${region}</span>
                <span class="acc-age" title="${createdStr}">⏱ ${uptime}</span>
                <span class="acc-vcpu" title="规格">⚡ ${i.cpu || '?'}C / ${i.ram || '?'}GB</span>
            </div>
        </div>
        <div style="padding:8px 12px;display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:6px 14px;font-size:12px">
            <div><span style="color:var(--text2)">实例名称</span> <code style="font-size:11px">${i.name}</code>
                <button class="btn btn-sm btn-secondary acc-copy-btn" onclick="copyToClipboard('${safeName}', this)" title="复制">📋</button>
            </div>
            <div><span style="color:var(--text2)">可用区</span> ${i.availability_zone || '-'}</div>
            <div><span style="color:var(--text2)">公网 IP</span> <code>${publicIp}</code>${ipBadge}
                ${publicIp !== '-' ? `<button class="btn btn-sm btn-secondary acc-copy-btn" onclick="copyToClipboard('${publicIp}', this)">📋</button>` : ''}
            </div>
            <div><span style="color:var(--text2)">内网 IP</span> <code>${privateIp}</code></div>
            <div><span style="color:var(--text2)">IPv6</span> <code style="font-size:11px">${i.ipv6 || '-'}</code></div>
            <div><span style="color:var(--text2)">SSH 用户</span> ${sshUser}</div>
            <div style="grid-column:span 2"><span style="color:var(--text2)">蓝图</span> ${i.blueprint_name || '-'} <code style="font-size:11px;margin-left:6px">${i.blueprint_id || ''}</code></div>
            <div><span style="color:var(--text2)">套餐</span> <code style="font-size:11px">${i.bundle_id || '-'}</code></div>
            <div><span style="color:var(--text2)">规格</span> <b>${i.cpu || '?'} vCPU · ${i.ram || '?'} GB RAM</b></div>
            <div><span style="color:var(--text2)">磁盘</span> ${(i.disks || []).map(d => `${d.name} (${d.size}GB)`).join(' / ') || '-'}</div>
            <div><span style="color:var(--text2)">密钥对</span> ${i.key_pair_name || '-'}</div>
            <div><span style="color:var(--text2)">创建时间</span> ${createdStr}</div>
            <div><span style="color:var(--text2)">已运行</span> <b style="color:var(--green)">${uptime}</b></div>
            <div style="grid-column:span 2"><span style="color:var(--text2)">ARN</span> <code style="font-size:10px;word-break:break-all">${i.arn || '-'}</code></div>
        </div>
        <div class="acc-card-footer" style="flex-wrap:wrap">
            ${i.state === 'stopped' ? `<button class="btn btn-sm btn-primary" onclick="lightsailAction('${safeName}','${region}','start')">▶ 启动</button>` : ''}
            ${i.state === 'running' ? `<button class="btn btn-sm btn-secondary" onclick="lightsailAction('${safeName}','${region}','stop')">⏹ 停止</button>` : ''}
            ${i.state === 'running' ? `<button class="btn btn-sm btn-secondary" onclick="lightsailAction('${safeName}','${region}','reboot')">🔄 重启</button>` : ''}
            <button class="btn btn-sm btn-secondary" title="开放常用端口" onclick="lightsailAction('${safeName}','${region}','open-ports')">🔓 开放端口</button>
            ${publicIp !== '-' ? `<button class="btn btn-sm btn-secondary" onclick="copyToClipboard('ssh ${sshUser}@${publicIp}', this)" title="复制 SSH 命令">SSH</button>` : ''}
            <button class="btn btn-sm btn-secondary" onclick="copyToClipboard('${safeName}', this)" title="复制名称">📋 名称</button>
            <button class="btn btn-sm btn-danger" onclick="lightsailDelete('${safeName}','${region}')">🗑 删除</button>
        </div>
    </div>`;
}

async function lightsailAction(name, region, action) {
    const labels = { start: '启动', stop: '停止', reboot: '重启', 'open-ports': '开放端口' };
    showLoading(`正在${labels[action]} Lightsail 实例 ${name}...`);
    try {
        await api(`/lightsail/instances/${encodeURIComponent(name)}/${action}?account_id=${_lsAccountId}&region=${region}`, { method: 'POST' });
        toast(`${labels[action]}指令已发送`);
        setTimeout(loadLightsailInstances, 1500);
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}

async function lightsailDelete(name, region) {
    if (!confirm(`确定删除 Lightsail 实例 "${name}" (${region}) ？此操作不可恢复！`)) return;
    showLoading('正在删除 Lightsail 实例...');
    try {
        await api(`/lightsail/instances/${encodeURIComponent(name)}?account_id=${_lsAccountId}&region=${region}`, { method: 'DELETE' });
        toast(`已删除 ${name}`);
        setTimeout(loadLightsailInstances, 1500);
    } catch (e) { toast(e.message, 'error'); }
    finally { hideLoading(); }
}

async function showLightsailLaunch() {
    if (!_lsAccountId) { toast('请先选择账号', 'error'); return; }
    const a = accountsCache.find(x => x.id === _lsAccountId);

    document.getElementById('ls-name').value = `ls-${Date.now().toString(36)}`;
    document.getElementById('ls-count').value = '1';
    document.getElementById('ls-userdata').value = '';

    // 区域下拉
    const regSel = document.getElementById('ls-region');
    if (!_lsRegions.length) {
        try { _lsRegions = await api('/lightsail/regions'); } catch (e) { _lsRegions = []; }
    }
    const def = a?.default_region || (_lsRegions[0]?.code || 'us-east-1');
    regSel.innerHTML = _lsRegions.map(r =>
        `<option value="${r.code}" ${r.code === def ? 'selected' : ''}>${r.display} (${r.code})</option>`
    ).join('');

    // 默认分类: Linux/Unix / 标准型 Linux
    document.getElementById('ls-blueprint-category').value = 'Linux/Unix';
    document.getElementById('ls-bundle-category').value = '标准型 Linux';

    document.getElementById('lightsail-launch-modal').classList.add('show');

    // 并行加载可用区/蓝图/套餐
    onLightsailRegionChange();
}

async function onLightsailRegionChange() {
    if (!_lsAccountId) return;
    const region = document.getElementById('ls-region').value;
    if (!region) return;

    // 1) 可用区
    try {
        const azSel = document.getElementById('ls-az');
        azSel.innerHTML = '<option value="">加载中...</option>';
        const zones = await api(`/lightsail/availability-zones?account_id=${_lsAccountId}&region=${region}`);
        azSel.innerHTML = '<option value="">自动 (使用第一个可用区)</option>' +
            zones.map(z => `<option value="${z}">${z}</option>`).join('');
    } catch (e) {
        document.getElementById('ls-az').innerHTML = '<option value="">自动</option>';
    }

    // 2) 蓝图
    try {
        const bp = document.getElementById('ls-blueprint');
        bp.innerHTML = '<option value="">加载蓝图中...</option>';
        _lsBlueprintsAll = await api(`/lightsail/blueprints?account_id=${_lsAccountId}&region=${region}`);
        filterLightsailBlueprints();
    } catch (e) {
        document.getElementById('ls-blueprint').innerHTML = `<option value="">加载失败: ${e.message}</option>`;
    }

    // 3) 套餐
    try {
        const bd = document.getElementById('ls-bundle');
        bd.innerHTML = '<option value="">加载套餐中...</option>';
        _lsBundlesAll = await api(`/lightsail/bundles?account_id=${_lsAccountId}&region=${region}`);
        filterLightsailBundles();
    } catch (e) {
        document.getElementById('ls-bundle').innerHTML = `<option value="">加载失败: ${e.message}</option>`;
    }
}

function filterLightsailBlueprints() {
    const cat = document.getElementById('ls-blueprint-category').value;
    const sel = document.getElementById('ls-blueprint');
    let list = _lsBlueprintsAll;
    if (cat) list = list.filter(b => b.category === cat);
    if (!list.length) { sel.innerHTML = '<option value="">无可用蓝图</option>'; return; }
    sel.innerHTML = list.map(b => {
        const label = `${b.name}${b.platform === 'WINDOWS' ? ' 🪟' : (b.type === 'app' ? ' 📦' : ' 🐧')} - ${b.description || b.id}`;
        return `<option value="${b.id}">${label}</option>`;
    }).join('');
    refreshSearchSelect('ls-blueprint');
}

function filterLightsailBundles() {
    const cat = document.getElementById('ls-bundle-category').value;
    const sel = document.getElementById('ls-bundle');
    let list = _lsBundlesAll;
    if (cat) list = list.filter(b => b.category === cat);
    if (!list.length) { sel.innerHTML = '<option value="">无可用套餐</option>'; return; }
    // 按价格排序
    list = [...list].sort((a, b) => (a.price || 0) - (b.price || 0));
    sel.innerHTML = list.map(b => {
        const desc = b.description || `${b.ram} GB · ${b.cpu} vCPU · ${b.disk} GB`;
        const price = b.price ? `$${b.price}/月` : '';
        return `<option value="${b.id}">${b.name} - ${desc} ${price ? '· ' + price : ''}</option>`;
    }).join('');
    refreshSearchSelect('ls-bundle');
}

async function lightsailLaunch(e) {
    e.preventDefault();
    if (!_lsAccountId) { toast('账号丢失，请重新打开', 'error'); return; }

    const data = {
        account_id: _lsAccountId,
        region: document.getElementById('ls-region').value,
        availability_zone: document.getElementById('ls-az').value || null,
        blueprint_id: document.getElementById('ls-blueprint').value,
        bundle_id: document.getElementById('ls-bundle').value,
        instance_name: document.getElementById('ls-name').value.trim(),
        count: parseInt(document.getElementById('ls-count').value) || 1,
        user_data: document.getElementById('ls-userdata').value || null,
        open_default_ports: document.getElementById('ls-open-ports').checked,
    };

    if (!data.region || !data.blueprint_id || !data.bundle_id || !data.instance_name) {
        toast('请填写所有必填项', 'error');
        return;
    }

    showLoading(`正在创建 ${data.count} 个 Lightsail 实例...`);
    try {
        const res = await api('/lightsail/launch', { method: 'POST', body: JSON.stringify(data) });
        hideModal('lightsail-launch-modal');
        const names = (res.instance_names || []).join(', ');
        toast(`✅ 已创建 ${data.count} 个实例: ${names}`);
        setTimeout(loadLightsailInstances, 1500);
    } catch (e) {
        toast(e.message, 'error');
    } finally { hideLoading(); }
}

// ==================== Init ====================
if (checkAuth()) { showUserInfo(); loadDashboard(); setTimeout(initSearchSelects, 100); }
