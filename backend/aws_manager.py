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
        """并发扫描多个区域的实例详情；返回 {region: [items]}"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        if not regions:
            regions = list(REGION_DISPLAY.keys())
        out = {}
        with ThreadPoolExecutor(max_workers=min(len(regions), 16)) as pool:
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

    def _detect_primary_email(self) -> str | None:
        """通过 account:GetPrimaryEmail 获取 root 邮箱 (需要权限或 Organization)"""
        try:
            sts = self._get_client("sts")
            account_id = sts.get_caller_identity()["Account"]
            acct = self._get_client("account", "us-east-1")
            resp = acct.get_primary_email(AccountId=account_id)
            email = resp.get("PrimaryEmail", "")
            if email and "@" in email:
                return email
        except Exception as e:
            logger.debug(f"get_primary_email failed: {e}")
        return None

    def _detect_email_from_organizations(self) -> str | None:
        """从 Organizations 获取邮箱 (describe_account 或 describe_organization)"""
        try:
            sts = self._get_client("sts")
            account_id = sts.get_caller_identity()["Account"]
            org = self._get_client("organizations")
            # 先尝试 describe_account (能拿到具体账号邮箱)
            try:
                resp = org.describe_account(AccountId=account_id)
                email = resp.get("Account", {}).get("Email", "")
                if email and "@" in email:
                    return email
            except Exception:
                pass
            # 再尝试 describe_organization (拿 master 邮箱)
            try:
                resp = org.describe_organization()
                email = resp.get("Organization", {}).get("MasterAccountEmail", "")
                if email and "@" in email:
                    return email
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Organizations email failed: {e}")
        return None

    def _detect_email_from_account_contact(self) -> str | None:
        """从 Account contact information 获取邮箱或名字"""
        try:
            acct = self._get_client("account", "us-east-1")
            contact = acct.get_contact_information()
            ci = contact.get("ContactInformation", {})
            # 优先邮箱
            email = ci.get("EmailAddress", "")
            if email and "@" in email:
                return email
            # 没有邮箱就用 FullName 作为显示名
            name = ci.get("FullName", "")
            if name:
                return name
        except Exception as e:
            logger.debug(f"Account contact failed: {e}")
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

    def _make_detect_client(self, service: str, region: str = None):
        """为并行检测创建独立的 boto3 client（不使用缓存，线程安全）"""
        region = region or self.account.default_region
        slow_services = {"service-quotas", "iam", "account", "organizations", "support", "budgets", "bedrock", "sso-admin", "license-manager"}
        if service in slow_services:
            config_kwargs = {"connect_timeout": 10, "read_timeout": 30, "retries": {"max_attempts": 2}}
        else:
            config_kwargs = {"connect_timeout": 5, "read_timeout": 10, "retries": {"max_attempts": 1}}
        if self.proxy_config:
            config_kwargs["proxies"] = self.proxy_config
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
            if "proxy" in err_str.lower() or "407" in err_str or "ProxyConnectionError" in err_str:
                info["_errors"].append(f"代理连接失败: {err_str[:150]}")
                info["_proxy_error"] = True
            else:
                info["_errors"].append(f"AWS 连接失败: {err_str[:150]}")
            return info

        account_id = info["account_id"]

        # 每个并行任务用独立 client，线程安全
        def _task_primary_email():
            acct = self._make_detect_client("account", "us-east-1")
            resp = acct.get_primary_email(AccountId=account_id)
            email = resp.get("PrimaryEmail", "")
            return email if email and "@" in email else None

        def _task_org_email():
            org = self._make_detect_client("organizations")
            try:
                resp = org.describe_account(AccountId=account_id)
                email = resp.get("Account", {}).get("Email", "")
                if email and "@" in email:
                    return email
            except Exception:
                pass
            try:
                resp = org.describe_organization()
                email = resp.get("Organization", {}).get("MasterAccountEmail", "")
                if email and "@" in email:
                    return email
            except Exception:
                pass
            return None

        def _task_budgets_email():
            bgt = self._make_detect_client("budgets", "us-east-1")
            resp = bgt.describe_budgets(AccountId=account_id, MaxResults=10)
            for budget in resp.get("Budgets", []):
                try:
                    notifs = bgt.describe_notifications_for_budget(
                        AccountId=account_id, BudgetName=budget["BudgetName"]
                    )
                    for n in notifs.get("Notifications", []):
                        subs = bgt.describe_subscribers_for_notification(
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
            return None

        def _task_contact_email():
            acct = self._make_detect_client("account", "us-east-1")
            contact = acct.get_contact_information()
            ci = contact.get("ContactInformation", {})
            email = ci.get("EmailAddress", "")
            if email and "@" in email:
                return email
            name = ci.get("FullName", "")
            return name if name else None

        def _task_creation_time():
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
            resp = iam.get_credential_report()
            report = resp["Content"].decode("utf-8")
            rows = list(csv.DictReader(io.StringIO(report)))
            for row in rows:
                if ":root" in row.get("arn", ""):
                    creation_str = row.get("user_creation_time", "")
                    if creation_str and creation_str != "N/A":
                        from dateutil import parser as dateparser
                        return dateparser.parse(creation_str)
                    break
            # fallback
            try:
                user = iam.get_user()
                return user["User"].get("CreateDate")
            except Exception:
                pass
            return None

        def _task_country():
            try:
                acct = self._make_detect_client("account", "us-east-1")
                contact = acct.get_contact_information()
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
            }
            return region_country.get(region, "US")

        # 并行执行所有检测任务
        task_map = {
            "email_primary": _task_primary_email,
            "email_org": _task_org_email,
            "email_budgets": _task_budgets_email,
            "email_contact": _task_contact_email,
            "creation_time": _task_creation_time,
            "country": _task_country,
        }
        results = {}
        with ThreadPoolExecutor(max_workers=7) as pool:
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

        # 邮箱优先级: primary > org > budgets > contact(邮箱) > contact(名字)
        email = results.get("email_primary") or results.get("email_org") or results.get("email_budgets")
        contact_result = results.get("email_contact")
        if not email and contact_result and "@" in contact_result:
            email = contact_result
        if not email and contact_result:
            email = contact_result  # FullName 作为显示名
        if not email:
            email = self.account.email if (self.account.email and "@" in (self.account.email or "")) else f"root ({info['account_id']})"
        info["email"] = email
        info["register_time"] = results.get("creation_time")
        info["country"] = results.get("country") or "US"

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

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()

        return info

    # ==================== AI 配额检测 ====================

    def detect_ai_info(self, region: str = "us-east-1") -> dict:
        """简化版 AI 检测: 只查指定区域 (默认 us-east-1) 的 Bedrock Anthropic 模型 + Claude 配额。

        返回:
        {
            "region": "us-east-1",
            "bedrock_models": [...],
            "bedrock_quotas": [...],
            "bedrock_enabled": bool,    # Bedrock 是否可访问
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
                    mid = m.get("modelId", "").lower()
                    provider = m.get("providerName", "").lower()
                    if "anthropic" in provider and "claude" in mid:
                        models.append({
                            "id": m.get("modelId", ""),
                            "name": m.get("modelName", ""),
                            "provider": m.get("providerName", ""),
                            "input": m.get("inputModalities", []),
                            "output": m.get("outputModalities", []),
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
            quotas = []
            try:
                sq = self._get_client("service-quotas", region)
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
                # fallback: 默认配额
                try:
                    sq = self._get_client("service-quotas", region)
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
        """通过 Cost Explorer 查询本年的 Credit 抵扣情况。

        AWS 不开放官方 API 查询 promotional credit 余额（仅 Billing Console 可见），
        但 Cost Explorer 提供 RECORD_TYPE=Credit 维度，可以查到当年已被使用/抵扣的额度。
        我们将这部分作为"已知 Credit 抵扣总额"返回，前端会展示为"AWS Credit"。

        返回:
        {
            "year": 2026,
            "currency": "USD",
            "credits_used_ytd": 123.45,        # 本年已抵扣
            "credits_used_last_30d": 12.34,    # 近 30 天已抵扣
            "monthly": [{"month":"2026-01","amount":10.0}, ...],
            "error": null
        }
        """
        from datetime import date, timedelta
        result = {
            "year": date.today().year,
            "currency": "USD",
            "credits_used_ytd": 0.0,
            "credits_used_last_30d": 0.0,
            "monthly": [],
            "error": None,
        }
        try:
            ce = self._get_client("ce", "us-east-1")
            today = date.today()
            year_start = date(today.year, 1, 1)
            # YTD by-month
            try:
                resp = ce.get_cost_and_usage(
                    TimePeriod={"Start": str(year_start), "End": str(today)},
                    Granularity="MONTHLY",
                    Metrics=["UnblendedCost"],
                    Filter={"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit"]}},
                )
                total = 0.0
                currency = "USD"
                monthly = []
                for r in resp.get("ResultsByTime", []):
                    start = r.get("TimePeriod", {}).get("Start", "")
                    amt_obj = r.get("Total", {}).get("UnblendedCost", {})
                    amt = abs(float(amt_obj.get("Amount", 0) or 0))  # Credit 是负值，用绝对值
                    currency = amt_obj.get("Unit") or currency
                    total += amt
                    monthly.append({"month": start[:7], "amount": round(amt, 2)})
                result["currency"] = currency
                result["credits_used_ytd"] = round(total, 2)
                result["monthly"] = monthly
            except Exception as e:
                msg = str(e)
                if "AccessDenied" in msg or "not authorized" in msg:
                    result["error"] = "无 Cost Explorer 权限 (ce:GetCostAndUsage)"
                elif "DataUnavailable" in msg or "has not yet been activated" in msg:
                    result["error"] = "Cost Explorer 未启用，请在 Billing Console 启用"
                else:
                    result["error"] = f"查询失败: {msg[:160]}"

            # 近 30 天
            try:
                d_start = today - timedelta(days=30)
                resp30 = ce.get_cost_and_usage(
                    TimePeriod={"Start": str(d_start), "End": str(today)},
                    Granularity="MONTHLY",
                    Metrics=["UnblendedCost"],
                    Filter={"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit"]}},
                )
                total30 = 0.0
                for r in resp30.get("ResultsByTime", []):
                    amt = abs(float(r.get("Total", {}).get("UnblendedCost", {}).get("Amount", 0) or 0))
                    total30 += amt
                result["credits_used_last_30d"] = round(total30, 2)
            except Exception:
                pass

        except Exception as e:
            result["error"] = f"查询异常: {str(e)[:160]}"
        return result

    # ==================== 账单查询 ====================
    def get_billing(self, year: int, month: int, granularity: str = "DAILY") -> dict:
        """
        查询指定年月的账单明细。

        返回结构:
        {
            "period": {"start": "2026-05-01", "end": "2026-06-01"},
            "total": 123.45,
            "currency": "USD",
            "by_service": [{"service": "Amazon Elastic Compute Cloud - Compute", "amount": 50.12}],
            "by_region": [{"region": "us-east-1", "amount": 30.45}],
            "daily": [{"date": "2026-05-01", "amount": 4.12}],   # granularity=DAILY 时有
            "monthly_total": 123.45,
            "error": null
        }
        """
        from calendar import monthrange
        from datetime import date, timedelta

        # Cost Explorer 是全局服务，端点固定在 us-east-1
        try:
            # 参数校验
            if not (2000 <= year <= 2100):
                return {"error": "年份无效", "total": 0, "by_service": [], "by_region": [], "daily": []}
            if not (1 <= month <= 12):
                return {"error": "月份无效", "total": 0, "by_service": [], "by_region": [], "daily": []}

            # 账单 API 的 End 是 exclusive，取下个月 1 号
            start_date = date(year, month, 1)
            last_day = monthrange(year, month)[1]
            # End 不能超过 "今天"，否则 CE 会报错
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
                "currency": "USD",
                "by_service": [],
                "by_region": [],
                "daily": [],
                "monthly_total": 0.0,
                "error": None,
            }

            # 1) 按服务分组（MONTHLY，一次返回整月汇总）
            try:
                resp = ce.get_cost_and_usage(
                    TimePeriod=period,
                    Granularity="MONTHLY",
                    Metrics=["UnblendedCost"],
                    GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
                )
                svc_total = 0.0
                currency = "USD"
                svc_agg = {}
                for r in resp.get("ResultsByTime", []):
                    for g in r.get("Groups", []):
                        svc = g["Keys"][0] if g.get("Keys") else "-"
                        amt_obj = g["Metrics"].get("UnblendedCost", {})
                        amt = float(amt_obj.get("Amount", 0) or 0)
                        currency = amt_obj.get("Unit") or currency
                        svc_agg[svc] = svc_agg.get(svc, 0.0) + amt
                        svc_total += amt
                result["currency"] = currency
                result["monthly_total"] = round(svc_total, 4)
                result["total"] = round(svc_total, 4)
                result["by_service"] = sorted(
                    [{"service": k, "amount": round(v, 4)} for k, v in svc_agg.items() if v > 0],
                    key=lambda x: x["amount"], reverse=True,
                )
            except Exception as e:
                msg = str(e)
                if "AccessDenied" in msg or "not authorized" in msg:
                    result["error"] = "凭证无 Cost Explorer 权限 (ce:GetCostAndUsage)，请在 IAM 授予 AWSBillingReadOnlyAccess 或类似权限。"
                elif "DataUnavailable" in msg or "has not yet been activated" in msg:
                    result["error"] = "该账号尚未启用 Cost Explorer。请先登录 AWS 控制台 Billing → Cost Explorer 点击 'Enable Cost Explorer'，约 24h 后可查询。"
                else:
                    result["error"] = f"账单查询失败: {msg[:200]}"
                return result

            # 2) 按区域分组（MONTHLY）
            try:
                resp = ce.get_cost_and_usage(
                    TimePeriod=period,
                    Granularity="MONTHLY",
                    Metrics=["UnblendedCost"],
                    GroupBy=[{"Type": "DIMENSION", "Key": "REGION"}],
                )
                reg_agg = {}
                for r in resp.get("ResultsByTime", []):
                    for g in r.get("Groups", []):
                        reg = g["Keys"][0] if g.get("Keys") else "-"
                        amt = float(g["Metrics"].get("UnblendedCost", {}).get("Amount", 0) or 0)
                        reg_agg[reg or "-"] = reg_agg.get(reg or "-", 0.0) + amt
                result["by_region"] = sorted(
                    [{"region": k, "amount": round(v, 4)} for k, v in reg_agg.items() if v > 0],
                    key=lambda x: x["amount"], reverse=True,
                )
            except Exception as e:
                logger.warning(f"billing by region failed: {e}")

            # 3) 每日走势
            if granularity.upper() == "DAILY":
                try:
                    resp = ce.get_cost_and_usage(
                        TimePeriod=period,
                        Granularity="DAILY",
                        Metrics=["UnblendedCost"],
                    )
                    daily = []
                    for r in resp.get("ResultsByTime", []):
                        d = r.get("TimePeriod", {}).get("Start", "")
                        amt = float(r.get("Total", {}).get("UnblendedCost", {}).get("Amount", 0) or 0)
                        daily.append({"date": d, "amount": round(amt, 4)})
                    result["daily"] = daily
                except Exception as e:
                    logger.warning(f"billing daily failed: {e}")

            return result
        except Exception as e:
            return {
                "error": f"账单查询异常: {str(e)[:200]}",
                "total": 0, "by_service": [], "by_region": [], "daily": [],
            }




