import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://depin:depin123@localhost:5432/depin")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    os.makedirs("data", exist_ok=True)
    connect_args = {"check_same_thread": False}

# 连接池: 与 main.py 的 ThreadPoolExecutor(max_workers=30) + Semaphore(20) 对齐.
# pool_size + max_overflow >= worker 上限, 否则批量并发时会 QueuePool overflow.
# pool_pre_ping 在拿连接前先发 SELECT 1 探活, 防止 PG 服务端踢掉空闲连接后取到死连接.
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_size=30,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,  # 30 分钟回收一次, 避免代理/防火墙 idle timeout
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from models import Base as ModelBase
    ModelBase.metadata.create_all(bind=engine)
    _migrate_db()


def _migrate_db():
    """自动添加缺失的列 (兼容已有数据库)"""
    from sqlalchemy import inspect, text
    insp = inspect(engine)

    # 定义需要检查的新列: (表名, 列名, SQL类型默认值)
    new_columns = [
        ("aws_accounts", "email", "VARCHAR(200)"),
        ("aws_accounts", "aws_account_id", "VARCHAR(20)"),
        ("aws_accounts", "arn", "VARCHAR(300)"),
        ("aws_accounts", "register_country", "VARCHAR(10)"),
        ("aws_accounts", "register_time", "TIMESTAMP"),
        ("aws_accounts", "added_at", "TIMESTAMP DEFAULT NOW()"),
        ("aws_accounts", "note", "TEXT DEFAULT ''"),
        ("aws_accounts", "group_name", "VARCHAR(100) DEFAULT ''"),
        ("aws_accounts", "total_vcpus", "INTEGER DEFAULT 0"),
        ("aws_accounts", "max_on_demand", "INTEGER DEFAULT 0"),
        ("aws_accounts", "total_usage", "INTEGER DEFAULT 0"),
        ("aws_accounts", "vcpu_data", "JSON" if not DATABASE_URL.startswith("postgres") else "JSONB"),
        ("aws_accounts", "account_status", "VARCHAR(30) DEFAULT 'unknown'"),
        ("aws_accounts", "status_reason", "TEXT DEFAULT ''"),
        ("aws_accounts", "status_checked_at", "TIMESTAMP"),
        ("instances", "private_key", "TEXT"),
        # Proxy 健康追踪 (新增于 P5 修复)
        ("proxies", "fail_count", "INTEGER DEFAULT 0"),
        ("proxies", "last_check_at", "TIMESTAMP"),
        ("proxies", "last_check_ok", "BOOLEAN"),
        ("proxies", "last_check_ip", "VARCHAR(50)"),
        ("proxies", "last_error", "TEXT"),
    ]

    for table, column, col_type in new_columns:
        if table in insp.get_table_names():
            existing = [c["name"] for c in insp.get_columns(table)]
            if column not in existing:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}'))
                    print(f"  [migrate] Added column {table}.{column}")
                except Exception as e:
                    print(f"  [migrate] Skip {table}.{column}: {e}")

    # 创建缺失的索引 (加速查询)
    indexes = [
        ("instances", "ix_instances_account_id", "account_id"),
        ("instances", "ix_instances_instance_id", "instance_id"),
        ("instances", "ix_instances_state", "state"),
        ("depin_tasks", "ix_depin_tasks_instance_id", "instance_id"),
        ("depin_tasks", "ix_depin_tasks_project_id", "project_id"),
        ("depin_tasks", "ix_depin_tasks_status", "status"),
    ]
    for table, idx_name, column in indexes:
        if table in insp.get_table_names():
            try:
                with engine.begin() as conn:
                    conn.execute(text(f'CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})'))
            except Exception:
                pass  # 索引已存在或不支持 IF NOT EXISTS
