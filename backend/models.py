from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, JSON, BigInteger
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    telegram_username = Column(String(200))
    display_name = Column(String(200))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime)

    accounts = relationship("AwsAccount", back_populates="user", cascade="all, delete-orphan")
    proxies = relationship("Proxy", back_populates="user", cascade="all, delete-orphan")
    auth_tokens = relationship("AuthToken", back_populates="user", cascade="all, delete-orphan")


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String(200), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)
    is_active = Column(Boolean, default=True)

    user = relationship("User", back_populates="auth_tokens")


class AwsAccount(Base):
    __tablename__ = "aws_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    access_key_id = Column(String(200), nullable=False)
    secret_access_key = Column(String(200), nullable=False)
    default_region = Column(String(50), default="us-east-1")
    is_active = Column(Boolean, default=True)

    # 新增: 账号详情字段
    email = Column(String(200))           # AWS 账号邮箱 (用作显示名)
    aws_account_id = Column(String(20))   # AWS 12位账号ID
    arn = Column(String(300))             # IAM ARN
    register_country = Column(String(10)) # 注册国家代码 (如 US, CN)
    register_time = Column(DateTime)      # AWS 账号注册/创建时间
    added_at = Column(DateTime, default=datetime.utcnow)  # 加入本平台时间
    note = Column(Text, default="")       # 备注
    group_name = Column(String(100), default="")  # 分组名称
    total_vcpus = Column(Integer, default=0)      # 总 vCPU 配额
    max_on_demand = Column(Integer, default=0)    # 单区域最高 On-Demand vCPU 限制
    total_usage = Column(Integer, default=0)      # 总 vCPU 使用量
    vcpu_data = Column(JSON)              # 各区域 vCPU 详情 JSON

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="accounts")
    instances = relationship("Instance", back_populates="account", cascade="all, delete-orphan")


class Instance(Base):
    __tablename__ = "instances"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("aws_accounts.id"), nullable=False, index=True)
    instance_id = Column(String(50), index=True)
    region = Column(String(50))
    instance_type = Column(String(50), default="t3.micro")
    state = Column(String(30), default="unknown", index=True)
    public_ip = Column(String(50))
    private_ip = Column(String(50))
    key_name = Column(String(100))
    private_key = Column(Text)  # PEM 私钥，用于 SSH 连接
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("AwsAccount", back_populates="instances")
    depin_tasks = relationship("DepinTask", back_populates="instance", cascade="all, delete-orphan")


class Proxy(Base):
    __tablename__ = "proxies"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    protocol = Column(String(10), default="http")
    host = Column(String(200), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(100))
    password = Column(String(200))
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="proxies")


class DepinProject(Base):
    __tablename__ = "depin_projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    install_script = Column(Text, nullable=False)
    health_check_cmd = Column(Text)
    config_template = Column(JSON)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DepinTask(Base):
    __tablename__ = "depin_tasks"

    id = Column(Integer, primary_key=True, index=True)
    instance_id = Column(Integer, ForeignKey("instances.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("depin_projects.id"), nullable=False, index=True)
    status = Column(String(30), default="pending", index=True)  # pending/installing/running/failed/stopped
    config = Column(JSON)
    log = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    instance = relationship("Instance", back_populates="depin_tasks")
    project = relationship("DepinProject")
