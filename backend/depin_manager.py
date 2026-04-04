import logging
from sqlalchemy.orm import Session
from models import Instance, DepinProject, DepinTask
from aws_manager import AwsManager, DEFAULT_USER_DATA

logger = logging.getLogger(__name__)

# 预置 DePIN 项目安装脚本
BUILTIN_PROJECTS = [
    {
        "name": "titan-network",
        "description": "Titan Network - 去中心化CDN与存储网络 (https://test4.titannet.io/) 需要 Identity Code",
        "install_script": """#!/bin/bash
set -e

IDENTITY_CODE="${identity_code}"
if [ -z "$IDENTITY_CODE" ]; then
    echo "ERROR: Identity Code is required! Get it from https://test4.titannet.io/"
    exit 1
fi

cd /opt

# 安装依赖
apt-get update && apt-get install -y wget unzip

# 下载 Titan Agent
if [ ! -f /opt/titanagent/agent ]; then
    wget -q https://pcdn.titannet.io/test4/bin/agent-linux.zip -O /tmp/agent-linux.zip
    mkdir -p /opt/titanagent
    unzip -o /tmp/agent-linux.zip -d /opt/titanagent
    chmod +x /opt/titanagent/agent
    rm -f /tmp/agent-linux.zip
fi

# 创建工作目录
mkdir -p /opt/titanagent/data

# 创建 systemd 服务
cat > /etc/systemd/system/titan-agent.service << EOF
[Unit]
Description=Titan Network Agent
After=network.target

[Service]
Type=simple
ExecStart=/opt/titanagent/agent --working-dir=/opt/titanagent/data --server-url=https://test4-api.titannet.io --key=${identity_code}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 停止旧进程(如果存在)
systemctl stop titan-agent 2>/dev/null || true
pkill -f '/opt/titanagent/agent' 2>/dev/null || true
sleep 2

# 启动服务
systemctl daemon-reload
systemctl enable titan-agent
systemctl start titan-agent

echo "Titan Network agent started with Identity Code: ${identity_code:0:8}..."
""",
        "health_check_cmd": "systemctl is-active titan-agent && journalctl -u titan-agent --no-pager -n 5",
        "config_template": {"identity_code": ""},
    },
    {
        "name": "grass-node",
        "description": "Grass - 去中心化带宽共享网络",
        "install_script": """#!/bin/bash
set -e
cd /home/ubuntu

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
    usermod -aG docker ubuntu
fi

docker pull cambriantech/grass-node:latest
docker run -d --name grass-node --restart=always \\
    -e GRASS_USER_ID="${GRASS_USER_ID}" \\
    cambriantech/grass-node:latest

echo "Grass node started successfully"
""",
        "health_check_cmd": "docker ps --filter name=grass-node --format '{{.Status}}'",
        "config_template": {"GRASS_USER_ID": ""},
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
docker run -d --name nodepay --restart=always \\
    -e NP_TOKEN="${NP_TOKEN}" \\
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


class DepinManager:
    def __init__(self, db: Session):
        self.db = db

    def init_builtin_projects(self):
        """初始化/更新内置 DePIN 项目"""
        for proj in BUILTIN_PROJECTS:
            existing = self.db.query(DepinProject).filter(DepinProject.name == proj["name"]).first()
            if existing:
                # 更新已有项目的脚本和配置
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
        aws_mgr: AwsManager,
        instance: Instance,
        project: DepinProject,
        config: dict = None,
    ) -> DepinTask:
        """在实例上部署 DePIN 项目"""
        task = DepinTask(
            instance_id=instance.id,
            project_id=project.id,
            status="installing",
            config=config,
        )
        self.db.add(task)
        self.db.commit()

        try:
            # 构建安装脚本，替换配置变量
            script = project.install_script
            if config:
                for key, value in config.items():
                    script = script.replace(f"${{{key}}}", str(value))

            # 通过 SSM 执行安装脚本
            cmd_id = aws_mgr.run_command_ssm(
                instance.instance_id,
                instance.region,
                [script],
            )
            task.status = "running"
            task.log = f"SSM Command ID: {cmd_id}"
            self.db.commit()
        except Exception as e:
            task.status = "failed"
            task.log = str(e)
            self.db.commit()
            logger.error(f"Deploy failed: {e}")

        self.db.refresh(task)
        return task

    def check_health(self, aws_mgr: AwsManager, task: DepinTask) -> dict:
        """检查 DePIN 任务健康状态"""
        project = self.db.query(DepinProject).get(task.project_id)
        if not project or not project.health_check_cmd:
            return {"status": "unknown", "message": "No health check configured"}

        instance = self.db.query(Instance).get(task.instance_id)
        try:
            cmd_id = aws_mgr.run_command_ssm(
                instance.instance_id,
                instance.region,
                [project.health_check_cmd],
            )
            return {"status": "checking", "command_id": cmd_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}
