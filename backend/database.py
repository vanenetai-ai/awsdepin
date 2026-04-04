import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://depin:depin123@localhost:5432/depin")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    os.makedirs("data", exist_ok=True)
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_size=20, max_overflow=10)
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
        ("aws_accounts", "vcpu_data", "JSON" if not DATABASE_URL.startswith("postgres") else "JSONB"),
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
