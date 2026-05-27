"""ProxyManager - 用户级代理池.

设计原则 (P1-P5 修复):
  1) 同一 AWS 账号始终走同一代理 (按 account_id hash 选), 防 AWS 风控关联.
  2) 代理失败由调用方上报, 连续 FAIL_THRESHOLD 次失败自动 quarantine.
  3) 没有可用代理 → 抛 ProxyRequiredError, 调用方负责拒绝直连 (绝不静默 fallback).
  4) socks5 真实支持 — 拼成 socks5h:// (DNS 走代理), 防 DNS 泄漏; boto3 端需要 PySocks.
  5) 跨线程安全 — 所有写操作 (last_used_at / fail_count) 用独立 engine connection,
     不污染调用方的 request-scoped session.

历史 bug:
  - `_index` 是实例字段, 每次新建 ProxyManager 都重置为 0 → 永远只用第 1 个代理.
  - boto3 不认 socks5:// (urllib3 不支持), 静默忽略 → IP 泄漏.
  - 每次拿代理都在 request session 上 commit, 并发时 SQLAlchemy session 不是线程安全.
"""

import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import update, func
from sqlalchemy.orm import Session
from database import engine
from models import Proxy

logger = logging.getLogger(__name__)

# 检测 PySocks 是否安装 (启动时一次性检测; 没装时 socks5 代理会被拒用)
try:
    import socks  # noqa: F401
    PYSOCKS_AVAILABLE = True
except ImportError:
    PYSOCKS_AVAILABLE = False
    logger.warning(
        "PySocks 未安装 - socks5 代理将被拒用 (boto3 会静默忽略, 导致 IP 泄漏). "
        "请执行: pip install PySocks==1.7.1"
    )


