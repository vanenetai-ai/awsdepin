import io
import time
import logging
import paramiko
from sqlalchemy.orm import Session
from models import Instance, DepinProject, DepinTask

logger = logging.getLogger(__name__)

# Titan L2 Edge Node 官方安装步骤
# https://titannet.gitbook.io/titan-network-en/resource-network-test/operate-nodes/l2-edge-node/installation-and-earnings/operation-on-linux
TITAN_VERSION = "v0.1.20"
TITAN_BUILD = "246b9dd"
TITAN_TARBALL = f"titan-edge_{TITAN_VERSION}_{TITAN_BUILD}_linux-amd64.tar.gz"
TITAN_URL = f"https://github.com/Titannet-dao/titan-node/releases/download/{TITAN_VERSION}/{TITAN_TARBALL}"

BUILTIN_PROJECTS = [
    {
        "name": "titan-network",
        "description": "Titan Network L2 Edge Node - 去中心化CDN与存储 (需要 Identity Code hash)",
        "install_script": f"""#!/bin/bash
set -e

HASH="${{identity_code}}"
if [ -z "$HASH" ]; then
    echo "ERROR: Identity Code hash is required! Get it from https://test4.titannet.io/"
    exit 1
fi

echo "=== Installing Titan L2 Edge Node ==="

# 下载 Titan Edge
cd /tmp
if [ ! -f {TITAN_TARBALL} ]; then
    wget -q {TITAN_URL} -O {TITAN_TARBALL}
fi

# 解压
tar -zxf {TITAN_TARBALL}
cd titan-edge_{TITAN_VERSION}_{TITAN_BUILD}_linux-amd64

# 安装二进制和库
sudo cp titan-edge /usr/local/bin/titan-edge
sudo cp libgoworkerd.so /usr/local/lib/libgoworkerd.so
sudo ldconfig

# 停止旧进程
pkill -f 'titan-edge daemon' 2>/dev/null || true
sleep 2

# 启动 daemon (后台)
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/lib
nohup titan-edge daemon start --init --url https://cassini-locator.titannet.io:5000/rpc/v0 > /var/log/titan-edge.log 2>&1 &

# 等待 daemon 启动
echo "Waiting for daemon to start..."
sleep 10

# 绑定设备
titan-edge bind --hash=$HASH https://api-test1.container1.titannet.io/api/v2/device/binding

echo "=== Titan L2 Edge Node installed and bound successfully ==="
""",
        "health_check_cmd": "pgrep -f 'titan-edge daemon' && tail -5 /var/log/titan-edge.log",
        "config_template": {"identity_code": ""},
    },
    {
        "name": "traffmonetizer",
        "description": "TraffMonetizer - 带宽变现 (✅ VPS/数据中心IP兼容)。需要 Token，从 https://traffmonetizer.com 获取",
        "install_script": """#!/bin/bash
set -e
TOKEN="${traffmonetizer_token}"
if [ -z "$TOKEN" ]; then echo "ERROR: Token 不能为空"; exit 1; fi

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
fi

docker rm -f traffmonetizer 2>/dev/null || true
docker pull traffmonetizer/cli_v2:latest
docker run -d --name traffmonetizer --restart=always \
    traffmonetizer/cli_v2:latest \
    start accept status --token "$TOKEN" --device-name "depin-$(hostname)"

echo "=== TraffMonetizer 已启动 ==="
docker ps --filter name=traffmonetizer --format 'Status: {{.Status}}'
""",
        "health_check_cmd": "docker ps --filter name=traffmonetizer --format '{{.Status}}'",
        "config_template": {"traffmonetizer_token": ""},
    },
    {
        "name": "repocket",
        "description": "Repocket - 带宽共享 (✅ VPS/数据中心IP兼容)。需要邮箱和 API Key，从 https://app.repocket.co 获取",
        "install_script": """#!/bin/bash
set -e
EMAIL="${repocket_email}"
APIKEY="${repocket_apikey}"
if [ -z "$EMAIL" ] || [ -z "$APIKEY" ]; then echo "ERROR: 邮箱和 API Key 不能为空"; exit 1; fi

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
fi

docker rm -f repocket 2>/dev/null || true
docker pull repocket/repocket:latest
docker run -d --name repocket --restart=always \
    -e RP_EMAIL="$EMAIL" \
    -e RP_API_KEY="$APIKEY" \
    repocket/repocket:latest

echo "=== Repocket 已启动 ==="
docker ps --filter name=repocket --format 'Status: {{.Status}}'
""",
        "health_check_cmd": "docker ps --filter name=repocket --format '{{.Status}}'",
        "config_template": {"repocket_email": "", "repocket_apikey": ""},
    },
    {
        "name": "proxyrack",
        "description": "Proxyrack - 代理网络 (✅ VPS/数据中心IP兼容)。需要 API Key，从 https://peer.proxyrack.com 获取",
        "install_script": """#!/bin/bash
set -e
API_KEY="${proxyrack_apikey}"
if [ -z "$API_KEY" ]; then echo "ERROR: API Key 不能为空"; exit 1; fi

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
fi

docker rm -f proxyrack 2>/dev/null || true
docker pull proxyrack/pop:latest
docker run -d --name proxyrack --restart=always \
    -e api_key="$API_KEY" \
    -e device_name="depin-$(hostname)" \
    proxyrack/pop:latest

echo "=== Proxyrack 已启动 ==="
docker ps --filter name=proxyrack --format 'Status: {{.Status}}'
""",
        "health_check_cmd": "docker ps --filter name=proxyrack --format '{{.Status}}'",
        "config_template": {"proxyrack_apikey": ""},
    },
    {
        "name": "proxylite",
        "description": "Proxylite - 代理网络 (✅ VPS/数据中心IP兼容)。需要 User ID，从 https://proxylite.ru 获取",
        "install_script": """#!/bin/bash
set -e
USER_ID="${proxylite_userid}"
if [ -z "$USER_ID" ]; then echo "ERROR: User ID 不能为空"; exit 1; fi

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
fi

docker rm -f proxylite 2>/dev/null || true
docker pull proxylite/proxyservice:latest
docker run -d --name proxylite --restart=always \
    -e USER_ID="$USER_ID" \
    proxylite/proxyservice:latest

echo "=== Proxylite 已启动 ==="
docker ps --filter name=proxylite --format 'Status: {{.Status}}'
""",
        "health_check_cmd": "docker ps --filter name=proxylite --format '{{.Status}}'",
        "config_template": {"proxylite_userid": ""},
    },
    {
        "name": "peer2profit",
        "description": "Peer2Profit - P2P带宽共享 (✅ VPS/数据中心IP兼容)。需要邮箱，从 https://peer2profit.com 获取",
        "install_script": """#!/bin/bash
set -e
EMAIL="${peer2profit_email}"
if [ -z "$EMAIL" ]; then echo "ERROR: 邮箱不能为空"; exit 1; fi

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
fi

docker rm -f peer2profit 2>/dev/null || true
docker pull mrcolorrain/peer2profit:latest
docker run -d --name peer2profit --restart=always \
    -e P2P_EMAIL="$EMAIL" \
    mrcolorrain/peer2profit:latest

echo "=== Peer2Profit 已启动 ==="
docker ps --filter name=peer2profit --format 'Status: {{.Status}}'
""",
        "health_check_cmd": "docker ps --filter name=peer2profit --format '{{.Status}}'",
        "config_template": {"peer2profit_email": ""},
    },
    {
        "name": "bitping",
        "description": "Bitping - 去中心化网络监测 (✅ VPS/数据中心IP兼容)。需要邮箱和密码，从 https://app.bitping.com 注册",
        "install_script": """#!/bin/bash
set -e
EMAIL="${bitping_email}"
PASSWORD="${bitping_password}"
if [ -z "$EMAIL" ] || [ -z "$PASSWORD" ]; then echo "ERROR: 邮箱和密码不能为空"; exit 1; fi

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
fi

docker rm -f bitping 2>/dev/null || true
mkdir -p /opt/bitping
docker pull mrcolorrain/bitping:latest
docker run -d --name bitping --restart=always \
    -v /opt/bitping:/root/.bitping \
    mrcolorrain/bitping:latest \
    bitping-node --server --email "$EMAIL" --password "$PASSWORD"

echo "=== Bitping 已启动 ==="
docker ps --filter name=bitping --format 'Status: {{.Status}}'
""",
        "health_check_cmd": "docker ps --filter name=bitping --format '{{.Status}}'",
        "config_template": {"bitping_email": "", "bitping_password": ""},
    },
    {
        "name": "mystnode",
        "description": "Mysterium Network (MystNode) - 去中心化VPN节点 (✅ VPS/数据中心IP兼容)。启动后需通过 Web 面板完成设置",
        "install_script": """#!/bin/bash
set -e

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
fi

docker rm -f mystnode 2>/dev/null || true
mkdir -p /opt/mysterium-node
docker pull mysteriumnetwork/myst:latest
docker run -d --name mystnode --restart=always \
    --cap-add NET_ADMIN \
    -p 4449:4449 \
    -v /opt/mysterium-node:/var/lib/mysterium-node \
    mysteriumnetwork/myst:latest \
    service --agreed-terms-and-conditions

echo "=== MystNode 已启动 ==="
echo "请访问 http://实例IP:4449 完成节点设置"
docker ps --filter name=mystnode --format 'Status: {{.Status}}'
""",
        "health_check_cmd": "docker ps --filter name=mystnode --format '{{.Status}}'",
        "config_template": {},
    },
    {
        "name": "earnfm",
        "description": "EarnFM - 带宽共享赚取收益 (✅ VPS/数据中心IP兼容)。需要 API Key，从 https://earn.fm 获取",
        "install_script": """#!/bin/bash
set -e
API_KEY="${earnfm_apikey}"
if [ -z "$API_KEY" ]; then echo "ERROR: API Key 不能为空"; exit 1; fi

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
fi

docker rm -f earnfm 2>/dev/null || true
docker pull earnfm/earnfm-client:latest
docker run -d --name earnfm --restart=always \
    -e EARNFM_TOKEN="$API_KEY" \
    earnfm/earnfm-client:latest

echo "=== EarnFM 已启动 ==="
docker ps --filter name=earnfm --format 'Status: {{.Status}}'
""",
        "health_check_cmd": "docker ps --filter name=earnfm --format '{{.Status}}'",
        "config_template": {"earnfm_apikey": ""},
    },
    {
        "name": "nodepay",
        "description": "Nodepay - 去中心化AI数据网络",
        "install_script": """#!/bin/bash
set -e
cd /home/ubuntu

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
    usermod -aG docker ubuntu
fi

docker pull nodepay/node:latest
docker run -d --name nodepay --restart=always \
    -e NP_TOKEN="${NP_TOKEN}" \
    nodepay/node:latest

echo "Nodepay node started successfully"
""",
        "health_check_cmd": "docker ps --filter name=nodepay --format '{{.Status}}'",
        "config_template": {"NP_TOKEN": ""},
    },
    {
        "name": "custom",
        "description": "自定义 DePIN 项目 - 用户自行提供安装脚本",
        "install_script": "#!/bin/bash\necho 'Please provide custom install script'",
        "health_check_cmd": "",
        "config_template": {"custom_script": ""},
    },
]


