import logging
from sqlalchemy.orm import Session
from models import Instance, DepinProject, DepinTask
from aws_manager import AwsManager, DEFAULT_USER_DATA

logger = logging.getLogger(__name__)

# 预置 DePIN 项目安装脚本
BUILTIN_PROJECTS = [
    {
        "name": "titan-network",
        "description": "Titan Network - 去中心化CDN与存储网络 (https://test1.titannet.io/)",
        "install_script": """#!/bin/bash
set -e
cd /home/ubuntu

# 安装 Docker (如果尚未安装)
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
    usermod -aG docker ubuntu
fi

# 拉取并运行 Titan Network 节点
docker pull nezha123/titan-edge:latest
mkdir -p /home/ubuntu/.titanedge

# 启动 Titan Edge 节点
docker run -d --name titan-edge --restart=always \\
    -v /home/ubuntu/.titanedge:/root/.titanedge \\
    -p 1234:1234 \\
    nezha123/titan-edge:latest

echo "Titan Network node started successfully"
""",
        "health_check_cmd": "docker ps --filter name=titan-edge --format '{{.Status}}'",
        "config_template": {"bind_code": "", "storage_size": "50GB"},
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
        """初始化内置 DePIN 项目"""
        for proj in BUILTIN_PROJECTS:
            existing = self.db.query(DepinProject).filter(DepinProject.name == proj["name"]).first()
            if not existing:
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
