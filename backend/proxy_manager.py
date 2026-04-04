import random
from datetime import datetime
from sqlalchemy.orm import Session
from models import Proxy


class ProxyManager:
    """旋转代理管理器 - 轮询/随机选择可用代理"""

    def __init__(self, db: Session):
        self.db = db
        self._index = 0

    def get_all(self):
        return self.db.query(Proxy).filter(Proxy.is_active == True).all()

    def get_next_proxy(self) -> dict | None:
        """轮询获取下一个代理"""
        proxies = self.get_all()
        if not proxies:
            return None
        proxy = proxies[self._index % len(proxies)]
        self._index += 1
        proxy.last_used_at = datetime.utcnow()
        self.db.commit()
        return self._format(proxy)

    def get_random_proxy(self) -> dict | None:
        """随机获取一个代理"""
        proxies = self.get_all()
        if not proxies:
            return None
        proxy = random.choice(proxies)
        proxy.last_used_at = datetime.utcnow()
        self.db.commit()
        return self._format(proxy)

    def get_proxy_for_boto3(self) -> dict | None:
        """获取 boto3 可用的代理配置"""
        p = self.get_next_proxy()
        if not p:
            return None
        return {
            "http": p["url"],
            "https": p["url"],
        }

    @staticmethod
    def _format(proxy: Proxy) -> dict:
        auth = ""
        if proxy.username and proxy.password:
            auth = f"{proxy.username}:{proxy.password}@"
        url = f"{proxy.protocol}://{auth}{proxy.host}:{proxy.port}"
        return {
            "id": proxy.id,
            "url": url,
            "host": proxy.host,
            "port": proxy.port,
        }
