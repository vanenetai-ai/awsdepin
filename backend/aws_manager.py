import boto3
import base64
import logging
from datetime import datetime
from botocore.config import Config
from sqlalchemy.orm import Session
from models import AwsAccount, Instance
from proxy_manager import ProxyManager

logger = logging.getLogger(__name__)

# 默认启用的区域 (排除 opt-in 区域如 me-south-1, af-south-1 等，它们会超时)
REGION_DISPLAY = {
    "us-east-1": "🇺🇸 美国 弗吉尼亚",
    "us-east-2": "🇺🇸 美国 俄亥俄",
    "us-west-1": "🇺🇸 美国 加利福尼亚",
    "us-west-2": "🇺🇸 美国 俄勒冈",
    "ap-south-1": "🇮🇳 印度 孟买",
    "ap-northeast-1": "🇯🇵 日本 东京",
    "ap-northeast-2": "🇰🇷 韩国 首尔",
    "ap-northeast-3": "🇯🇵 日本 大阪",
    "ap-southeast-1": "🇸🇬 新加坡",
    "ap-southeast-2": "🇦🇺 澳大利亚 悉尼",
    "ca-central-1": "🇨🇦 加拿大",
    "eu-central-1": "🇩🇪 德国 法兰克福",
    "eu-west-1": "🇮🇪 爱尔兰",
    "eu-west-2": "🇬🇧 英国 伦敦",
    "eu-west-3": "🇫🇷 法国 巴黎",
    "eu-north-1": "🇸🇪 瑞典 斯德哥尔摩",
    "sa-east-1": "🇧🇷 巴西 圣保罗",
}

# 国家代码到国旗 emoji
COUNTRY_FLAGS = {
    "US": "🇺🇸", "CN": "🇨🇳", "JP": "🇯🇵", "KR": "🇰🇷", "DE": "🇩🇪",
    "GB": "🇬🇧", "FR": "🇫🇷", "CA": "🇨🇦", "AU": "🇦🇺", "IN": "🇮🇳",
    "BR": "🇧🇷", "SG": "🇸🇬", "IE": "🇮🇪", "SE": "🇸🇪", "TH": "🇹🇭",
    "VN": "🇻🇳", "PH": "🇵🇭", "MY": "🇲🇾", "ID": "🇮🇩", "TW": "🇹🇼",
    "HK": "🇭🇰", "RU": "🇷🇺", "TR": "🇹🇷", "MX": "🇲🇽", "AR": "🇦🇷",
    "CL": "🇨🇱", "CO": "🇨🇴", "PE": "🇵🇪", "ZA": "🇿🇦", "NG": "🇳🇬",
    "EG": "🇪🇬", "SA": "🇸🇦", "AE": "🇦🇪", "IL": "🇮🇱", "PK": "🇵🇰",
    "BD": "🇧🇩", "NZ": "🇳🇿", "IT": "🇮🇹", "ES": "🇪🇸", "NL": "🇳🇱",
    "PL": "🇵🇱", "CH": "🇨🇭", "AT": "🇦🇹", "BE": "🇧🇪", "PT": "🇵🇹",
    "CZ": "🇨🇿", "RO": "🇷🇴", "HU": "🇭🇺", "FI": "🇫🇮", "NO": "🇳🇴",
    "DK": "🇩🇰", "GR": "🇬🇷", "UA": "🇺🇦", "BH": "🇧🇭",
}

# EBS 卷类型最大容量 (GiB) - 来自 AWS 官方文档 (2024+)
# gp3 / io2 已提到 64 TiB; io2 在 >16 TiB 或 IOPS>64000 时 AWS 自动启用 Block Express
EBS_MAX_VOLUME_GIB = {
    "gp3": 65536,
    "io2": 65536,
    "gp2": 16384,
    "io1": 16384,
    "st1": 16384,
    "sc1": 16384,
    "standard": 1024,
}
EBS_MIN_VOLUME_GIB = {
    "gp3": 1, "gp2": 1, "io1": 4, "io2": 4,
    "st1": 125, "sc1": 125, "standard": 1,
}


def validate_volume_size(volume_size: int, volume_type: str) -> None:
    """按 volume_type 校验磁盘大小, 不满足直接抛 ValueError (前端会显示中文消息)"""
    vt = (volume_type or "gp3").lower()
    if vt not in EBS_MAX_VOLUME_GIB:
        raise ValueError(f"不支持的磁盘类型: {volume_type} (支持: {', '.join(EBS_MAX_VOLUME_GIB.keys())})")
    max_gib = EBS_MAX_VOLUME_GIB[vt]
    min_gib = EBS_MIN_VOLUME_GIB.get(vt, 1)
    if volume_size < min_gib:
        raise ValueError(f"{vt} 磁盘最小 {min_gib} GiB, 当前 {volume_size}")
    if volume_size > max_gib:
        raise ValueError(
            f"{vt} 磁盘最大 {max_gib} GiB ({max_gib // 1024} TiB), 当前 {volume_size}. "
            f"如需更大请选 gp3 或 io2 (上限 65536 GiB / 64 TiB)"
        )


# 默认安全组配置 - 开放 SSH
DEFAULT_SG_NAME = "depin-sg"
DEFAULT_SG_DESC = "Security group for DePIN nodes"

# Ubuntu 22.04 AMI (各区域不同，这里列出常用的)
UBUNTU_AMIS = {
    "us-east-1": "ami-0c7217cdde317cfec",
    "us-east-2": "ami-05fb0b8c1424f266b",
    "us-west-1": "ami-0ce2cb35386fc22e9",
    "us-west-2": "ami-008fe2fc65df48dac",
    "eu-west-1": "ami-0905a3c97561e0b69",
    "eu-central-1": "ami-0faab6bdbac9486fb",
    "ap-southeast-1": "ami-078c1149d8ad719a7",
    "ap-northeast-1": "ami-0d52744d6551d851e",
}

DEFAULT_USER_DATA = """#!/bin/bash
apt-get update -y
apt-get install -y docker.io docker-compose curl wget
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu
"""


class ProxyRequiredError(Exception):
    """没有可用代理时抛出 - 防止 boto3 走服务器出口 IP 直连 AWS.

    我们的设计原则是: **所有 AWS API 调用必须经过代理**。这样:
      1) 服务器真实 IP 不暴露给 AWS (避免风控关联多账号)
      2) 不同账号可以走不同出口 IP, 模拟"各自登陆"
      3) 中国直连 AWS 经常被 GFW reset, 代理能稳定多了

    一旦代理池为空 / 全部禁用 / 用户没有自己的代理, 立刻抛这个错误,
    上层用 try/except 转成 400 提示用户"先去代理管理添加代理"。
    """
    pass


