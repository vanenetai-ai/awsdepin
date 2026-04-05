import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from database import get_db, init_db, SessionLocal
from models import User, AwsAccount, Instance, Proxy, DepinProject, DepinTask
from aws_manager import AwsManager
from proxy_manager import ProxyManager
from depin_manager import DepinManager
from auth import get_current_user, create_token, get_or_create_user, get_user_by_token
from telegram_bot import start_bot, stop_bot, get_bot_token, set_bot_token, is_bot_configured, verify_bot_token, restart_bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 线程池用于并发执行 AWS 等阻塞操作
executor = ThreadPoolExecutor(max_workers=50)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        dm = DepinManager(db)
        dm.init_builtin_projects()
    finally:
        db.close()
    await start_bot()
    yield
    await stop_bot()

app = FastAPI(title="AWS DePIN Manager", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ==================== Schemas ====================

class AccountCreate(BaseModel):
    name: str
    access_key_id: str
    secret_access_key: str
    default_region: str = "us-east-1"

class AccountUpdate(BaseModel):
    name: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    default_region: Optional[str] = None
    is_active: Optional[bool] = None
    note: Optional[str] = None
    group_name: Optional[str] = None

class BatchDeleteRequest(BaseModel):
    ids: list[int]

class ProxyCreate(BaseModel):
    protocol: str = "http"
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None

class LaunchRequest(BaseModel):
    account_id: int
    region: Optional[str] = None
    instance_type: str = "t3.micro"
    ami_id: Optional[str] = None

class DeployRequest(BaseModel):
    instance_id: int
    project_id: int
    config: Optional[dict] = None

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    install_script: str
    health_check_cmd: Optional[str] = None
    config_template: Optional[dict] = None

class BatchLaunchRequest(BaseModel):
    account_id: int
    region: Optional[str] = None
    instance_type: str = "t3.micro"
    count: int = 1

class BatchDeployRequest(BaseModel):
    instance_ids: list[int]
    project_id: int
    config: Optional[dict] = None

class BatchAccountCreate(BaseModel):
    text: str  # 多行文本，每行一个账号
    default_region: str = "us-east-1"

class BatchProxyCreate(BaseModel):
    text: str  # 多行文本，每行一个代理


# ==================== Auth ====================

@app.get("/api/auth/check")
async def auth_check(user: User = Depends(get_current_user)):
    return {
        "user_id": user.id,
        "telegram_id": user.telegram_id,
        "display_name": user.display_name,
        "telegram_username": user.telegram_username,
    }

@app.post("/api/auth/login")
async def auth_login(token: str = Query(...), db: Session = Depends(get_db)):
    """通过 token 登录，返回用户信息"""
    user = get_user_by_token(db, token)
    if not user:
        raise HTTPException(401, "无效或过期的登录链接")
    if not user.is_active:
        raise HTTPException(403, "账号已被禁用")
    return {
        "user_id": user.id,
        "display_name": user.display_name,
        "telegram_username": user.telegram_username,
        "token": token,
    }


# ==================== Helper: 确保资源属于当前用户 ====================

def _get_user_account(db: Session, user: User, account_id: int) -> AwsAccount:
    account = db.query(AwsAccount).filter(
        AwsAccount.id == account_id, AwsAccount.user_id == user.id
    ).first()
    if not account:
        raise HTTPException(404, "Account not found")
    return account

def _get_user_instance(db: Session, user: User, instance_id: int) -> Instance:
    instance = (
        db.query(Instance)
        .join(AwsAccount)
        .filter(Instance.id == instance_id, AwsAccount.user_id == user.id)
        .first()
    )
    if not instance:
        raise HTTPException(404, "Instance not found")
    return instance

def _get_user_proxy(db: Session, user: User, proxy_id: int) -> Proxy:
    proxy = db.query(Proxy).filter(
        Proxy.id == proxy_id, Proxy.user_id == user.id
    ).first()
    if not proxy:
        raise HTTPException(404, "Proxy not found")
    return proxy


# ==================== AWS Accounts ====================

@app.get("/api/accounts")
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from aws_manager import COUNTRY_FLAGS
    accounts = db.query(AwsAccount).filter(AwsAccount.user_id == user.id).order_by(AwsAccount.id).all()
    result = []
    for a in accounts:
        try:
            result.append({
                "id": a.id, "name": a.name, "default_region": a.default_region,
                "is_active": a.is_active, "created_at": str(a.created_at),
                "access_key_id": a.access_key_id[:16] + "..." if len(a.access_key_id) > 16 else a.access_key_id,
                "instance_count": len(a.instances),
                "email": getattr(a, 'email', '') or "",
                "aws_account_id": getattr(a, 'aws_account_id', '') or "",
                "arn": getattr(a, 'arn', '') or "",
                "register_country": getattr(a, 'register_country', '') or "",
                "country_flag": COUNTRY_FLAGS.get(getattr(a, 'register_country', '') or "", ""),
                "register_time": str(a.register_time) if getattr(a, 'register_time', None) else None,
                "added_at": str(a.added_at) if getattr(a, 'added_at', None) else str(a.created_at),
                "note": getattr(a, 'note', '') or "",
                "group_name": getattr(a, 'group_name', '') or "",
                "total_vcpus": getattr(a, 'total_vcpus', 0) or 0,
                "max_on_demand": getattr(a, 'max_on_demand', 0) or 0,
                "vcpu_data": getattr(a, 'vcpu_data', None),
            })
        except Exception as e:
            logger.error(f"Error serializing account {a.id}: {e}")
            result.append({
                "id": a.id, "name": a.name, "default_region": a.default_region,
                "is_active": a.is_active, "created_at": str(a.created_at),
                "access_key_id": a.access_key_id[:16] + "...",
                "instance_count": len(a.instances),
                "email": "", "aws_account_id": "", "arn": "",
                "register_country": "", "country_flag": "",
                "register_time": None, "added_at": str(a.created_at),
                "note": "", "group_name": "", "total_vcpus": 0, "vcpu_data": None,
            })
    return result

@app.post("/api/accounts")
async def create_account(data: AccountCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # 检查代理池是否有可用代理
    from proxy_manager import ProxyManager
    pm = ProxyManager(db)
    if not pm.get_all():
        raise HTTPException(400, "请先添加代理！账号操作必须通过代理进行")

    account = AwsAccount(user_id=user.id, **data.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    loop = asyncio.get_event_loop()

    # 验证凭证 + 自动检测账号信息 (使用代理)
    def _verify_and_detect():
        mgr = AwsManager(account, db, use_proxy=True)
        result = mgr.verify_credentials()
        if result.get("valid"):
            try:
                mgr.detect_account_info()
            except Exception as e:
                logger.warning(f"Auto detect failed for account {account.id}: {e}")
        return result

    result = await loop.run_in_executor(executor, _verify_and_detect)
    db.refresh(account)
    return {"id": account.id, "name": account.name, "verify": result}

@app.put("/api/accounts/{account_id}")
def update_account(account_id: int, data: AccountUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = _get_user_account(db, user, account_id)
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(account, k, v)
    db.commit()
    return {"ok": True}

@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = _get_user_account(db, user, account_id)
    db.delete(account)
    db.commit()
    return {"ok": True}

@app.post("/api/accounts/batch")
async def batch_create_accounts(data: BatchAccountCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量添加账号 - 并发验证检测，大幅提速"""
    # 检查代理池
    from proxy_manager import ProxyManager
    pm = ProxyManager(db)
    if not pm.get_all():
        raise HTTPException(400, "请先添加代理！账号操作必须通过代理进行")

    lines = [l.strip() for l in data.text.strip().split('\n') if l.strip()]
    created, errors = [], []
    loop = asyncio.get_event_loop()

    # 第一步：先串行创建所有账号记录（DB操作很快）
    accounts_to_verify = []
    for idx, line in enumerate(lines):
        try:
            parts = line.split()
            if len(parts) == 2:
                name = f"account-{idx+1}"
                ak, sk = parts[0], parts[1]
            elif len(parts) >= 3:
                name, ak, sk = parts[0], parts[1], parts[2]
            else:
                errors.append({"line": idx+1, "error": "格式错误，至少需要 AccessKeyId 和 SecretAccessKey"})
                continue

            account = AwsAccount(
                user_id=user.id, name=name,
                access_key_id=ak, secret_access_key=sk,
                default_region=data.default_region,
            )
            db.add(account)
            db.commit()
            db.refresh(account)
            accounts_to_verify.append((idx, name, account.id))
        except Exception as e:
            errors.append({"line": idx+1, "error": str(e)})

    # 第二步：并发验证+检测所有账号（耗时操作）
    async def _verify_one(idx, name, account_id):
        try:
            def do():
                s = SessionLocal()
                try:
                    a = s.query(AwsAccount).get(account_id)
                    mgr = AwsManager(a, s, use_proxy=True)
                    result = mgr.verify_credentials()
                    if result.get("valid"):
                        try:
                            mgr.detect_account_info()
                        except Exception:
                            pass
                    s.refresh(a)
                    return {"id": a.id, "name": a.email or name, "verify": result}
                finally:
                    s.close()
            r = await loop.run_in_executor(executor, do)
            created.append(r)
        except Exception as e:
            errors.append({"line": idx+1, "error": str(e)})

    await asyncio.gather(*[_verify_one(idx, name, aid) for idx, name, aid in accounts_to_verify])
    return {"created": created, "errors": errors}


@app.post("/api/accounts/{account_id}/verify")
async def verify_account(account_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, lambda: AwsManager(account, db).verify_credentials())
    return result

@app.get("/api/accounts/{account_id}/regions")
async def list_regions(account_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: AwsManager(account, db).list_regions())

@app.post("/api/accounts/{account_id}/detect")
async def detect_account(account_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """检测账号信息: 邮箱、注册时间、国家、ARN"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(executor, lambda: AwsManager(account, db).detect_account_info())
    db.refresh(account)
    return {
        "email": account.email, "arn": account.arn,
        "aws_account_id": account.aws_account_id,
        "register_time": str(account.register_time) if account.register_time else None,
        "register_country": account.register_country,
        "name": account.name,
    }

@app.post("/api/accounts/{account_id}/vcpus")
async def get_account_vcpus(account_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取账号各区域 vCPU 配额详情"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, lambda: AwsManager(account, db).get_vcpu_quotas_all_regions())
    # 保存到数据库
    account.total_vcpus = result["total_vcpus"]
    account.max_on_demand = result.get("max_on_demand", 0)
    account.vcpu_data = result["regions"]
    db.commit()
    return result

@app.post("/api/accounts/batch-delete")
def batch_delete_accounts(data: BatchDeleteRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量删除账号"""
    deleted = 0
    for aid in data.ids:
        account = db.query(AwsAccount).filter(AwsAccount.id == aid, AwsAccount.user_id == user.id).first()
        if account:
            db.delete(account)
            deleted += 1
    db.commit()
    return {"deleted": deleted}

@app.post("/api/accounts/detect-all")
async def detect_all_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """并发检测所有账号信息"""
    accounts = db.query(AwsAccount).filter(AwsAccount.user_id == user.id).all()
    loop = asyncio.get_event_loop()
    results, errors = [], []

    async def _detect_one(acc):
        try:
            def do():
                s = SessionLocal()
                try:
                    a = s.query(AwsAccount).get(acc.id)
                    mgr = AwsManager(a, s)
                    return mgr.detect_account_info()
                finally:
                    s.close()
            info = await loop.run_in_executor(executor, do)
            results.append({"id": acc.id, "info": info})
        except Exception as e:
            errors.append({"id": acc.id, "error": str(e)[:100]})

    await asyncio.gather(*[_detect_one(a) for a in accounts])
    return {"detected": len(results), "errors": len(errors), "results": results}

@app.get("/api/accounts/groups")
def list_account_groups(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取所有分组名称"""
    accounts = db.query(AwsAccount).filter(AwsAccount.user_id == user.id).all()
    groups = sorted(set(a.group_name for a in accounts if a.group_name))
    return groups


# ==================== Instances ====================

@app.get("/api/instances")
def list_instances(account_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Instance).join(AwsAccount).filter(AwsAccount.user_id == user.id)
    if account_id:
        q = q.filter(Instance.account_id == account_id)
    instances = q.all()
    return [
        {
            "id": i.id, "account_id": i.account_id, "instance_id": i.instance_id,
            "region": i.region, "instance_type": i.instance_type, "state": i.state,
            "public_ip": i.public_ip, "private_ip": i.private_ip,
            "created_at": str(i.created_at),
            "account_name": i.account.email or i.account.name if i.account else "",
            "task_count": len(i.depin_tasks),
            "projects": ", ".join(set(t.project.name for t in i.depin_tasks if t.project)) or "-",
        }
        for i in instances
    ]

@app.post("/api/instances/launch")
async def launch_instance(data: LaunchRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = _get_user_account(db, user, data.account_id)
    loop = asyncio.get_event_loop()
    def _launch():
        mgr = AwsManager(account, db)
        return mgr.launch_instance(region=data.region, instance_type=data.instance_type)
    instance = await loop.run_in_executor(executor, _launch)
    return {
        "id": instance.id, "instance_id": instance.instance_id,
        "region": instance.region, "state": instance.state,
    }

@app.post("/api/instances/batch-launch")
async def batch_launch(data: BatchLaunchRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量启动实例 - 并发执行"""
    account = _get_user_account(db, user, data.account_id)
    loop = asyncio.get_event_loop()
    results = []
    errors = []

    async def _launch_one(idx):
        try:
            def do():
                s = SessionLocal()
                try:
                    acc = s.query(AwsAccount).get(account.id)
                    mgr = AwsManager(acc, s)
                    inst = mgr.launch_instance(region=data.region, instance_type=data.instance_type)
                    return {"id": inst.id, "instance_id": inst.instance_id, "region": inst.region}
                finally:
                    s.close()
            r = await loop.run_in_executor(executor, do)
            results.append(r)
        except Exception as e:
            errors.append({"index": idx, "error": str(e)})

    tasks = [_launch_one(i) for i in range(data.count)]
    await asyncio.gather(*tasks)
    return {"launched": results, "errors": errors}

@app.post("/api/instances/{instance_id}/sync")
async def sync_instance(instance_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    instance = _get_user_instance(db, user, instance_id)
    loop = asyncio.get_event_loop()
    def _sync():
        mgr = AwsManager(instance.account, db)
        mgr.sync_instance(instance)
    await loop.run_in_executor(executor, _sync)
    return {"state": instance.state, "public_ip": instance.public_ip}

@app.post("/api/instances/{instance_id}/start")
async def start_instance(instance_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    instance = _get_user_instance(db, user, instance_id)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, lambda: AwsManager(instance.account, db).start_instance(instance.instance_id, instance.region))
    return {"ok": True}

@app.post("/api/instances/{instance_id}/stop")
async def stop_instance(instance_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    instance = _get_user_instance(db, user, instance_id)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, lambda: AwsManager(instance.account, db).stop_instance(instance.instance_id, instance.region))
    return {"ok": True}

@app.post("/api/instances/{instance_id}/terminate")
async def terminate_instance(instance_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    instance = _get_user_instance(db, user, instance_id)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, lambda: AwsManager(instance.account, db).terminate_instance(instance.instance_id, instance.region))
    instance.state = "terminated"
    db.commit()
    return {"ok": True}

@app.post("/api/instances/{instance_id}/reboot")
async def reboot_instance(instance_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    instance = _get_user_instance(db, user, instance_id)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, lambda: AwsManager(instance.account, db).reboot_instance(instance.instance_id, instance.region))
    return {"ok": True}

@app.post("/api/instances/sync-all")
async def sync_all_instances(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """并发同步所有实例"""
    instances = (
        db.query(Instance).join(AwsAccount)
        .filter(AwsAccount.user_id == user.id, Instance.state != "terminated")
        .all()
    )
    loop = asyncio.get_event_loop()

    async def _sync_one(inst):
        try:
            def do():
                s = SessionLocal()
                try:
                    i = s.query(Instance).get(inst.id)
                    mgr = AwsManager(i.account, s)
                    mgr.sync_instance(i)
                finally:
                    s.close()
            await loop.run_in_executor(executor, do)
        except Exception as e:
            logger.error(f"Sync {inst.instance_id} failed: {e}")

    await asyncio.gather(*[_sync_one(i) for i in instances])
    return {"synced": len(instances)}

@app.post("/api/instances/batch-start")
async def batch_start(instance_ids: list[int], user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量启动实例"""
    loop = asyncio.get_event_loop()
    results, errors = [], []

    async def _start_one(iid):
        try:
            def do():
                s = SessionLocal()
                try:
                    inst = s.query(Instance).join(AwsAccount).filter(Instance.id == iid, AwsAccount.user_id == user.id).first()
                    if not inst:
                        raise ValueError("Instance not found")
                    AwsManager(inst.account, s).start_instance(inst.instance_id, inst.region)
                    return iid
                finally:
                    s.close()
            r = await loop.run_in_executor(executor, do)
            results.append(r)
        except Exception as e:
            errors.append({"id": iid, "error": str(e)})

    await asyncio.gather(*[_start_one(i) for i in instance_ids])
    return {"started": results, "errors": errors}

@app.post("/api/instances/batch-stop")
async def batch_stop(instance_ids: list[int], user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量停止实例"""
    loop = asyncio.get_event_loop()
    results, errors = [], []

    async def _stop_one(iid):
        try:
            def do():
                s = SessionLocal()
                try:
                    inst = s.query(Instance).join(AwsAccount).filter(Instance.id == iid, AwsAccount.user_id == user.id).first()
                    if not inst:
                        raise ValueError("Instance not found")
                    AwsManager(inst.account, s).stop_instance(inst.instance_id, inst.region)
                    return iid
                finally:
                    s.close()
            r = await loop.run_in_executor(executor, do)
            results.append(r)
        except Exception as e:
            errors.append({"id": iid, "error": str(e)})

    await asyncio.gather(*[_stop_one(i) for i in instance_ids])
    return {"stopped": results, "errors": errors}

@app.delete("/api/instances/{instance_id}")
def delete_instance_record(instance_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除实例记录 (仅从数据库删除，不终止 AWS 实例)"""
    instance = _get_user_instance(db, user, instance_id)
    db.delete(instance)
    db.commit()
    return {"ok": True}

@app.post("/api/instances/batch-delete")
def batch_delete_instances(data: BatchDeleteRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量删除实例记录"""
    deleted = 0
    for iid in data.ids:
        inst = db.query(Instance).join(AwsAccount).filter(Instance.id == iid, AwsAccount.user_id == user.id).first()
        if inst:
            db.delete(inst)
            deleted += 1
    db.commit()
    return {"deleted": deleted}

@app.get("/api/instances/types")
def list_instance_types(user: User = Depends(get_current_user)):
    """返回常用 EC2 实例类型列表"""
    return [
        {"type": "t2.micro", "vcpu": 1, "mem": "1 GiB", "category": "通用"},
        {"type": "t2.small", "vcpu": 1, "mem": "2 GiB", "category": "通用"},
        {"type": "t2.medium", "vcpu": 2, "mem": "4 GiB", "category": "通用"},
        {"type": "t2.large", "vcpu": 2, "mem": "8 GiB", "category": "通用"},
        {"type": "t2.xlarge", "vcpu": 4, "mem": "16 GiB", "category": "通用"},
        {"type": "t3.micro", "vcpu": 2, "mem": "1 GiB", "category": "通用"},
        {"type": "t3.small", "vcpu": 2, "mem": "2 GiB", "category": "通用"},
        {"type": "t3.medium", "vcpu": 2, "mem": "4 GiB", "category": "通用"},
        {"type": "t3.large", "vcpu": 2, "mem": "8 GiB", "category": "通用"},
        {"type": "t3.xlarge", "vcpu": 4, "mem": "16 GiB", "category": "通用"},
        {"type": "t3.2xlarge", "vcpu": 8, "mem": "32 GiB", "category": "通用"},
        {"type": "t3a.micro", "vcpu": 2, "mem": "1 GiB", "category": "通用AMD"},
        {"type": "t3a.small", "vcpu": 2, "mem": "2 GiB", "category": "通用AMD"},
        {"type": "t3a.medium", "vcpu": 2, "mem": "4 GiB", "category": "通用AMD"},
        {"type": "t3a.large", "vcpu": 2, "mem": "8 GiB", "category": "通用AMD"},
        {"type": "t3a.xlarge", "vcpu": 4, "mem": "16 GiB", "category": "通用AMD"},
        {"type": "t3a.2xlarge", "vcpu": 8, "mem": "32 GiB", "category": "通用AMD"},
        {"type": "m5.large", "vcpu": 2, "mem": "8 GiB", "category": "内存优化"},
        {"type": "m5.xlarge", "vcpu": 4, "mem": "16 GiB", "category": "内存优化"},
        {"type": "m5.2xlarge", "vcpu": 8, "mem": "32 GiB", "category": "内存优化"},
        {"type": "m5a.large", "vcpu": 2, "mem": "8 GiB", "category": "内存AMD"},
        {"type": "m5a.xlarge", "vcpu": 4, "mem": "16 GiB", "category": "内存AMD"},
        {"type": "c5.large", "vcpu": 2, "mem": "4 GiB", "category": "计算优化"},
        {"type": "c5.xlarge", "vcpu": 4, "mem": "8 GiB", "category": "计算优化"},
        {"type": "c5.2xlarge", "vcpu": 8, "mem": "16 GiB", "category": "计算优化"},
        {"type": "c5a.large", "vcpu": 2, "mem": "4 GiB", "category": "计算AMD"},
        {"type": "c5a.xlarge", "vcpu": 4, "mem": "8 GiB", "category": "计算AMD"},
        {"type": "r5.large", "vcpu": 2, "mem": "16 GiB", "category": "大内存"},
        {"type": "r5.xlarge", "vcpu": 4, "mem": "32 GiB", "category": "大内存"},
    ]

@app.get("/api/instances/amis")
def list_amis(user: User = Depends(get_current_user)):
    """返回常用 AMI 镜像列表 (预定义)"""
    return [
        {"id": "", "name": "Ubuntu 22.04 LTS (默认)", "os": "Ubuntu", "desc": "自动选择区域对应的 Ubuntu 22.04 AMI"},
        {"id": "amazon-linux-2", "name": "Amazon Linux 2", "os": "Amazon Linux", "desc": "AWS 官方 Amazon Linux 2"},
        {"id": "amazon-linux-2023", "name": "Amazon Linux 2023", "os": "Amazon Linux", "desc": "AWS 官方 Amazon Linux 2023"},
        {"id": "debian-12", "name": "Debian 12", "os": "Debian", "desc": "Debian 12 Bookworm"},
        {"id": "ubuntu-20.04", "name": "Ubuntu 20.04 LTS", "os": "Ubuntu", "desc": "Ubuntu 20.04 Focal Fossa"},
        {"id": "ubuntu-24.04", "name": "Ubuntu 24.04 LTS", "os": "Ubuntu", "desc": "Ubuntu 24.04 Noble Numbat"},
    ]


# ==================== Proxies ====================

@app.get("/api/proxies")
def list_proxies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proxies = db.query(Proxy).filter(Proxy.user_id == user.id).all()
    return [
        {
            "id": p.id, "protocol": p.protocol, "host": p.host, "port": p.port,
            "username": p.username or "", "is_active": p.is_active,
            "last_used_at": str(p.last_used_at) if p.last_used_at else None,
        }
        for p in proxies
    ]

@app.post("/api/proxies")
def create_proxy(data: ProxyCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proxy = Proxy(user_id=user.id, **data.model_dump())
    db.add(proxy)
    db.commit()
    db.refresh(proxy)
    return {"id": proxy.id}

@app.post("/api/proxies/batch")
def batch_create_proxies(proxies: list[ProxyCreate], user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    created = []
    for data in proxies:
        proxy = Proxy(user_id=user.id, **data.model_dump())
        db.add(proxy)
        created.append(proxy)
    db.commit()
    return {"created": len(created)}

def _smart_parse_proxy(line: str) -> dict:
    """智能解析代理字符串，支持各种格式:
    host:port
    host:port:user:pass
    user:pass@host:port
    protocol://host:port
    protocol://user:pass@host:port
    protocol://host:port:user:pass
    host port (空格分隔)
    protocol host port user pass (空格分隔)
    """
    line = line.strip()
    if not line:
        raise ValueError("空行")

    protocol, host, port, username, password = "http", "", 0, None, None

    # 检测协议前缀
    if "://" in line:
        proto, rest = line.split("://", 1)
        protocol = proto.lower().replace("socks5h", "socks5")
        if protocol not in ("http", "https", "socks5", "socks4"):
            protocol = "http"
    else:
        rest = line
        # 根据常见端口猜测协议
        # 先解析完再判断

    # 处理 user:pass@host:port 格式
    if "@" in rest:
        auth_part, hostport_part = rest.rsplit("@", 1)
        if ":" in auth_part:
            username, password = auth_part.split(":", 1)
        else:
            username = auth_part
        rest = hostport_part

    # 尝试用空格分隔
    if " " in rest and ":" not in rest:
        parts = rest.split()
        if len(parts) >= 2:
            host = parts[0]
            port = int(parts[1])
            if len(parts) >= 4 and not username:
                username, password = parts[2], parts[3]
            return {"protocol": protocol, "host": host, "port": port, "username": username, "password": password}

    # 用冒号分隔
    parts = rest.split(":")
    if len(parts) == 2:
        host, port = parts[0], int(parts[1])
    elif len(parts) == 3:
        # 可能是 host:port:user 或 ip:port:something
        host, port = parts[0], int(parts[1])
        username = parts[2]
    elif len(parts) == 4:
        host, port = parts[0], int(parts[1])
        username, password = parts[2], parts[3]
    elif len(parts) >= 5:
        # host:port:user:pass:protocol 或其他
        host, port = parts[0], int(parts[1])
        username, password = parts[2], parts[3]
        if parts[4].lower() in ("http", "https", "socks5", "socks4"):
            protocol = parts[4].lower()
    else:
        raise ValueError(f"无法解析: {line}")

    if not host or port <= 0:
        raise ValueError(f"无效的地址或端口: {line}")

    # 根据端口猜测协议 (如果没有显式指定)
    if "://" not in line:
        if port in (1080, 1081, 7890, 7891):
            protocol = "socks5"
        elif port in (443, 8443):
            protocol = "https"

    return {"protocol": protocol, "host": host, "port": port, "username": username, "password": password}


@app.post("/api/proxies/batch-text")
def batch_create_proxies_text(data: BatchProxyCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量添加代理 - 智能识别各种格式"""
    lines = [l.strip() for l in data.text.strip().split('\n') if l.strip()]
    created, errors = [], []

    for idx, line in enumerate(lines):
        try:
            parsed = _smart_parse_proxy(line)
            proxy = Proxy(user_id=user.id, **parsed)
            db.add(proxy)
            created.append({"host": parsed["host"], "port": parsed["port"], "protocol": parsed["protocol"]})
        except Exception as e:
            errors.append({"line": idx+1, "error": str(e), "text": line[:50]})

    if created:
        db.commit()
    return {"created": len(created), "errors": errors}


@app.post("/api/proxies/{proxy_id}/test")
async def test_proxy(proxy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """测试代理是否可用，返回出口IP"""
    proxy = _get_user_proxy(db, user, proxy_id)
    loop = asyncio.get_event_loop()

    def _test():
        import httpx as hx
        auth = ""
        if proxy.username and proxy.password:
            auth = f"{proxy.username}:{proxy.password}@"
        proxy_url = f"{proxy.protocol}://{auth}{proxy.host}:{proxy.port}"
        try:
            with hx.Client(proxies={"http://": proxy_url, "https://": proxy_url}, timeout=15) as client:
                resp = client.get("https://api.ipify.org?format=json")
                ip = resp.json().get("ip", "unknown")
                return {"ok": True, "ip": ip, "proxy": f"{proxy.host}:{proxy.port}"}
        except Exception as e:
            return {"ok": False, "error": str(e), "proxy": f"{proxy.host}:{proxy.port}"}

    return await loop.run_in_executor(executor, _test)


@app.post("/api/proxies/test-all")
async def test_all_proxies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量测试所有代理"""
    proxies = db.query(Proxy).filter(Proxy.user_id == user.id, Proxy.is_active == True).all()
    loop = asyncio.get_event_loop()
    results = []

    async def _test_one(p):
        def do():
            import httpx as hx
            auth = ""
            if p.username and p.password:
                auth = f"{p.username}:{p.password}@"
            proxy_url = f"{p.protocol}://{auth}{p.host}:{p.port}"
            try:
                with hx.Client(proxies={"http://": proxy_url, "https://": proxy_url}, timeout=10) as client:
                    resp = client.get("https://api.ipify.org?format=json")
                    ip = resp.json().get("ip", "unknown")
                    return {"id": p.id, "ok": True, "ip": ip}
            except Exception as e:
                return {"id": p.id, "ok": False, "error": str(e)[:80]}
        r = await loop.run_in_executor(executor, do)
        results.append(r)

    await asyncio.gather(*[_test_one(p) for p in proxies])
    return {"results": results, "total": len(proxies), "ok": sum(1 for r in results if r["ok"])}


@app.delete("/api/proxies/{proxy_id}")
def delete_proxy(proxy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proxy = _get_user_proxy(db, user, proxy_id)
    db.delete(proxy)
    db.commit()
    return {"ok": True}

@app.put("/api/proxies/{proxy_id}/toggle")
def toggle_proxy(proxy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proxy = _get_user_proxy(db, user, proxy_id)
    proxy.is_active = not proxy.is_active
    db.commit()
    return {"is_active": proxy.is_active}


# ==================== DePIN Projects (全局共享) ====================

@app.get("/api/projects")
def list_projects(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    projects = db.query(DepinProject).all()
    return [
        {
            "id": p.id, "name": p.name, "description": p.description,
            "is_active": p.is_active, "config_template": p.config_template,
        }
        for p in projects
    ]

@app.post("/api/projects")
def create_project(data: ProjectCreate, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    project = DepinProject(**data.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name}

@app.get("/api/projects/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    project = db.query(DepinProject).get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return {
        "id": project.id, "name": project.name, "description": project.description,
        "install_script": project.install_script, "health_check_cmd": project.health_check_cmd,
        "config_template": project.config_template,
    }


# ==================== DePIN Tasks ====================

@app.get("/api/tasks")
def list_tasks(instance_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(DepinTask).join(Instance).join(AwsAccount).filter(AwsAccount.user_id == user.id)
    if instance_id:
        q = q.filter(DepinTask.instance_id == instance_id)
    tasks = q.all()
    return [
        {
            "id": t.id, "instance_id": t.instance_id, "project_id": t.project_id,
            "status": t.status, "config": t.config, "log": t.log,
            "created_at": str(t.created_at),
            "project_name": t.project.name if t.project else "",
            "instance_ip": t.instance.public_ip if t.instance else "",
        }
        for t in tasks
    ]

@app.post("/api/tasks/deploy")
async def deploy_task(data: DeployRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    instance = _get_user_instance(db, user, data.instance_id)
    project = db.query(DepinProject).get(data.project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    loop = asyncio.get_event_loop()
    def _deploy():
        mgr = AwsManager(instance.account, db)
        dm = DepinManager(db)
        return dm.deploy_project(mgr, instance, project, data.config)
    task = await loop.run_in_executor(executor, _deploy)
    return {"id": task.id, "status": task.status, "log": task.log}

@app.post("/api/tasks/batch-deploy")
async def batch_deploy(data: BatchDeployRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量部署 - 并发执行"""
    project = db.query(DepinProject).get(data.project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    loop = asyncio.get_event_loop()
    results, errors = [], []

    async def _deploy_one(iid):
        try:
            def do():
                s = SessionLocal()
                try:
                    inst = s.query(Instance).join(AwsAccount).filter(Instance.id == iid, AwsAccount.user_id == user.id).first()
                    if not inst:
                        raise ValueError("Instance not found")
                    proj = s.query(DepinProject).get(data.project_id)
                    mgr = AwsManager(inst.account, s)
                    dm = DepinManager(s)
                    t = dm.deploy_project(mgr, inst, proj, data.config)
                    return {"task_id": t.id, "instance_id": iid, "status": t.status}
                finally:
                    s.close()
            r = await loop.run_in_executor(executor, do)
            results.append(r)
        except Exception as e:
            errors.append({"instance_id": iid, "error": str(e)})

    await asyncio.gather(*[_deploy_one(i) for i in data.instance_ids])
    return {"deployed": results, "errors": errors}

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除部署任务"""
    task = (
        db.query(DepinTask).join(Instance).join(AwsAccount)
        .filter(DepinTask.id == task_id, AwsAccount.user_id == user.id)
        .first()
    )
    if not task:
        raise HTTPException(404, "Task not found")
    db.delete(task)
    db.commit()
    return {"ok": True}

@app.post("/api/tasks/{task_id}/health")
async def check_task_health(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = (
        db.query(DepinTask).join(Instance).join(AwsAccount)
        .filter(DepinTask.id == task_id, AwsAccount.user_id == user.id)
        .first()
    )
    if not task:
        raise HTTPException(404, "Task not found")
    instance = db.query(Instance).get(task.instance_id)
    loop = asyncio.get_event_loop()
    def _check():
        mgr = AwsManager(instance.account, db)
        dm = DepinManager(db)
        return dm.check_health(mgr, task)
    return await loop.run_in_executor(executor, _check)


# ==================== Dashboard ====================

@app.get("/api/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts = db.query(AwsAccount).filter(AwsAccount.user_id == user.id).count()
    instances = db.query(Instance).join(AwsAccount).filter(AwsAccount.user_id == user.id).count()
    running = db.query(Instance).join(AwsAccount).filter(AwsAccount.user_id == user.id, Instance.state == "running").count()
    proxies = db.query(Proxy).filter(Proxy.user_id == user.id, Proxy.is_active == True).count()
    tasks = db.query(DepinTask).join(Instance).join(AwsAccount).filter(AwsAccount.user_id == user.id).count()
    tasks_running = db.query(DepinTask).join(Instance).join(AwsAccount).filter(AwsAccount.user_id == user.id, DepinTask.status == "running").count()
    return {
        "accounts": accounts,
        "instances": instances,
        "instances_running": running,
        "proxies_active": proxies,
        "tasks": tasks,
        "tasks_running": tasks_running,
    }


# ==================== Admin ====================

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")


from fastapi import Request as _Request

async def get_admin(request: _Request):
    """验证管理员密码（通过 header 或 query）"""
    pwd = request.headers.get("X-Admin-Password", "") or request.query_params.get("admin_pwd", "")
    if pwd != ADMIN_PASSWORD:
        raise HTTPException(403, "管理员密码错误")
    return True


@app.get("/api/admin/stats")
def admin_stats(_=Depends(get_admin), db: Session = Depends(get_db)):
    users = db.query(User).count()
    accounts = db.query(AwsAccount).count()
    instances = db.query(Instance).count()
    running = db.query(Instance).filter(Instance.state == "running").count()
    tasks = db.query(DepinTask).count()
    return {
        "users": users, "accounts": accounts,
        "instances": instances, "instances_running": running,
        "tasks": tasks,
        "bot_configured": is_bot_configured(),
        "bot_token_masked": ("***" + get_bot_token()[-6:]) if get_bot_token() else "",
    }


@app.get("/api/admin/users")
def admin_list_users(_=Depends(get_admin), db: Session = Depends(get_db)):
    users = db.query(User).all()
    return [
        {
            "id": u.id, "telegram_id": u.telegram_id,
            "telegram_username": u.telegram_username,
            "display_name": u.display_name,
            "is_active": u.is_active,
            "created_at": str(u.created_at),
            "last_login_at": str(u.last_login_at) if u.last_login_at else None,
            "account_count": len(u.accounts),
        }
        for u in users
    ]


@app.put("/api/admin/users/{user_id}/toggle")
def admin_toggle_user(user_id: int, _=Depends(get_admin), db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    user.is_active = not user.is_active
    db.commit()
    return {"is_active": user.is_active}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, _=Depends(get_admin), db: Session = Depends(get_db)):
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    db.delete(user)
    db.commit()
    return {"ok": True}


class BotTokenUpdate(BaseModel):
    token: str


@app.get("/api/admin/bot")
def admin_get_bot(_=Depends(get_admin)):
    return {
        "configured": is_bot_configured(),
        "token_masked": ("***" + get_bot_token()[-6:]) if get_bot_token() else "",
    }


@app.post("/api/admin/bot")
async def admin_set_bot(data: BotTokenUpdate, _=Depends(get_admin)):
    if data.token:
        result = await verify_bot_token(data.token)
        if not result.get("valid"):
            raise HTTPException(400, f"Bot token 无效: {result.get('error')}")
        set_bot_token(data.token)
        await restart_bot()
        return {"ok": True, "bot": result}
    else:
        set_bot_token("")
        await stop_bot()
        return {"ok": True, "bot": None}


@app.post("/api/admin/bot/verify")
async def admin_verify_bot(data: BotTokenUpdate, _=Depends(get_admin)):
    return await verify_bot_token(data.token)


@app.post("/api/admin/login")
def admin_login(password: str = Query(...)):
    if password != ADMIN_PASSWORD:
        raise HTTPException(403, "密码错误")
    return {"ok": True}


# ==================== Static Files (本地开发用) ====================

frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
