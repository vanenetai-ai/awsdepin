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

    def get_account_email_and_arn(self) -> dict:
        """获取账号邮箱、ARN、账号ID - 多种方法尝试"""
        result = {"email": None, "arn": None, "account_id": None}
        try:
            sts = self._get_client("sts")
            identity = sts.get_caller_identity()
            result["arn"] = identity.get("Arn", "")
            result["account_id"] = identity.get("Account", "")
        except Exception as e:
            logger.error(f"STS failed: {e}")
            return result

        # 方法1: Account get-contact-information (最直接，需要账单权限)
        try:
            account_client = self._get_client("account", "us-east-1")
            contact = account_client.get_contact_information()
            email = contact.get("ContactInformation", {}).get("EmailAddress", "")
            if email:
                result["email"] = email
        except Exception:
            pass

        # 方法2: Organizations 获取邮箱 (需要 org 权限)
        if not result["email"]:
            try:
                org = self._get_client("organizations")
                acct = org.describe_account(AccountId=result["account_id"])
                email = acct["Account"].get("Email", "")
                if email:
                    result["email"] = email
            except Exception:
                pass

        # 方法3: IAM 账号别名
        if not result["email"]:
            try:
                iam = self._get_client("iam")
                aliases = iam.list_account_aliases().get("AccountAliases", [])
                if aliases:
                    result["email"] = aliases[0]
            except Exception:
                pass

        # 方法4: IAM credential report 获取 root 邮箱
        if not result["email"]:
            try:
                import time, csv, io
                iam = self._get_client("iam")
                try:
                    iam.generate_credential_report()
                except Exception:
                    pass
                time.sleep(3)  # 等待报告生成
                resp = iam.get_credential_report()
                report = resp["Content"].decode("utf-8")
                reader = csv.DictReader(io.StringIO(report))
                for row in reader:
                    user = row.get("user", "")
                    arn_val = row.get("arn", "")
                    # root 账号的 user 列就是邮箱地址
                    if ":root" in arn_val or user == "<root_account>":
                        # 某些报告中 user 列直接是邮箱
                        if "@" in user:
                            result["email"] = user
                            break
                    # 也检查其他用户行中是否有邮箱格式的 user
                    elif "@" in user:
                        result["email"] = user
                        break
            except Exception:
                pass

        # 方法4: 从 ARN 推断
        if not result["email"] and result["arn"]:
            arn = result["arn"]
            if ":user/" in arn:
                result["email"] = arn.split(":user/")[-1]
            elif ":root" in arn:
                # root 密钥，用账号ID作为标识
                result["email"] = f"root ({result['account_id']})"

        # 方法5: 用 access key 前缀 + 账号ID 作为 fallback
        if not result["email"]:
            result["email"] = f"{self.account.access_key_id[:8]}...({result['account_id']})"

        return result

    def get_account_creation_time(self) -> datetime | None:
        """获取 AWS 账号创建时间 (通过最早的 IAM 用户或 trail)"""
        try:
            # 方法1: 通过 IAM credential report 获取 root 账号创建时间
            iam = self._get_client("iam")
            # 获取用户列表，root 的 CreateDate 就是账号创建时间
            users = iam.list_users(MaxItems=1)
            if users.get("Users"):
                return users["Users"][0].get("CreateDate")
        except Exception:
            pass

        try:
            # 方法2: 获取当前用户的创建时间
            iam = self._get_client("iam")
            user = iam.get_user()
            return user["User"].get("CreateDate")
        except Exception:
            pass

        return None

    def get_account_country(self) -> str:
        """尝试检测账号注册国家"""
        # 方法1: 通过 account contact information 获取国家代码
        try:
            account_client = self._get_client("account", "us-east-1")
            contact = account_client.get_contact_information()
            country = contact.get("ContactInformation", {}).get("CountryCode", "")
            if country:
                return country
        except Exception:
            pass

        # 方法2: 通过默认区域推断国家
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
        """并发获取所有区域的 vCPU 配额 (19线程并发, 每区域15s超时)"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        regions = list(REGION_DISPLAY.keys())
        regions_data = {}
        total_vcpus = 0

        with ThreadPoolExecutor(max_workers=19) as pool:
            futures = {pool.submit(self._get_region_vcpu, r): r for r in regions}
            try:
                for future in as_completed(futures, timeout=60):
                    region = futures[future]
                    try:
                        data = future.result(timeout=15)
                        regions_data[region] = data
                        total_vcpus += data["on_demand_limit"]
                    except Exception:
                        regions_data[region] = {
                            "display": REGION_DISPLAY.get(region, region),
                            "on_demand_limit": 5, "on_demand_usage": 0,
                            "spot_limit": 5, "spot_usage": 0,
                        }
                        total_vcpus += 5
            except Exception:
                # 超时的区域填默认值
                for r in regions:
                    if r not in regions_data:
                        regions_data[r] = {
                            "display": REGION_DISPLAY.get(r, r),
                            "on_demand_limit": 5, "on_demand_usage": 0,
                            "spot_limit": 5, "spot_usage": 0,
                        }
                        total_vcpus += 5

        # max_on_demand = 单区域最高 on_demand_limit
        max_on_demand = max((d["on_demand_limit"] for d in regions_data.values()), default=5)
        return {"regions": regions_data, "total_vcpus": total_vcpus, "max_on_demand": max_on_demand}

    def detect_account_info(self) -> dict:
        """一次性检测账号所有信息并更新数据库"""
        info = {}

        # 1. 邮箱/ARN/账号ID
        email_info = self.get_account_email_and_arn()
        info.update(email_info)

        # 2. 注册时间
        reg_time = self.get_account_creation_time()
        info["register_time"] = reg_time

        # 3. 国家
        info["country"] = self.get_account_country()

        # 4. 更新数据库
        if email_info.get("email"):
            self.account.email = email_info["email"]
            self.account.name = email_info["email"]  # 用邮箱作为名称
        if email_info.get("account_id"):
            self.account.aws_account_id = email_info["account_id"]
        if email_info.get("arn"):
            self.account.arn = email_info["arn"]
        if reg_time:
            self.account.register_time = reg_time
        self.account.register_country = info["country"]

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()

        return info
