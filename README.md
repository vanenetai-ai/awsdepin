# AWS DePIN Manager

多用户 AWS EC2 实例管理 + DePIN 项目一键部署平台。通过 Telegram Bot 登录，支持多用户数据隔离、批量并发操作。

## 功能

- 🔑 Telegram Bot 登录认证，多用户隔离
- ☁️ AWS 多账号管理，EC2 实例生命周期管理
- 🚀 DePIN 项目一键部署（Titan Network / Grass / Nodepay 等）
- 🔄 代理管理，支持 HTTP/HTTPS/SOCKS5
- ⚡ 批量并发操作（启动/停止/部署）
- ⚙️ 管理员后台（用户管理、Bot 配置）

## 快速部署

### 1. 克隆项目

```bash
git clone https://github.com/vanenetai-ai/awsdepin.git
cd awsdepin
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# 必须修改：管理员密码
ADMIN_PASSWORD=your-secure-password

# 必须修改：网站公开访问地址
BASE_URL=https://your-domain.com

# 可选：Telegram Bot Token（也可通过管理后台配置）
TELEGRAM_BOT_TOKEN=
```

### 3. Docker 部署

```bash
docker-compose up -d
```

### 4. 配置 Telegram Bot

1. 在 Telegram 中找 [@BotFather](https://t.me/BotFather)，发送 `/newbot` 创建机器人
2. 获取 Bot Token
3. 访问 `http://your-server/admin.html`，用管理员密码登录
4. 在 Bot 配置区域粘贴 Token，点击「验证」确认有效后「保存并启动」

### 5. 用户使用

用户在 Telegram 中向你的 Bot 发送 `/start`，即可获取登录链接。

## 本地开发

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

访问 `http://localhost:8000` 即可。管理后台在 `http://localhost:8000/admin.html`。

## 项目结构

```
├── backend/
│   ├── main.py           # FastAPI 主应用
│   ├── models.py         # 数据库模型
│   ├── database.py       # 数据库配置
│   ├── auth.py           # 认证模块
│   ├── telegram_bot.py   # Telegram Bot
│   ├── aws_manager.py    # AWS EC2 管理
│   ├── proxy_manager.py  # 代理管理
│   ├── depin_manager.py  # DePIN 部署管理
│   └── requirements.txt
├── frontend/
│   ├── index.html        # 主页面
│   ├── login.html        # 登录页
│   ├── admin.html        # 管理后台
│   ├── app.js            # 前端逻辑
│   └── style.css         # 样式
├── docker-compose.yml
├── Dockerfile
├── nginx.conf
└── .env.example
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | 空（通过管理后台配置） |
| `BASE_URL` | 网站公开地址 | `http://localhost:8000` |
| `ADMIN_PASSWORD` | 管理员密码 | `admin` |
| `DATABASE_URL` | 数据库连接 | `sqlite:///./data/app.db` |

## License

MIT
