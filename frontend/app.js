const API = '/api';

// ==================== Auth ====================

function getToken() {
    return localStorage.getItem('auth_token') || '';
}

function checkAuth() {
    const token = getToken();
    if (!token) {
        window.location.href = '/login.html';
        return false;
    }
    return true;
}

function logout() {
    localStorage.removeItem('auth_token');
    localStorage.removeItem('user_name');
    window.location.href = '/login.html';
}

function showUserInfo() {
    const name = localStorage.getItem('user_name') || '用户';
    const el = document.getElementById('user-info');
    if (el) el.textContent = name;
}

// ==================== Utils ====================

async function api(path, opts = {}) {
    const token = getToken();
    const headers = {
        'Content-Type': 'application/json',
        ...(token ? { 'Authorization': 'Bearer ' + token } : {}),
        ...opts.headers,
    };
    const res = await fetch(API + path, { ...opts, headers });
    if (res.status === 401) {
        logout();
        throw new Error('登录已过期');
    }
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || '请求失败');
    }
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
    if (id === 'deploy-modal') {
        loadInstanceOptions('deploy-instance');
        loadProjectOptions('deploy-project');
    }
}

function hideModal(id) {
    document.getElementById(id).classList.remove('show');
}

function stateBadge(state) {
    const map = {
        running: 'green', pending: 'yellow', stopped: 'red',
        terminated: 'gray', installing: 'blue', failed: 'red',
    };
    return `<span class="badge badge-${map[state] || 'gray'}">${state}</span>`;
}

// ==================== Navigation ====================

document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
        document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        item.classList.add('active');
        const tab = item.dataset.tab;
        document.getElementById('tab-' + tab).classList.add('active');
        loadTabData(tab);
    });
});

