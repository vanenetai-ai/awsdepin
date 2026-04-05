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


class AwsManager:
    def __init__(self, account: AwsAccount, db: Session, use_proxy: bool = True):
        self.account = account
        self.db = db
        self.proxy_config = None
        self._client_cache = {}
        self._resource_cache = {}
        if use_proxy:
            pm = ProxyManager(db)
            self.proxy_config = pm.get_proxy_for_boto3()

    def _get_client(self, service: str, region: str = None):
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
        if self.proxy_config:
            config_kwargs["proxies"] = self.proxy_config
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
        region = region or self.account.default_region
        cache_key = f"{service}:{region}"
        if cache_key in self._resource_cache:
            return self._resource_cache[cache_key]
        config_kwargs = {}
        if self.proxy_config:
            config_kwargs["proxies"] = self.proxy_config
        resource = boto3.resource(
            service,
            aws_access_key_id=self.account.access_key_id,
            aws_secret_access_key=self.account.secret_access_key,
            region_name=region,
            config=Config(**config_kwargs) if config_kwargs else None,
        )
        self._resource_cache[cache_key] = resource
        return resource

    def verify_credentials(self) -> dict:
        """验证 AWS 凭证是否有效"""
        try:
            sts = self._get_client("sts")
            identity = sts.get_caller_identity()
            return {"valid": True, "account_id": identity["Account"], "arn": identity["Arn"]}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    def list_regions(self) -> list:
        ec2 = self._get_client("ec2", "us-east-1")
        resp = ec2.describe_regions()
        return [r["RegionName"] for r in resp["Regions"]]

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

    def launch_instance(
        self,
        region: str = None,
        instance_type: str = "t3.micro",
        user_data: str = None,
        volume_size: int = 20,
        volume_type: str = "gp3",
    ) -> Instance:
        region = region or self.account.default_region
        ami_id = UBUNTU_AMIS.get(region)
        if not ami_id:
            # 动态查找最新 Ubuntu AMI
            ec2 = self._get_client("ec2", region)
            resp = ec2.describe_images(
                Filters=[
                    {"Name": "name", "Values": ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]},
                    {"Name": "state", "Values": ["available"]},
                ],
                Owners=["099720109477"],
            )
            images = sorted(resp["Images"], key=lambda x: x["CreationDate"], reverse=True)
            ami_id = images[0]["ImageId"] if images else None
            if not ami_id:
                raise ValueError(f"No Ubuntu AMI found in region {region}")

        sg_id = self._ensure_security_group(region)
        key_name, private_key = self._ensure_key_pair(region)
        ud = user_data or DEFAULT_USER_DATA

        ec2 = self._get_client("ec2", region)
        resp = ec2.run_instances(
            ImageId=ami_id,
            InstanceType=instance_type,
            MinCount=1,
            MaxCount=1,
            KeyName=key_name,
            SecurityGroupIds=[sg_id],
            UserData=ud,
            BlockDeviceMappings=[{
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "VolumeSize": volume_size,
                    "VolumeType": volume_type,
                    "DeleteOnTermination": True,
                },
            }],
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": f"depin-{self.account.name}"},
                    {"Key": "ManagedBy", "Value": "aws-depin-manager"},
                ],
            }],
        )

        inst_data = resp["Instances"][0]
        instance = Instance(
            account_id=self.account.id,
            instance_id=inst_data["InstanceId"],
            region=region,
            instance_type=instance_type,
            state=inst_data["State"]["Name"],
            key_name=key_name,
            private_key=private_key,
        )
        self.db.add(instance)
        self.db.commit()
        self.db.refresh(instance)
        return instance

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

    # ==================== 账号信息检测 ====================

    def _get_credential_report(self) -> list:
        """获取 credential report 并解析为行列表，带缓存"""
        if hasattr(self, '_cred_report_cache'):
            return self._cred_report_cache
        import time, csv, io
        iam = self._get_client("iam")
        for _ in range(3):
            try:
                resp = iam.generate_credential_report()
                if resp.get("State") == "COMPLETE":
                    break
            except Exception:
                pass
            time.sleep(1)
        resp = iam.get_credential_report()
        report = resp["Content"].decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(report)))
        self._cred_report_cache = rows
        return rows

    def _detect_email_from_organizations(self) -> str | None:
        """从 Organizations describe_organization 获取 MasterAccountEmail"""
        try:
            org = self._get_client("organizations")
            resp = org.describe_organization()
            email = resp.get("Organization", {}).get("MasterAccountEmail", "")
            if email and "@" in email:
                return email
        except Exception as e:
            logger.debug(f"Organizations API failed: {e}")
        return None

    def _detect_email_from_budgets(self) -> str | None:
        """从 Budgets 通知订阅者获取邮箱"""
        try:
            sts = self._get_client("sts")
            account_id = sts.get_caller_identity()["Account"]
            budgets = self._get_client("budgets", "us-east-1")
            resp = budgets.describe_budgets(AccountId=account_id, MaxResults=10)
            for budget in resp.get("Budgets", []):
                try:
                    notifs = budgets.describe_notifications_for_budget(
                        AccountId=account_id, BudgetName=budget["BudgetName"]
                    )
                    for n in notifs.get("Notifications", []):
                        subs = budgets.describe_subscribers_for_notification(
                            AccountId=account_id,
                            BudgetName=budget["BudgetName"],
                            Notification=n
                        )
                        for s in subs.get("Subscribers", []):
                            if s.get("SubscriptionType") == "EMAIL":
                                addr = s.get("Address", "")
                                if "@" in addr:
                                    return addr
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Budgets API failed: {e}")
        return None

    def _detect_email_from_credential_report(self) -> str | None:
        """从 credential report 的 root 行获取邮箱（部分账号 root user 就是邮箱）"""
        try:
            rows = self._get_credential_report()
            for row in rows:
                arn_val = row.get("arn", "")
                if ":root" in arn_val:
                    user = row.get("user", "")
                    if "@" in user:
                        return user
                    break
        except Exception:
            pass
        return None

    def _detect_creation_time(self) -> datetime | None:
        """从 credential report root 行获取账号创建时间"""
        try:
            rows = self._get_credential_report()
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
            iam = self._get_client("iam")
            user = iam.get_user()
            return user["User"].get("CreateDate")
        except Exception:
            pass
        return None

    def _detect_country(self) -> str:
        """检测账号注册国家"""
        try:
            account_client = self._get_client("account", "us-east-1")
            contact = account_client.get_contact_information()
            country = contact.get("ContactInformation", {}).get("CountryCode", "")
            if country:
                return country
        except Exception:
            pass
        region = self.account.default_region
        region_country = {
            "us-east-1": "US", "us-east-2": "US", "us-west-1": "US", "us-west-2": "US",
            "eu-west-1": "IE", "eu-west-2": "GB", "eu-west-3": "FR", "eu-central-1": "DE",
            "eu-north-1": "SE", "ap-northeast-1": "JP", "ap-northeast-2": "KR",
            "ap-northeast-3": "JP", "ap-southeast-1": "SG", "ap-southeast-2": "AU",
            "ap-south-1": "IN", "sa-east-1": "BR", "ca-central-1": "CA",
            "me-south-1": "BH", "af-south-1": "ZA",
        }
        return region_country.get(region, "US")

    def _detect_default_region_vcpu(self) -> int:
        """快速获取默认区域(us-east-1)的 on-demand vCPU 配额"""
        region = "us-east-1"
        try:
            sq = self._get_client("service-quotas", region)
            try:
                resp = sq.get_service_quota(ServiceCode="ec2", QuotaCode="L-1216C47A")
                return int(resp["Quota"]["Value"])
            except Exception:
                resp = sq.get_aws_default_service_quota(ServiceCode="ec2", QuotaCode="L-1216C47A")
                return int(resp["Quota"]["Value"])
        except Exception:
            return 5

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

    def detect_account_info(self) -> dict:
        """一次性检测账号所有信息并更新数据库 - 全部并行，无超时，必须等真实数据"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        info = {"email": None, "arn": None, "account_id": None}

        # 先获取 STS 身份
        try:
            sts = self._get_client("sts")
            identity = sts.get_caller_identity()
            info["arn"] = identity.get("Arn", "")
            info["account_id"] = identity.get("Account", "")
        except Exception as e:
            logger.error(f"STS failed: {e}")
            return info

        # 并行执行所有检测任务 - 不设超时，等每个任务完成
        results = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(self._detect_email_from_organizations): "email_org",
                pool.submit(self._detect_email_from_budgets): "email_budgets",
                pool.submit(self._detect_email_from_credential_report): "email_cred",
                pool.submit(self._detect_creation_time): "creation_time",
                pool.submit(self._detect_country): "country",
                pool.submit(self.get_vcpu_quotas_all_regions): "vcpus",
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    logger.warning(f"Detection {key} failed: {e}")
                    results[key] = None

        # 邮箱: 优先 organizations，其次 budgets，最后 credential report
        email = results.get("email_org") or results.get("email_budgets") or results.get("email_cred")
        if not email:
            email = self.account.email if (self.account.email and "@" in (self.account.email or "")) else f"root ({info['account_id']})"
        info["email"] = email

        # 注册时间
        info["register_time"] = results.get("creation_time")

        # 国家
        info["country"] = results.get("country") or "US"

        # vCPU (全区域扫描) - 必须是真实数据
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

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()

        return info

    # ==================== AI 配额检测 ====================

    def detect_ai_info(self) -> dict:
        """检测 Bedrock AI 配额、Anthropic 模型、Kiro/SSO 订阅 - 无超时，等真实数据"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        result = {"bedrock_models": [], "bedrock_quotas": [], "sso_instances": 0, "licenses": []}

        def _detect_bedrock_models():
            """只获取 global Claude 模型 (us-east-1)"""
            models = []
            try:
                bedrock = self._get_client("bedrock", "us-east-1")
                resp = bedrock.list_foundation_models()
                for m in resp.get("modelSummaries", []):
                    mid = m.get("modelId", "").lower()
                    provider = m.get("providerName", "").lower()
                    # 只取 anthropic claude 模型
                    if "anthropic" in provider and "claude" in mid:
                        models.append({
                            "id": m.get("modelId", ""),
                            "name": m.get("modelName", ""),
                            "provider": m.get("providerName", ""),
                            "input": m.get("inputModalities", []),
                            "output": m.get("outputModalities", []),
                        })
            except Exception as e:
                logger.warning(f"Bedrock models error: {e}")
            return models

        def _detect_bedrock_quotas():
            """获取 Bedrock 配额 - 只取 Anthropic/Claude 相关"""
            quotas = []
            try:
                sq = self._get_client("service-quotas", "us-east-1")
                paginator = sq.get_paginator("list_service_quotas")
                for page in paginator.paginate(ServiceCode="bedrock"):
                    for q in page.get("Quotas", []):
                        name = q.get("QuotaName", "").lower()
                        if ("anthropic" in name or "claude" in name) and ("token" in name or "request" in name):
                            quotas.append({
                                "name": q.get("QuotaName", ""),
                                "value": q.get("Value", 0),
                                "code": q.get("QuotaCode", ""),
                            })
            except Exception as e:
                logger.warning(f"Bedrock quotas error: {e}")
                # fallback: 尝试默认配额
                try:
                    sq = self._get_client("service-quotas", "us-east-1")
                    paginator = sq.get_paginator("list_aws_default_service_quotas")
                    for page in paginator.paginate(ServiceCode="bedrock"):
                        for q in page.get("Quotas", []):
                            name = q.get("QuotaName", "").lower()
                            if ("anthropic" in name or "claude" in name) and ("token" in name or "request" in name):
                                quotas.append({
                                    "name": q.get("QuotaName", ""),
                                    "value": q.get("Value", 0),
                                    "code": q.get("QuotaCode", ""),
                                })
                except Exception:
                    pass
            return quotas

        def _detect_sso():
            """检测 IAM Identity Center (Kiro 订阅)"""
            try:
                sso = self._get_client("sso-admin", "us-east-1")
                resp = sso.list_instances()
                return len(resp.get("Instances", []))
            except Exception:
                return 0

        def _detect_licenses():
            """检测 License Manager 许可证"""
            licenses = []
            try:
                lm = self._get_client("license-manager", "us-east-1")
                resp = lm.list_received_licenses(MaxResults=10)
                for lic in resp.get("Licenses", []):
                    licenses.append({
                        "name": lic.get("LicenseName", ""),
                        "product": lic.get("ProductName", ""),
                        "status": lic.get("Status", ""),
                    })
            except Exception:
                pass
            return licenses

        # 并行执行 - 无超时，等每个任务完成
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks = {
                pool.submit(_detect_bedrock_models): "models",
                pool.submit(_detect_bedrock_quotas): "quotas",
                pool.submit(_detect_sso): "sso",
                pool.submit(_detect_licenses): "licenses",
            }
            for future in as_completed(tasks):
                key = tasks[future]
                try:
                    val = future.result()
                    if key == "models":
                        result["bedrock_models"] = val
                    elif key == "quotas":
                        result["bedrock_quotas"] = val
                    elif key == "sso":
                        result["sso_instances"] = val
                    elif key == "licenses":
                        result["licenses"] = val
                except Exception as e:
                    logger.warning(f"AI detection {key} failed: {e}")

        return result


