"""
AWS Lightsail 实例管理模块

提供完整的 Lightsail 生命周期管理:
- 蓝图 (Blueprints): 操作系统/应用镜像 (Ubuntu / Debian / Amazon Linux / WordPress / LAMP / Node.js ...)
- 套餐 (Bundles): 实例规格 (nano / micro / small / medium / large / xlarge / 2xlarge, 含 CPU 优化型/内存优化型/标准型)
- 区域 (Regions): 所有支持 Lightsail 的 AWS 区域
- 实例操作: 创建 / 列表 / 启动 / 停止 / 重启 / 删除 / 状态同步
"""
import logging
import boto3
from botocore.config import Config
from sqlalchemy.orm import Session
from models import AwsAccount
from proxy_manager import ProxyManager
from aws_manager import ProxyRequiredError

logger = logging.getLogger(__name__)


# Lightsail 支持的所有区域 (与 EC2 不完全相同)
LIGHTSAIL_REGIONS = {
    "us-east-1": "🇺🇸 美国 弗吉尼亚",
    "us-east-2": "🇺🇸 美国 俄亥俄",
    "us-west-2": "🇺🇸 美国 俄勒冈",
    "ca-central-1": "🇨🇦 加拿大 中部",
    "eu-west-1": "🇮🇪 爱尔兰",
    "eu-west-2": "🇬🇧 英国 伦敦",
    "eu-west-3": "🇫🇷 法国 巴黎",
    "eu-central-1": "🇩🇪 德国 法兰克福",
    "eu-north-1": "🇸🇪 瑞典 斯德哥尔摩",
    "ap-south-1": "🇮🇳 印度 孟买",
    "ap-northeast-1": "🇯🇵 日本 东京",
    "ap-northeast-2": "🇰🇷 韩国 首尔",
    "ap-southeast-1": "🇸🇬 新加坡",
    "ap-southeast-2": "🇦🇺 澳大利亚 悉尼",
}


