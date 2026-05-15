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

function filterAccounts() {
    const q = (document.getElementById('account-search')?.value || '').toLowerCase();
    const group = document.getElementById('account-group-filter')?.value || '';
    const filtered = accountsCache.filter(a => {
        if (group && a.group_name !== group) return false;
        if (!q) return true;
        return (a.email || '').toLowerCase().includes(q) || (a.name || '').toLowerCase().includes(q) ||
            (a.group_name || '').toLowerCase().includes(q) || (a.note || '').toLowerCase().includes(q) ||
            (a.aws_account_id || '').includes(q) || String(a.id).includes(q);
    });
    renderAccountCards(filtered);
}

function renderAccountCards(list) {
    const grid = document.getElementById('accounts-grid');
    if (!grid) return;
    grid.innerHTML = list.map(a => {
        const displayName = a.email || a.name || a.access_key_id;
        const age = timeAgo(a.register_time || a.added_at);
        const flag = a.country_flag || '';
        const vcpuUsage = a.total_usage || 0;
        const vcpuText = a.max_on_demand ? `${vcpuUsage}/${a.max_on_demand} vCPUs` : '';
        const checked = selectedAccounts.has(a.id) ? 'checked' : '';
        return `
        <div class="acc-card" data-id="${a.id}">
            <div class="acc-card-header">
                <div class="acc-card-left">
                    <input type="checkbox" class="acc-check" data-id="${a.id}" ${checked} onchange="toggleAccountSelect(${a.id}, this.checked)">
                    <span class="acc-num">#${a.id}</span>
                    <span class="acc-name" title="${displayName}">${displayName.length > 22 ? displayName.substring(0,22)+'...' : displayName}</span>
                    ${flag ? `<span class="acc-flag">${flag}</span>` : ''}
                    ${age ? `<span class="acc-age">${age}</span>` : ''}
                    ${vcpuText ? `<span class="acc-vcpu" onclick="showVcpuDetail(${a.id})" title="点击查看详情">⚡ ${vcpuText}</span>` : ''}
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
            </div>
            <div class="acc-card-footer">
                <button class="btn btn-sm btn-secondary" onclick="editAccountInline(${a.id})">✏️</button>
                <button class="btn btn-sm btn-danger" onclick="deleteAccount(${a.id})">🗑</button>
                <button class="btn btn-sm btn-secondary" onclick="detectAccount(${a.id})">🔍 检测</button>
                <button class="btn btn-sm btn-secondary" onclick="detectAI(${a.id})">🤖 AI</button>
                <button class="btn btn-sm btn-secondary" onclick="showVcpuDetail(${a.id})">⚡ vCPU</button>
                <button class="btn btn-sm btn-secondary" onclick="showBilling(${a.id})">💰 账单</button>
                <span class="acc-footer-spacer"></span>
                <button class="btn btn-sm btn-secondary" onclick="window.location.hash='instances';loadInstances()">EC2 实例</button>
                <button class="btn btn-sm btn-secondary" onclick="showLightsail(${a.id})">⛵ Lightsail 实例</button>
            </div>
        </div>`;
    }).join('');
}