class ProxyManager:
    """按用户隔离的代理池. 必须传 user_id."""

    # 连续失败到这个数, 临时 quarantine, 不再分给新请求.
    # 后台健康检查跑成功一次会重置 fail_count.
    FAIL_THRESHOLD = 5

    def __init__(self, db: Session, user_id: Optional[int] = None):
        self.db = db
        self.user_id = user_id
        # 记下上次选中的代理 ID, 调用方可以拿来上报成功/失败
        self.last_picked_id: Optional[int] = None

    # ========== 查询 ==========

    def _healthy_query(self):
        """返回 user 当前所有"健康"代理 (active + fail_count < 阈值), 按 id 排序保证稳定."""
        q = self.db.query(Proxy).filter(
            Proxy.is_active == True,  # noqa: E712
            (Proxy.fail_count == None) | (Proxy.fail_count < self.FAIL_THRESHOLD),  # noqa: E711
        )
        if self.user_id is not None:
            q = q.filter(Proxy.user_id == self.user_id)
        # 过滤掉 socks5 (如果 PySocks 没装) - 避免静默走真实 IP
        if not PYSOCKS_AVAILABLE:
            q = q.filter(Proxy.protocol != "socks5")
        return q.order_by(Proxy.id.asc())

    def get_all(self):
        """所有 active 代理 (含 quarantine 的, 用于前端展示)."""
        q = self.db.query(Proxy).filter(Proxy.is_active == True)  # noqa: E712
        if self.user_id is not None:
            q = q.filter(Proxy.user_id == self.user_id)
        return q.order_by(Proxy.id.asc()).all()

    # ========== 主入口 ==========

    def get_proxy_for_account(self, account_id: int) -> Optional[dict]:
        """按 AWS 账号 hash 稳定选代理.

        同一个 AWS 账号永远走同一个代理 (除非代理被 quarantine 或删除导致池子缩小,
        此时 hash 取模结果变化, 但这是不可避免的). 这样能保证 AWS 风控看到的
        该账号出口 IP 历史是稳定的, 不会因为"轮询"频繁换 IP 触发关联告警.
        """
        proxies = self._healthy_query().all()
        if not proxies:
            return None
        proxy = proxies[account_id % len(proxies)]
        self.last_picked_id = proxy.id
        self._touch(proxy.id)
        return self._format(proxy)

    def get_proxy_round_robin(self) -> Optional[dict]:
        """无账号上下文时 (粘贴 AK/SK 批量验证阶段) 用轮询.

        计数器存数据库 (按 user_id 的代理总数 + 全表 last_used_at 旧的优先),
        避免实例字段在多请求间丢失.
        """
        proxies = self._healthy_query().all()
        if not proxies:
            return None
        # 选 last_used_at 最旧的那个 (NULL 视为最旧) - 天然轮询效果, 跨进程稳定
        proxies.sort(key=lambda p: (p.last_used_at or datetime.min, p.id))
        proxy = proxies[0]
        self.last_picked_id = proxy.id
        self._touch(proxy.id)
        return self._format(proxy)

    def get_proxy_for_boto3(self, account_id: Optional[int] = None) -> Optional[dict]:
        """返回 boto3 Config(proxies=...) 直接能用的 dict."""
        p = self.get_proxy_for_account(account_id) if account_id else self.get_proxy_round_robin()
        if not p:
            return None
        return {"http": p["url"], "https": p["url"]}

    # ========== 上报 (跨线程安全, 用独立 connection) ==========

    @staticmethod
    def report_success(proxy_id: int):
        """业务调用成功 → 重置 fail_count.

        用独立 engine connection, 不依赖任何 request session, 多线程安全.
        """
        if not proxy_id:
            return
        try:
            with engine.begin() as conn:
                conn.execute(
                    update(Proxy)
                    .where(Proxy.id == proxy_id)
                    .values(fail_count=0, last_check_ok=True, last_check_at=datetime.utcnow(), last_error=None)
                )
        except Exception as e:
            logger.warning(f"report_success({proxy_id}) failed: {e}")

    @staticmethod
    def report_failure(proxy_id: int, error: str = ""):
        """业务调用失败 → fail_count += 1; 达到阈值会被 _healthy_query 排除."""
        if not proxy_id:
            return
        try:
            with engine.begin() as conn:
                conn.execute(
                    update(Proxy)
                    .where(Proxy.id == proxy_id)
                    .values(
                        fail_count=func.coalesce(Proxy.fail_count, 0) + 1,
                        last_check_ok=False,
                        last_check_at=datetime.utcnow(),
                        last_error=(error or "")[:500],
                    )
                )
        except Exception as e:
            logger.warning(f"report_failure({proxy_id}) failed: {e}")

    @staticmethod
    def report_health_check(proxy_id: int, ok: bool, ip: str = "", error: str = ""):
        """后台健康检查写结果 — 成功就清零 fail_count, 失败就累加."""
        if not proxy_id:
            return
        try:
            with engine.begin() as conn:
                if ok:
                    conn.execute(
                        update(Proxy)
                        .where(Proxy.id == proxy_id)
                        .values(
                            fail_count=0,
                            last_check_ok=True,
                            last_check_at=datetime.utcnow(),
                            last_check_ip=ip or None,
                            last_error=None,
                        )
                    )
                else:
                    conn.execute(
                        update(Proxy)
                        .where(Proxy.id == proxy_id)
                        .values(
                            fail_count=func.coalesce(Proxy.fail_count, 0) + 1,
                            last_check_ok=False,
                            last_check_at=datetime.utcnow(),
                            last_error=(error or "")[:500],
                        )
                    )
        except Exception as e:
            logger.warning(f"report_health_check({proxy_id}) failed: {e}")

    # ========== 内部 ==========

    @staticmethod
    def _touch(proxy_id: int):
        """更新 last_used_at, 走独立 connection, 不污染 request session."""
        try:
            with engine.begin() as conn:
                conn.execute(
                    update(Proxy).where(Proxy.id == proxy_id).values(last_used_at=datetime.utcnow())
                )
        except Exception as e:
            logger.debug(f"_touch({proxy_id}) failed: {e}")

    @staticmethod
    def _format(proxy: Proxy) -> dict:
        """拼 boto3 / httpx 能直接用的代理 URL.

        socks5 → socks5h:// 强制带 h, 让 DNS 在代理对端解析 (防 DNS 泄漏到本地).
        """
        auth = ""
        if proxy.username and proxy.password:
            auth = f"{proxy.username}:{proxy.password}@"
        elif proxy.username:
            auth = f"{proxy.username}@"

        proto = (proxy.protocol or "http").lower()
        scheme = "socks5h" if proto == "socks5" else proto
        url = f"{scheme}://{auth}{proxy.host}:{proxy.port}"
        return {
            "id": proxy.id,
            "url": url,
            "host": proxy.host,
            "port": proxy.port,
            "protocol": proto,
        }

    # ========== 后台健康检查 ==========

    @staticmethod
    def check_one(proxy: Proxy, timeout: int = 10) -> tuple[bool, str, str]:
        """实际 ping 一次代理, 返回 (ok, out_ip, error_msg). 不写库."""
        import httpx as hx
        formatted = ProxyManager._format(proxy)
        url = formatted["url"]
        try:
            with hx.Client(proxies={"http://": url, "https://": url}, timeout=timeout) as client:
                resp = client.get("https://api.ipify.org?format=json")
                ip = resp.json().get("ip", "")
                return (True, ip, "")
        except Exception as e:
            return (False, "", str(e)[:300])
