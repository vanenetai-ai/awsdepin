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
        renderAccountCards(accountsCache);
    } catch (e) { toast(e.message, 'error'); if (grid) grid.innerHTML = ''; }
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
                <span class="acc-footer-spacer"></span>
                <button class="btn btn-sm btn-secondary" onclick="window.location.hash='instances';loadInstances()">EC2 实例</button>
                <button class="btn btn-sm btn-secondary">Lightsail 实例</button>
            </div>
        </div>`;
    }).join('');
}

function toggleCardExpand(id) {
    const el = document.getElementById('acc-expand-' + id);
    if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
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

async function loadInstances() {
    try {
        const list = await api('/instances');
        selectedInstances.clear();
        updateInstanceBatchBar();
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
    } catch (e) { toast(e.message, 'error'); }
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
async function loadTasks() {
    try {
        const list = await api('/tasks');
        document.querySelector('#tasks-table tbody').innerHTML = list.map(t => `<tr>
            <td>${t.id}</td><td>${t.project_name}</td><td>${t.instance_ip || '-'}</td><td>${stateBadge(t.status)}</td>
            <td title="${t.log || ''}">${(t.log || '-').substring(0, 40)}</td><td>${t.created_at}</td>
            <td class="action-btns">
                <button class="btn btn-sm btn-secondary" onclick="checkHealth(${t.id})">健康检查</button>
                <button class="btn btn-sm btn-danger" onclick="deleteTask(${t.id})">删除</button>
            </td></tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
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
async function loadAccountOptions(sid) { try { const l = accountsCache.length ? accountsCache : await api('/accounts'); document.getElementById(sid).innerHTML = l.map(a => `<option value="${a.id}">${a.email || a.name} (${a.default_region})</option>`).join(''); } catch(e){} }
async function loadInstanceOptions(sid) {
    try {
        const l = await api('/instances');
        const sel = document.getElementById(sid);
        sel.innerHTML = l.filter(i => i.state==='running').map(i => `<option value="${i.id}" selected>${i.public_ip||i.instance_id} (${i.account_name})</option>`).join('');
    } catch(e){}
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

// ==================== Init ====================
if (checkAuth()) { showUserInfo(); loadDashboard(); setTimeout(initSearchSelects, 100); }