def ssh_execute(ip: str, private_key_pem: str, script: str, username: str = "ubuntu", timeout: int = 300) -> str:
    """通过 SSH 在远程实例上执行脚本（SFTP 上传后执行），返回输出"""
    key = paramiko.RSAKey.from_private_key(io.StringIO(private_key_pem))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # 重试连接（实例刚启动可能还没准备好）
    for attempt in range(5):
        try:
            client.connect(ip, username=username, pkey=key, timeout=15, banner_timeout=15)
            break
        except Exception as e:
            if attempt == 4:
                raise ConnectionError(f"SSH connect failed after 5 attempts: {e}")
            logger.info(f"SSH attempt {attempt+1} to {ip} failed, retrying in 10s...")
            time.sleep(10)

    try:
        # 通过 SFTP 上传脚本到临时文件
        sftp = client.open_sftp()
        remote_path = "/tmp/_depin_deploy.sh"
        with sftp.file(remote_path, "w") as f:
            f.write(script)
        sftp.chmod(remote_path, 0o755)
        sftp.close()

        # 执行脚本
        stdin, stdout, stderr = client.exec_command(
            f"sudo bash {remote_path} 2>&1",
            timeout=timeout,
        )
        output = stdout.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()

        if exit_code != 0:
            output += f"\n[EXIT CODE]: {exit_code}"
        return output
    finally:
        client.close()