function toggleCardExpand(id) {
    const el = document.getElementById('acc-expand-' + id);
    if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

function viewAccountInstances(accountId) {
    const a = accountsCache.find(x => x.id === accountId);
    const name = a ? (a.email || a.name || String(accountId)) : String(accountId);
    // 切换到实例 Tab
    document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    const navInst = document.querySelector('.nav-item[data-tab="instances"]');
    const tabInst = document.getElementById('tab-instances');
    if (navInst) navInst.classList.add('active');
    if (tabInst) tabInst.classList.add('active');
    // 设置搜索框并触发过滤
    const search = document.getElementById('instance-search');
    if (search) search.value = name.split('@')[0] || name;
    loadInstances().then(() => filterInstances());
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

// ==================== AI Detection ====================
async function detectAI(id) {
    const a = accountsCache.find(x => x.id === id);
    const body = document.getElementById('ai-body');
    const modal = document.getElementById('ai-modal');
    modal.classList.add('show');
    body.innerHTML = `<div style="text-align:center;padding:30px"><div class="spinner"></div><div style="margin-top:12px;color:var(--text2)">正在检测 AI 能力，可能需要 30-60 秒...</div></div>`;

    try {
        const res = await api(`/accounts/${id}/detect-ai`, { method: 'POST' });
        renderAiResults(res, a);
        toast('AI 检测完成');
    } catch (e) {
        body.innerHTML = `<div style="color:var(--red);padding:20px;text-align:center">检测失败: ${e.message}</div>`;
        toast(e.message, 'error');
    }
}

function renderAiResults(data, account) {
    const body = document.getElementById('ai-body');
    const accName = account ? (account.email || account.name) : '';
    let html = '';

    // Header
    if (accName) html += `<div style="padding:8px 0;margin-bottom:8px;border-bottom:1px solid var(--border);color:var(--text2);font-size:13px">账号: ${accName}</div>`;

    // SSO / Kiro
    const ssoCount = data.sso_instances || 0;
    html += `<div class="ai-section">
        <div class="ai-section-title">🔐 Kiro / IAM Identity Center (SSO)</div>
        <div class="ai-section-body">
            ${ssoCount > 0
                ? `<span class="badge badge-green">已启用</span> ${ssoCount} 个 SSO 实例 (可能有 Kiro 订阅)`
                : `<span class="badge badge-gray">未检测到</span> 无 SSO 实例`}
        </div>
    </div>`;

    // Licenses
    const licenses = data.licenses || [];
    html += `<div class="ai-section">
        <div class="ai-section-title">📜 License Manager 许可证</div>
        <div class="ai-section-body">`;
    if (licenses.length) {
        html += `<table class="ai-table"><thead><tr><th>名称</th><th>产品</th><th>状态</th></tr></thead><tbody>`;
        for (const l of licenses) {
            html += `<tr><td>${l.name}</td><td>${l.product}</td><td>${l.status}</td></tr>`;
        }
        html += '</tbody></table>';
    } else {
        html += '<span style="color:var(--text2)">无许可证</span>';
    }
    html += '</div></div>';

    // Bedrock Models
    const models = data.bedrock_models || [];
    html += `<div class="ai-section">
        <div class="ai-section-title">🧠 Bedrock Anthropic 模型 (us-east-1)</div>
        <div class="ai-section-body">`;
    if (models.length) {
        html += `<div style="margin-bottom:6px;color:var(--text2);font-size:12px">共 ${models.length} 个模型</div>`;
        html += `<table class="ai-table"><thead><tr><th>模型 ID</th><th>名称</th><th>输入</th><th>输出</th></tr></thead><tbody>`;
        for (const m of models) {
            html += `<tr>
                <td><code style="font-size:11px">${m.id}</code></td>
                <td>${m.name}</td>
                <td>${(m.input || []).join(', ')}</td>
                <td>${(m.output || []).join(', ')}</td>
            </tr>`;
        }
        html += '</tbody></table>';
    } else {
        html += '<span style="color:var(--text2)">未检测到 Anthropic 模型</span>';
    }
    html += '</div></div>';

    // Bedrock Quotas
    const quotas = data.bedrock_quotas || [];
    html += `<div class="ai-section">
        <div class="ai-section-title">📊 Bedrock 关键配额 (Anthropic/Claude)</div>
        <div class="ai-section-body">`;
    if (quotas.length) {
        html += `<table class="ai-table"><thead><tr><th>配额名称</th><th>值</th></tr></thead><tbody>`;
        for (const q of quotas) {
            const val = q.value >= 1000000 ? (q.value / 1000000).toFixed(1) + 'M' : q.value >= 1000 ? (q.value / 1000).toFixed(1) + 'K' : q.value;
            html += `<tr><td style="font-size:12px">${q.name}</td><td style="font-weight:bold;color:var(--blue)">${val}</td></tr>`;
        }
        html += '</tbody></table>';
    } else {
        html += '<span style="color:var(--text2)">未检测到 Bedrock 配额 (可能未开通)</span>';
    }
    html += '</div></div>';

    body.innerHTML = html;
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
        const res = await api(`/accounts/${_billingAccountId}/billing?year=${year}&month=${month}&granularity=DAILY`);
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

async function showLightsail(accountId) {
    _lsAccountId = accountId;
    const a = accountsCache.find(x => x.id === accountId);
    const accName = a ? (a.email || a.name || ('#' + accountId)) : ('#' + accountId);
    const hint = document.getElementById('lightsail-acc-hint');
    if (hint) hint.textContent = `账号: ${accName}  ·  默认区域: ${a?.default_region || '-'}`;

    // 加载区域列表 (用于过滤下拉)
    try {
        if (!_lsRegions.length) _lsRegions = await api('/lightsail/regions');
        const sel = document.getElementById('ls-region-filter');
        const def = a?.default_region || '';
        sel.innerHTML = '<option value="">全部区域 (扫描所有)</option>' +
            _lsRegions.map(r => `<option value="${r.code}" ${r.code === def ? 'selected' : ''}>${r.display} (${r.code})</option>`).join('');
    } catch (e) { /* ignore */ }

    document.getElementById('lightsail-list').innerHTML =
        '<div style="text-align:center;padding:30px;color:var(--text2)">正在加载实例列表...</div>';
    document.getElementById('lightsail-modal').classList.add('show');
    loadLightsailInstances();
}

async function loadLightsailInstances() {
    if (!_lsAccountId) return;
    const region = document.getElementById('ls-region-filter')?.value || '';
    const body = document.getElementById('lightsail-list');
    body.innerHTML = `<div style="text-align:center;padding:30px"><div class="spinner"></div><div style="margin-top:12px;color:var(--text2)">正在拉取 Lightsail 实例${region ? ' ('+region+')' : ' (扫描所有区域)'}...</div></div>`;
    try {
        const url = region ? `/lightsail/instances?account_id=${_lsAccountId}&region=${region}` : `/lightsail/instances?account_id=${_lsAccountId}`;
        const res = await api(url);
        renderLightsailInstances(res.instances || []);
    } catch (e) {
        body.innerHTML = `<div style="color:var(--red);padding:20px;text-align:center">获取失败: ${e.message}</div>`;
    }
}

function renderLightsailInstances(list) {
    const body = document.getElementById('lightsail-list');
    if (!list.length) {
        body.innerHTML = '<div style="padding:30px;text-align:center;color:var(--text2);border:1px dashed var(--border);border-radius:8px">该区域暂无 Lightsail 实例。点击右上角「+ 创建 Lightsail 实例」开始创建。</div>';
        return;
    }
    let html = `<table class="ai-table" style="width:100%">
        <thead><tr>
            <th>名称</th><th>区域</th><th>状态</th><th>蓝图</th><th>套餐</th>
            <th>公网IP</th><th>规格</th><th>操作</th>
        </tr></thead><tbody>`;
    for (const i of list) {
        const stateColor = i.state === 'running' ? 'green' : (i.state === 'stopped' ? 'red' : (i.state === 'pending' ? 'yellow' : 'gray'));
        const specs = `${i.cpu || '?'}C / ${i.ram || '?'}GB`;
        const safeName = i.name.replace(/'/g, "\\'");
        const region = i.region;
        html += `<tr>
            <td><b>${i.name}</b><br><span style="font-size:11px;color:var(--text2)">${i.availability_zone || ''}</span></td>
            <td>${region}</td>
            <td><span class="badge badge-${stateColor}">${i.state}</span></td>
            <td style="font-size:12px">${i.blueprint_name || i.blueprint_id || '-'}</td>
            <td style="font-size:12px"><code>${i.bundle_id || '-'}</code></td>
            <td><code>${i.public_ip || '-'}</code>${i.is_static_ip ? ' <span class="badge badge-blue" style="font-size:10px">静态</span>' : ''}</td>
            <td style="font-size:12px">${specs}</td>
            <td class="action-btns">
                ${i.state === 'stopped' ? `<button class="btn btn-sm btn-primary" onclick="lightsailAction('${safeName}','${region}','start')">▶ 启动</button>` : ''}
                ${i.state === 'running' ? `<button class="btn btn-sm btn-secondary" onclick="lightsailAction('${safeName}','${region}','stop')">⏹ 停止</button>` : ''}
                ${i.state === 'running' ? `<button class="btn btn-sm btn-secondary" onclick="lightsailAction('${safeName}','${region}','reboot')">🔄</button>` : ''}
                <button class="btn btn-sm btn-secondary" title="开放常用端口" onclick="lightsailAction('${safeName}','${region}','open-ports')">🔓</button>
                <button class="btn btn-sm btn-danger" onclick="lightsailDelete('${safeName}','${region}')">🗑</button>
            </td>
        </tr>`;
    }
    html += '</tbody></table>';
    body.innerHTML = html;
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
