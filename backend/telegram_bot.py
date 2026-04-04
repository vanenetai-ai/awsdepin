import os
import logging
import asyncio
import httpx
from database import SessionLocal
from auth import get_or_create_user, create_token

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TOKEN_FILE = os.path.join(os.getenv("DATA_DIR", "data"), "bot_token.txt")

_running = False
_task = None
_bot_token = ""


def _load_token():
    """从文件加载持久化的 bot token"""
    global _bot_token
    # 优先用环境变量
    if BOT_TOKEN:
        _bot_token = BOT_TOKEN
        return
    # 从文件读取
    try:
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "r") as f:
                t = f.read().strip()
                if t:
                    _bot_token = t
                    logger.info("Bot token loaded from file")
    except Exception as e:
        logger.error(f"Failed to load bot token: {e}")


def _save_token(token: str):
    """持久化 bot token 到文件"""
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            f.write(token)
    except Exception as e:
        logger.error(f"Failed to save bot token: {e}")


# 启动时加载
_load_token()


def get_tg_api():
    return f"https://api.telegram.org/bot{_bot_token}"


def set_bot_token(token: str):
    global _bot_token
    _bot_token = token
    _save_token(token)


def get_bot_token():
    return _bot_token


def is_bot_configured():
    return bool(_bot_token)


async def send_message(chat_id: int, text: str, parse_mode: str = "HTML"):
    if not is_bot_configured():
        return
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            await client.post(f"{get_tg_api()}/sendMessage", json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            })
        except Exception as e:
            logger.error(f"Failed to send TG message: {e}")


async def handle_update(update: dict):
    msg = update.get("message")
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    user_info = msg.get("from", {})
    tg_id = user_info.get("id", chat_id)
    username = user_info.get("username", "")
    first_name = user_info.get("first_name", "")
    last_name = user_info.get("last_name", "")
    display = f"{first_name} {last_name}".strip() or username or str(tg_id)

    if text == "/start":
        db = SessionLocal()
        try:
            user = get_or_create_user(db, tg_id, username, display)
            token = create_token(db, user)
            login_url = f"{BASE_URL}/login.html?token={token}"
            await send_message(chat_id,
                f"👋 欢迎使用 <b>AWS DePIN 管理平台</b>！\n\n"
                f"🔗 点击下方链接登录：\n"
                f"<a href=\"{login_url}\">{login_url}</a>\n\n"
                f"⏰ 链接有效期 30 天\n"
                f"⚠️ 请勿将链接分享给他人"
            )
        except Exception as e:
            logger.error(f"Handle /start error: {e}")
            await send_message(chat_id, f"❌ 登录链接生成失败: {e}")
        finally:
            db.close()

    elif text == "/login":
        db = SessionLocal()
        try:
            user = get_or_create_user(db, tg_id, username, display)
            token = create_token(db, user)
            login_url = f"{BASE_URL}/login.html?token={token}"
            await send_message(chat_id,
                f"🔗 新的登录链接：\n"
                f"<a href=\"{login_url}\">{login_url}</a>\n\n"
                f"⏰ 有效期 30 天"
            )
        except Exception as e:
            logger.error(f"Handle /login error: {e}")
            await send_message(chat_id, f"❌ 生成失败: {e}")
        finally:
            db.close()

    elif text == "/help":
        await send_message(chat_id,
            "📖 <b>命令列表</b>\n\n"
            "/start - 获取登录链接\n"
            "/login - 重新获取登录链接\n"
            "/help - 帮助信息"
        )
    else:
        await send_message(chat_id,
            "发送 /start 获取登录链接\n发送 /help 查看帮助"
        )


async def poll_updates():
    """长轮询获取 Telegram 更新"""
    global _running
    _running = True
    offset = 0

    if not is_bot_configured():
        logger.warning("Telegram bot token not configured, polling disabled")
        return

    logger.info("Telegram bot polling started")

    async with httpx.AsyncClient(timeout=60) as client:
        while _running:
            try:
                resp = await client.get(f"{get_tg_api()}/getUpdates", params={
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": '["message"]',
                })
                data = resp.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        asyncio.create_task(handle_update(update))
            except httpx.ReadTimeout:
                continue
            except Exception as e:
                logger.error(f"Telegram polling error: {e}")
                await asyncio.sleep(5)


async def verify_bot_token(token: str) -> dict:
    """验证 bot token 是否有效"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
            if data.get("ok"):
                bot = data["result"]
                return {"valid": True, "username": bot.get("username"), "name": bot.get("first_name")}
            return {"valid": False, "error": data.get("description", "Unknown error")}
    except Exception as e:
        return {"valid": False, "error": str(e)}


async def start_bot():
    global _task
    if not is_bot_configured():
        logger.info("No bot token configured, skipping bot start")
        return
    _task = asyncio.create_task(poll_updates())
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(f"{get_tg_api()}/setMyCommands", json={
                "commands": [
                    {"command": "start", "description": "获取登录链接"},
                    {"command": "login", "description": "重新获取登录链接"},
                    {"command": "help", "description": "帮助信息"},
                ]
            })
        except Exception:
            pass


async def restart_bot():
    """重启 bot（更换 token 后调用）"""
    await stop_bot()
    await asyncio.sleep(1)
    await start_bot()


async def stop_bot():
    global _running, _task
    _running = False
    if _task:
        _task.cancel()
        _task = None