class DepinManager:
    def __init__(self, db: Session):
        self.db = db

    def init_builtin_projects(self):
        """初始化/更新内置 DePIN 项目"""
        for proj in BUILTIN_PROJECTS:
            existing = self.db.query(DepinProject).filter(DepinProject.name == proj["name"]).first()
            if existing:
                existing.description = proj["description"]
                existing.install_script = proj["install_script"]
                existing.health_check_cmd = proj.get("health_check_cmd", "")
                existing.config_template = proj.get("config_template")
            else:
                p = DepinProject(
                    name=proj["name"],
                    description=proj["description"],
                    install_script=proj["install_script"],
                    health_check_cmd=proj.get("health_check_cmd", ""),
                    config_template=proj.get("config_template"),
                )
                self.db.add(p)
        self.db.commit()

    def deploy_project(
        self,
        aws_mgr,
        instance: Instance,
        project: DepinProject,
        config: dict = None,
    ) -> DepinTask:
        """在实例上通过 SSH 部署 DePIN 项目"""
        task = DepinTask(
            instance_id=instance.id,
            project_id=project.id,
            status="installing",
            config=config,
        )
        self.db.add(task)
        self.db.commit()

        try:
            # 检查实例是否有 IP 和私钥
            if not instance.public_ip:
                # 尝试同步获取 IP
                aws_mgr.sync_instance(instance)
                self.db.refresh(instance)
            if not instance.public_ip:
                raise ValueError("实例没有公网 IP，请先同步实例状态")
            if not instance.private_key:
                raise ValueError("实例没有保存 SSH 私钥，无法连接")

            # 构建安装脚本，替换配置变量
            script = project.install_script
            if config:
                for key, value in config.items():
                    script = script.replace(f"${{{key}}}", str(value))

            # 通过 SSH 执行
            task.log = "正在通过 SSH 连接..."
            self.db.commit()

            output = ssh_execute(
                ip=instance.public_ip,
                private_key_pem=instance.private_key,
                script=script,
                username="ubuntu",
                timeout=600,
            )

            # 检查是否有错误
            if "[EXIT CODE]:" in output and "[EXIT CODE]: 0" not in output:
                task.status = "failed"
            else:
                task.status = "running"
            task.log = output[-2000:] if len(output) > 2000 else output  # 截断过长日志

        except Exception as e:
            task.status = "failed"
            task.log = f"部署失败: {str(e)}"
            logger.error(f"Deploy failed for instance {instance.id}: {e}")

        self.db.commit()
        self.db.refresh(task)
        return task

    def check_health(self, aws_mgr, task: DepinTask) -> dict:
        """通过 SSH 检查 DePIN 任务健康状态"""
        project = self.db.query(DepinProject).get(task.project_id)
        if not project or not project.health_check_cmd:
            return {"status": "unknown", "message": "No health check configured"}

        instance = self.db.query(Instance).get(task.instance_id)
        if not instance.public_ip or not instance.private_key:
            return {"status": "error", "message": "实例无 IP 或无 SSH 密钥"}

        try:
            output = ssh_execute(
                ip=instance.public_ip,
                private_key_pem=instance.private_key,
                script=project.health_check_cmd,
                username="ubuntu",
                timeout=30,
            )
            is_ok = "[EXIT CODE]:" not in output or "[EXIT CODE]: 0" in output
            task.status = "running" if is_ok else "failed"
            task.log = output[-1000:]
            self.db.commit()
            return {"status": task.status, "message": output[:500]}
        except Exception as e:
            return {"status": "error", "message": str(e)}