class AwsManager:
    """严格代理模式. 全局硬约束: 没有可用代理就不能调任何 AWS API.

    设计 (不可绕过):
    - __init__ 拿不到代理 → 抛 ProxyRequiredError, 实例根本建不出来
    - _get_client / _get_resource / _make_detect_client 三个 boto3 入口都强制
      proxy_config 必须非空, 否则抛 ProxyRequiredError
    - 历史 use_proxy / require_proxy 参数保留但不再生效 (传 False 会被忽略并打日志),
      避免任何调用代码意外绕过.
    """

    def __init__(
        self,
        account: AwsAccount,
        db: Session,
        use_proxy: bool = True,        # 保留入参兼容旧代码, 但已强制 True
        require_proxy: bool = True,    # 同上, 已强制 True
    ):
        """严格代理模式: 没代理就拒绝建实例.

        Args:
            account: AWS 账号
            db: SQLAlchemy 会话
            use_proxy / require_proxy: 已废弃, 传 False 会被忽略并打 warning.
                之前的"宽松模式"已被彻底关闭 (用户明确要求: 没走代理任何账号操作都不能通过).
        """
        if not use_proxy or not require_proxy:
            logger.warning(
                f"AwsManager: use_proxy={use_proxy} / require_proxy={require_proxy} 已废弃, "
                f"严格代理模式下被忽略. 调用栈: {account.id if account else '?'}"
            )

        self.account = account
        self.db = db
        self.proxy_config = None
        self.proxy_id: int = 0   # 当前选中的代理 ID, 业务层可用于上报 success/failure
        self._client_cache = {}
        self._resource_cache = {}
        # 强制 True - 任何调用都必须走代理
        self.use_proxy = True
        self.require_proxy = True

        # P1 修复: 按账号 id hash 稳定选代理 (同账号永远走同代理, 防 AWS 风控关联)
        # P3 修复: ProxyManager 内部用独立 engine connection 写 last_used_at, 不污染 db
        user_id = getattr(account, "user_id", None)
        pm = ProxyManager(db, user_id=user_id)
        p = pm.get_proxy_for_account(account.id) if getattr(account, "id", None) else pm.get_proxy_round_robin()
        if p:
            self.proxy_config = {"http": p["url"], "https": p["url"]}
            self.proxy_id = p["id"]
        if not self.proxy_config:
            raise ProxyRequiredError(
                "代理池为空 (或仅有 socks5 但 PySocks 未安装, 或所有代理已被 quarantine): "
                "该用户没有可用代理。请先到「代理管理」页面添加可用代理 (并确保 pip install PySocks 后重启), "
                "否则所有 AWS 调用会暴露服务器真实 IP。"
            )

    def _assert_proxy(self):
        """所有 boto3 入口的硬性守门: 没代理就拒绝, 不接受任何借口."""
        if not self.proxy_config:
            raise ProxyRequiredError("代理已失效或被清空, 拒绝直连 AWS (严格代理模式)")

    def _get_client(self, service: str, region: str = None):
        self._assert_proxy()
        region = region or self.account.default_region
        cache_key = f"{service}:{region}"
        if cache_key in self._client_cache:
            return self._client_cache[cache_key]
        # 某些 API 需要更长超时
        slow_services = {"service-quotas", "iam", "account", "organizations", "support", "budgets", "bedrock", "sso-admin", "license-manager"}
        if service in slow_services:
            config_kwargs = {"connect_timeout": 10, "read_timeout": 30, "retries": {"max_attempts": 2}}
        else:
            config_kwargs = {"connect_timeout": 5, "read_timeout": 10, "retries": {"max_attempts": 1}}
        config_kwargs["proxies"] = self.proxy_config   # 强制带上, 不再走 if-else
        client = boto3.client(
            service,
            aws_access_key_id=self.account.access_key_id,
            aws_secret_access_key=self.account.secret_access_key,
            region_name=region,
            config=Config(**config_kwargs),
        )
        self._client_cache[cache_key] = client
        return client

    def _get_resource(self, service: str, region: str = None):
        self._assert_proxy()
        region = region or self.account.default_region
        cache_key = f"{service}:{region}"
        if cache_key in self._resource_cache:
            return self._resource_cache[cache_key]
        config_kwargs = {"proxies": self.proxy_config}   # 强制带上
        resource = boto3.resource(
            service,
            aws_access_key_id=self.account.access_key_id,
            aws_secret_access_key=self.account.secret_access_key,
            region_name=region,
            config=Config(**config_kwargs),
        )
        self._resource_cache[cache_key] = resource
        return resource

    def verify_credentials(self) -> dict:
        """验证 AWS 凭证是否有效，并识别账号状态。

        返回字段:
            valid: True/False
            account_id, arn (valid=True 时)
            error (valid=False 时)
            status: active / invalid_credentials / disabled / unknown
        """
        try:
            sts = self._get_client("sts")
            identity = sts.get_caller_identity()
            return {
                "valid": True,
                "account_id": identity["Account"],
                "arn": identity["Arn"],
                "status": "active",
            }
        except Exception as e:
            err = str(e)
            status = self._classify_credential_error(err)
            return {"valid": False, "error": err, "status": status}

    @staticmethod
    def _classify_credential_error(err: str) -> str:
        """根据 AWS 报错文本判断账号状态"""
        s = (err or "").lower()
        # ✅ 先识别"网络层错误"——这类错误跟 AK/SK 是否有效无关, 不能据此把账号判失效
        if AwsManager._is_network_error(err):
            return "network_error"
        # AK/SK 失效
        invalid_markers = [
            "invalidclienttokenid",
            "signaturedoesnotmatch",
            "the security token included in the request is invalid",
            "the aws access key id needs a subscription",
            "auth failure",
            "authfailure",
            "invalidaccesskeyid",
            "tokenrefreshrequired",
            "the request signature we calculated does not match",
        ]
        for m in invalid_markers:
            if m in s:
                return "invalid_credentials"
        # 账号被禁用 / 暂停
        disabled_markers = [
            "account is suspended",
            "account is closed",
            "is disabled",
            "is not authorized to perform: sts:getcalleridentity",
            "your account has been suspended",
        ]
        for m in disabled_markers:
            if m in s:
                return "disabled"
        return "unknown"

    @staticmethod
    def _is_network_error(err: str) -> bool:
        """判断错误是否为网络层错误 (连接被 reset/超时/TLS失败/代理失败 等), 与 AK/SK 是否有效无关.

        典型场景:
        - 服务器在中国直连 AWS, 被 GFW reset:
          'Connection was closed before we received a valid response from endpoint URL'
          'EndpointConnectionError'
        - 代理或网络抖动:
          'ConnectTimeoutError' / 'ReadTimeoutError' / 'ConnectionResetError' / 'ProxyConnectionError'
        - DNS 失败 / TLS 握手失败:
          'NameResolutionError' / 'SSLError' / 'BadStatusLine'
        """
        if not err:
            return False
        s = err.lower()
        markers = [
            "connection was closed before we received",
            "endpointconnectionerror",
            "could not connect to the endpoint url",
            "connecttimeout",
            "readtimeout",
            "connection reset",
            "connectionreseterror",
            "connection aborted",
            "remote end closed connection",
            "broken pipe",
            "max retries exceeded",
            "name or service not known",
            "nameresolutionerror",
            "temporary failure in name resolution",
            "no route to host",
            "network is unreachable",
            "ssl: ",
            "sslerror",
            "ssl handshake",
            "certificate verify failed",
            "proxyconnectionerror",
            "proxy",
            "407 proxy authentication",
            "badstatusline",
            "httpsconnectionpool",
            "tunnel connection failed",
            "got socket error",
            "newconnectionerror",
        ]
        for m in markers:
            if m in s:
                return True
        return False

    def list_regions(self) -> list:
        """列出已启用的区域 (含 opt-in 已开启的)"""
        ec2 = self._get_client("ec2", "us-east-1")
        resp = ec2.describe_regions(AllRegions=False)
        return [r["RegionName"] for r in resp["Regions"]]

    def list_all_regions(self) -> list:
        """列出所有 AWS 区域 (含未启用的 opt-in)。用于全量扫描。"""
        try:
            ec2 = self._get_client("ec2", "us-east-1")
            resp = ec2.describe_regions(AllRegions=True)
            return [(r["RegionName"], r.get("OptInStatus", "opt-in-not-required")) for r in resp["Regions"]]
        except Exception as e:
            logger.warning(f"list_all_regions failed: {e}")
            return [(r, "opt-in-not-required") for r in REGION_DISPLAY.keys()]

    def list_enabled_regions(self) -> list:
        """只列出已启用 (opted-in 或默认开启) 的区域 - 用于扫描实例时避免 opt-in 未开启的报错"""
        try:
            ec2 = self._get_client("ec2", "us-east-1")
            resp = ec2.describe_regions(AllRegions=True)
            enabled = []
            for r in resp["Regions"]:
                status = r.get("OptInStatus", "opt-in-not-required")
                if status in ("opt-in-not-required", "opted-in"):
                    enabled.append(r["RegionName"])
            return enabled
        except Exception as e:
            logger.warning(f"list_enabled_regions failed: {e}")
            return list(REGION_DISPLAY.keys())

    def enable_all_regions(self) -> dict:
        """通过 account API 启用所有 opt-in 区域。
        要求账号有 account:EnableRegion 权限 (root / AdminAccess 默认有)。

        返回:
        {
            "total": 18,            # 总 opt-in 区域数
            "already_enabled": 3,
            "newly_enabled": 5,
            "failed": [{region, error}],
            "regions": [{region, status, before, after}]
        }
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        result = {
            "total": 0,
            "already_enabled": 0,
            "newly_enabled": 0,
            "failed": [],
            "regions": [],
        }
        # 1) 列出全部区域 (含 opt-in 状态)
        try:
            ec2 = self._get_client("ec2", "us-east-1")
            resp = ec2.describe_regions(AllRegions=True)
            all_regions = resp["Regions"]
        except Exception as e:
            return {"error": f"无法列出区域: {str(e)[:200]}", **result}

        # 2) 取出所有 opt-in 区域 (status != opt-in-not-required 的都是要 opt-in 的)
        opt_in_regions = [
            (r["RegionName"], r.get("OptInStatus"))
            for r in all_regions
            if r.get("OptInStatus") in ("not-opted-in", "opted-in", "enabling", "disabling")
        ]
        result["total"] = len(opt_in_regions)

        # 3) 已启用的直接计数, 未启用的并行调 enable_region
        try:
            account_client = self._get_client("account", "us-east-1")
        except Exception as e:
            return {"error": f"无法初始化 account client: {str(e)[:200]}", **result}

        def _enable_one(region: str, current_status: str):
            entry = {"region": region, "before": current_status, "after": current_status, "status": "skipped"}
            if current_status == "opted-in":
                entry["status"] = "already_enabled"
                return entry
            if current_status == "enabling":
                entry["status"] = "enabling_in_progress"
                entry["after"] = "enabling"
                return entry
            try:
                account_client.enable_region(RegionName=region)
                entry["status"] = "enabled"
                entry["after"] = "enabling"
            except Exception as e:
                msg = str(e)
                if "already" in msg.lower() and "enabled" in msg.lower():
                    entry["status"] = "already_enabled"
                    entry["after"] = "opted-in"
                else:
                    entry["status"] = "failed"
                    entry["error"] = msg[:200]
                    result["failed"].append({"region": region, "error": msg[:200]})
            return entry

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(_enable_one, r, s): r for r, s in opt_in_regions}
            for fut in as_completed(futs):
                try:
                    e = fut.result()
                    result["regions"].append(e)
                    if e["status"] == "already_enabled":
                        result["already_enabled"] += 1
                    elif e["status"] in ("enabled", "enabling_in_progress"):
                        result["newly_enabled"] += 1
                except Exception as ex:
                    logger.warning(f"enable region future failed: {ex}")

        return result


    def _ensure_security_group(self, region: str) -> str:
        ec2 = self._get_client("ec2", region)
        try:
            resp = ec2.describe_security_groups(
                Filters=[{"Name": "group-name", "Values": [DEFAULT_SG_NAME]}]
            )
            if resp["SecurityGroups"]:
                return resp["SecurityGroups"][0]["GroupId"]
        except Exception:
            pass

        resp = ec2.create_security_group(
            GroupName=DEFAULT_SG_NAME, Description=DEFAULT_SG_DESC
        )
        sg_id = resp["GroupId"]
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
                 "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
                {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
                 "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
                {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
                 "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
                # 常见 DePIN 端口范围
                {"IpProtocol": "tcp", "FromPort": 1234, "ToPort": 1234,
                 "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
                {"IpProtocol": "tcp", "FromPort": 3000, "ToPort": 9999,
                 "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
            ],
        )
        return sg_id

    def _ensure_key_pair(self, region: str) -> tuple:
        """返回 (key_name, private_key_pem)，已存在的密钥从本地文件读取"""
        key_name = f"depin-key-{region}"
        ec2 = self._get_client("ec2", region)
        import os
        key_dir = "/app/data/keys"
        key_path = os.path.join(key_dir, f"{key_name}.pem")
        try:
            ec2.describe_key_pairs(KeyNames=[key_name])
            # 密钥已存在，尝试从本地读取私钥
            if os.path.exists(key_path):
                with open(key_path, "r") as f:
                    return key_name, f.read()
            return key_name, None
        except Exception:
            pass
        resp = ec2.create_key_pair(KeyName=key_name)
        private_key = resp["KeyMaterial"]
        # 保存私钥到本地
        os.makedirs(key_dir, exist_ok=True)
        with open(key_path, "w") as f:
            f.write(private_key)
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass
        return key_name, private_key

    # ==================== AMI 查询 (按区域动态拉取) ====================

    # AMI 名称模式 → owner / 描述
    AMI_TEMPLATES = {
        # Ubuntu (Canonical = 099720109477)
        "ubuntu-22.04":     {"owners": ["099720109477"], "name_pattern": "ubuntu/images/hvm-ssd*/ubuntu-jammy-22.04-amd64-server-*",  "arch": "x86_64", "label": "Ubuntu 22.04 LTS"},
        "ubuntu-24.04":     {"owners": ["099720109477"], "name_pattern": "ubuntu/images/hvm-ssd*/ubuntu-noble-24.04-amd64-server-*",  "arch": "x86_64", "label": "Ubuntu 24.04 LTS"},
        "ubuntu-20.04":     {"owners": ["099720109477"], "name_pattern": "ubuntu/images/hvm-ssd/ubuntu-focal-20.04-amd64-server-*",   "arch": "x86_64", "label": "Ubuntu 20.04 LTS"},
        "ubuntu-22.04-arm": {"owners": ["099720109477"], "name_pattern": "ubuntu/images/hvm-ssd*/ubuntu-jammy-22.04-arm64-server-*",  "arch": "arm64",  "label": "Ubuntu 22.04 LTS (ARM)"},
        # Debian (Debian = 136693071363)
        "debian-12":        {"owners": ["136693071363"], "name_pattern": "debian-12-amd64-*",                                          "arch": "x86_64", "label": "Debian 12 Bookworm"},
        "debian-11":        {"owners": ["136693071363"], "name_pattern": "debian-11-amd64-*",                                          "arch": "x86_64", "label": "Debian 11 Bullseye"},
        # Amazon Linux (amazon = 137112412989)
        "amazon-linux-2023": {"owners": ["137112412989"], "name_pattern": "al2023-ami-2023*-x86_64",                                   "arch": "x86_64", "label": "Amazon Linux 2023"},
        "amazon-linux-2":    {"owners": ["137112412989"], "name_pattern": "amzn2-ami-hvm-*-x86_64-gp2",                                "arch": "x86_64", "label": "Amazon Linux 2"},
        # CentOS (CentOS = 125523088429), Rocky (Rocky = 792107900819), AlmaLinux (Alma = 764336703387)
        "rocky-9":          {"owners": ["792107900819"], "name_pattern": "Rocky-9-EC2-Base-*.x86_64",                                  "arch": "x86_64", "label": "Rocky Linux 9"},
        "alma-9":           {"owners": ["764336703387"], "name_pattern": "AlmaLinux OS 9.*-*",                                         "arch": "x86_64", "label": "AlmaLinux 9"},
        # Windows (amazon = 801119661308 for Win Server)
        "windows-2022":     {"owners": ["801119661308"], "name_pattern": "Windows_Server-2022-English-Full-Base-*",                    "arch": "x86_64", "label": "Windows Server 2022", "platform": "windows"},
        "windows-2019":     {"owners": ["801119661308"], "name_pattern": "Windows_Server-2019-English-Full-Base-*",                    "arch": "x86_64", "label": "Windows Server 2019", "platform": "windows"},
    }

    def list_amis_for_region(self, region: str) -> list:
        """按区域查询常用 AMI - 返回每个模板对应的最新 AMI ID。
        前端创建实例时按区域拉一次, 用户选择想用哪个发行版 + 架构。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        ec2 = self._get_client("ec2", region)
        out = []

        def _query(key, tpl):
            try:
                resp = ec2.describe_images(
                    Owners=tpl["owners"],
                    Filters=[
                        {"Name": "name", "Values": [tpl["name_pattern"]]},
                        {"Name": "state", "Values": ["available"]},
                        {"Name": "architecture", "Values": [tpl["arch"]]},
                    ],
                    MaxResults=20,
                )
                images = sorted(resp.get("Images", []), key=lambda x: x.get("CreationDate", ""), reverse=True)
                if not images:
                    return None
                im = images[0]
                return {
                    "key": key,
                    "label": tpl["label"],
                    "ami_id": im["ImageId"],
                    "name": im.get("Name", ""),
                    "arch": tpl["arch"],
                    "platform": tpl.get("platform", "linux"),
                    "creation_date": im.get("CreationDate", ""),
                    "description": im.get("Description", "") or im.get("Name", ""),
                    "root_device_type": im.get("RootDeviceType", "ebs"),
                }
            except Exception as e:
                logger.warning(f"AMI query {key} in {region} failed: {e}")
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(_query, k, v): k for k, v in self.AMI_TEMPLATES.items()}
            for fut in as_completed(futs):
                r = fut.result()
                if r:
                    out.append(r)

        # 按 label 友好排序: Ubuntu → Debian → AmazonLinux → Rocky → Alma → Windows
        order = ["Ubuntu", "Debian", "Amazon", "Rocky", "Alma", "Windows"]
        def _rank(item):
            label = item["label"]
            for i, p in enumerate(order):
                if label.startswith(p):
                    return i
            return 99
        out.sort(key=lambda x: (_rank(x), x["label"]))
        return out

    def _ensure_security_group_with_cidrs(self, region: str, allow_cidrs: list = None, enable_ipv6: bool = False) -> str:
        """支持自定义 CIDR 段 + IPv6 的安全组。
        allow_cidrs=None / [] → 默认 0.0.0.0/0 全开
        否则只放行 CIDR 列表 + 22 端口。"""
        ec2 = self._get_client("ec2", region)
        sg_name = DEFAULT_SG_NAME
        if allow_cidrs:
            # 限定 CIDR 时用独立 SG (避免污染默认 SG)
            sg_name = f"{DEFAULT_SG_NAME}-restricted-{abs(hash(','.join(sorted(allow_cidrs)))) % 100000}"

        # 找现有 SG
        try:
            resp = ec2.describe_security_groups(
                Filters=[{"Name": "group-name", "Values": [sg_name]}]
            )
            if resp["SecurityGroups"]:
                return resp["SecurityGroups"][0]["GroupId"]
        except Exception:
            pass

        resp = ec2.create_security_group(
            GroupName=sg_name, Description=f"DePIN SG ({'restricted' if allow_cidrs else 'open'})"
        )
        sg_id = resp["GroupId"]

        ranges_v4 = [{"CidrIp": c} for c in allow_cidrs] if allow_cidrs else [{"CidrIp": "0.0.0.0/0"}]
        ranges_v6 = [{"CidrIpv6": "::/0"}] if (enable_ipv6 and not allow_cidrs) else []

        perms = []
        for fp, tp in [(22, 22), (80, 80), (443, 443), (3000, 9999)]:
            p = {"IpProtocol": "tcp", "FromPort": fp, "ToPort": tp, "IpRanges": ranges_v4}
            if ranges_v6:
                p["Ipv6Ranges"] = ranges_v6
            perms.append(p)
        # ICMP (允许 ping)
        perms.append({"IpProtocol": "icmp", "FromPort": -1, "ToPort": -1, "IpRanges": ranges_v4})
        try:
            ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=perms)
        except Exception as e:
            logger.warning(f"authorize SG ingress failed: {e}")
        return sg_id

    def launch_instance(
        self,
        region: str = None,
        instance_type: str = "t3.micro",
        user_data: str = None,
        volume_size: int = 20,
        volume_type: str = "gp3",
        ami_id: str = None,
        ami_key: str = None,
        password: str = None,
        spot: bool = False,
        enable_ipv6: bool = False,
        static_ip: bool = False,
        allow_cidrs: list = None,
        instance_name: str = None,
        count: int = 1,
        gfw_check: bool = False,
    ) -> dict:
        """启动 EC2 实例 (增强版, 支持 Lightsail-like 各种选项)

        参数:
            ami_id    用户直接指定的 AMI ID (优先级最高)
            ami_key   AMI_TEMPLATES 里的 key (如 "ubuntu-22.04" / "windows-2022")
            password  Linux 设置 ubuntu/admin 密码; Windows 设置 Administrator 密码
            spot      使用 Spot 实例 (省钱但可能被中断)
            enable_ipv6 关联 IPv6 地址
            static_ip   分配 EIP (按用量计费, 实例停机时收费)
            allow_cidrs 入站白名单 IP (列表), 留空 = 0.0.0.0/0 全开
            count       同区域同时开 N 台
            gfw_check   开机后探测中国大陆是否封锁该 IP, 写入 tag

        返回:
            {
                "instances": [{...}],   # 与原 list_instances_detailed 同结构
                "errors": [...]
            }
        """
        region = region or self.account.default_region
        count = max(1, min(int(count or 1), 50))   # 限 50 台

        # 0) 磁盘大小按类型校验 (>40000 GiB 等大盘需要 gp3 / io2)
        validate_volume_size(int(volume_size), volume_type)

        # 1) AMI: 优先 ami_id, 然后 ami_key, 最后默认 Ubuntu 22.04
        if not ami_id:
            if ami_key and ami_key in self.AMI_TEMPLATES:
                # 按区域查这个 key 的最新 AMI
                tpl = self.AMI_TEMPLATES[ami_key]
                ec2 = self._get_client("ec2", region)
                resp = ec2.describe_images(
                    Owners=tpl["owners"],
                    Filters=[
                        {"Name": "name", "Values": [tpl["name_pattern"]]},
                        {"Name": "state", "Values": ["available"]},
                        {"Name": "architecture", "Values": [tpl["arch"]]},
                    ],
                    MaxResults=20,
                )
                images = sorted(resp.get("Images", []), key=lambda x: x.get("CreationDate", ""), reverse=True)
                if not images:
                    raise ValueError(f"区域 {region} 没找到 {ami_key} 对应的 AMI")
                ami_id = images[0]["ImageId"]
            else:
                # 默认 Ubuntu 22.04
                ami_id = UBUNTU_AMIS.get(region)
                if not ami_id:
                    ec2 = self._get_client("ec2", region)
                    resp = ec2.describe_images(
                        Filters=[
                            {"Name": "name", "Values": ["ubuntu/images/hvm-ssd*/ubuntu-jammy-22.04-amd64-server-*"]},
                            {"Name": "state", "Values": ["available"]},
                        ],
                        Owners=["099720109477"],
                    )
                    images = sorted(resp["Images"], key=lambda x: x["CreationDate"], reverse=True)
                    ami_id = images[0]["ImageId"] if images else None
                    if not ami_id:
                        raise ValueError(f"No Ubuntu AMI found in region {region}")

        # 2) 推断平台 (linux / windows) - 用于密码设置和 user_data
        ec2 = self._get_client("ec2", region)
        platform = "linux"
        try:
            img_resp = ec2.describe_images(ImageIds=[ami_id])
            if img_resp.get("Images"):
                pf = (img_resp["Images"][0].get("PlatformDetails", "") or "").lower()
                if "windows" in pf:
                    platform = "windows"
        except Exception:
            pass

        # 3) 安全组 (支持 CIDR 白名单)
        sg_id = self._ensure_security_group_with_cidrs(region, allow_cidrs=allow_cidrs, enable_ipv6=enable_ipv6)

        # 4) 密钥对
        key_name, private_key = self._ensure_key_pair(region)

        # 5) UserData: Linux 设密码 + 装 docker; Windows 用 PowerShell 设密码
        ud = user_data
        if platform == "linux":
            ud_parts = [user_data] if user_data else [DEFAULT_USER_DATA]
            if password:
                # 给 ubuntu 用户设密码并允许 SSH 密码登录
                ud_parts.append(f"""
echo 'ubuntu:{password}' | chpasswd
echo 'root:{password}' | chpasswd
sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
systemctl restart ssh || systemctl restart sshd
""".strip())
            ud = "\n".join(p for p in ud_parts if p)
        else:
            # Windows: 用 <powershell> 设密码
            if password:
                ud = f"""<powershell>
$Password = ConvertTo-SecureString '{password}' -AsPlainText -Force
Get-LocalUser -Name 'Administrator' | Set-LocalUser -Password $Password
</powershell>"""

        # 6) 网络接口 (IPv6 / 公网 IP)
        network_interfaces = [{
            "DeviceIndex": 0,
            "AssociatePublicIpAddress": True,
            "Groups": [sg_id],
            "DeleteOnTermination": True,
        }]
        if enable_ipv6:
            network_interfaces[0]["Ipv6AddressCount"] = 1

        # 7) Spot 实例
        market_options = {}
        if spot:
            market_options = {
                "MarketType": "spot",
                "SpotOptions": {"SpotInstanceType": "one-time", "InstanceInterruptionBehavior": "terminate"},
            }

        # 8) 名称 (按数量 -1 / -2 后缀)
        base_name = instance_name or f"depin-{self.account.name or self.account.id}"

        # 9) 启动
        run_kwargs = {
            "ImageId": ami_id,
            "InstanceType": instance_type,
            "MinCount": count,
            "MaxCount": count,
            "KeyName": key_name,
            "UserData": ud or "",
            "NetworkInterfaces": network_interfaces,
            "BlockDeviceMappings": [{
                "DeviceName": "/dev/sda1" if platform == "linux" else "/dev/sda1",
                "Ebs": {
                    "VolumeSize": volume_size,
                    "VolumeType": volume_type,
                    "DeleteOnTermination": True,
                },
            }],
            "TagSpecifications": [{
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": base_name},
                    {"Key": "ManagedBy", "Value": "aws-depin-manager"},
                ],
            }],
        }
        if market_options:
            run_kwargs["InstanceMarketOptions"] = market_options

        resp = ec2.run_instances(**run_kwargs)

        # 10) 多台时给每台改名 (-1, -2, -3...)
        instances_data = resp.get("Instances", [])
        instance_ids = [i["InstanceId"] for i in instances_data]
        if count > 1:
            try:
                for idx, iid in enumerate(instance_ids, 1):
                    ec2.create_tags(Resources=[iid], Tags=[{"Key": "Name", "Value": f"{base_name}-{idx}"}])
            except Exception as e:
                logger.warning(f"rename instances failed: {e}")

        # 11) 静态 IP (EIP) - 可选, 仅第 1 台
        if static_ip and instance_ids:
            try:
                # 等实例进入 running 才能 associate
                allocate_resp = ec2.allocate_address(Domain="vpc")
                eip_alloc_id = allocate_resp["AllocationId"]
                # 异步关联, 不阻塞主流程
                import threading
                def _assoc():
                    import time as _t
                    for _ in range(30):
                        try:
                            ec2.associate_address(InstanceId=instance_ids[0], AllocationId=eip_alloc_id)
                            return
                        except Exception:
                            _t.sleep(3)
                threading.Thread(target=_assoc, daemon=True).start()
            except Exception as e:
                logger.warning(f"allocate EIP failed: {e}")

        # 12) 写入数据库 (每台一条)
        out_instances = []
        for idx, inst_data in enumerate(instances_data):
            try:
                instance = Instance(
                    account_id=self.account.id,
                    instance_id=inst_data["InstanceId"],
                    region=region,
                    instance_type=instance_type,
                    state=inst_data.get("State", {}).get("Name", "pending"),
                    key_name=key_name,
                    private_key=private_key,
                )
                self.db.add(instance)
                self.db.commit()
                self.db.refresh(instance)
                out_instances.append({
                    "id": instance.id,
                    "instance_id": instance.instance_id,
                    "region": region,
                    "state": instance.state,
                    "instance_type": instance_type,
                    "name": f"{base_name}-{idx+1}" if count > 1 else base_name,
                    "ami_id": ami_id,
                    "platform": platform,
                    "spot": bool(spot),
                    "ipv6": bool(enable_ipv6),
                    "static_ip": bool(static_ip),
                })
            except Exception as e:
                logger.warning(f"save instance {inst_data.get('InstanceId')} failed: {e}")
                self.db.rollback()

        return {
            "ok": True,
            "count": len(out_instances),
            "instances": out_instances,
            "platform": platform,
            "ami_id": ami_id,
            "key_name": key_name,
            "region": region,
        }

    # 兼容旧调用 (只返回单个 Instance) - 用于 batch-launch / 单台 launch
    def launch_instance_legacy(
        self,
        region: str = None,
        instance_type: str = "t3.micro",
        user_data: str = None,
        volume_size: int = 20,
        volume_type: str = "gp3",
    ) -> Instance:
        result = self.launch_instance(
            region=region, instance_type=instance_type, user_data=user_data,
            volume_size=volume_size, volume_type=volume_type, count=1,
        )
        if not result.get("instances"):
            raise RuntimeError("启动失败, 没有返回实例")
        first = result["instances"][0]
        return self.db.query(Instance).get(first["id"])


    def get_instance_status(self, instance_id: str, region: str) -> dict:
        ec2 = self._get_client("ec2", region)
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        inst = resp["Reservations"][0]["Instances"][0]
        return {
            "instance_id": instance_id,
            "state": inst["State"]["Name"],
            "public_ip": inst.get("PublicIpAddress"),
            "private_ip": inst.get("PrivateIpAddress"),
            "instance_type": inst.get("InstanceType"),
            "launch_time": str(inst.get("LaunchTime")),
        }

    def sync_instance(self, instance: Instance):
        """同步实例状态到数据库"""
        try:
            status = self.get_instance_status(instance.instance_id, instance.region)
            instance.state = status["state"]
            instance.public_ip = status.get("public_ip")
            instance.private_ip = status.get("private_ip")
            self.db.commit()
        except Exception as e:
            logger.error(f"Sync instance {instance.instance_id} failed: {e}")

    def start_instance(self, instance_id: str, region: str):
        ec2 = self._get_client("ec2", region)
        ec2.start_instances(InstanceIds=[instance_id])

    def stop_instance(self, instance_id: str, region: str):
        ec2 = self._get_client("ec2", region)
        ec2.stop_instances(InstanceIds=[instance_id])

    def terminate_instance(self, instance_id: str, region: str):
        ec2 = self._get_client("ec2", region)
        ec2.terminate_instances(InstanceIds=[instance_id])

    def reboot_instance(self, instance_id: str, region: str):
        ec2 = self._get_client("ec2", region)
        ec2.reboot_instances(InstanceIds=[instance_id])

    def run_command_ssm(self, instance_id: str, region: str, commands: list[str]) -> str:
        """通过 SSM 在实例上执行命令"""
        ssm = self._get_client("ssm", region)
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands},
        )
        return resp["Command"]["CommandId"]

    def list_instances_aws(self, region: str = None) -> list:
        region = region or self.account.default_region
        ec2 = self._get_client("ec2", region)
        resp = ec2.describe_instances(
            Filters=[{"Name": "tag:ManagedBy", "Values": ["aws-depin-manager"]}]
        )
        instances = []
        for res in resp["Reservations"]:
            for inst in res["Instances"]:
                instances.append({
                    "instance_id": inst["InstanceId"],
                    "state": inst["State"]["Name"],
                    "public_ip": inst.get("PublicIpAddress"),
                    "instance_type": inst.get("InstanceType"),
                    "launch_time": str(inst.get("LaunchTime")),
                })
        return instances

    def list_instances_detailed(self, region: str, all_managed: bool = False) -> list:
        """从 AWS API 拉取该区域所有 EC2 实例的详细信息（含 DNS / AZ / 架构 / launch_time）。

        all_managed=False: 只列出本平台 ManagedBy 标签创建的实例
        all_managed=True: 列出该账号该区域所有实例
        """
        ec2 = self._get_client("ec2", region)
        kwargs = {}
        if not all_managed:
            kwargs["Filters"] = [{"Name": "tag:ManagedBy", "Values": ["aws-depin-manager"]}]

        # 同时拉取 EIP 信息（关联到实例 ID）
        eip_map = {}
        try:
            eips = ec2.describe_addresses().get("Addresses", [])
            for a in eips:
                iid = a.get("InstanceId")
                if iid:
                    eip_map[iid] = a.get("PublicIp")
        except Exception:
            pass

        items = []
        try:
            paginator = ec2.get_paginator("describe_instances")
            for page in paginator.paginate(**kwargs):
                for res in page.get("Reservations", []):
                    for inst in res.get("Instances", []):
                        iid = inst["InstanceId"]
                        public_ip = inst.get("PublicIpAddress")
                        is_static = bool(eip_map.get(iid)) and (eip_map.get(iid) == public_ip)
                        # 标签
                        tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                        items.append({
                            "instance_id": iid,
                            "name": tags.get("Name", ""),
                            "state": inst.get("State", {}).get("Name", "unknown"),
                            "instance_type": inst.get("InstanceType"),
                            "region": region,
                            "availability_zone": inst.get("Placement", {}).get("AvailabilityZone"),
                            "public_ip": public_ip,
                            "private_ip": inst.get("PrivateIpAddress"),
                            "public_dns": inst.get("PublicDnsName") or "",
                            "private_dns": inst.get("PrivateDnsName") or "",
                            "image_id": inst.get("ImageId"),
                            "platform": inst.get("Platform") or inst.get("PlatformDetails") or "Linux/UNIX",
                            "architecture": inst.get("Architecture", ""),
                            "vpc_id": inst.get("VpcId"),
                            "subnet_id": inst.get("SubnetId"),
                            "key_name": inst.get("KeyName"),
                            "is_static_ip": is_static,
                            "elastic_ip": eip_map.get(iid),
                            "launch_time": inst.get("LaunchTime").isoformat() if inst.get("LaunchTime") else None,
                            "tags": tags,
                            "managed": tags.get("ManagedBy") == "aws-depin-manager",
                            "cpu_options": inst.get("CpuOptions", {}),
                            "monitoring": inst.get("Monitoring", {}).get("State", "disabled"),
                        })
        except Exception as e:
            logger.warning(f"list_instances_detailed {region} failed: {e}")
            raise
        return items

    def list_instances_detailed_all_regions(self, regions: list = None, all_managed: bool = False) -> dict:
        """并发扫描所有已启用区域的实例详情；返回 {region: [items]}

        默认行为变更: 不再只扫 REGION_DISPLAY 里的 17 个常用区域,
        而是先调 describe_regions(AllRegions=True) 拿到所有 opted-in 区域 (含 hk/me/af/eu-south/jakarta/melbourne 等 opt-in),
        覆盖账号实际能跑实例的所有地方。这样就不会"实例没全部检测出来"。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        if not regions:
            try:
                regions = self.list_enabled_regions()
                if not regions:
                    regions = list(REGION_DISPLAY.keys())
            except Exception as e:
                logger.warning(f"list_enabled_regions failed, fallback to REGION_DISPLAY: {e}")
                regions = list(REGION_DISPLAY.keys())
        out = {}
        with ThreadPoolExecutor(max_workers=min(len(regions) or 1, 32)) as pool:
            futs = {pool.submit(self.list_instances_detailed, r, all_managed): r for r in regions}
            for fut in as_completed(futs):
                r = futs[fut]
                try:
                    out[r] = fut.result()
                except Exception as e:
                    logger.warning(f"detailed scan {r} failed: {e}")
                    out[r] = []
        return out


    # ==================== 账号信息检测 ====================
    # 注意: 旧版的 _detect_email_from_* / _detect_country / _detect_creation_time /
    # _get_credential_report / _detect_primary_email / _detect_account_information /
    # _detect_default_region_vcpu 已全部清理. 现在所有探测逻辑都内联在
    # detect_account_info() 内部, 用线程池并行 + 独立 client 跑, 见下方.

    def _get_region_vcpu(self, region: str) -> dict:

        """获取单个区域的 vCPU 配额"""
        on_demand_limit, on_demand_usage, spot_limit, spot_usage = 5, 0, 5, 0
        try:
            sq = self._get_client("service-quotas", region)
            try:
                resp = sq.get_service_quota(ServiceCode="ec2", QuotaCode="L-1216C47A")
                on_demand_limit = int(resp["Quota"]["Value"])
            except Exception:
                try:
                    resp = sq.get_aws_default_service_quota(ServiceCode="ec2", QuotaCode="L-1216C47A")
                    on_demand_limit = int(resp["Quota"]["Value"])
                except Exception:
                    pass
            try:
                resp = sq.get_service_quota(ServiceCode="ec2", QuotaCode="L-34B43A08")
                spot_limit = int(resp["Quota"]["Value"])
            except Exception:
                try:
                    resp = sq.get_aws_default_service_quota(ServiceCode="ec2", QuotaCode="L-34B43A08")
                    spot_limit = int(resp["Quota"]["Value"])
                except Exception:
                    pass
            try:
                ec2 = self._get_client("ec2", region)
                resp = ec2.describe_instances(Filters=[{"Name": "instance-state-name", "Values": ["running", "pending"]}])
                for res in resp.get("Reservations", []):
                    for inst in res.get("Instances", []):
                        vc = inst.get("CpuOptions", {}).get("CoreCount", 1) * inst.get("CpuOptions", {}).get("ThreadsPerCore", 1)
                        if inst.get("InstanceLifecycle") == "spot":
                            spot_usage += vc
                        else:
                            on_demand_usage += vc
            except Exception:
                pass
        except Exception:
            pass
        return {
            "display": REGION_DISPLAY.get(region, region),
            "on_demand_limit": on_demand_limit, "on_demand_usage": on_demand_usage,
            "spot_limit": spot_limit, "spot_usage": spot_usage,
        }

    def get_vcpu_quotas_all_regions(self) -> dict:
        """并发获取所有区域的 vCPU 配额 - 无超时，等每个区域返回真实数据"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        regions = list(REGION_DISPLAY.keys())
        regions_data = {}
        total_vcpus = 0

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(self._get_region_vcpu, r): r for r in regions}
            for future in as_completed(futures):
                region = futures[future]
                try:
                    data = future.result()
                    regions_data[region] = data
                    total_vcpus += data["on_demand_limit"]
                except Exception as e:
                    logger.warning(f"vCPU {region} failed: {e}")
                    regions_data[region] = {
                        "display": REGION_DISPLAY.get(region, region),
                        "on_demand_limit": 5, "on_demand_usage": 0,
                        "spot_limit": 5, "spot_usage": 0,
                    }
                    total_vcpus += 5

        max_on_demand = max((d["on_demand_limit"] for d in regions_data.values()), default=5)
        total_usage = sum(d["on_demand_usage"] for d in regions_data.values())
        return {"regions": regions_data, "total_vcpus": total_vcpus, "max_on_demand": max_on_demand, "total_usage": total_usage}

    def _make_detect_client(self, service: str, region: str = None):
        """为并行检测创建独立的 boto3 client（不使用缓存，线程安全）.

        严格代理: 没 proxy_config 直接抛错. 历史 bug 是这里只用 `if self.proxy_config`,
        proxy_config 为空时静默走服务器真实 IP — 直接堵死.
        """
        self._assert_proxy()
        region = region or self.account.default_region
        slow_services = {"service-quotas", "iam", "account", "organizations", "support", "budgets", "bedrock", "sso-admin", "license-manager"}
        if service in slow_services:
            config_kwargs = {"connect_timeout": 10, "read_timeout": 30, "retries": {"max_attempts": 2}}
        else:
            config_kwargs = {"connect_timeout": 5, "read_timeout": 10, "retries": {"max_attempts": 1}}
        config_kwargs["proxies"] = self.proxy_config   # 强制
        return boto3.client(
            service,
            aws_access_key_id=self.account.access_key_id,
            aws_secret_access_key=self.account.secret_access_key,
            region_name=region,
            config=Config(**config_kwargs),
        )

    def detect_account_info(self) -> dict:
        """一次性检测账号所有信息并更新数据库 - 并行执行，每个任务独立 client"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        info = {"email": None, "arn": None, "account_id": None, "_errors": [], "_proxy_error": False}

        # 先获取 STS 身份（必须先拿到 account_id）
        try:
            sts = self._get_client("sts")
            identity = sts.get_caller_identity()
            info["arn"] = identity.get("Arn", "")
            info["account_id"] = identity.get("Account", "")
        except Exception as e:
            err_str = str(e)
            logger.error(f"STS failed: {e}")
            status = self._classify_credential_error(err_str)
            # 1) 代理或网络错误 → 不修改账号状态, 仅记录到 _errors / _proxy_error
            if status == "network_error" or "proxy" in err_str.lower() or "407" in err_str or "ProxyConnectionError" in err_str:
                info["_proxy_error"] = "proxy" in err_str.lower() or "407" in err_str
                if info["_proxy_error"]:
                    info["_errors"].append(f"代理连接失败: {err_str[:150]}")
                else:
                    info["_errors"].append(f"网络连接失败 (与 AK/SK 是否有效无关): {err_str[:200]}")
                # ⚠️ 重要: 网络层错误绝不能据此把账号判定为 invalid_credentials
                info["_network_error"] = True
                # 修复历史误判: 如果之前因为网络错误被错误标成了 invalid_credentials, 重置为 unknown
                # (status_reason 含网络错误关键字 → 之前的 invalid_credentials 是误判)
                if self.account.account_status == "invalid_credentials":
                    old_reason = (self.account.status_reason or "").lower()
                    if self._is_network_error(old_reason) or not old_reason:
                        self.account.account_status = "unknown"
                        self.account.status_reason = "上次因网络错误被误判为失效, 已重置. 待网络恢复后重新检测"
                        self.account.status_checked_at = datetime.utcnow()
                        try:
                            self.db.commit()
                        except Exception:
                            self.db.rollback()
            elif status in ("invalid_credentials", "disabled"):
                # 2) 真的是 AK/SK 失效 / 账号被禁用 → 写库
                info["_errors"].append(f"凭证失效或账号被禁用: {err_str[:200]}")
                self.account.account_status = status
                self.account.status_reason = err_str[:300]
                self.account.status_checked_at = datetime.utcnow()
                try:
                    self.db.commit()
                except Exception:
                    self.db.rollback()
            else:
                # 3) 未知错误 → 不写死成 invalid_credentials, 保持原状态, 让用户重试
                info["_errors"].append(f"AWS 调用失败 (原因不明, 未修改账号状态): {err_str[:200]}")
            info["account_status"] = self.account.account_status
            return info

        account_id = info["account_id"]

        # ============ 邮箱探测策略 ============
        # ⚠️  实测结论 (2026-05): AWS **没有任何公开 SDK API** 能从凭证拿到 root 邮箱.
        #     - account:GetPrimaryEmail        → root 凭证强制 AccessDenied (即便有 policy)
        #     - account:GetContactInformation  → 返回 FullName/Country/Phone, EmailAddress 字段固定为空
        #     - account:GetAccountInformation  → 仅返回 metadata, 无邮箱字段
        #     - account:GetAlternateContact    → 仅当用户在控制台手动设了备用联系人才有
        #     - organizations:DescribeAccount  → 仅当账号在组织里, 且能拿到的是 master email
        #     - invoicing:* / taxsettings:*    → 当前 SDK 无邮箱字段, 部分 API 在 boto3 里压根不存在
        #     - S3 / IAM 标签 / SES / SNS      → 全部不靠谱, 99% 拿不到
        # 因此现在只保留两个最有价值的探测:
        #   1) AlternateContact[BILLING]  - 唯一能 API 拿到邮箱的字段, 但前提账号设过
        #   2) Organizations              - 极少数情况能拿到 master email
        # 其它一律不再尝试. contact_info 只用来拿 country.
        REGION_TO_COUNTRY = {
            "us-east-1": "US", "us-east-2": "US", "us-west-1": "US", "us-west-2": "US",
            "eu-west-1": "IE", "eu-west-2": "GB", "eu-west-3": "FR", "eu-central-1": "DE",
            "eu-north-1": "SE", "ap-northeast-1": "JP", "ap-northeast-2": "KR",
            "ap-northeast-3": "JP", "ap-southeast-1": "SG", "ap-southeast-2": "AU",
            "ap-south-1": "IN", "sa-east-1": "BR", "ca-central-1": "CA",
        }

        def _task_alt_billing():
            """AlternateContact[BILLING] - 唯一能 API 拿到邮箱的官方字段
            (前提: 用户在控制台 My Account → Alternate Contacts 手动设过)"""
            acct = self._make_detect_client("account", "us-east-1")
            try:
                resp = acct.get_alternate_contact(AlternateContactType="BILLING")
                ac = resp.get("AlternateContact", {}) if isinstance(resp, dict) else {}
                email = ac.get("EmailAddress", "")
                if email and "@" in email:
                    return email
            except Exception:
                return None
            return None

        def _task_org_email():
            """组织 (Organizations) 主账号邮箱 - 仅当账号在组织里"""
            org = self._make_detect_client("organizations")
            try:
                resp = org.describe_organization()
                email = resp.get("Organization", {}).get("MasterAccountEmail", "")
                if email and "@" in email:
                    return email
            except Exception:
                pass
            try:
                resp = org.describe_account(AccountId=account_id)
                email = resp.get("Account", {}).get("Email", "")
                if email and "@" in email:
                    return email
            except Exception:
                pass
            return None

        def _task_contact_info():
            """只用来拿 country / FullName 显示 (邮箱字段实测固定为空)"""
            try:
                acct = self._make_detect_client("account", "us-east-1")
                contact = acct.get_contact_information()
                ci = contact.get("ContactInformation", {}) or {}
                name = ci.get("FullName", "")
                country = ci.get("CountryCode", "") or REGION_TO_COUNTRY.get(self.account.default_region, "US")
                return {"name": name or None, "country": country}
            except Exception:
                return {"name": None, "country": REGION_TO_COUNTRY.get(self.account.default_region, "US")}

        def _task_creation_time():
            """IAM credential report - 拿 root 创建日期 (= 账号注册时间)"""
            import time as _time, csv, io
            iam = self._make_detect_client("iam")
            for _ in range(3):
                try:
                    resp = iam.generate_credential_report()
                    if resp.get("State") == "COMPLETE":
                        break
                except Exception:
                    pass
                _time.sleep(1)
            try:
                resp = iam.get_credential_report()
                rows = list(csv.DictReader(io.StringIO(resp["Content"].decode("utf-8"))))
                for row in rows:
                    if ":root" in row.get("arn", ""):
                        creation_str = row.get("user_creation_time", "")
                        if creation_str and creation_str != "N/A":
                            from dateutil import parser as dateparser
                            return dateparser.parse(creation_str)
                        break
            except Exception:
                pass
            # fallback: 当前 IAM 用户创建时间
            try:
                user = iam.get_user()
                return user["User"].get("CreateDate")
            except Exception:
                return None

        # 并行执行检测任务 - 邮箱只剩 2 个最有价值的来源
        task_map = {
            "email_alt_billing": _task_alt_billing,
            "email_org":         _task_org_email,
            "contact_info":      _task_contact_info,
            "creation_time":     _task_creation_time,
        }
        results = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(fn): key for key, fn in task_map.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                    logger.info(f"Detection {key}: {results[key]}")
                except Exception as e:
                    err_msg = str(e)[:100]
                    logger.warning(f"Detection {key} failed: {err_msg}")
                    info["_errors"].append(f"{key}: {err_msg}")
                    results[key] = None


        # vCPU 单独调用（内部有自己的线程池）
        try:
            results["vcpus"] = self.get_vcpu_quotas_all_regions()
            logger.info(f"Detection vcpus: total={results['vcpus'].get('total_vcpus')}")
        except Exception as e:
            logger.warning(f"Detection vcpus failed: {e}")
            info["_errors"].append(f"vcpus: {str(e)[:100]}")
            results["vcpus"] = None

        # === 邮箱处理 (优先级: primary > alt_billing > org > contact_info.email) ===
        # contact_info 任务返回 dict, 单独取它的 email/name/country
        ci = results.get("contact_info") or {}
        ci_email = ci.get("email")  if isinstance(ci, dict) else None
        ci_name  = ci.get("name")   if isinstance(ci, dict) else None
        ci_country = ci.get("country") if isinstance(ci, dict) else None

        # 优先级 (从高到低):
        #   1) email_primary    = account:GetPrimaryEmail (root 真实主邮箱, 最权威)
        #   2) email_alt_billing = AlternateContact[BILLING] (账号买卖常驻邮箱)
        #   3) email_invoice    = invoicing:BatchGetInvoiceProfile.ReceiverEmail (发票收件人 = root 邮箱)
        #   4) email_tax        = taxsettings:GetTaxRegistration PointOfContact 邮箱
        #   5) email_org        = Organizations 主账号邮箱
        ordered_email_keys = (
            "email_primary",
            "email_alt_billing",
            "email_invoice",
            "email_tax",
            "email_org",
        )
        diagnosis = {}
        for k in ordered_email_keys:
            v = results.get(k)
            if v is None:
                err_match = next((e for e in info.get("_errors", []) if e.startswith(f"{k}:")), None)
                if err_match:
                    err_text = err_match[len(k) + 2:]
                    low = err_text.lower()
                    if "accessdenied" in low or "not authorized" in low:
                        diagnosis[k] = "denied"
                    elif "awsorganizationsnotinuse" in low:
                        diagnosis[k] = "not_in_org"
                    elif "resourcenotfound" in low:
                        diagnosis[k] = "not_found"
                    else:
                        diagnosis[k] = "error"
                else:
                    diagnosis[k] = "empty"
            elif isinstance(v, str) and "@" in v:
                diagnosis[k] = "hit"
            else:
                diagnosis[k] = "no_email"   # alt_billing 可能返回 FullName

        # contact_info 单独标
        if ci_email:
            diagnosis["contact_info"] = "hit"
        elif ci_name:
            diagnosis["contact_info"] = "no_email"
        else:
            diagnosis["contact_info"] = "empty"

        # 先找一个真正包含 @ 的邮箱
        email = None
        all_emails = []
        for k in ordered_email_keys:
            v = results.get(k)
            if v and isinstance(v, str) and "@" in v and "amazonaws" not in v.lower():
                all_emails.append((k, v))
                if not email:
                    email = v
        # contact_info.email 作为最后一个邮箱来源
        if ci_email and "amazonaws" not in ci_email.lower():
            all_emails.append(("contact_info", ci_email))
            if not email:
                email = ci_email
        # 没有邮箱 → 用 alt_billing / contact 里的名字, 再兜底 "root (id)"
        if not email:
            alt_val = results.get("email_alt_billing")
            if alt_val and isinstance(alt_val, str):
                email = alt_val
        if not email and ci_name:
            email = ci_name
        if not email:
            email = self.account.email if (self.account.email and "@" in (self.account.email or "")) else f"root ({info['account_id']})"

        info["email"] = email
        info["email_diagnosis"] = diagnosis
        if all_emails:
            info["email_sources"] = [k for k, _ in all_emails]
            info["all_emails"] = list({v for _, v in all_emails})
        info["register_time"] = results.get("creation_time")
        info["country"] = ci_country or "US"


        # vCPU
        vcpu_result = results.get("vcpus")
        if isinstance(vcpu_result, dict) and vcpu_result.get("regions"):
            info["total_vcpus"] = vcpu_result.get("total_vcpus", 0)
            info["max_on_demand"] = vcpu_result.get("max_on_demand", 5)
            info["total_usage"] = vcpu_result.get("total_usage", 0)
            info["vcpu_data"] = vcpu_result.get("regions")
        else:
            info["total_vcpus"] = self.account.total_vcpus or 0
            info["max_on_demand"] = self.account.max_on_demand or 0
            info["total_usage"] = self.account.total_usage or 0
            info["vcpu_data"] = self.account.vcpu_data

        # 更新数据库
        self.account.email = info["email"]
        self.account.name = info["email"]
        self.account.aws_account_id = info["account_id"]
        self.account.arn = info["arn"]
        if info["register_time"]:
            self.account.register_time = info["register_time"]
        self.account.register_country = info["country"]
        self.account.total_vcpus = info["total_vcpus"]
        self.account.max_on_demand = info["max_on_demand"]
        self.account.total_usage = info["total_usage"]
        if info["vcpu_data"]:
            self.account.vcpu_data = info["vcpu_data"]

        # 判断账号状态: STS 通过 && 至少一个区域 vCPU > 0 → active；
        # 否则 (所有区域 limit=0 通常意味着账号被禁/限制) → disabled
        if isinstance(vcpu_result, dict) and vcpu_result.get("regions"):
            max_limit = max((d.get("on_demand_limit", 0) for d in vcpu_result["regions"].values()), default=0)
            if max_limit <= 0:
                self.account.account_status = "disabled"
                self.account.status_reason = "所有区域 vCPU 配额为 0，账号可能被 AWS 限制或禁用"
            else:
                self.account.account_status = "active"
                self.account.status_reason = ""
        else:
            # vCPU 检测失败但 STS 成功 → 暂定为 active
            if self.account.account_status not in ("invalid_credentials", "disabled"):
                self.account.account_status = "active"
                self.account.status_reason = ""
        self.account.status_checked_at = datetime.utcnow()
        info["account_status"] = self.account.account_status

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()

        return info

    # ==================== AI 配额检测 ====================

    # 只关注的 3 个 Claude 模型 (id 关键词 -> 显示名)
    AI_TARGET_MODELS = [
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-opus-4-6",   "Claude Opus 4.6"),
        ("claude-opus-4-7",   "Claude Opus 4.7"),
    ]

    @classmethod
    def _is_target_quota(cls, quota_name: str) -> tuple[bool, str | None]:
        """配额名是否属于 3 个目标模型之一. 返回 (匹配?, 模型显示名)"""
        n = (quota_name or "").lower().replace(" ", "-").replace(".", "-")
        for kw, display in cls.AI_TARGET_MODELS:
            # 匹配多种写法: claude-sonnet-4-6 / claude sonnet 4.6 / claude_sonnet_4_6
            kw_norm = kw.lower().replace("-", "")
            n_norm = n.replace("-", "").replace("_", "")
            if kw_norm in n_norm:
                return True, display
        return False, None

    def detect_ai_info(self, region: str = "us-east-1") -> dict:
        """AI 检测: 只查 Claude Sonnet 4.6 / Opus 4.6 / Opus 4.7 三个模型的可用性 + 配额。

        优化:
        - 模型查询并行 + 只过滤目标模型 ID
        - 配额查询用 paginator.PaginationConfig(MaxItems=200) 限制翻页
        - 配额提前用关键字剪枝, 避免返回上百条无关配额
        - 模型 + 配额并行执行

        返回:
        {
            "region": "us-east-1",
            "bedrock_models": [{"id":..., "name":..., "target_name":...}],   # 仅 3 个目标
            "bedrock_quotas": [{"name":..., "value":..., "model":...}],     # 仅 3 个模型相关
            "bedrock_enabled": bool,
            "error": Optional[str],
        }
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        result = {
            "region": region,
            "bedrock_models": [],
            "bedrock_quotas": [],
            "bedrock_enabled": False,
            "error": None,
        }

        def _detect_models():
            models = []
            try:
                bedrock = self._get_client("bedrock", region)
                resp = bedrock.list_foundation_models()
                for m in resp.get("modelSummaries", []):
                    mid = m.get("modelId", "")
                    mid_low = mid.lower()
                    provider = m.get("providerName", "").lower()
                    if "anthropic" not in provider:
                        continue
                    # 只保留 3 个目标模型
                    matched = None
                    for kw, display in self.AI_TARGET_MODELS:
                        if kw in mid_low:
                            matched = display
                            break
                    if not matched:
                        continue
                    models.append({
                        "id": mid,
                        "name": m.get("modelName", ""),
                        "target_name": matched,
                        "provider": m.get("providerName", ""),
                        "input": m.get("inputModalities", []),
                        "output": m.get("outputModalities", []),
                        "status": m.get("modelLifecycle", {}).get("status", ""),
                    })
                result["bedrock_enabled"] = True
            except Exception as e:
                msg = str(e)
                if "AccessDenied" in msg or "not authorized" in msg or "UnrecognizedClient" in msg:
                    result["error"] = f"无 Bedrock 访问权限或区域 {region} 未开通"
                else:
                    result["error"] = f"Bedrock 查询失败: {msg[:120]}"
                logger.warning(f"Bedrock models error: {e}")
            return models

        def _detect_quotas():
            """只拉 3 个目标模型的 token / request 配额, 限制最多翻 2 页"""
            quotas = []

            def _scan(api_method: str):
                try:
                    sq = self._get_client("service-quotas", region)
                    paginator = sq.get_paginator(api_method)
                    # 限制最多 200 条 (一页 100, 顶多翻 2 页), 避免拖慢
                    page_iter = paginator.paginate(
                        ServiceCode="bedrock",
                        PaginationConfig={"MaxItems": 200, "PageSize": 100},
                    )
                    for page in page_iter:
                        for q in page.get("Quotas", []):
                            name = q.get("QuotaName", "")
                            ok, model = self._is_target_quota(name)
                            if not ok:
                                continue
                            n_low = name.lower()
                            if "token" not in n_low and "request" not in n_low:
                                continue
                            quotas.append({
                                "name": name,
                                "value": q.get("Value", 0),
                                "code": q.get("QuotaCode", ""),
                                "unit": q.get("Unit", "None"),
                                "model": model,
                            })
                    return True
                except Exception as e:
                    logger.warning(f"Bedrock quotas via {api_method} failed: {e}")
                    return False

            # 先用账号实际配额 (可能没权限), 失败再退默认配额
            if not _scan("list_service_quotas"):
                _scan("list_aws_default_service_quotas")
            return quotas

        # 并行执行模型 + 配额查询
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = {
                pool.submit(_detect_models): "models",
                pool.submit(_detect_quotas): "quotas",
            }
            for future in as_completed(tasks):
                key = tasks[future]
                try:
                    val = future.result()
                    if key == "models":
                        result["bedrock_models"] = val
                    elif key == "quotas":
                        result["bedrock_quotas"] = val
                except Exception as e:
                    logger.warning(f"AI detection {key} failed: {e}")

        return result

    # ==================== Claude 对话 ====================

    def invoke_claude(self, prompt: str = "你好", model_id: str = None, region: str = "us-east-1", max_tokens: int = 256) -> dict:
        """通过 Bedrock 调用 Claude 模型进行对话测试。

        返回:
        {
            "ok": bool,
            "model_id": str,
            "region": str,
            "prompt": str,
            "reply": str,
            "input_tokens": int,
            "output_tokens": int,
            "error": Optional[str],
        }
        """
        import json as _json
        result = {
            "ok": False,
            "model_id": model_id or "",
            "region": region,
            "prompt": prompt,
            "reply": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "error": None,
        }

        # 自动挑选可用 Claude 模型
        if not model_id:
            try:
                bedrock = self._get_client("bedrock", region)
                resp = bedrock.list_foundation_models()
                # 优先用 inference profile 兼容的较新版本
                preferred_keywords = [
                    "claude-3-5-sonnet", "claude-3-5-haiku",
                    "claude-3-haiku", "claude-3-sonnet",
                    "claude-instant",
                ]
                ids = []
                for m in resp.get("modelSummaries", []):
                    mid = m.get("modelId", "")
                    provider = m.get("providerName", "").lower()
                    if "anthropic" in provider and "claude" in mid.lower():
                        # 仅保留支持 ON_DEMAND 的模型
                        inference = m.get("inferenceTypesSupported", []) or []
                        if not inference or "ON_DEMAND" in inference:
                            ids.append(mid)
                # 按偏好排序
                def _rank(mid):
                    low = mid.lower()
                    for i, kw in enumerate(preferred_keywords):
                        if kw in low:
                            return i
                    return 99
                ids.sort(key=_rank)
                if not ids:
                    result["error"] = f"区域 {region} 没有可用的 Claude 模型 (Anthropic provider)"
                    return result
                model_id = ids[0]
                result["model_id"] = model_id
            except Exception as e:
                result["error"] = f"列出模型失败: {str(e)[:200]}"
                return result

        try:
            runtime = self._get_client("bedrock-runtime", region)
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            resp = runtime.invoke_model(
                modelId=model_id,
                body=_json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            payload = _json.loads(resp["body"].read())
            # Claude 3 messages API 返回结构
            content_parts = payload.get("content", []) or []
            text_parts = [c.get("text", "") for c in content_parts if c.get("type") == "text"]
            reply = "".join(text_parts).strip() or _json.dumps(payload)[:500]
            usage = payload.get("usage", {}) or {}
            result.update({
                "ok": True,
                "reply": reply,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            })
        except Exception as e:
            msg = str(e)
            if "AccessDenied" in msg or "not authorized" in msg:
                result["error"] = f"无权限调用模型 {model_id}: 请在 AWS 控制台 Bedrock → Model access 申请开启该模型"
            elif "ValidationException" in msg and "on-demand throughput" in msg.lower():
                result["error"] = f"模型 {model_id} 不支持按需调用，请改用 inference profile 或其它模型"
            elif "AccessDeniedException" in msg:
                result["error"] = f"调用被拒绝: {msg[:200]}"
            else:
                result["error"] = f"调用失败: {msg[:300]}"
        return result

    # ==================== Free Credit / 促销额度 ====================

    def get_credit_summary(self) -> dict:
        """查询账号 Credit / Free Tier 使用情况。

        ⚠️ 关键事实:
        AWS **没有公开 SDK API** 能查 Promotional Credit 的"剩余余额"。
        控制台 Billing → Credits 页面用的是 `aws-portal:GetCredits` 私有 API。
        我们能合规查到的:
        1. `freetier:GetFreeTierUsage` - Free Tier 12 个月免费额度的使用率 (按服务)
        2. `ce:GetCostAndUsage` 配合 RECORD_TYPE=Credit - 历史已抵扣金额

        返回:
        {
            "currency": "USD",
            "balance": 0.0,                # 余额 (无法 API 查询, 始终 0, 提示用户去控制台)
            "balance_unavailable": True,    # 标记: 余额无法 API 查询
            "used_this_year": 12.50,        # 本年累计 Credit 抵扣金额
            "used_last_month": 1.20,        # 上月 Credit 抵扣
            "used_this_month": 0.80,        # 本月 Credit 抵扣 (MTD)
            "used_total_history": 50.30,    # 历史累计 Credit 抵扣 (Cost Explorer 最多查 12-14 个月)
            "free_tier_usage": [             # Free Tier 12 个月免费额度使用率
                {
                    "service": "Amazon EC2",
                    "operation": "BoxUsage:t2.micro",
                    "actual_usage": 250,
                    "limit": 750,
                    "unit": "Hrs",
                    "free_tier_type": "12 months free",
                    "forecasted_usage": 700,
                    "usage_pct": 33.3,
                },
                ...
            ],
            "free_tier_total_count": 18,
            "credits": [],                   # 兼容旧字段, 始终空
            "total": 0.0,                    # 兼容旧字段
            "used": 12.50,                   # 兼容旧字段 = used_this_year
            "expires_at": None,
            "source": "cost_explorer+freetier",
            "console_url": "https://console.aws.amazon.com/billing/home#/credits",
            "error": None,
        }
        """
        from datetime import date, timedelta
        from calendar import monthrange

        result = {
            "currency": "USD",
            "balance": 0.0,
            "balance_unavailable": True,
            "used_this_year": 0.0,
            "used_last_month": 0.0,
            "used_this_month": 0.0,
            "used_total_history": 0.0,
            "free_tier_usage": [],
            "free_tier_total_count": 0,
            # 兼容旧字段:
            "credits": [],
            "total": 0.0,
            "used": 0.0,
            "expires_at": None,
            "source": "none",
            "console_url": "https://console.aws.amazon.com/billing/home#/credits",
            "error": None,
            "warnings": [],
        }

        today = date.today()
        currency = "USD"

        # ============ 1) Free Tier 使用情况 (新账号 12 个月免费额度) ============
        try:
            ft = self._get_client("freetier", "us-east-1")
            ft_items = []
            try:
                paginator = ft.get_paginator("get_free_tier_usage")
                for page in paginator.paginate():
                    ft_items.extend(page.get("freeTierUsages", []))
            except Exception as e1:
                # 部分版本 boto3 不支持 paginate, 直接调
                try:
                    next_token = None
                    while True:
                        kwargs = {}
                        if next_token:
                            kwargs["nextToken"] = next_token
                        resp = ft.get_free_tier_usage(**kwargs)
                        ft_items.extend(resp.get("freeTierUsages", []))
                        next_token = resp.get("nextToken")
                        if not next_token:
                            break
                except Exception as e2:
                    msg = str(e2)
                    if "AccessDenied" in msg or "not authorized" in msg:
                        result["warnings"].append("无 freetier:GetFreeTierUsage 权限, 跳过 Free Tier 用量")
                    else:
                        result["warnings"].append(f"Free Tier 查询失败: {msg[:120]}")

            for item in ft_items:
                actual = float(item.get("actualUsageAmount", 0) or 0)
                limit = float(item.get("limit", 0) or 0)
                forecasted = float(item.get("forecastedUsageAmount", 0) or 0)
                pct = (actual / limit * 100.0) if limit > 0 else 0.0
                result["free_tier_usage"].append({
                    "service": item.get("service", ""),
                    "operation": item.get("operation", ""),
                    "region": item.get("region", "global"),
                    "description": item.get("description", ""),
                    "actual_usage": round(actual, 2),
                    "forecasted_usage": round(forecasted, 2),
                    "limit": round(limit, 2),
                    "unit": item.get("unit", ""),
                    "free_tier_type": item.get("freeTierType", ""),
                    "usage_pct": round(pct, 1),
                    "exceeds": actual > limit,
                })
            result["free_tier_total_count"] = len(result["free_tier_usage"])
            # 按使用率倒序
            result["free_tier_usage"].sort(key=lambda x: x["usage_pct"], reverse=True)
        except Exception as e:
            logger.debug(f"freetier client init failed: {e}")
            result["warnings"].append(f"Free Tier 初始化失败: {str(e)[:120]}")

        # ============ 2) Cost Explorer 历史 Credit 抵扣 ============
        # AWS Cost Explorer 在 RECORD_TYPE=Credit 维度返回 Credit 抵扣金额 (UnblendedCost 为负数)
        # 这是查询 "已用了多少 Credit" 的官方方法
        try:
            ce = self._get_client("ce", "us-east-1")

            def _query_credit(start: date, end: date) -> float:
                """查询指定时段的 Credit 抵扣金额 (绝对值)"""
                resp = ce.get_cost_and_usage(
                    TimePeriod={"Start": str(start), "End": str(end)},
                    Granularity="MONTHLY",
                    Metrics=["UnblendedCost"],
                    Filter={"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit"]}},
                )
                total = 0.0
                for r in resp.get("ResultsByTime", []):
                    amt_obj = r.get("Total", {}).get("UnblendedCost", {})
                    amt = abs(float(amt_obj.get("Amount", 0) or 0))
                    total += amt
                return total

            # 时间区间
            year_start = date(today.year, 1, 1)
            month_start = date(today.year, today.month, 1)
            # 上月起止
            if today.month == 1:
                last_month_start = date(today.year - 1, 12, 1)
                last_month_end = date(today.year, 1, 1)
            else:
                last_month_start = date(today.year, today.month - 1, 1)
                last_month_end = date(today.year, today.month, 1)
            # 历史: Cost Explorer 最多查 14 个月
            history_start = today.replace(day=1) - timedelta(days=14*31)
            history_start = date(history_start.year, history_start.month, 1)

            try:
                # 一次性按月拉 14 个月历史 Credit 抵扣 (避免多次调用 token)
                resp = ce.get_cost_and_usage(
                    TimePeriod={"Start": str(history_start), "End": str(today)},
                    Granularity="MONTHLY",
                    Metrics=["UnblendedCost"],
                    Filter={"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit"]}},
                )
                total_history = 0.0
                used_this_year = 0.0
                used_this_month = 0.0
                used_last_month = 0.0
                for r in resp.get("ResultsByTime", []):
                    period_start = r.get("TimePeriod", {}).get("Start", "")
                    amt_obj = r.get("Total", {}).get("UnblendedCost", {})
                    amt = abs(float(amt_obj.get("Amount", 0) or 0))
                    cur = amt_obj.get("Unit") or currency
                    currency = cur or currency
                    total_history += amt
                    if period_start >= str(year_start):
                        used_this_year += amt
                    if period_start >= str(month_start):
                        used_this_month += amt
                    if str(last_month_start) <= period_start < str(last_month_end):
                        used_last_month += amt

                result["currency"] = currency
                result["used_total_history"] = round(total_history, 2)
                result["used_this_year"] = round(used_this_year, 2)
                result["used_this_month"] = round(used_this_month, 2)
                result["used_last_month"] = round(used_last_month, 2)
                # 兼容旧字段
                result["used"] = result["used_this_year"]
                result["total"] = result["used_total_history"]
                result["source"] = "cost_explorer+freetier" if result["free_tier_total_count"] else "cost_explorer"
            except Exception as e:
                msg = str(e)
                if "AccessDenied" in msg or "not authorized" in msg:
                    result["warnings"].append("无 ce:GetCostAndUsage 权限, 跳过 Credit 抵扣查询 (建议授予 AWSBillingReadOnlyAccess)")
                elif "DataUnavailable" in msg or "has not yet been activated" in msg:
                    result["warnings"].append("Cost Explorer 未启用, 在控制台 Billing → Cost Explorer 启用后约 24h 可查询")
                else:
                    result["warnings"].append(f"Credit 抵扣查询失败: {msg[:160]}")
                if result["free_tier_total_count"] > 0:
                    result["source"] = "freetier"
        except Exception as e:
            result["warnings"].append(f"Cost Explorer 初始化失败: {str(e)[:120]}")

        # ============ 3) 汇总错误 ============
        # 如果两个数据源都没数据, 不算错误, 只是该账号没用 Credit
        if result["source"] == "none" and not result["warnings"]:
            result["source"] = "empty"  # 明确表示账号没有 Credit / Free Tier 使用
        elif result["source"] == "none" and result["warnings"]:
            result["error"] = "; ".join(result["warnings"])

        return result


    # ==================== 账单查询 ====================
    def get_billing(self, year: int, month: int, granularity: str = "DAILY") -> dict:
        """
        查询指定年月的账单明细，与 AWS 控制台 Billing 页面口径一致。

        重点修复:
        - 之前用 UnblendedCost 是「list 价 (含 Credit/折扣前)」，AWS 控制台显示的是
          NetUnblendedCost (扣除 Credit / 退款 / 企业折扣后的真实付费金额)。
        - 现在两个都查并返回:
            total          = NetUnblendedCost  (实付, 与 AWS 控制台账单一致)
            gross_total    = UnblendedCost     (毛额, 显示折扣前 list 价)
            credits_used   = total_credits     (本期 Credit 抵扣金额)
            refunds        = total_refunds     (本期退款)
        - 每日走势, 按服务/按区域/按 RecordType 都改用 NetUnblendedCost

        返回结构:
        {
            "period": {"start": "...", "end": "..."},
            "total": 12.34,           # NetUnblendedCost - 实付
            "gross_total": 25.00,     # UnblendedCost - 毛额
            "credits_used": 12.66,    # 本期 Credit 抵扣
            "refunds": 0.0,
            "currency": "USD",
            "by_service":     [...],  # 实付分服务 (NetUnblendedCost)
            "by_region":      [...],
            "by_record_type": [{"type":"Usage","amount":...},{"type":"Credit","amount":-12.66}],
            "daily":          [{"date":..., "amount":..., "gross":...}],
            "monthly_total":  12.34,
            "error": null
        }
        """
        from calendar import monthrange
        from datetime import date, timedelta

        try:
            if not (2000 <= year <= 2100):
                return {"error": "年份无效", "total": 0, "by_service": [], "by_region": [], "daily": []}
            if not (1 <= month <= 12):
                return {"error": "月份无效", "total": 0, "by_service": [], "by_region": [], "daily": []}

            start_date = date(year, month, 1)
            last_day = monthrange(year, month)[1]
            today = date.today()
            end_date = date(year, month, last_day) + timedelta(days=1)
            if end_date > today:
                end_date = today
            if start_date >= end_date:
                return {
                    "error": "查询区间无效（开始日期不能晚于当前日期）",
                    "period": {"start": str(start_date), "end": str(end_date)},
                    "total": 0, "by_service": [], "by_region": [], "daily": [],
                }

            period = {"Start": str(start_date), "End": str(end_date)}
            ce = self._get_client("ce", "us-east-1")

            result = {
                "period": {"start": str(start_date), "end": str(end_date)},
                "total": 0.0,
                "gross_total": 0.0,
                "credits_used": 0.0,
                "refunds": 0.0,
                "currency": "USD",
                "by_service": [],
                "by_region": [],
                "by_record_type": [],
                "daily": [],
                "monthly_total": 0.0,
                "error": None,
            }

            # 同时查 NetUnblendedCost (实付) 和 UnblendedCost (毛额)
            METRICS = ["NetUnblendedCost", "UnblendedCost"]

            # ⚠️ 关键 Filter: AWS 控制台 "Charges by service" 页面排除 Credit/Refund/BundledDiscount
            # 等抵扣类记录, 只显示原始用量 (pre-tax service charges)。
            # 不加这个 Filter, 按 SERVICE 分组时同一服务的 Usage(+) 和 Credit(-) 会被加在一起求和,
            # 导致看到的金额比控制台小很多 (例如 Kiro 643.82 - 361.29 credits = 282.53)。
            EXCLUDE_CREDIT_FILTER = {
                "Not": {
                    "Dimensions": {
                        "Key": "RECORD_TYPE",
                        "Values": ["Credit", "Refund", "Bundled Discount"],
                    }
                }
            }

            def _amt(grp_or_total, key):
                """兼容字段：若 NetUnblendedCost 不可用 (老账号), 退回 UnblendedCost"""
                m = grp_or_total.get(key, {})
                if not m or m.get("Amount") is None:
                    m = grp_or_total.get("UnblendedCost", {})
                return float(m.get("Amount", 0) or 0), (m.get("Unit") or "USD")

            # 1) 按服务分组 (UnblendedCost 毛额, 与 AWS 控制台 "Charges by service" 一致)
            #    每个 Service 同时返回:
            #      amount = UnblendedCost (毛额) - 与控制台一致
            #      net    = NetUnblendedCost (实付, 已扣 Credit)
            try:
                resp = ce.get_cost_and_usage(
                    TimePeriod=period,
                    Granularity="MONTHLY",
                    Metrics=METRICS,
                    GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
                    Filter=EXCLUDE_CREDIT_FILTER,
                )
                svc_total_net_usage, svc_total_gross_usage = 0.0, 0.0
                currency = "USD"
                svc_agg = {}
                for r in resp.get("ResultsByTime", []):
                    for g in r.get("Groups", []):
                        svc = g["Keys"][0] if g.get("Keys") else "-"
                        net, cur = _amt(g["Metrics"], "NetUnblendedCost")
                        gross, _ = _amt(g["Metrics"], "UnblendedCost")
                        currency = cur or currency
                        prev = svc_agg.get(svc, {"net": 0.0, "gross": 0.0})
                        prev["net"] += net
                        prev["gross"] += gross
                        svc_agg[svc] = prev
                        svc_total_net_usage += net
                        svc_total_gross_usage += gross
                result["currency"] = currency
                # by_service.amount 用 UnblendedCost 毛额 (对齐 AWS 控制台 "Charges by service")
                result["by_service"] = sorted(
                    [{"service": k, "amount": round(v["gross"], 4), "net": round(v["net"], 4)}
                     for k, v in svc_agg.items() if abs(v["net"]) > 0.0001 or abs(v["gross"]) > 0.0001],
                    key=lambda x: x["amount"], reverse=True,
                )
            except Exception as e:
                msg = str(e)
                if "AccessDenied" in msg or "not authorized" in msg:
                    result["error"] = "凭证无 Cost Explorer 权限 (ce:GetCostAndUsage)，请在 IAM 授予 AWSBillingReadOnlyAccess。"
                elif "DataUnavailable" in msg or "has not yet been activated" in msg:
                    result["error"] = "该账号尚未启用 Cost Explorer。请在 AWS 控制台 Billing → Cost Explorer 点 'Enable Cost Explorer'，约 24h 后可查询。"
                else:
                    result["error"] = f"账单查询失败: {msg[:200]}"
                return result

            # 1.5) 全月汇总 (不加 Filter, 包含 Credit/Refund) - 这才是真实"实付"和"毛额"
            #      total       = NetUnblendedCost (实付, 与 AWS Bill 账单一致)
            #      gross_total = UnblendedCost - Credit/Refund 抵扣 = pre-tax service charges
            #      因为 by_service 已经排除 Credit/Refund, 所以 gross_total 直接用上面累计的就行
            try:
                resp_total = ce.get_cost_and_usage(
                    TimePeriod=period,
                    Granularity="MONTHLY",
                    Metrics=METRICS,
                )
                month_net_total = 0.0
                for r in resp_total.get("ResultsByTime", []):
                    n, _ = _amt(r.get("Total", {}), "NetUnblendedCost")
                    month_net_total += n
                result["total"] = round(month_net_total, 4)
                # gross_total 用排除 Credit/Refund 后的 by_service 总和 (= 控制台 "pre-tax service charges")
                result["gross_total"] = round(svc_total_gross_usage, 4)
                result["monthly_total"] = round(month_net_total, 4)
            except Exception as e:
                logger.warning(f"billing total failed: {e}")
                result["total"] = round(svc_total_net_usage, 4)
                result["gross_total"] = round(svc_total_gross_usage, 4)
                result["monthly_total"] = round(svc_total_net_usage, 4)

            # 2) 按区域分组 (UnblendedCost 毛额, 同样排除 Credit/Refund)
            try:
                resp = ce.get_cost_and_usage(
                    TimePeriod=period,
                    Granularity="MONTHLY",
                    Metrics=METRICS,
                    GroupBy=[{"Type": "DIMENSION", "Key": "REGION"}],
                    Filter=EXCLUDE_CREDIT_FILTER,
                )
                reg_agg = {}
                for r in resp.get("ResultsByTime", []):
                    for g in r.get("Groups", []):
                        reg = g["Keys"][0] if g.get("Keys") else "-"
                        gross, _ = _amt(g["Metrics"], "UnblendedCost")
                        reg_agg[reg or "-"] = reg_agg.get(reg or "-", 0.0) + gross
                result["by_region"] = sorted(
                    [{"region": k, "amount": round(v, 4)} for k, v in reg_agg.items() if abs(v) > 0.0001],
                    key=lambda x: x["amount"], reverse=True,
                )
            except Exception as e:
                logger.warning(f"billing by region failed: {e}")

            # 3) 按 RecordType 分组: 看 Usage / Credit / Refund / Tax 等
            #    注意: Credit / Refund 在 UnblendedCost 里是负数 (= 抵扣金额)
            try:
                resp = ce.get_cost_and_usage(
                    TimePeriod=period,
                    Granularity="MONTHLY",
                    Metrics=METRICS,
                    GroupBy=[{"Type": "DIMENSION", "Key": "RECORD_TYPE"}],
                )
                rt_agg = {}
                credits_used = 0.0
                refunds = 0.0
                for r in resp.get("ResultsByTime", []):
                    for g in r.get("Groups", []):
                        rt = g["Keys"][0] if g.get("Keys") else "-"
                        # RecordType 用 UnblendedCost 才能看到 Credit 的负数
                        gross, _ = _amt(g["Metrics"], "UnblendedCost")
                        rt_agg[rt] = rt_agg.get(rt, 0.0) + gross
                        if rt == "Credit":
                            credits_used += abs(gross)
                        elif rt == "Refund":
                            refunds += abs(gross)
                result["by_record_type"] = sorted(
                    [{"type": k, "amount": round(v, 4)} for k, v in rt_agg.items() if abs(v) > 0.0001],
                    key=lambda x: abs(x["amount"]), reverse=True,
                )
                result["credits_used"] = round(credits_used, 4)
                result["refunds"] = round(refunds, 4)
            except Exception as e:
                logger.warning(f"billing by record_type failed: {e}")

            # 4) 每日走势 (实付 + 毛额)
            if granularity.upper() == "DAILY":
                try:
                    resp = ce.get_cost_and_usage(
                        TimePeriod=period,
                        Granularity="DAILY",
                        Metrics=METRICS,
                    )
                    daily = []
                    for r in resp.get("ResultsByTime", []):
                        d = r.get("TimePeriod", {}).get("Start", "")
                        net, _ = _amt(r.get("Total", {}), "NetUnblendedCost")
                        gross, _ = _amt(r.get("Total", {}), "UnblendedCost")
                        daily.append({"date": d, "amount": round(net, 4), "gross": round(gross, 4)})
                    result["daily"] = daily
                except Exception as e:
                    logger.warning(f"billing daily failed: {e}")

            return result
        except Exception as e:
            return {
                "error": f"账单查询异常: {str(e)[:200]}",
                "total": 0, "by_service": [], "by_region": [], "daily": [],
            }

    # ==================== IAM 权限诊断 + 策略生成器 ====================

    # 本平台用到的全部 AWS API 权限清单 (按功能分组)
    # 点亮一行就需要那些权限. 用户可以根据需要选择性授权 (最小权限) 或一键给全部 (Admin)
    PERMISSION_GROUPS = {
        "core": {
            "title": "🔑 核心 (账号识别 / 必装)",
            "essential": True,
            "actions": ["sts:GetCallerIdentity"],
            "probes": [("sts", "us-east-1", "get_caller_identity", {})],
        },
        "account_email": {
            "title": "📧 账号邮箱 / 注册信息",
            "essential": False,
            "actions": [
                "account:GetAccountInformation",
                "account:GetPrimaryEmail",
                "account:GetContactInformation",
                "account:GetAlternateContact",
            ],
            "probes": [
                ("account", "us-east-1", "get_account_information", {}),
                ("account", "us-east-1", "get_contact_information", {}),
                ("account", "us-east-1", "get_alternate_contact", {"AlternateContactType": "BILLING"}),
            ],
        },
        "regions_optin": {
            "title": "🌐 区域启用 (开通 opt-in)",
            "essential": False,
            "actions": [
                "ec2:DescribeRegions",
                "account:ListRegions",
                "account:GetRegionOptStatus",
                "account:EnableRegion",
                "account:DisableRegion",
            ],
            "probes": [
                ("ec2", "us-east-1", "describe_regions", {"AllRegions": True}),
                ("account", "us-east-1", "list_regions", {"MaxResults": 5}),
            ],
        },
        "billing_credits": {
            "title": "💰 账单 / Credit 抵扣",
            "essential": False,
            "actions": [
                "ce:GetCostAndUsage",
                "ce:GetCostForecast",
                "ce:GetUsageForecast",
                "ce:GetCostCategories",
            ],
            "probes": [
                ("ce", "us-east-1", "get_cost_and_usage", {
                    "TimePeriod": {"Start": "2099-01-01", "End": "2099-01-02"},
                    "Granularity": "MONTHLY",
                    "Metrics": ["UnblendedCost"],
                }),
            ],
            "console_enable": "https://console.aws.amazon.com/cost-management/home#/cost-explorer",
            "console_note": "需要先在控制台 Billing → Cost Explorer 点 Enable Cost Explorer, 约 24h 后才能 API 查询.",
        },
        "invoicing_email": {
            "title": "🧾 Invoicing (发票收件邮箱 = root 邮箱备选)",
            "essential": False,
            "actions": [
                "invoicing:BatchGetInvoiceProfile",
                "invoicing:ListInvoiceUnits",
                "invoicing:GetInvoiceUnit",
            ],
            "probes": [
                # 用空 AccountIds 探测权限 (服务会返回 ValidationException, 但不是 AccessDenied → 视为有权限)
                ("invoicing", "us-east-1", "batch_get_invoice_profile", {"AccountIds": []}),
            ],
            "console_note": "AWSBillingReadOnlyAccess 已包含; 这是 account:GetPrimaryEmail 之外能拿到 root 邮箱的备选 API",
        },
        "tax_email": {
            "title": "🏛 Tax Settings (税务联系人邮箱)",
            "essential": False,
            "actions": [
                "tax:GetTaxRegistration",
                "tax:GetTaxInheritance",
                "taxsettings:GetTaxRegistration",
                "taxsettings:ListTaxRegistrations",
            ],
            "probes": [
                ("taxsettings", "us-east-1", "get_tax_registration", {}),
            ],
            "console_note": "AWSBillingReadOnlyAccess 已包含; 欧盟/巴西/印度/沙特账号常在税务联系人填 root 邮箱",
        },
        "free_tier": {
            "title": "🎁 Free Tier 用量",
            "essential": False,
            "actions": ["freetier:GetFreeTierUsage"],
            "probes": [("freetier", "us-east-1", "get_free_tier_usage", {})],
        },
        "ec2_manage": {
            "title": "🖥 EC2 实例管理",
            "essential": False,
            "actions": [
                "ec2:DescribeInstances",
                "ec2:DescribeImages",
                "ec2:DescribeAddresses",
                "ec2:DescribeKeyPairs",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeVpcs",
                "ec2:DescribeSubnets",
                "ec2:RunInstances",
                "ec2:TerminateInstances",
                "ec2:StartInstances",
                "ec2:StopInstances",
                "ec2:RebootInstances",
                "ec2:CreateKeyPair",
                "ec2:CreateSecurityGroup",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:CreateTags",
                "ec2:AllocateAddress",
                "ec2:AssociateAddress",
            ],
            "probes": [
                ("ec2", "us-east-1", "describe_instances", {"MaxResults": 5}),
                ("ec2", "us-east-1", "describe_security_groups", {"MaxResults": 5}),
            ],
        },
        "lightsail": {
            "title": "⛵ Lightsail (光帆)",
            "essential": False,
            "actions": [
                "lightsail:GetRegions",
                "lightsail:GetBlueprints",
                "lightsail:GetBundles",
                "lightsail:GetInstances",
                "lightsail:GetInstance",
                "lightsail:CreateInstances",
                "lightsail:StartInstance",
                "lightsail:StopInstance",
                "lightsail:RebootInstance",
                "lightsail:DeleteInstance",
                "lightsail:OpenInstancePublicPorts",
            ],
            "probes": [
                ("lightsail", "us-east-1", "get_regions", {}),
                ("lightsail", "us-east-1", "get_blueprints", {}),
            ],
        },
        "vcpu_quota": {
            "title": "⚡ vCPU 配额检测",
            "essential": False,
            "actions": [
                "servicequotas:GetServiceQuota",
                "servicequotas:ListServiceQuotas",
                "servicequotas:ListAWSDefaultServiceQuotas",
                "servicequotas:GetAWSDefaultServiceQuota",
            ],
            "probes": [
                ("service-quotas", "us-east-1", "get_aws_default_service_quota", {
                    "ServiceCode": "ec2", "QuotaCode": "L-1216C47A",
                }),
            ],
        },
        "iam_report": {
            "title": "🔐 IAM credential report (注册时间识别)",
            "essential": False,
            "actions": [
                "iam:GenerateCredentialReport",
                "iam:GetCredentialReport",
                "iam:GetUser",
            ],
            "probes": [("iam", "us-east-1", "get_user", {})],
        },
        "organizations": {
            "title": "🏢 Organizations (组织内账号邮箱)",
            "essential": False,
            "actions": [
                "organizations:DescribeAccount",
                "organizations:DescribeOrganization",
            ],
            "probes": [("organizations", "us-east-1", "describe_organization", {})],
        },
        "bedrock_ai": {
            "title": "🤖 Bedrock / Claude AI",
            "essential": False,
            "actions": [
                "bedrock:ListFoundationModels",
                "bedrock:GetFoundationModel",
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:Converse",
            ],
            "probes": [("bedrock", "us-east-1", "list_foundation_models", {})],
        },
        "budgets_sns_ses": {
            "title": "🔔 Budgets / SNS / SES (邮箱兜底)",
            "essential": False,
            "actions": [
                "budgets:DescribeBudgets",
                "budgets:DescribeNotificationsForBudget",
                "budgets:DescribeSubscribersForNotification",
                "sns:ListSubscriptions",
                "ses:ListIdentities",
            ],
            "probes": [],   # 这些都是 best-effort, 不强制探测
        },
    }

    def diagnose_permissions(self) -> dict:
        """逐项探测当前 AK/SK 拥有哪些权限, 并生成最小 IAM 策略 JSON.

        返回:
        {
            "groups": [
                {
                    "key": "billing_credits",
                    "title": "💰 账单 / Credit 抵扣",
                    "essential": False,
                    "actions": ["ce:GetCostAndUsage", ...],
                    "status": "ok" | "denied" | "not_enabled" | "partial" | "error",
                    "message": "...",
                    "probe_results": [{"api":"ce.get_cost_and_usage","ok":bool,"error":str?}],
                    "console_enable": "https://...",
                    "console_note": "...",
                }
            ],
            "summary": {
                "total_groups": 12,
                "ok": 6,
                "denied": 4,
                "not_enabled": 1,
                "error": 1,
            },
            "policy_minimal":   { "Version":"2012-10-17", "Statement":[...] },  # 缺失功能用的最小策略
            "policy_full":      { ... },  # 全功能策略 (一次给全)
            "aws_managed_alts": [
                "AdministratorAccess",
                "PowerUserAccess",
                "ReadOnlyAccess",
                "AWSBillingReadOnlyAccess",
                "AmazonEC2FullAccess",
                "AmazonLightsailFullAccess",
                "AmazonBedrockFullAccess",
            ],
            "iam_console_url":  "https://console.aws.amazon.com/iam/...",
            "ami_console_url":  "https://...",
        }
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        groups_out = []
        all_actions_missing = set()
        all_actions_full = set()

        def _probe_one(svc, region, method, kwargs):
            """单个 API 探测, 返回 (ok:bool, error_kind:str, error_msg:str)"""
            try:
                client = self._make_detect_client(svc, region)
                fn = getattr(client, method)
                fn(**kwargs)
                return True, "ok", ""
            except Exception as e:
                msg = str(e)
                low = msg.lower()
                if "accessdenied" in low or "not authorized" in low or "unauthorizedoperation" in low or "forbidden" in low:
                    return False, "denied", msg[:200]
                if "validationexception" in low and method == "get_cost_and_usage":
                    # 时间区间无效但权限有 → 视为 ok
                    return True, "ok", ""
                if "not yet been activated" in low or "data is not available" in low or "datanotavailable" in low or "dataunavailable" in low:
                    return False, "not_enabled", msg[:200]
                if "invalidclienttokenid" in low or "signaturedoesnotmatch" in low or "authfailure" in low:
                    return False, "invalid_credentials", msg[:200]
                if "unrecognizedclient" in low:
                    return False, "denied", msg[:200]
                if "throttling" in low or "ratelimit" in low:
                    return True, "throttled", msg[:200]   # 节流也说明权限是有的
                return False, "error", msg[:200]

        # 并发探测每组
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {}
            for key, grp in self.PERMISSION_GROUPS.items():
                probes = grp.get("probes") or []
                if not probes:
                    # 没探测项就直接标记为 unknown
                    groups_out.append({
                        "key": key,
                        "title": grp["title"],
                        "essential": grp.get("essential", False),
                        "actions": list(grp["actions"]),
                        "status": "unknown",
                        "message": "未探测 (best-effort 服务, 见前端可选授权)",
                        "probe_results": [],
                        "console_enable": grp.get("console_enable"),
                        "console_note": grp.get("console_note"),
                    })
                    all_actions_full.update(grp["actions"])
                    continue
                for idx, (svc, region, method, kwargs) in enumerate(probes):
                    futures[pool.submit(_probe_one, svc, region, method, kwargs)] = (key, idx, svc, method)

            # 收集探测结果
            collected = {}
            for fut in as_completed(futures):
                key, idx, svc, method = futures[fut]
                try:
                    ok, kind, err = fut.result()
                except Exception as e:
                    ok, kind, err = False, "error", str(e)[:200]
                collected.setdefault(key, []).append({
                    "api": f"{svc}.{method}",
                    "ok": ok, "kind": kind, "error": err if not ok else "",
                })

        for key, grp in self.PERMISSION_GROUPS.items():
            all_actions_full.update(grp["actions"])
            if not grp.get("probes"):
                continue
            results = collected.get(key, [])
            ok_count = sum(1 for r in results if r["ok"])
            kinds = {r["kind"] for r in results}
            if ok_count == len(results) and ok_count > 0:
                status = "ok"
                message = f"✓ {ok_count}/{len(results)} 个 API 都通过"
            elif "denied" in kinds and ok_count == 0:
                status = "denied"
                message = "❌ 全部被拒 (无权限) — 请添加下方策略"
                all_actions_missing.update(grp["actions"])
            elif "not_enabled" in kinds:
                status = "not_enabled"
                message = "⚠ 服务未启用 (Cost Explorer 之类的需要在控制台先 Enable)"
            elif "invalid_credentials" in kinds:
                status = "invalid"
                message = "❌ AK/SK 失效"
            elif ok_count > 0:
                status = "partial"
                message = f"部分通过 ({ok_count}/{len(results)})"
                # 加策略, 但用户可能少了某些细分权限
                all_actions_missing.update(grp["actions"])
            else:
                status = "error"
                message = "其它错误, 请查看下方明细"
                all_actions_missing.update(grp["actions"])

            groups_out.append({
                "key": key,
                "title": grp["title"],
                "essential": grp.get("essential", False),
                "actions": list(grp["actions"]),
                "status": status,
                "message": message,
                "probe_results": results,
                "console_enable": grp.get("console_enable"),
                "console_note": grp.get("console_note"),
            })

        # 汇总
        summary = {
            "total_groups": len(groups_out),
            "ok": sum(1 for g in groups_out if g["status"] == "ok"),
            "denied": sum(1 for g in groups_out if g["status"] == "denied"),
            "not_enabled": sum(1 for g in groups_out if g["status"] == "not_enabled"),
            "partial": sum(1 for g in groups_out if g["status"] == "partial"),
            "error": sum(1 for g in groups_out if g["status"] in ("error", "invalid")),
            "unknown": sum(1 for g in groups_out if g["status"] == "unknown"),
        }

        # 生成最小补缺策略 (用户当前缺啥就只贴啥)
        def _make_policy(action_set):
            if not action_set:
                return None
            return {
                "Version": "2012-10-17",
                "Statement": [{
                    "Sid": "DepinManagerPolicy",
                    "Effect": "Allow",
                    "Action": sorted(list(action_set)),
                    "Resource": "*",
                }],
            }

        # IAM 用户名 (从 ARN 推)
        iam_user_name = ""
        try:
            arn = self.account.arn or ""
            if "/user/" in arn:
                iam_user_name = arn.split("/user/")[-1]
            elif "/role/" in arn:
                iam_user_name = arn.split("/role/")[-1]
        except Exception:
            pass

        return {
            "account_id": self.account.aws_account_id or "",
            "iam_user": iam_user_name,
            "groups": groups_out,
            "summary": summary,
            "policy_minimal": _make_policy(all_actions_missing),
            "policy_full":    _make_policy(all_actions_full),
            "aws_managed_alts": [
                {"name": "AdministratorAccess", "desc": "全权限 (最简单, 推荐自用账号)", "arn": "arn:aws:iam::aws:policy/AdministratorAccess"},
                {"name": "PowerUserAccess", "desc": "几乎所有服务 (除 IAM 外)", "arn": "arn:aws:iam::aws:policy/PowerUserAccess"},
                {"name": "ReadOnlyAccess", "desc": "只读 (但不能开实例 / Lightsail)", "arn": "arn:aws:iam::aws:policy/ReadOnlyAccess"},
                {"name": "AWSBillingReadOnlyAccess", "desc": "Cost Explorer / 账单只读 (邮箱+账单+Credit 抵扣需要)", "arn": "arn:aws:iam::aws:policy/AWSBillingReadOnlyAccess"},
                {"name": "AmazonEC2FullAccess", "desc": "EC2 全管 (开/停/删实例 + AMI + EIP)", "arn": "arn:aws:iam::aws:policy/AmazonEC2FullAccess"},
                {"name": "AmazonLightsailFullAccess", "desc": "Lightsail 全管", "arn": "arn:aws:iam::aws:policy/AmazonLightsailFullAccess"},
                {"name": "AmazonBedrockFullAccess", "desc": "Bedrock / Claude AI 调用", "arn": "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"},
                {"name": "IAMReadOnlyAccess", "desc": "IAM 只读 (注册时间识别)", "arn": "arn:aws:iam::aws:policy/IAMReadOnlyAccess"},
                {"name": "AWSAccountManagementFullAccess", "desc": "Account API (邮箱/区域 opt-in)", "arn": "arn:aws:iam::aws:policy/AWSAccountManagementFullAccess"},
                {"name": "AWSFreeTier_ReadOnlyAccess", "desc": "Free Tier 用量只读", "arn": "arn:aws:iam::aws:policy/AWSFreeTier_ReadOnlyAccess"},
            ],
            "iam_console_url": (
                f"https://console.aws.amazon.com/iam/home#/users/{iam_user_name}/permissions" if iam_user_name
                else "https://console.aws.amazon.com/iam/home#/users"
            ),
            "cli_command_examples": {
                "attach_admin": (
                    f"aws iam attach-user-policy --user-name {iam_user_name or '<USERNAME>'} "
                    "--policy-arn arn:aws:iam::aws:policy/AdministratorAccess"
                ),
                "attach_billing": (
                    f"aws iam attach-user-policy --user-name {iam_user_name or '<USERNAME>'} "
                    "--policy-arn arn:aws:iam::aws:policy/AWSBillingReadOnlyAccess"
                ),
                "put_inline_minimal": (
                    f"aws iam put-user-policy --user-name {iam_user_name or '<USERNAME>'} "
                    "--policy-name DepinManagerMissing --policy-document file://policy_minimal.json"
                ),
                "enable_cost_explorer": "https://console.aws.amazon.com/cost-management/home#/cost-explorer",
                "enable_billing_user_access": (
                    "登录 root → My Account → IAM User and Role Access to Billing Information → Activate"
                ),
            },
        }