function loadTabData(tab) {
    const loaders = {
        dashboard: loadDashboard,
        accounts: loadAccounts,
        instances: loadInstances,
        proxies: loadProxies,
        projects: loadProjects,
        tasks: loadTasks,
    };
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
            <div class="stat-card"><div class="label">运行中任务</div><div class="value green">${d.tasks_running}</div></div>
        `;
    } catch (e) {
        toast(e.message, 'error');
    }
}

// ==================== Accounts ====================

async function loadAccounts() {
    try {
        const list = await api('/accounts');
        const tbody = document.querySelector('#accounts-table tbody');
        tbody.innerHTML = list.map(a => `
            <tr>
                <td>${a.id}</td>
                <td>${a.name}</td>
                <td><code>${a.access_key_id}</code></td>
                <td>${a.default_region}</td>
                <td>${a.instance_count}</td>
                <td>${a.is_active ? stateBadge('running') : stateBadge('stopped')}</td>
                <td class="action-btns">
                    <button class="btn btn-sm btn-secondary" onclick="verifyAccount(${a.id})">验证</button>
                    <button class="btn btn-sm btn-danger" onclick="deleteAccount(${a.id})">删除</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function createAccount(e) {
    e.preventDefault();
    try {
        const data = {
            name: document.getElementById('acc-name').value,
            access_key_id: document.getElementById('acc-key').value,
            secret_access_key: document.getElementById('acc-secret').value,
            default_region: document.getElementById('acc-region').value,
        };
        const res = await api('/accounts', { method: 'POST', body: JSON.stringify(data) });
        hideModal('account-modal');
        toast(res.verify?.valid ? `账号已添加，验证通过` : `账号已添加，验证失败: ${res.verify?.error || ''}`);
        loadAccounts();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function verifyAccount(id) {
    try {
        const res = await api(`/accounts/${id}/verify`, { method: 'POST' });
        toast(res.valid ? `验证通过 (${res.account_id})` : `验证失败: ${res.error}`, res.valid ? 'success' : 'error');
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function deleteAccount(id) {
    if (!confirm('确定删除此账号？关联的实例记录也会被删除。')) return;
    try {
        await api(`/accounts/${id}`, { method: 'DELETE' });
        toast('账号已删除');
        loadAccounts();
    } catch (e) {
        toast(e.message, 'error');
    }
}

// ==================== Instances ====================

async function loadInstances() {
    try {
        const list = await api('/instances');
        const tbody = document.querySelector('#instances-table tbody');
        tbody.innerHTML = list.map(i => `
            <tr>
                <td>${i.id}</td>
                <td>${i.account_name}</td>
                <td><code>${i.instance_id || '-'}</code></td>
                <td>${i.region}</td>
                <td>${i.instance_type}</td>
                <td>${stateBadge(i.state)}</td>
                <td>${i.public_ip || '-'}</td>
                <td>${i.task_count}</td>
                <td class="action-btns">
                    <button class="btn btn-sm btn-secondary" onclick="syncInstance(${i.id})">同步</button>
                    ${i.state === 'stopped' ? `<button class="btn btn-sm btn-primary" onclick="startInstance(${i.id})">启动</button>` : ''}
                    ${i.state === 'running' ? `<button class="btn btn-sm btn-secondary" onclick="stopInstance(${i.id})">停止</button>` : ''}
                    <button class="btn btn-sm btn-danger" onclick="terminateInstance(${i.id})">终止</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function launchInstance(e) {
    e.preventDefault();
    try {
        const data = {
            account_id: parseInt(document.getElementById('launch-account').value),
            region: document.getElementById('launch-region').value || null,
            instance_type: document.getElementById('launch-type').value,
        };
        const res = await api('/instances/launch', { method: 'POST', body: JSON.stringify(data) });
        hideModal('launch-modal');
        toast(`实例已启动: ${res.instance_id}`);
        loadInstances();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function syncInstance(id) {
    try {
        const res = await api(`/instances/${id}/sync`, { method: 'POST' });
        toast(`已同步: ${res.state} / ${res.public_ip || 'no IP'}`);
        loadInstances();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function startInstance(id) {
    try {
        await api(`/instances/${id}/start`, { method: 'POST' });
        toast('启动指令已发送');
        loadInstances();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function stopInstance(id) {
    try {
        await api(`/instances/${id}/stop`, { method: 'POST' });
        toast('停止指令已发送');
        loadInstances();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function terminateInstance(id) {
    if (!confirm('确定终止此实例？此操作不可逆。')) return;
    try {
        await api(`/instances/${id}/terminate`, { method: 'POST' });
        toast('实例已终止');
        loadInstances();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function syncAllInstances() {
    try {
        const res = await api('/instances/sync-all', { method: 'POST' });
        toast(`已同步 ${res.synced} 个实例`);
        loadInstances();
    } catch (e) {
        toast(e.message, 'error');
    }
}

// ==================== Proxies ====================

async function loadProxies() {
    try {
        const list = await api('/proxies');
        const tbody = document.querySelector('#proxies-table tbody');
        tbody.innerHTML = list.map(p => `
            <tr>
                <td>${p.id}</td>
                <td>${p.protocol.toUpperCase()}</td>
                <td>${p.host}</td>
                <td>${p.port}</td>
                <td>${p.username || '-'}</td>
                <td>
                    <span class="badge ${p.is_active ? 'badge-green' : 'badge-red'}" 
                          style="cursor:pointer" onclick="toggleProxy(${p.id})">
                        ${p.is_active ? '活跃' : '禁用'}
                    </span>
                </td>
                <td>${p.last_used_at || '-'}</td>
                <td class="action-btns">
                    <button class="btn btn-sm btn-danger" onclick="deleteProxy(${p.id})">删除</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function createProxy(e) {
    e.preventDefault();
    try {
        const data = {
            protocol: document.getElementById('proxy-protocol').value,
            host: document.getElementById('proxy-host').value,
            port: parseInt(document.getElementById('proxy-port').value),
            username: document.getElementById('proxy-user').value || null,
            password: document.getElementById('proxy-pass').value || null,
        };
        await api('/proxies', { method: 'POST', body: JSON.stringify(data) });
        hideModal('proxy-modal');
        toast('代理已添加');
        loadProxies();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function toggleProxy(id) {
    try {
        const res = await api(`/proxies/${id}/toggle`, { method: 'PUT' });
        toast(res.is_active ? '代理已启用' : '代理已禁用');
        loadProxies();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function deleteProxy(id) {
    if (!confirm('确定删除此代理？')) return;
    try {
        await api(`/proxies/${id}`, { method: 'DELETE' });
        toast('代理已删除');
        loadProxies();
    } catch (e) {
        toast(e.message, 'error');
    }
}

// ==================== Projects ====================

let projectsCache = [];

async function loadProjects() {
    try {
        projectsCache = await api('/projects');
        const grid = document.getElementById('projects-grid');
        grid.innerHTML = projectsCache.map(p => `
            <div class="card">
                <h4>${p.name}</h4>
                <p>${p.description || '无描述'}</p>
                ${p.config_template ? `
                    <div class="config-tags">
                        ${Object.keys(p.config_template).map(k => `<span class="config-tag">${k}</span>`).join('')}
                    </div>
                ` : ''}
            </div>
        `).join('');
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function createProject(e) {
    e.preventDefault();
    try {
        const data = {
            name: document.getElementById('proj-name').value,
            description: document.getElementById('proj-desc').value || null,
            install_script: document.getElementById('proj-script').value,
            health_check_cmd: document.getElementById('proj-health').value || null,
        };
        await api('/projects', { method: 'POST', body: JSON.stringify(data) });
        hideModal('project-modal');
        toast('项目已添加');
        loadProjects();
    } catch (e) {
        toast(e.message, 'error');
    }
}

// ==================== Tasks ====================

async function loadTasks() {
    try {
        const list = await api('/tasks');
        const tbody = document.querySelector('#tasks-table tbody');
        tbody.innerHTML = list.map(t => `
            <tr>
                <td>${t.id}</td>
                <td>${t.project_name}</td>
                <td>${t.instance_ip || '-'}</td>
                <td>${stateBadge(t.status)}</td>
                <td title="${t.log || ''}">${(t.log || '-').substring(0, 40)}</td>
                <td>${t.created_at}</td>
                <td class="action-btns">
                    <button class="btn btn-sm btn-secondary" onclick="checkHealth(${t.id})">健康检查</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function deployTask(e) {
    e.preventDefault();
    try {
        const configFields = document.querySelectorAll('#deploy-config-fields input');
        const config = {};
        configFields.forEach(f => { if (f.value) config[f.dataset.key] = f.value; });

        const data = {
            instance_id: parseInt(document.getElementById('deploy-instance').value),
            project_id: parseInt(document.getElementById('deploy-project').value),
            config: Object.keys(config).length ? config : null,
        };
        const res = await api('/tasks/deploy', { method: 'POST', body: JSON.stringify(data) });
        hideModal('deploy-modal');
        toast(`部署任务已创建: ${res.status}`);
        loadTasks();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function checkHealth(taskId) {
    try {
        const res = await api(`/tasks/${taskId}/health`, { method: 'POST' });
        toast(`健康检查: ${res.status} ${res.message || res.command_id || ''}`, 'info');
    } catch (e) {
        toast(e.message, 'error');
    }
}

// ==================== Helpers ====================

async function loadAccountOptions(selectId) {
    try {
        const list = await api('/accounts');
        const sel = document.getElementById(selectId);
        sel.innerHTML = list.map(a => `<option value="${a.id}">${a.name} (${a.default_region})</option>`).join('');
    } catch (e) { /* ignore */ }
}

async function loadInstanceOptions(selectId) {
    try {
        const list = await api('/instances');
        const sel = document.getElementById(selectId);
        sel.innerHTML = list
            .filter(i => i.state === 'running')
            .map(i => `<option value="${i.id}">${i.public_ip || i.instance_id} (${i.account_name})</option>`)
            .join('');
    } catch (e) { /* ignore */ }
}

async function loadProjectOptions(selectId) {
    try {
        if (!projectsCache.length) projectsCache = await api('/projects');
        const sel = document.getElementById(selectId);
        sel.innerHTML = projectsCache.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
    } catch (e) { /* ignore */ }
}

async function loadProjectConfig() {
    const projId = document.getElementById('deploy-project').value;
    const container = document.getElementById('deploy-config-fields');
    container.innerHTML = '';
    try {
        const proj = await api(`/projects/${projId}`);
        if (proj.config_template) {
            for (const [key, defaultVal] of Object.entries(proj.config_template)) {
                container.innerHTML += `
                    <div class="form-group">
                        <label>${key}</label>
                        <input type="text" data-key="${key}" value="${defaultVal}" placeholder="${key}">
                    </div>
                `;
            }
        }
    } catch (e) { /* ignore */ }
}

// ==================== Init ====================

if (checkAuth()) {
    showUserInfo();
    loadDashboard();
}
