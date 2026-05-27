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
from aws_manager import AwsManager, ProxyRequiredError
from lightsail_manager import LightsailManager, LIGHTSAIL_REGIONS
from proxy_manager import ProxyManager, PYSOCKS_AVAILABLE
from depin_manager import DepinManager
from auth import get_current_user, create_token, get_or_create_user, get_user_by_token
from telegram_bot import start_bot, stop_bot, get_bot_token, set_bot_token, is_bot_configured, verify_bot_token, restart_bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# P4 修复: 线程池 / 数据库连接池 / 批量并发 三者必须对齐, 否则会 QueuePool overflow.
# database.py: pool_size=30, max_overflow=20 (合计 50 个 connection)
# executor: 30 worker  ← 主力并发
# BATCH_SEMAPHORE: 20  ← batch 端点 (留 10 给单点请求, 避免饿死)
executor = ThreadPoolExecutor(max_workers=30)
BATCH_SEMAPHORE = asyncio.Semaphore(20)


async def _batch_run(coros: list):
    """对一组协程做带 Semaphore 限并发的 asyncio.gather, 防止打爆数据库/代理."""
    async def _bounded(c):
        async with BATCH_SEMAPHORE:
            return await c
    return await asyncio.gather(*[_bounded(c) for c in coros])


# 后台调度器: 代理健康检查 (P5 修复)
_scheduler = None


def _proxy_health_check_job():
    """每 10 分钟跑一次: 遍历所有 active 代理, ping api.ipify.org 写健康状态."""
    s = SessionLocal()
    try:
        proxies = s.query(Proxy).filter(Proxy.is_active == True).all()  # noqa: E712
        for p in proxies:
            ok, ip, err = ProxyManager.check_one(p, timeout=10)
            ProxyManager.report_health_check(p.id, ok, ip, err)
        logger.info(f"[proxy-health] checked {len(proxies)} proxies")
    except Exception as e:
        logger.error(f"[proxy-health] job failed: {e}")
    finally:
        s.close()


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

    # P5: 启动代理健康检查后台 job
    global _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(_proxy_health_check_job, "interval", minutes=10, id="proxy-health", coalesce=True, max_instances=1)
        _scheduler.start()
        logger.info("Proxy health check scheduler started (every 10 min)")
    except Exception as e:
        logger.error(f"Failed to start proxy health scheduler: {e}")

    if not PYSOCKS_AVAILABLE:
        logger.warning(
            "=" * 60 + "\n"
            "WARNING: PySocks 未安装! socks5 代理会被拒用 (避免静默 IP 泄漏).\n"
            "如果你有 socks5 代理 (例如 IPRoyal), 请执行:\n"
            "    pip install PySocks==1.7.1\n"
            "然后重启服务.\n"
            + "=" * 60
        )

    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)
    await stop_bot()

app = FastAPI(title="AWS DePIN Manager", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ==================== 全局异常处理 ====================

@app.exception_handler(ProxyRequiredError)
async def _proxy_required_handler(request, exc: ProxyRequiredError):
    """没代理时直接拒绝调 AWS, 把异常转成 400 让前端提示用户去添加代理.

    设计原则: 所有 AWS API 调用必须走代理, 绝不允许 fallback 到服务器出口 IP,
    避免暴露真实服务器 IP 给 AWS 风控关联多个账号。
    """
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc) or "代理池为空, 请先到「代理管理」添加代理"},
    )


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
    volume_size: int = 20
    volume_type: str = "gp3"

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
    volume_size: int = 20
    volume_type: str = "gp3"

class BatchDeployRequest(BaseModel):
    instance_ids: list[int]
    project_id: int
    config: Optional[dict] = None

class BatchAccountCreate(BaseModel):
    text: str  # 多行文本，每行一个账号
    default_region: str = "us-east-1"

class BatchProxyCreate(BaseModel):
    text: str  # 多行文本，每行一个代理
    default_protocol: Optional[str] = "auto"   # "auto" / "http" / "https" / "socks5"


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
                "access_key_id": a.access_key_id,
                "secret_access_key": a.secret_access_key,
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
                "total_usage": getattr(a, 'total_usage', 0) or 0,
                "vcpu_data": getattr(a, 'vcpu_data', None),
                "account_status": getattr(a, 'account_status', 'unknown') or 'unknown',
                "status_reason": getattr(a, 'status_reason', '') or '',
                "status_checked_at": str(a.status_checked_at) if getattr(a, 'status_checked_at', None) else None,
            })
        except Exception as e:
            logger.error(f"Error serializing account {a.id}: {e}")
            result.append({
                "id": a.id, "name": a.name, "default_region": a.default_region,
                "is_active": a.is_active, "created_at": str(a.created_at),
                "access_key_id": a.access_key_id,
                "secret_access_key": a.secret_access_key,
                "instance_count": len(a.instances),
                "email": "", "aws_account_id": "", "arn": "",
                "register_country": "", "country_flag": "",
                "register_time": None, "added_at": str(a.created_at),
                "note": "", "group_name": "", "total_vcpus": 0, "vcpu_data": None,
            })
    return result

