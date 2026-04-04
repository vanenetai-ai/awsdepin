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
    if (id === 'launch-modal') loadAccountOptions('launch-account');
    if (id === 'deploy-modal') { loadInstanceOptions('deploy-instance'); loadProjectOptions('deploy-project'); }
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
    try {
        accountsCache = await api('/accounts');
        renderAccountCards(accountsCache);
    } catch (e) { toast(e.message, 'error'); }
}

function renderAccountCards(list) {
    const grid = document.getElementById('accounts-grid');
    if (!grid) return;
    grid.innerHTML = list.map(a => {
        const displayName = a.email || a.name || a.access_key_id;
        const age = timeAgo(a.register_time || a.added_at);
        const flag = a.country_flag || '';
        const vcpuText = a.total_vcpus ? `${a.total_vcpus} vCPUs` : '';
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
    try {
        await api('/accounts/batch-delete', { method: 'POST', body: JSON.stringify({ ids: [...selectedAccounts] }) });
        toast(`已删除 ${selectedAccounts.size} 个账号`);
        selectedAccounts.clear();
        updateBatchBar();
        loadAccounts();
    } catch (e) { toast(e.message, 'error'); }
}

async function detectAccount(id) {
    toast('正在检测账号信息...', 'info');
    try {
        const res = await api(`/accounts/${id}/detect`, { method: 'POST' });
        toast(`检测完成: ${res.email || res.name}`);
        loadAccounts();
    } catch (e) { toast(e.message, 'error'); }
}

async function showVcpuDetail(id) {
    const a = accountsCache.find(x => x.id === id);
    const modal = document.getElementById('vcpu-modal');
    const body = document.getElementById('vcpu-body');
    modal.classList.add('show');

    // 如果已有缓存数据先显示
    if (a && a.vcpu_data) {
        renderVcpuTable(a.vcpu_data);
    } else {
        body.innerHTML = '<div style="text-align:center;padding:20px">加载中...</div>';
    }

    // 实时获取
    try {
        const res = await api(`/accounts/${id}/vcpus`, { method: 'POST' });
        renderVcpuTable(res.regions);
        // 更新缓存
        if (a) { a.vcpu_data = res.regions; a.total_vcpus = res.total_vcpus; }
        loadAccounts(); // 刷新卡片上的 vCPU 数字
    } catch (e) {
        body.innerHTML = `<div style="color:var(--red);padding:20px">获取失败: ${e.message}</div>`;
    }
}

function renderVcpuTable(regions) {
    const body = document.getElementById('vcpu-body');
    let html = `<table class="vcpu-table"><thead><tr><th>地区</th><th>On-Demand (已用/全部)</th><th>Spot (已用/全部)</th></tr></thead><tbody>`;
    for (const [region, data] of Object.entries(regions)) {
        html += `<tr>
            <td>${data.display || region} (${region})</td>
            <td>${data.on_demand_usage}/${data.on_demand_limit}</td>
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
    try {
        const data = { name: document.getElementById('acc-name').value, access_key_id: document.getElementById('acc-key').value, secret_access_key: document.getElementById('acc-secret').value, default_region: document.getElementById('acc-region').value };
        const res = await api('/accounts', { method: 'POST', body: JSON.stringify(data) });
        hideModal('account-modal');
        toast(res.verify?.valid ? '账号已添加，验证通过' : `账号已添加，验证失败: ${res.verify?.error || ''}`);
        loadAccounts();
    } catch (e) { toast(e.message, 'error'); }
}

async function verifyAccount(id) {
    try {
        const res = await api(`/accounts/${id}/verify`, { method: 'POST' });
        toast(res.valid ? `验证通过 (${res.account_id})` : `验证失败: ${res.error}`, res.valid ? 'success' : 'error');
    } catch (e) { toast(e.message, 'error'); }
}

async function deleteAccount(id) {
    if (!confirm('确定删除此账号？')) return;
    try { await api(`/accounts/${id}`, { method: 'DELETE' }); toast('账号已删除'); loadAccounts(); } catch (e) { toast(e.message, 'error'); }
}

async function batchCreateAccounts(e) {
    e.preventDefault();
    try {
        const data = { text: document.getElementById('batch-acc-text').value, default_region: document.getElementById('batch-acc-region').value };
        const res = await api('/accounts/batch', { method: 'POST', body: JSON.stringify(data) });
        hideModal('batch-account-modal');
        toast(`批量添加: ${res.created?.length || 0} 成功, ${res.errors?.length || 0} 失败`, res.errors?.length ? 'error' : 'success');
        document.getElementById('batch-acc-text').value = '';
        loadAccounts();
    } catch (e) { toast(e.message, 'error'); }
}

// ==================== Instances ====================
async function loadInstances() {
    try {
        const list = await api('/instances');
        document.querySelector('#instances-table tbody').innerHTML = list.map(i => `<tr>
            <td>${i.id}</td><td>${i.account_name}</td><td><code>${i.instance_id || '-'}</code></td><td>${i.region}</td>
            <td>${i.instance_type}</td><td>${stateBadge(i.state)}</td><td>${i.public_ip || '-'}</td><td>${i.task_count}</td>
            <td class="action-btns">
                <button class="btn btn-sm btn-secondary" onclick="syncInstance(${i.id})">同步</button>
                ${i.state === 'stopped' ? `<button class="btn btn-sm btn-primary" onclick="startInstance(${i.id})">启动</button>` : ''}
                ${i.state === 'running' ? `<button class="btn btn-sm btn-secondary" onclick="stopInstance(${i.id})">停止</button>` : ''}
                <button class="btn btn-sm btn-danger" onclick="terminateInstance(${i.id})">终止</button>
            </td></tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}
async function launchInstance(e) { e.preventDefault(); try { const data = { account_id: parseInt(document.getElementById('launch-account').value), region: document.getElementById('launch-region').value || null, instance_type: document.getElementById('launch-type').value }; const res = await api('/instances/launch', { method: 'POST', body: JSON.stringify(data) }); hideModal('launch-modal'); toast(`实例已启动: ${res.instance_id}`); loadInstances(); } catch (e) { toast(e.message, 'error'); } }
async function syncInstance(id) { try { const res = await api(`/instances/${id}/sync`, { method: 'POST' }); toast(`已同步: ${res.state}`); loadInstances(); } catch (e) { toast(e.message, 'error'); } }
async function startInstance(id) { try { await api(`/instances/${id}/start`, { method: 'POST' }); toast('启动指令已发送'); loadInstances(); } catch (e) { toast(e.message, 'error'); } }
async function stopInstance(id) { try { await api(`/instances/${id}/stop`, { method: 'POST' }); toast('停止指令已发送'); loadInstances(); } catch (e) { toast(e.message, 'error'); } }
async function terminateInstance(id) { if (!confirm('确定终止？')) return; try { await api(`/instances/${id}/terminate`, { method: 'POST' }); toast('已终止'); loadInstances(); } catch (e) { toast(e.message, 'error'); } }
async function syncAllInstances() { try { const res = await api('/instances/sync-all', { method: 'POST' }); toast(`已同步 ${res.synced} 个`); loadInstances(); } catch (e) { toast(e.message, 'error'); } }

// ==================== Proxies ====================
async function loadProxies() {
    try {
        const list = await api('/proxies');
        document.querySelector('#proxies-table tbody').innerHTML = list.map(p => `<tr>
            <td>${p.id}</td><td>${p.protocol.toUpperCase()}</td><td>${p.host}</td><td>${p.port}</td><td>${p.username || '-'}</td>
            <td><span class="badge ${p.is_active ? 'badge-green' : 'badge-red'}" style="cursor:pointer" onclick="toggleProxy(${p.id})">${p.is_active ? '活跃' : '禁用'}</span></td>
            <td>${p.last_used_at || '-'}</td>
            <td class="action-btns"><button class="btn btn-sm btn-danger" onclick="deleteProxy(${p.id})">删除</button></td></tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}
async function createProxy(e) { e.preventDefault(); try { const data = { protocol: document.getElementById('proxy-protocol').value, host: document.getElementById('proxy-host').value, port: parseInt(document.getElementById('proxy-port').value), username: document.getElementById('proxy-user').value || null, password: document.getElementById('proxy-pass').value || null }; await api('/proxies', { method: 'POST', body: JSON.stringify(data) }); hideModal('proxy-modal'); toast('代理已添加'); loadProxies(); } catch (e) { toast(e.message, 'error'); } }
async function toggleProxy(id) { try { const res = await api(`/proxies/${id}/toggle`, { method: 'PUT' }); toast(res.is_active ? '已启用' : '已禁用'); loadProxies(); } catch (e) { toast(e.message, 'error'); } }
async function deleteProxy(id) { if (!confirm('确定删除？')) return; try { await api(`/proxies/${id}`, { method: 'DELETE' }); toast('已删除'); loadProxies(); } catch (e) { toast(e.message, 'error'); } }
async function batchCreateProxies(e) { e.preventDefault(); try { const data = { text: document.getElementById('batch-proxy-text').value }; const res = await api('/proxies/batch-text', { method: 'POST', body: JSON.stringify(data) }); hideModal('batch-proxy-modal'); toast(`批量添加: ${res.created} 成功`); document.getElementById('batch-proxy-text').value = ''; loadProxies(); } catch (e) { toast(e.message, 'error'); } }

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
            <td class="action-btns"><button class="btn btn-sm btn-secondary" onclick="checkHealth(${t.id})">健康检查</button></td></tr>`).join('');
    } catch (e) { toast(e.message, 'error'); }
}
async function deployTask(e) { e.preventDefault(); try { const cf = {}; document.querySelectorAll('#deploy-config-fields input').forEach(f => { if (f.value) cf[f.dataset.key] = f.value; }); const res = await api('/tasks/deploy', { method: 'POST', body: JSON.stringify({ instance_id: parseInt(document.getElementById('deploy-instance').value), project_id: parseInt(document.getElementById('deploy-project').value), config: Object.keys(cf).length ? cf : null }) }); hideModal('deploy-modal'); toast(`部署: ${res.status}`); loadTasks(); } catch (e) { toast(e.message, 'error'); } }
async function checkHealth(id) { try { const res = await api(`/tasks/${id}/health`, { method: 'POST' }); toast(`健康检查: ${res.status} ${res.message || ''}`, 'info'); } catch (e) { toast(e.message, 'error'); } }

// ==================== Helpers ====================
async function loadAccountOptions(sid) { try { const l = await api('/accounts'); document.getElementById(sid).innerHTML = l.map(a => `<option value="${a.id}">${a.name} (${a.default_region})</option>`).join(''); } catch(e){} }
async function loadInstanceOptions(sid) { try { const l = await api('/instances'); document.getElementById(sid).innerHTML = l.filter(i => i.state==='running').map(i => `<option value="${i.id}">${i.public_ip||i.instance_id} (${i.account_name})</option>`).join(''); } catch(e){} }
async function loadProjectOptions(sid) { try { if (!projectsCache.length) projectsCache = await api('/projects'); document.getElementById(sid).innerHTML = projectsCache.map(p => `<option value="${p.id}">${p.name}</option>`).join(''); } catch(e){} }
async function loadProjectConfig() { const pid = document.getElementById('deploy-project').value; const c = document.getElementById('deploy-config-fields'); c.innerHTML = ''; try { const p = await api(`/projects/${pid}`); if (p.config_template) for (const [k,v] of Object.entries(p.config_template)) c.innerHTML += `<div class="form-group"><label>${k}</label><input type="text" data-key="${k}" value="${v}" placeholder="${k}"></div>`; } catch(e){} }

// ==================== Init ====================
if (checkAuth()) { showUserInfo(); loadDashboard(); }