# ============================================================
# 内置蓝图 (Blueprints) 完整列表
# 当 AWS API 调用失败或受限时作为回退
# ============================================================
BUILTIN_BLUEPRINTS = [
    # ============= Linux / Unix OS only =============
    {"id": "amazon_linux_2", "name": "Amazon Linux 2", "group": "amazon_linux", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "Amazon Linux 2 (基于 AL2)"},
    {"id": "amazon_linux_2023", "name": "Amazon Linux 2023", "group": "amazon_linux_2023", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "Amazon Linux 2023 (最新)"},
    {"id": "ubuntu_24_04", "name": "Ubuntu 24.04 LTS", "group": "ubuntu", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "Ubuntu 24.04 Noble Numbat"},
    {"id": "ubuntu_22_04", "name": "Ubuntu 22.04 LTS", "group": "ubuntu", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "Ubuntu 22.04 Jammy Jellyfish"},
    {"id": "ubuntu_20_04", "name": "Ubuntu 20.04 LTS", "group": "ubuntu", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "Ubuntu 20.04 Focal Fossa"},
    {"id": "debian_12", "name": "Debian 12", "group": "debian", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "Debian 12 Bookworm"},
    {"id": "debian_11", "name": "Debian 11", "group": "debian", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "Debian 11 Bullseye"},
    {"id": "freebsd_14", "name": "FreeBSD 14", "group": "freebsd", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "FreeBSD 14"},
    {"id": "opensuse_15", "name": "openSUSE 15", "group": "opensuse", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "openSUSE Leap 15"},
    {"id": "centos_stream_9", "name": "CentOS Stream 9", "group": "centos", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "CentOS Stream 9"},
    {"id": "alma_linux_9", "name": "AlmaLinux 9", "group": "alma_linux", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "AlmaLinux 9 (RHEL 兼容)"},
    {"id": "rocky_linux_9", "name": "Rocky Linux 9", "group": "rocky_linux", "platform": "LINUX_UNIX", "type": "os", "category": "Linux/Unix", "description": "Rocky Linux 9 (RHEL 兼容)"},

    # ============= Windows OS only =============
    {"id": "windows_server_2022", "name": "Windows Server 2022", "group": "windows_server_2022", "platform": "WINDOWS", "type": "os", "category": "Windows", "description": "Windows Server 2022"},
    {"id": "windows_server_2019", "name": "Windows Server 2019", "group": "windows_server_2019", "platform": "WINDOWS", "type": "os", "category": "Windows", "description": "Windows Server 2019"},
    {"id": "windows_server_2016", "name": "Windows Server 2016", "group": "windows_server_2016", "platform": "WINDOWS", "type": "os", "category": "Windows", "description": "Windows Server 2016"},
    {"id": "windows_server_2022_core", "name": "Windows Server 2022 Core", "group": "windows_server_2022_core", "platform": "WINDOWS", "type": "os", "category": "Windows", "description": "Windows Server 2022 (Core, 无桌面)"},
    {"id": "windows_server_2019_sql_web_2019", "name": "Windows Server 2019 + SQL Server 2019 Web", "group": "windows_server_2019_sql_web_2019", "platform": "WINDOWS", "type": "os", "category": "Windows", "description": "Windows Server 2019 + SQL Server 2019 Web 版"},

    # ============= Apps + OS (基于 Bitnami) =============
    {"id": "wordpress", "name": "WordPress", "group": "wordpress", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "WordPress 单站点"},
    {"id": "wordpress_multisite", "name": "WordPress Multisite", "group": "wordpress_multisite", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "WordPress 多站点"},
    {"id": "lamp_8_bitnami", "name": "LAMP (PHP 8)", "group": "lamp_8", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Linux + Apache + MySQL + PHP 8"},
    {"id": "lamp_7_bitnami", "name": "LAMP (PHP 7)", "group": "lamp_7", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Linux + Apache + MySQL + PHP 7"},
    {"id": "nodejs", "name": "Node.js", "group": "nodejs", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Node.js 运行环境"},
    {"id": "magento", "name": "Magento", "group": "magento", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Magento 电商平台"},
    {"id": "mean", "name": "MEAN", "group": "mean", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "MongoDB + Express + Angular + Node.js"},
    {"id": "drupal", "name": "Drupal", "group": "drupal", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Drupal 内容管理"},
    {"id": "gitlab", "name": "GitLab CE", "group": "gitlab", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "GitLab Community 版"},
    {"id": "redmine", "name": "Redmine", "group": "redmine", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Redmine 项目管理"},
    {"id": "nginx", "name": "Nginx", "group": "nginx", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Nginx Web 服务器"},
    {"id": "ghost_bitnami", "name": "Ghost", "group": "ghost", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Ghost 博客平台"},
    {"id": "joomla", "name": "Joomla", "group": "joomla", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Joomla CMS"},
    {"id": "prestashop", "name": "PrestaShop", "group": "prestashop", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "PrestaShop 电商平台"},
    {"id": "django", "name": "Django", "group": "django", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Django Python 框架"},
    {"id": "plesk_ubuntu", "name": "Plesk Hosting Stack on Ubuntu", "group": "plesk", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "Plesk 主机管理面板"},
    {"id": "cpanel_whm_linux", "name": "cPanel & WHM for Linux", "group": "cpanel", "platform": "LINUX_UNIX", "type": "app", "category": "应用 + OS", "description": "cPanel & WHM (含 14 天试用)"},
]


# ============================================================
# 内置套餐 (Bundles) 完整列表
# ============================================================
# 标准实例 (Linux 价格, Windows 价格更高)
BUILTIN_BUNDLES_STANDARD = [
    {"id": "nano_3_0", "name": "Nano (3.5 USD)", "category": "标准型 Linux", "platform": "LINUX_UNIX", "ram": 0.5, "cpu": 2, "disk": 20, "transfer": 1024, "price": 3.5, "description": "0.5 GB RAM · 2 vCPU · 20 GB SSD · 1 TB"},
    {"id": "micro_3_0", "name": "Micro (5 USD)", "category": "标准型 Linux", "platform": "LINUX_UNIX", "ram": 1, "cpu": 2, "disk": 40, "transfer": 2048, "price": 5, "description": "1 GB RAM · 2 vCPU · 40 GB SSD · 2 TB"},
    {"id": "small_3_0", "name": "Small (10 USD)", "category": "标准型 Linux", "platform": "LINUX_UNIX", "ram": 2, "cpu": 2, "disk": 60, "transfer": 3072, "price": 10, "description": "2 GB RAM · 2 vCPU · 60 GB SSD · 3 TB"},
    {"id": "medium_3_0", "name": "Medium (20 USD)", "category": "标准型 Linux", "platform": "LINUX_UNIX", "ram": 4, "cpu": 2, "disk": 80, "transfer": 4096, "price": 20, "description": "4 GB RAM · 2 vCPU · 80 GB SSD · 4 TB"},
    {"id": "large_3_0", "name": "Large (40 USD)", "category": "标准型 Linux", "platform": "LINUX_UNIX", "ram": 8, "cpu": 2, "disk": 160, "transfer": 5120, "price": 40, "description": "8 GB RAM · 2 vCPU · 160 GB SSD · 5 TB"},
    {"id": "xlarge_3_0", "name": "XLarge (80 USD)", "category": "标准型 Linux", "platform": "LINUX_UNIX", "ram": 16, "cpu": 4, "disk": 320, "transfer": 6144, "price": 80, "description": "16 GB RAM · 4 vCPU · 320 GB SSD · 6 TB"},
    {"id": "2xlarge_3_0", "name": "2XLarge (160 USD)", "category": "标准型 Linux", "platform": "LINUX_UNIX", "ram": 32, "cpu": 8, "disk": 640, "transfer": 7168, "price": 160, "description": "32 GB RAM · 8 vCPU · 640 GB SSD · 7 TB"},

    # Windows 标准型
    {"id": "nano_win_3_0", "name": "Nano Windows (8 USD)", "category": "标准型 Windows", "platform": "WINDOWS", "ram": 0.5, "cpu": 2, "disk": 30, "transfer": 1024, "price": 8, "description": "0.5 GB RAM · 2 vCPU · 30 GB SSD · 1 TB"},
    {"id": "micro_win_3_0", "name": "Micro Windows (12 USD)", "category": "标准型 Windows", "platform": "WINDOWS", "ram": 1, "cpu": 2, "disk": 40, "transfer": 2048, "price": 12, "description": "1 GB RAM · 2 vCPU · 40 GB SSD · 2 TB"},
    {"id": "small_win_3_0", "name": "Small Windows (20 USD)", "category": "标准型 Windows", "platform": "WINDOWS", "ram": 2, "cpu": 2, "disk": 60, "transfer": 3072, "price": 20, "description": "2 GB RAM · 2 vCPU · 60 GB SSD · 3 TB"},
    {"id": "medium_win_3_0", "name": "Medium Windows (40 USD)", "category": "标准型 Windows", "platform": "WINDOWS", "ram": 4, "cpu": 2, "disk": 80, "transfer": 4096, "price": 40, "description": "4 GB RAM · 2 vCPU · 80 GB SSD · 4 TB"},
    {"id": "large_win_3_0", "name": "Large Windows (70 USD)", "category": "标准型 Windows", "platform": "WINDOWS", "ram": 8, "cpu": 2, "disk": 160, "transfer": 5120, "price": 70, "description": "8 GB RAM · 2 vCPU · 160 GB SSD · 5 TB"},
    {"id": "xlarge_win_3_0", "name": "XLarge Windows (120 USD)", "category": "标准型 Windows", "platform": "WINDOWS", "ram": 16, "cpu": 4, "disk": 320, "transfer": 6144, "price": 120, "description": "16 GB RAM · 4 vCPU · 320 GB SSD · 6 TB"},
    {"id": "2xlarge_win_3_0", "name": "2XLarge Windows (240 USD)", "category": "标准型 Windows", "platform": "WINDOWS", "ram": 32, "cpu": 8, "disk": 640, "transfer": 7168, "price": 240, "description": "32 GB RAM · 8 vCPU · 640 GB SSD · 7 TB"},
]

# 计算优化 / 内存优化 / 通用型 (基于 m6/c6/r6 的 Lightsail for Research)
BUILTIN_BUNDLES_OPTIMIZED = [
    # 计算优化
    {"id": "compute_xlarge_3_0", "name": "Compute Optimized XL (60 USD)", "category": "计算优化", "platform": "LINUX_UNIX", "ram": 8, "cpu": 4, "disk": 100, "transfer": 6144, "price": 60, "description": "高 CPU/内存 比 - 8 GB RAM · 4 vCPU · 100 GB SSD"},
    {"id": "compute_2xlarge_3_0", "name": "Compute Optimized 2XL (120 USD)", "category": "计算优化", "platform": "LINUX_UNIX", "ram": 16, "cpu": 8, "disk": 200, "transfer": 7168, "price": 120, "description": "高 CPU/内存 比 - 16 GB RAM · 8 vCPU · 200 GB SSD"},
    {"id": "compute_4xlarge_3_0", "name": "Compute Optimized 4XL (240 USD)", "category": "计算优化", "platform": "LINUX_UNIX", "ram": 32, "cpu": 16, "disk": 400, "transfer": 8192, "price": 240, "description": "高 CPU/内存 比 - 32 GB RAM · 16 vCPU · 400 GB SSD"},
    # 内存优化
    {"id": "memory_xlarge_3_0", "name": "Memory Optimized XL (90 USD)", "category": "内存优化", "platform": "LINUX_UNIX", "ram": 32, "cpu": 4, "disk": 150, "transfer": 6144, "price": 90, "description": "高内存 - 32 GB RAM · 4 vCPU · 150 GB SSD"},
    {"id": "memory_2xlarge_3_0", "name": "Memory Optimized 2XL (180 USD)", "category": "内存优化", "platform": "LINUX_UNIX", "ram": 64, "cpu": 8, "disk": 300, "transfer": 7168, "price": 180, "description": "高内存 - 64 GB RAM · 8 vCPU · 300 GB SSD"},
    # 通用 (Research)
    {"id": "general_xlarge_3_0", "name": "General Purpose XL (50 USD)", "category": "通用型", "platform": "LINUX_UNIX", "ram": 16, "cpu": 4, "disk": 200, "transfer": 6144, "price": 50, "description": "通用研究型 - 16 GB RAM · 4 vCPU · 200 GB SSD"},
    {"id": "general_2xlarge_3_0", "name": "General Purpose 2XL (100 USD)", "category": "通用型", "platform": "LINUX_UNIX", "ram": 32, "cpu": 8, "disk": 400, "transfer": 7168, "price": 100, "description": "通用研究型 - 32 GB RAM · 8 vCPU · 400 GB SSD"},
]

BUILTIN_BUNDLES = BUILTIN_BUNDLES_STANDARD + BUILTIN_BUNDLES_OPTIMIZED


# 默认启动脚本: 安装 Docker 等常用工具 (Linux 系统)
DEFAULT_LIGHTSAIL_USER_DATA = """#!/bin/bash
apt-get update -y || yum update -y
apt-get install -y docker.io curl wget || yum install -y docker curl wget
systemctl enable docker && systemctl start docker
"""


class LightsailManager:
    def __init__(
        self,
        account: AwsAccount,
        db: Session,
        use_proxy: bool = True,
        require_proxy: bool = True,
    ):
        """同 AwsManager: 默认强制走代理, 没代理直接抛 ProxyRequiredError, 防止暴露服务器 IP."""
        self.account = account
        self.db = db
        self.proxy_config = None
        self.proxy_id: int = 0   # 当前选中的代理 ID
        self._client_cache = {}
        self.use_proxy = use_proxy
        self.require_proxy = require_proxy
        if use_proxy:
            # P1 修复: 按账号 id hash 稳定选代理 (同账号永远走同代理, 防 AWS 风控关联)
            user_id = getattr(account, "user_id", None)
            pm = ProxyManager(db, user_id=user_id)
            p = pm.get_proxy_for_account(account.id) if getattr(account, "id", None) else pm.get_proxy_round_robin()
            if p:
                self.proxy_config = {"http": p["url"], "https": p["url"]}
                self.proxy_id = p["id"]
            if not self.proxy_config and require_proxy:
                raise ProxyRequiredError(
                    "代理池为空 (或仅有 socks5 但 PySocks 未安装): 该用户没有可用代理。"
                    "请先到「代理管理」页面添加可用代理 (并确保 pip install PySocks 后重启), "
                    "否则所有 AWS 调用会暴露服务器真实 IP。"
                )

    def _get_client(self, region: str = None):
        if self.use_proxy and self.require_proxy and not self.proxy_config:
            raise ProxyRequiredError("代理已失效, 拒绝直连 AWS")
        region = region or self.account.default_region
        # Lightsail 在某些区域不支持，用 us-east-1 兜底
        if region not in LIGHTSAIL_REGIONS:
            region = "us-east-1"
        if region in self._client_cache:
            return self._client_cache[region]
        config_kwargs = {"connect_timeout": 8, "read_timeout": 30, "retries": {"max_attempts": 2}}
        if self.proxy_config:
            config_kwargs["proxies"] = self.proxy_config
        client = boto3.client(
            "lightsail",
            aws_access_key_id=self.account.access_key_id,
            aws_secret_access_key=self.account.secret_access_key,
            region_name=region,
            config=Config(**config_kwargs),
        )
        self._client_cache[region] = client
        return client

    # ==================== 元数据 ====================

    def list_regions(self) -> list:
        """返回所有支持 Lightsail 的区域"""
        try:
            client = self._get_client("us-east-1")
            resp = client.get_regions()
            regions = []
            for r in resp.get("regions", []):
                code = r.get("name", "")
                regions.append({
                    "code": code,
                    "display": LIGHTSAIL_REGIONS.get(code, r.get("displayName", code)),
                    "availability_zones": [z.get("zoneName") for z in r.get("availabilityZones", [])],
                })
            return regions or [{"code": k, "display": v, "availability_zones": []} for k, v in LIGHTSAIL_REGIONS.items()]
        except Exception as e:
            logger.warning(f"Lightsail list_regions failed, fallback: {e}")
            return [{"code": k, "display": v, "availability_zones": []} for k, v in LIGHTSAIL_REGIONS.items()]

    def list_blueprints(self, region: str = None) -> list:
        """列出所有蓝图 (操作系统/应用镜像)"""
        try:
            client = self._get_client(region)
            resp = client.get_blueprints(includeInactive=False)
            blueprints = []
            for b in resp.get("blueprints", []):
                blueprints.append({
                    "id": b.get("blueprintId", ""),
                    "name": b.get("name", ""),
                    "group": b.get("group", ""),
                    "platform": b.get("platform", ""),
                    "type": b.get("type", ""),
                    "description": b.get("description", ""),
                    "version": b.get("version", ""),
                    "is_active": b.get("isActive", True),
                    "category": _classify_blueprint(b),
                })
            if not blueprints:
                raise ValueError("空蓝图列表")
            return blueprints
        except Exception as e:
            logger.warning(f"Lightsail list_blueprints failed in {region}, using builtin: {e}")
            return BUILTIN_BLUEPRINTS

    def list_bundles(self, region: str = None) -> list:
        """列出所有套餐 (实例规格)"""
        try:
            client = self._get_client(region)
            resp = client.get_bundles(includeInactive=False)
            bundles = []
            for b in resp.get("bundles", []):
                bundles.append({
                    "id": b.get("bundleId", ""),
                    "name": b.get("name", ""),
                    "ram": b.get("ramSizeInGb", 0),
                    "cpu": b.get("cpuCount", 0),
                    "disk": b.get("diskSizeInGb", 0),
                    "transfer": b.get("transferPerMonthInGb", 0),
                    "price": b.get("price", 0),
                    "platform": (b.get("supportedPlatforms") or ["LINUX_UNIX"])[0] if b.get("supportedPlatforms") else "LINUX_UNIX",
                    "is_active": b.get("isActive", True),
                    "instance_type": b.get("instanceType", ""),
                    "description": _build_bundle_desc(b),
                    "category": _classify_bundle(b),
                })
            if not bundles:
                raise ValueError("空套餐列表")
            return bundles
        except Exception as e:
            logger.warning(f"Lightsail list_bundles failed in {region}, using builtin: {e}")
            return BUILTIN_BUNDLES

    def list_availability_zones(self, region: str) -> list:
        """获取指定区域的可用区"""
        try:
            client = self._get_client(region)
            resp = client.get_regions(includeAvailabilityZones=True)
            for r in resp.get("regions", []):
                if r.get("name") == region:
                    return [z.get("zoneName") for z in r.get("availabilityZones", []) if z.get("state") == "available"]
        except Exception as e:
            logger.warning(f"list_availability_zones {region} failed: {e}")
        # 通用回退
        return [f"{region}a", f"{region}b", f"{region}c"]

    # ==================== 实例操作 ====================

    def list_instances(self, region: str = None) -> list:
        """列出指定区域的所有 Lightsail 实例"""
        client = self._get_client(region)
        try:
            resp = client.get_instances()
            instances = []
            for inst in resp.get("instances", []):
                instances.append(_serialize_instance(inst, region or self.account.default_region))
            return instances
        except Exception as e:
            logger.error(f"list_instances {region} failed: {e}")
            raise

    def list_instances_all_regions(self) -> dict:
        """并发列出所有区域的 Lightsail 实例"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = {}
        with ThreadPoolExecutor(max_workers=14) as pool:
            futures = {pool.submit(self.list_instances, r): r for r in LIGHTSAIL_REGIONS.keys()}
            for future in as_completed(futures):
                region = futures[future]
                try:
                    results[region] = future.result()
                except Exception as e:
                    logger.warning(f"Lightsail {region} fetch failed: {str(e)[:100]}")
                    results[region] = []
        return results

    def create_instance(
        self,
        instance_name: str,
        region: str,
        availability_zone: str,
        blueprint_id: str,
        bundle_id: str,
        user_data: str = None,
        key_pair_name: str = None,
        tags: list = None,
        count: int = 1,
    ) -> dict:
        """创建 Lightsail 实例

        参数:
        - instance_name: 实例基础名称 (count > 1 时会自动追加 -1, -2 ...)
        - region: 区域代码, 如 us-east-1
        - availability_zone: 可用区, 如 us-east-1a
        - blueprint_id: 蓝图 ID, 如 ubuntu_22_04 / wordpress / amazon_linux_2023
        - bundle_id: 套餐 ID, 如 nano_3_0 / small_3_0 / large_3_0
        - user_data: 启动脚本 (Linux Bash, 可选)
        - key_pair_name: 已存在的 SSH 密钥对名称 (留空使用 LightsailDefaultKeyPair)
        - tags: 标签列表
        - count: 创建数量
        """
        client = self._get_client(region)

        # 名称合规化 (字母/数字/_/-, 最多 254)
        base_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in instance_name)[:200] or "lightsail"
        if count > 1:
            instance_names = [f"{base_name}-{i+1}" for i in range(count)]
        else:
            instance_names = [base_name]

        params = {
            "instanceNames": instance_names,
            "availabilityZone": availability_zone,
            "blueprintId": blueprint_id,
            "bundleId": bundle_id,
            "userData": user_data or DEFAULT_LIGHTSAIL_USER_DATA,
            "tags": tags or [
                {"key": "ManagedBy", "value": "aws-depin-manager"},
                {"key": "AccountId", "value": str(self.account.id)},
            ],
        }
        if key_pair_name:
            params["keyPairName"] = key_pair_name

        resp = client.create_instances(**params)
        operations = resp.get("operations", [])
        return {
            "instance_names": instance_names,
            "region": region,
            "operations": [{"id": o.get("id"), "status": o.get("status"), "type": o.get("operationType")} for o in operations],
        }

    def get_instance(self, instance_name: str, region: str) -> dict:
        client = self._get_client(region)
        resp = client.get_instance(instanceName=instance_name)
        return _serialize_instance(resp.get("instance", {}), region)

    def start_instance(self, instance_name: str, region: str) -> dict:
        client = self._get_client(region)
        resp = client.start_instance(instanceName=instance_name)
        return {"operations": [{"id": o.get("id"), "status": o.get("status")} for o in resp.get("operations", [])]}

    def stop_instance(self, instance_name: str, region: str, force: bool = False) -> dict:
        client = self._get_client(region)
        resp = client.stop_instance(instanceName=instance_name, force=force)
        return {"operations": [{"id": o.get("id"), "status": o.get("status")} for o in resp.get("operations", [])]}

    def reboot_instance(self, instance_name: str, region: str) -> dict:
        client = self._get_client(region)
        resp = client.reboot_instance(instanceName=instance_name)
        return {"operations": [{"id": o.get("id"), "status": o.get("status")} for o in resp.get("operations", [])]}

    def delete_instance(self, instance_name: str, region: str, force_delete_addons: bool = True) -> dict:
        client = self._get_client(region)
        resp = client.delete_instance(instanceName=instance_name, forceDeleteAddOns=force_delete_addons)
        return {"operations": [{"id": o.get("id"), "status": o.get("status")} for o in resp.get("operations", [])]}

    def open_instance_ports(self, instance_name: str, region: str, ports: list = None) -> dict:
        """开放实例端口 (默认开放 22/80/443 + 常用 DePIN 端口)"""
        client = self._get_client(region)
        ports = ports or [
            {"fromPort": 22, "toPort": 22, "protocol": "tcp"},
            {"fromPort": 80, "toPort": 80, "protocol": "tcp"},
            {"fromPort": 443, "toPort": 443, "protocol": "tcp"},
            {"fromPort": 3000, "toPort": 9999, "protocol": "tcp"},
        ]
        opened = []
        for p in ports:
            try:
                client.open_instance_public_ports(
                    portInfo=p,
                    instanceName=instance_name,
                )
                opened.append(p)
            except Exception as e:
                logger.warning(f"open port {p} failed: {e}")
        return {"opened": opened}


# ============================================================
# 内部辅助函数
# ============================================================

def _classify_blueprint(b: dict) -> str:
    platform = (b.get("platform") or "").upper()
    btype = (b.get("type") or "").lower()
    name = (b.get("name") or "").lower()
    if platform == "WINDOWS":
        return "Windows"
    if btype == "app" or any(k in name for k in ["wordpress", "lamp", "node", "magento", "drupal", "gitlab", "ghost", "joomla", "redmine", "django", "mean", "plesk", "cpanel", "prestashop", "nginx"]):
        return "应用 + OS"
    return "Linux/Unix"


def _classify_bundle(b: dict) -> str:
    platforms = b.get("supportedPlatforms") or []
    if "WINDOWS" in platforms:
        return "标准型 Windows"
    name = (b.get("name") or "").lower()
    bid = (b.get("bundleId") or "").lower()
    if "compute" in name or "compute" in bid:
        return "计算优化"
    if "memory" in name or "memory" in bid:
        return "内存优化"
    if "general" in name or "general" in bid:
        return "通用型"
    return "标准型 Linux"


def _build_bundle_desc(b: dict) -> str:
    ram = b.get("ramSizeInGb", 0)
    cpu = b.get("cpuCount", 0)
    disk = b.get("diskSizeInGb", 0)
    transfer = b.get("transferPerMonthInGb", 0)
    transfer_str = f"{transfer/1024:.0f} TB" if transfer >= 1024 else f"{transfer} GB"
    return f"{ram} GB RAM · {cpu} vCPU · {disk} GB SSD · {transfer_str}"


def _serialize_instance(inst: dict, region: str) -> dict:
    if not inst:
        return {}
    state = (inst.get("state") or {}).get("name", "unknown")
    blueprint = inst.get("blueprintName") or inst.get("blueprintId") or ""
    bundle = inst.get("bundleId") or ""
    hardware = inst.get("hardware") or {}
    return {
        "name": inst.get("name", ""),
        "arn": inst.get("arn", ""),
        "region": region,
        "availability_zone": (inst.get("location") or {}).get("availabilityZone", ""),
        "blueprint_id": inst.get("blueprintId", ""),
        "blueprint_name": blueprint,
        "bundle_id": bundle,
        "state": state,
        "public_ip": inst.get("publicIpAddress", ""),
        "private_ip": inst.get("privateIpAddress", ""),
        "ipv6": (inst.get("ipv6Addresses") or [None])[0] if inst.get("ipv6Addresses") else None,
        "ssh_username": inst.get("username", ""),
        "ram": hardware.get("ramSizeInGb", 0),
        "cpu": hardware.get("cpuCount", 0),
        "disks": [{"name": d.get("name"), "size": d.get("sizeInGb")} for d in hardware.get("disks", [])],
        "created_at": str(inst.get("createdAt", "")),
        "is_static_ip": inst.get("isStaticIp", False),
        "key_pair_name": inst.get("sshKeyName", ""),
    }