@app.post("/api/accounts")
async def create_account(data: AccountCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # 检查代理池是否有可用代理 (按当前登录用户隔离, 避免用到别人的代理)
    pm = ProxyManager(db, user_id=user.id)
    if not pm.get_all():
        raise HTTPException(400, "请先在「代理管理」添加代理。所有 AWS 调用都会经过你自己的代理, 防止泄露服务器真实 IP。")

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
    # 检查代理池 (按当前用户隔离)
    pm = ProxyManager(db, user_id=user.id)
    if not pm.get_all():
        raise HTTPException(400, "请先在「代理管理」添加代理。所有 AWS 调用都会经过你自己的代理, 防止泄露服务器真实 IP。")

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

    await _batch_run([_verify_one(idx, name, aid) for idx, name, aid in accounts_to_verify])
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


@app.post("/api/accounts/{account_id}/enable-all-regions")
async def enable_all_regions(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通过 AK/SK 直接启用账号下所有 opt-in 区域 (调 account:EnableRegion API)。

    需要凭证有 account:EnableRegion 权限 (AdministratorAccess / 根用户默认有)。
    返回每个区域的启用结果。
    """
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(executor, lambda: AwsManager(account, db).enable_all_regions())
    except Exception as e:
        raise HTTPException(400, f"启用区域失败: {str(e)[:300]}")
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


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
        "max_on_demand": getattr(account, 'max_on_demand', 0) or 0,
        "total_usage": getattr(account, 'total_usage', 0) or 0,
        "_errors": info.get("_errors", []),
        "_proxy_error": info.get("_proxy_error", False),
    }

@app.post("/api/accounts/{account_id}/detect-ai")
async def detect_ai(
    account_id: int,
    region: str = Query("us-east-1", description="检测的区域，默认 us-east-1"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """检测账号 AI 能力: 只查指定区域 (默认 us-east-1) 的 Bedrock Anthropic 模型 + Claude 配额"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, lambda: AwsManager(account, db).detect_ai_info(region=region))
    return result


class ClaudeInvokeRequest(BaseModel):
    prompt: str = "你好"
    model_id: Optional[str] = None
    region: str = "us-east-1"
    max_tokens: int = 256


@app.post("/api/accounts/{account_id}/bedrock/invoke")
async def invoke_claude(
    account_id: int,
    data: ClaudeInvokeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通过 Bedrock 调用 Claude 模型 - 在面板上直接试聊"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        executor,
        lambda: AwsManager(account, db).invoke_claude(
            prompt=data.prompt,
            model_id=data.model_id,
            region=data.region,
            max_tokens=data.max_tokens,
        ),
    )
    return result


@app.post("/api/accounts/{account_id}/vcpus")
async def get_account_vcpus(account_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取账号各区域 vCPU 配额详情"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, lambda: AwsManager(account, db).get_vcpu_quotas_all_regions())
    # 保存到数据库
    account.total_vcpus = result["total_vcpus"]
    account.max_on_demand = result.get("max_on_demand", 0)
    account.total_usage = result.get("total_usage", 0)
    account.vcpu_data = result["regions"]
    db.commit()
    return result


@app.get("/api/accounts/{account_id}/credits")
async def get_account_credits(
    account_id: int,
    _ts: Optional[int] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查询账号 AWS Credit (本年已抵扣 + 近 30 天)"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        executor,
        lambda: AwsManager(account, db).get_credit_summary(),
    )
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/api/accounts/{account_id}/billing")
async def get_account_billing(
    account_id: int,
    year: int = Query(..., ge=2000, le=2100, description="年份，例如 2026"),
    month: int = Query(..., ge=1, le=12, description="月份，1-12"),
    granularity: str = Query("DAILY", description="DAILY 或 MONTHLY"),
    _ts: Optional[int] = Query(None, description="客户端时间戳，仅用于绕过缓存"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查询指定账号某年某月的账单消费明细 (Cost Explorer) - 不缓存，每次都重新查询"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        executor,
        lambda: AwsManager(account, db).get_billing(year=year, month=month, granularity=granularity),
    )
    # 强制禁止任何中间层 (浏览器/nginx/CDN) 缓存账单结果
    return JSONResponse(
        content=result,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/accounts/{account_id}/permissions")
async def diagnose_account_permissions(
    account_id: int,
    _ts: Optional[int] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """诊断账号 AK/SK 拥有/缺失的权限, 并生成最小 IAM 策略 JSON.

    返回每组功能的探测结果 + 一键复制粘贴的 IAM 策略, 让用户能立刻给账号补权限。
    AWS CLI / boto3 / 本平台用的都是同一套 STS 凭证, **没有任何方法能绕过 IAM 策略**,
    所以"邮箱拿不到 / 账单不对 / Free Tier 显示空"等问题都是 IAM 权限问题, 不是 SDK 问题.
    """
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        executor,
        lambda: AwsManager(account, db).diagnose_permissions(),
    )
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


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

    await _batch_run([_detect_one(a) for a in accounts])
    return {"detected": len(results), "errors": len(errors), "results": results}

@app.get("/api/accounts/groups")
def list_account_groups(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取所有分组名称"""
    accounts = db.query(AwsAccount).filter(AwsAccount.user_id == user.id).all()
    groups = sorted(set(a.group_name for a in accounts if a.group_name))
    return groups

@app.post("/api/accounts/reset-status")
def reset_accounts_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """一键修复"假性失效"——把所有 status_reason 含网络错误关键字的账号重置为 unknown.

    场景: 服务器到 AWS 的网络抖动 (中国直连被 GFW reset、代理掉线、DNS 超时 等) 时,
    detect_account_info 之前会把账号误判为 invalid_credentials, 导致前端显示"AK/SK 失效".
    新版逻辑已经会自动忽略网络错误, 但**已经被错误标记的存量账号**需要这个端点一键重置.
    """
    accounts = db.query(AwsAccount).filter(AwsAccount.user_id == user.id).all()
    reset = 0
    skipped = 0
    for a in accounts:
        if a.account_status != "invalid_credentials":
            skipped += 1
            continue
        reason = (a.status_reason or "").lower()
        # 没有 status_reason (旧账号) 也一并重置, 让用户能再点一次"检测"
        if not reason or AwsManager._is_network_error(reason):
            a.account_status = "unknown"
            a.status_reason = "已手动重置, 等待重新检测"
            from datetime import datetime as _dt
            a.status_checked_at = _dt.utcnow()
            reset += 1
        else:
            skipped += 1
    db.commit()
    return {"reset": reset, "skipped": skipped, "total": len(accounts)}


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
    """简单创建 (兼容旧 UI) - 默认 Ubuntu 22.04, 1 台"""
    account = _get_user_account(db, user, data.account_id)
    loop = asyncio.get_event_loop()
    def _launch():
        mgr = AwsManager(account, db)
        return mgr.launch_instance_legacy(region=data.region, instance_type=data.instance_type, volume_size=data.volume_size, volume_type=data.volume_type)
    try:
        instance = await loop.run_in_executor(executor, _launch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"创建实例失败: {str(e)[:300]}")
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
                    inst = mgr.launch_instance_legacy(region=data.region, instance_type=data.instance_type, volume_size=data.volume_size, volume_type=data.volume_type)
                    return {"id": inst.id, "instance_id": inst.instance_id, "region": inst.region}
                finally:
                    s.close()
            r = await loop.run_in_executor(executor, do)
            results.append(r)
        except Exception as e:
            errors.append({"index": idx, "error": str(e)})

    await _batch_run([_launch_one(i) for i in range(data.count)])
    return {"launched": results, "errors": errors}


# ==================== 高级创建 EC2 (账号详情面板用) ====================

class LaunchAdvancedRequest(BaseModel):
    account_id: int
    region: str
    instance_type: str
    ami_id: Optional[str] = None        # AMI ID (优先级最高)
    ami_key: Optional[str] = None       # AMI 模板 key (如 "ubuntu-22.04")
    password: Optional[str] = None      # SSH/RDP 密码
    instance_name: Optional[str] = None
    spot: bool = False
    enable_ipv6: bool = False
    static_ip: bool = False
    allow_cidrs: Optional[list[str]] = None  # 入站 IP 白名单
    user_data: Optional[str] = None
    count: int = 1
    volume_size: int = 20
    volume_type: str = "gp3"
    gfw_check: bool = False


@app.post("/api/instances/launch-advanced")
async def launch_advanced(
    data: LaunchAdvancedRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """高级创建 EC2 实例 - 像 Lightsail 创建表单一样, 支持密码/Spot/IPv6/EIP/CIDR/AMI模板/数量"""
    account = _get_user_account(db, user, data.account_id)
    loop = asyncio.get_event_loop()

    def _do():
        mgr = AwsManager(account, db)
        return mgr.launch_instance(
            region=data.region,
            instance_type=data.instance_type,
            ami_id=data.ami_id,
            ami_key=data.ami_key,
            password=data.password,
            instance_name=data.instance_name,
            spot=data.spot,
            enable_ipv6=data.enable_ipv6,
            static_ip=data.static_ip,
            allow_cidrs=data.allow_cidrs,
            user_data=data.user_data,
            count=data.count,
            volume_size=data.volume_size,
            volume_type=data.volume_type,
            gfw_check=data.gfw_check,
        )

    try:
        result = await loop.run_in_executor(executor, _do)
    except ValueError as e:
        # 我们自己抛的友好校验错误 (如磁盘大小超限), 原样返回
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"创建 EC2 实例失败: {str(e)[:300]}")
    return result


@app.get("/api/accounts/{account_id}/amis")
async def list_amis_for_region(
    account_id: int,
    region: str = Query(..., description="区域代码"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按区域查询常用 AMI 列表 (Ubuntu/Debian/AmazonLinux/Rocky/Alma/Windows 等)"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(executor, lambda: AwsManager(account, db).list_amis_for_region(region))
    except Exception as e:
        raise HTTPException(400, f"查询 AMI 失败: {str(e)[:300]}")
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "public, max-age=300"},  # AMI 列表缓存 5 分钟
    )


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

    await _batch_run([_sync_one(i) for i in instances])
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

    await _batch_run([_start_one(i) for i in instance_ids])
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

    await _batch_run([_stop_one(i) for i in instance_ids])
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

class Ec2DirectAction(BaseModel):
    account_id: int
    instance_id: str
    region: str
    force: Optional[bool] = False  # terminate 时, 实例开了 termination protection 会自动关闭再终止


def _humanize_aws_error(action_label: str, instance_id: str, region: str, exc: Exception) -> HTTPException:
    """把 boto3 ClientError 转成对用户可读的 HTTPException"""
    msg = str(exc)
    low = msg.lower()
    # InvalidInstanceID.NotFound 通常是 region 不对 (前端传错区域)
    if "invalidinstanceid.notfound" in low or "does not exist" in low:
        return HTTPException(404, f"实例 {instance_id} 在区域 {region} 不存在 (区域可能传错; 同一账号在不同区域是隔离的)")
    if "invalidinstanceid.malformed" in low:
        return HTTPException(400, f"实例 ID 格式无效: {instance_id}")
    if "incorrectinstancestate" in low or "is not in a state from which it can be" in low:
        return HTTPException(409, f"实例 {instance_id} 当前状态无法执行{action_label} (例如已停止/已终止/启动中)")
    if "operationnotpermitted" in low and "terminationprotection" in low.replace(" ", ""):
        return HTTPException(409, f"实例 {instance_id} 开启了【终止保护】, 请先在 AWS 控制台关闭 disableApiTermination 再操作")
    if "unauthorizedoperation" in low or "accessdenied" in low or "not authorized to perform" in low:
        # 提取出缺失的 action 名称, 帮用户排查权限
        import re as _re
        m = _re.search(r"perform:\s*([\w:-]+)", msg)
        action = m.group(1) if m else "ec2:*"
        return HTTPException(403, f"AK/SK 缺权限 {action}, 无法{action_label}该实例 (建议授予 AmazonEC2FullAccess)")
    if "optinrequired" in low or "the security token included in the request is invalid" in low:
        return HTTPException(401, f"AK/SK 失效或区域 {region} 未启用 (opt-in)")
    if "throttling" in low or "requestlimitexceeded" in low:
        return HTTPException(429, f"AWS API 限流, 请稍后重试")
    # 兜底: 截短错误并把 AWS Error Code 提到前面
    if "(" in msg and ")" in msg:
        # boto3 错误格式: "An error occurred (XxxException) when calling the StopInstances operation: ..."
        return HTTPException(500, msg.split("\n")[0][:300])
    return HTTPException(500, f"{action_label}失败: {msg[:200]}")


def _ec2_direct_action(db: Session, user: User, data: "Ec2DirectAction", method_name: str, action_label: str):
    """4 个 direct 端点公用的封装: 取 account → 调 AwsManager → 同步 DB 状态 → 友好错误"""
    account = _get_user_account(db, user, data.account_id)
    mgr = AwsManager(account, db)
    try:
        method = getattr(mgr, method_name)
        if method_name == "terminate_instance":
            method(data.instance_id, data.region)   # terminate 不收 force, 已在签名层处理
        else:
            method(data.instance_id, data.region)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"EC2 {method_name} {data.instance_id}@{data.region} failed: {e}")
        raise _humanize_aws_error(action_label, data.instance_id, data.region, e)

    # 同步本地 Instance (如果存在) 的状态, 避免 UI 刷新前显示旧状态
    try:
        local = db.query(Instance).filter(
            Instance.account_id == account.id,
            Instance.instance_id == data.instance_id,
        ).first()
        if local:
            if method_name == "terminate_instance":
                local.state = "shutting-down"
            elif method_name == "stop_instance":
                local.state = "stopping"
            elif method_name == "start_instance":
                local.state = "pending"
            elif method_name == "reboot_instance":
                local.state = "rebooting"
            db.commit()
    except Exception:
        db.rollback()


@app.post("/api/instances/direct/start")
async def ec2_direct_start(data: Ec2DirectAction, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """通过 instance_id + region 直接启动一个 EC2 实例 (不需要本地 Instance 记录)"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, lambda: _ec2_direct_action(db, user, data, "start_instance", "启动"))
    return {"ok": True, "instance_id": data.instance_id, "region": data.region, "action": "start"}


@app.post("/api/instances/direct/stop")
async def ec2_direct_stop(data: Ec2DirectAction, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, lambda: _ec2_direct_action(db, user, data, "stop_instance", "停止"))
    return {"ok": True, "instance_id": data.instance_id, "region": data.region, "action": "stop"}


@app.post("/api/instances/direct/reboot")
async def ec2_direct_reboot(data: Ec2DirectAction, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, lambda: _ec2_direct_action(db, user, data, "reboot_instance", "重启"))
    return {"ok": True, "instance_id": data.instance_id, "region": data.region, "action": "reboot"}


@app.post("/api/instances/direct/terminate")
async def ec2_direct_terminate(data: Ec2DirectAction, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """终止实例; 若开了 termination protection 会先关闭再终止 (data.force=True)"""
    account = _get_user_account(db, user, data.account_id)
    loop = asyncio.get_event_loop()

    def _do():
        mgr = AwsManager(account, db)
        try:
            mgr.terminate_instance(data.instance_id, data.region)
        except Exception as e:
            err = str(e).lower()
            # termination protection: 自动关掉再终止
            if "operationnotpermitted" in err and "terminationprotection" in err.replace(" ", ""):
                if data.force:
                    try:
                        ec2 = mgr._get_client("ec2", data.region)
                        ec2.modify_instance_attribute(
                            InstanceId=data.instance_id,
                            DisableApiTermination={"Value": False},
                        )
                        mgr.terminate_instance(data.instance_id, data.region)
                        return
                    except Exception as e2:
                        raise _humanize_aws_error("终止 (强制)", data.instance_id, data.region, e2)
                else:
                    raise HTTPException(
                        409,
                        f"实例 {data.instance_id} 开启了【终止保护】; 请用 force=true 重试或在控制台手动关闭",
                    )
            logger.warning(f"EC2 terminate {data.instance_id}@{data.region} failed: {e}")
            raise _humanize_aws_error("终止", data.instance_id, data.region, e)

        # 同步本地状态
        try:
            local = db.query(Instance).filter(
                Instance.account_id == account.id,
                Instance.instance_id == data.instance_id,
            ).first()
            if local:
                local.state = "shutting-down"
                db.commit()
        except Exception:
            db.rollback()

    await loop.run_in_executor(executor, _do)
    return {"ok": True, "instance_id": data.instance_id, "region": data.region, "action": "terminate"}


@app.get("/api/accounts/{account_id}/ec2-detail")
async def get_account_ec2_detail(
    account_id: int,
    region: Optional[str] = Query(None, description="留空扫描所有区域；指定则只查该区域"),
    all_managed: bool = Query(True, description="True=列出账号所有 EC2 实例（含手动创建的）；False=仅本平台创建的"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """详细列出指定账号下 EC2 实例（含 DNS / AZ / 架构 / launch_time），用于账号-实例面板"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()

    def _do():
        mgr = AwsManager(account, db)
        if region:
            return {"region": region, "instances": mgr.list_instances_detailed(region, all_managed=all_managed)}
        all_data = mgr.list_instances_detailed_all_regions(all_managed=all_managed)
        flat = []
        for r, items in all_data.items():
            flat.extend(items)
        return {"region": "all", "instances": flat, "by_region": all_data}

    try:
        result = await loop.run_in_executor(executor, _do)
    except Exception as e:
        raise HTTPException(400, f"加载实例详情失败: {str(e)[:300]}")
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/api/instances/types")
def list_instance_types(user: User = Depends(get_current_user)):
    """返回完整 EC2 实例类型列表"""
    return [
        # === 通用突发 (T系列) ===
        {"type":"t2.nano","vcpu":1,"mem":"0.5 GiB","category":"通用突发"},
        {"type":"t2.micro","vcpu":1,"mem":"1 GiB","category":"通用突发"},
        {"type":"t2.small","vcpu":1,"mem":"2 GiB","category":"通用突发"},
        {"type":"t2.medium","vcpu":2,"mem":"4 GiB","category":"通用突发"},
        {"type":"t2.large","vcpu":2,"mem":"8 GiB","category":"通用突发"},
        {"type":"t2.xlarge","vcpu":4,"mem":"16 GiB","category":"通用突发"},
        {"type":"t2.2xlarge","vcpu":8,"mem":"32 GiB","category":"通用突发"},
        {"type":"t3.nano","vcpu":2,"mem":"0.5 GiB","category":"通用突发"},
        {"type":"t3.micro","vcpu":2,"mem":"1 GiB","category":"通用突发"},
        {"type":"t3.small","vcpu":2,"mem":"2 GiB","category":"通用突发"},
        {"type":"t3.medium","vcpu":2,"mem":"4 GiB","category":"通用突发"},
        {"type":"t3.large","vcpu":2,"mem":"8 GiB","category":"通用突发"},
        {"type":"t3.xlarge","vcpu":4,"mem":"16 GiB","category":"通用突发"},
        {"type":"t3.2xlarge","vcpu":8,"mem":"32 GiB","category":"通用突发"},
        {"type":"t3a.nano","vcpu":2,"mem":"0.5 GiB","category":"通用突发AMD"},
        {"type":"t3a.micro","vcpu":2,"mem":"1 GiB","category":"通用突发AMD"},
        {"type":"t3a.small","vcpu":2,"mem":"2 GiB","category":"通用突发AMD"},
        {"type":"t3a.medium","vcpu":2,"mem":"4 GiB","category":"通用突发AMD"},
        {"type":"t3a.large","vcpu":2,"mem":"8 GiB","category":"通用突发AMD"},
        {"type":"t3a.xlarge","vcpu":4,"mem":"16 GiB","category":"通用突发AMD"},
        {"type":"t3a.2xlarge","vcpu":8,"mem":"32 GiB","category":"通用突发AMD"},
        # === 通用 (M系列) ===
        {"type":"m5.large","vcpu":2,"mem":"8 GiB","category":"通用"},
        {"type":"m5.xlarge","vcpu":4,"mem":"16 GiB","category":"通用"},
        {"type":"m5.2xlarge","vcpu":8,"mem":"32 GiB","category":"通用"},
        {"type":"m5.4xlarge","vcpu":16,"mem":"64 GiB","category":"通用"},
        {"type":"m5.8xlarge","vcpu":32,"mem":"128 GiB","category":"通用"},
        {"type":"m5.12xlarge","vcpu":48,"mem":"192 GiB","category":"通用"},
        {"type":"m5.16xlarge","vcpu":64,"mem":"256 GiB","category":"通用"},
        {"type":"m5a.large","vcpu":2,"mem":"8 GiB","category":"通用AMD"},
        {"type":"m5a.xlarge","vcpu":4,"mem":"16 GiB","category":"通用AMD"},
        {"type":"m5a.2xlarge","vcpu":8,"mem":"32 GiB","category":"通用AMD"},
        {"type":"m5a.4xlarge","vcpu":16,"mem":"64 GiB","category":"通用AMD"},
        {"type":"m5a.8xlarge","vcpu":32,"mem":"128 GiB","category":"通用AMD"},
        {"type":"m6i.large","vcpu":2,"mem":"8 GiB","category":"通用6代"},
        {"type":"m6i.xlarge","vcpu":4,"mem":"16 GiB","category":"通用6代"},
        {"type":"m6i.2xlarge","vcpu":8,"mem":"32 GiB","category":"通用6代"},
        {"type":"m6i.4xlarge","vcpu":16,"mem":"64 GiB","category":"通用6代"},
        {"type":"m6i.8xlarge","vcpu":32,"mem":"128 GiB","category":"通用6代"},
        {"type":"m6a.large","vcpu":2,"mem":"8 GiB","category":"通用6代AMD"},
        {"type":"m6a.xlarge","vcpu":4,"mem":"16 GiB","category":"通用6代AMD"},
        {"type":"m6a.2xlarge","vcpu":8,"mem":"32 GiB","category":"通用6代AMD"},
        {"type":"m6a.4xlarge","vcpu":16,"mem":"64 GiB","category":"通用6代AMD"},
        {"type":"m7i.large","vcpu":2,"mem":"8 GiB","category":"通用7代"},
        {"type":"m7i.xlarge","vcpu":4,"mem":"16 GiB","category":"通用7代"},
        {"type":"m7i.2xlarge","vcpu":8,"mem":"32 GiB","category":"通用7代"},
        {"type":"m7i.4xlarge","vcpu":16,"mem":"64 GiB","category":"通用7代"},
        # === 计算优化 (C系列) ===
        {"type":"c5.large","vcpu":2,"mem":"4 GiB","category":"计算优化"},
        {"type":"c5.xlarge","vcpu":4,"mem":"8 GiB","category":"计算优化"},
        {"type":"c5.2xlarge","vcpu":8,"mem":"16 GiB","category":"计算优化"},
        {"type":"c5.4xlarge","vcpu":16,"mem":"32 GiB","category":"计算优化"},
        {"type":"c5.9xlarge","vcpu":36,"mem":"72 GiB","category":"计算优化"},
        {"type":"c5a.large","vcpu":2,"mem":"4 GiB","category":"计算AMD"},
        {"type":"c5a.xlarge","vcpu":4,"mem":"8 GiB","category":"计算AMD"},
        {"type":"c5a.2xlarge","vcpu":8,"mem":"16 GiB","category":"计算AMD"},
        {"type":"c5a.4xlarge","vcpu":16,"mem":"32 GiB","category":"计算AMD"},
        {"type":"c6i.large","vcpu":2,"mem":"4 GiB","category":"计算6代"},
        {"type":"c6i.xlarge","vcpu":4,"mem":"8 GiB","category":"计算6代"},
        {"type":"c6i.2xlarge","vcpu":8,"mem":"16 GiB","category":"计算6代"},
        {"type":"c6i.4xlarge","vcpu":16,"mem":"32 GiB","category":"计算6代"},
        {"type":"c6a.large","vcpu":2,"mem":"4 GiB","category":"计算6代AMD"},
        {"type":"c6a.xlarge","vcpu":4,"mem":"8 GiB","category":"计算6代AMD"},
        {"type":"c6a.2xlarge","vcpu":8,"mem":"16 GiB","category":"计算6代AMD"},
        {"type":"c7i.large","vcpu":2,"mem":"4 GiB","category":"计算7代"},
        {"type":"c7i.xlarge","vcpu":4,"mem":"8 GiB","category":"计算7代"},
        {"type":"c7i.2xlarge","vcpu":8,"mem":"16 GiB","category":"计算7代"},
        # === 内存优化 (R系列) ===
        {"type":"r5.large","vcpu":2,"mem":"16 GiB","category":"内存优化"},
        {"type":"r5.xlarge","vcpu":4,"mem":"32 GiB","category":"内存优化"},
        {"type":"r5.2xlarge","vcpu":8,"mem":"64 GiB","category":"内存优化"},
        {"type":"r5.4xlarge","vcpu":16,"mem":"128 GiB","category":"内存优化"},
        {"type":"r5a.large","vcpu":2,"mem":"16 GiB","category":"内存AMD"},
        {"type":"r5a.xlarge","vcpu":4,"mem":"32 GiB","category":"内存AMD"},
        {"type":"r5a.2xlarge","vcpu":8,"mem":"64 GiB","category":"内存AMD"},
        {"type":"r6i.large","vcpu":2,"mem":"16 GiB","category":"内存6代"},
        {"type":"r6i.xlarge","vcpu":4,"mem":"32 GiB","category":"内存6代"},
        {"type":"r6i.2xlarge","vcpu":8,"mem":"64 GiB","category":"内存6代"},
        {"type":"r6a.large","vcpu":2,"mem":"16 GiB","category":"内存6代AMD"},
        {"type":"r6a.xlarge","vcpu":4,"mem":"32 GiB","category":"内存6代AMD"},
        # === 存储优化 ===
        {"type":"i3.large","vcpu":2,"mem":"15.25 GiB","category":"存储优化"},
        {"type":"i3.xlarge","vcpu":4,"mem":"30.5 GiB","category":"存储优化"},
        {"type":"i3.2xlarge","vcpu":8,"mem":"61 GiB","category":"存储优化"},
        {"type":"i3en.large","vcpu":2,"mem":"16 GiB","category":"存储优化"},
        {"type":"i3en.xlarge","vcpu":4,"mem":"32 GiB","category":"存储优化"},
        # === GPU/加速计算 ===
        {"type":"g4dn.xlarge","vcpu":4,"mem":"16 GiB","category":"GPU"},
        {"type":"g4dn.2xlarge","vcpu":8,"mem":"32 GiB","category":"GPU"},
        {"type":"g4dn.4xlarge","vcpu":16,"mem":"64 GiB","category":"GPU"},
        {"type":"g5.xlarge","vcpu":4,"mem":"16 GiB","category":"GPU"},
        {"type":"g5.2xlarge","vcpu":8,"mem":"32 GiB","category":"GPU"},
        {"type":"p3.2xlarge","vcpu":8,"mem":"61 GiB","category":"GPU高性能"},
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


# ==================== Lightsail (光帆) ====================

class LightsailLaunchRequest(BaseModel):
    account_id: int
    region: str
    availability_zone: Optional[str] = None  # 留空则取该区域第一个可用区
    blueprint_id: str  # 如 ubuntu_22_04 / wordpress / amazon_linux_2023
    bundle_id: str     # 如 nano_3_0 / small_3_0 / large_3_0
    instance_name: str
    count: int = 1
    user_data: Optional[str] = None
    open_default_ports: bool = True


@app.get("/api/lightsail/regions")
def lightsail_list_regions(user: User = Depends(get_current_user)):
    """列出 Lightsail 支持的所有区域 (静态列表，无需账号)"""
    return [{"code": k, "display": v} for k, v in LIGHTSAIL_REGIONS.items()]


@app.get("/api/lightsail/blueprints")
async def lightsail_list_blueprints(
    account_id: int = Query(...),
    region: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出 Lightsail 蓝图 (操作系统/应用镜像)"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: LightsailManager(account, db).list_blueprints(region),
    )


@app.get("/api/lightsail/bundles")
async def lightsail_list_bundles(
    account_id: int = Query(...),
    region: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出 Lightsail 套餐 (实例规格)"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: LightsailManager(account, db).list_bundles(region),
    )


@app.get("/api/lightsail/availability-zones")
async def lightsail_list_az(
    account_id: int = Query(...),
    region: str = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出指定区域的可用区"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: LightsailManager(account, db).list_availability_zones(region),
    )


@app.get("/api/lightsail/instances")
async def lightsail_list_instances(
    account_id: int = Query(...),
    region: Optional[str] = Query(None, description="留空则扫描所有区域"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出指定账号的 Lightsail 实例"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()

    def _list():
        mgr = LightsailManager(account, db)
        if region:
            return {"region": region, "instances": mgr.list_instances(region)}
        # 扫描所有区域
        all_data = mgr.list_instances_all_regions()
        flat = []
        for r, items in all_data.items():
            for it in items:
                it["region"] = r
                flat.append(it)
        return {"region": "all", "instances": flat, "by_region": all_data}

    return await loop.run_in_executor(executor, _list)


@app.post("/api/lightsail/launch")
async def lightsail_launch(
    data: LightsailLaunchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建 Lightsail 实例 (光帆开机)"""
    account = _get_user_account(db, user, data.account_id)
    loop = asyncio.get_event_loop()

    def _do():
        mgr = LightsailManager(account, db)
        # 如果未指定可用区，取第一个
        az = data.availability_zone
        if not az:
            zones = mgr.list_availability_zones(data.region)
            if not zones:
                raise ValueError(f"区域 {data.region} 没有可用区")
            az = zones[0]

        result = mgr.create_instance(
            instance_name=data.instance_name,
            region=data.region,
            availability_zone=az,
            blueprint_id=data.blueprint_id,
            bundle_id=data.bundle_id,
            user_data=data.user_data,
            count=data.count,
        )

        # 自动打开常用端口
        if data.open_default_ports:
            for name in result.get("instance_names", []):
                try:
                    mgr.open_instance_ports(name, data.region)
                except Exception as e:
                    logger.warning(f"open ports for {name} failed: {e}")

        return result

    try:
        return await loop.run_in_executor(executor, _do)
    except Exception as e:
        raise HTTPException(400, f"创建 Lightsail 实例失败: {str(e)[:300]}")


@app.post("/api/lightsail/instances/{instance_name}/start")
async def lightsail_start(
    instance_name: str,
    account_id: int = Query(...),
    region: str = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: LightsailManager(account, db).start_instance(instance_name, region),
    )


@app.post("/api/lightsail/instances/{instance_name}/stop")
async def lightsail_stop(
    instance_name: str,
    account_id: int = Query(...),
    region: str = Query(...),
    force: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: LightsailManager(account, db).stop_instance(instance_name, region, force=force),
    )


@app.post("/api/lightsail/instances/{instance_name}/reboot")
async def lightsail_reboot(
    instance_name: str,
    account_id: int = Query(...),
    region: str = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: LightsailManager(account, db).reboot_instance(instance_name, region),
    )


@app.delete("/api/lightsail/instances/{instance_name}")
async def lightsail_delete(
    instance_name: str,
    account_id: int = Query(...),
    region: str = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: LightsailManager(account, db).delete_instance(instance_name, region),
    )


@app.post("/api/lightsail/instances/{instance_name}/open-ports")
async def lightsail_open_ports(
    instance_name: str,
    account_id: int = Query(...),
    region: str = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """开放常用端口 (22/80/443/3000-9999)"""
    account = _get_user_account(db, user, account_id)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor,
        lambda: LightsailManager(account, db).open_instance_ports(instance_name, region),
    )


# ==================== Proxies ====================

@app.get("/api/proxies")
def list_proxies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    proxies = db.query(Proxy).filter(Proxy.user_id == user.id).order_by(Proxy.id).all()
    fail_threshold = ProxyManager.FAIL_THRESHOLD
    return [
        {
            "id": p.id, "protocol": p.protocol, "host": p.host, "port": p.port,
            "username": p.username or "", "is_active": p.is_active,
            "last_used_at": str(p.last_used_at) if p.last_used_at else None,
            # 新增: 健康追踪字段 (P5)
            "fail_count": p.fail_count or 0,
            "last_check_at": str(p.last_check_at) if p.last_check_at else None,
            "last_check_ok": p.last_check_ok,
            "last_check_ip": p.last_check_ip or "",
            "last_error": (p.last_error or "")[:200],
            "quarantined": (p.fail_count or 0) >= fail_threshold,
            "fail_threshold": fail_threshold,
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

def _smart_parse_proxy(line: str, default_protocol: str = "auto") -> dict:
    """智能解析代理字符串，支持各种格式:
    host:port
    host:port:user:pass               ← IPRoyal / 大多数住宅代理常用格式
    user:pass@host:port
    protocol://host:port
    protocol://user:pass@host:port
    host port (空格分隔)

    default_protocol:
        "auto" — 根据端口猜 (1080/1081/7890/7891 → socks5; 443/8443 → https; 其他 → http)
        "http" / "https" / "socks5" — 强制指定 (批量粘贴时用户已选好类型)
    """
    line = line.strip()
    if not line:
        raise ValueError("空行")

    # 起始协议: 优先 URL 前缀, 否则用 default_protocol (auto 留到最后猜)
    protocol, host, port, username, password = None, "", 0, None, None
    explicit_protocol = False

    if "://" in line:
        proto, rest = line.split("://", 1)
        protocol = proto.lower().replace("socks5h", "socks5")
        if protocol not in ("http", "https", "socks5", "socks4"):
            protocol = "http"
        explicit_protocol = True
    else:
        rest = line
        # default_protocol 不是 auto 就直接采用 (用户在前端已显式选了)
        if default_protocol in ("http", "https", "socks5"):
            protocol = default_protocol
            explicit_protocol = True
        else:
            protocol = None   # 留到最后端口猜测阶段

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
            return _finalize_parsed(protocol, host, port, username, password, explicit_protocol)

    # 用冒号分隔
    parts = rest.split(":")
    if len(parts) == 2:
        host, port = parts[0], int(parts[1])
    elif len(parts) == 3:
        host, port = parts[0], int(parts[1])
        username = parts[2]
    elif len(parts) == 4:
        host, port = parts[0], int(parts[1])
        username, password = parts[2], parts[3]
    elif len(parts) >= 5:
        host, port = parts[0], int(parts[1])
        username, password = parts[2], parts[3]
        if parts[4].lower() in ("http", "https", "socks5", "socks4"):
            protocol = parts[4].lower()
            explicit_protocol = True
    else:
        raise ValueError(f"无法解析: {line}")

    if not host or port <= 0:
        raise ValueError(f"无效的地址或端口: {line}")

    return _finalize_parsed(protocol, host, port, username, password, explicit_protocol)


def _finalize_parsed(protocol, host, port, username, password, explicit):
    """确定最终协议: 如果没有显式指定, 根据端口猜.

    新策略 (修复原 bug): IPRoyal / SmartProxy / 大多数住宅代理用 4 段格式 host:port:user:pass,
    端口通常是 4 位数 (12321 / 7777 / 10001 等). 原代码默认 http → boto3 当 HTTP 代理用 →
    根本连不上 (socks5 端口拒 HTTP 报文). 改进: 有 user:pass 且端口非常见 HTTP 端口时, 倾向 socks5.
    """
    if not explicit and not protocol:
        # 端口白名单
        if port in (1080, 1081, 7890, 7891):
            protocol = "socks5"
        elif port in (443, 8443):
            protocol = "https"
        elif port in (80, 8080, 3128, 8000, 8888, 8118):
            protocol = "http"
        elif username and password and port > 10000:
            # 启发式: 大端口+带认证 → 大概率是住宅代理 (IPRoyal/SmartProxy/Bright Data 等)
            # 默认 socks5 比默认 http 安全得多 (走错协议会连不上, 但不会暴露 IP)
            protocol = "socks5"
        else:
            protocol = "http"
    return {"protocol": protocol or "http", "host": host, "port": port, "username": username, "password": password}


@app.post("/api/proxies/batch-text")
def batch_create_proxies_text(data: BatchProxyCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量添加代理 - 智能识别各种格式. 可在 default_protocol 显式指定协议."""
    lines = [l.strip() for l in data.text.strip().split('\n') if l.strip()]
    created, errors = [], []
    default_protocol = (getattr(data, "default_protocol", None) or "auto").lower()

    for idx, line in enumerate(lines):
        try:
            parsed = _smart_parse_proxy(line, default_protocol=default_protocol)
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
    """测试代理是否可用，返回出口IP. socks5 用 socks5h:// (DNS 走代理, 防泄漏).
    成功/失败会更新 last_check_at / last_check_ok / last_check_ip / last_error / fail_count.
    """
    proxy = _get_user_proxy(db, user, proxy_id)
    if proxy.protocol == "socks5" and not PYSOCKS_AVAILABLE:
        return {"ok": False, "error": "PySocks 未安装, socks5 代理无法使用 (pip install PySocks==1.7.1)", "proxy": f"{proxy.host}:{proxy.port}"}
    loop = asyncio.get_event_loop()

    def _test():
        ok, ip, err = ProxyManager.check_one(proxy, timeout=15)
        ProxyManager.report_health_check(proxy.id, ok, ip, err)
        if ok:
            return {"ok": True, "ip": ip, "proxy": f"{proxy.host}:{proxy.port}"}
        return {"ok": False, "error": err, "proxy": f"{proxy.host}:{proxy.port}"}

    return await loop.run_in_executor(executor, _test)


@app.post("/api/proxies/test-all")
async def test_all_proxies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """批量测试所有代理. 用 Semaphore 限并发, 避免打爆代理出口."""
    proxies = db.query(Proxy).filter(Proxy.user_id == user.id, Proxy.is_active == True).all()
    loop = asyncio.get_event_loop()
    results = []

    async def _test_one(p):
        if p.protocol == "socks5" and not PYSOCKS_AVAILABLE:
            results.append({"id": p.id, "ok": False, "error": "PySocks 未安装"})
            return
        def do():
            ok, ip, err = ProxyManager.check_one(p, timeout=10)
            ProxyManager.report_health_check(p.id, ok, ip, err)
            return {"id": p.id, "ok": ok, "ip": ip} if ok else {"id": p.id, "ok": False, "error": err[:80]}
        r = await loop.run_in_executor(executor, do)
        results.append(r)

    await _batch_run([_test_one(p) for p in proxies])
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

    await _batch_run([_deploy_one(i) for i in data.instance_ids])
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
