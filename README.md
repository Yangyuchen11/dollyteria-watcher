# Dollyteria (dollyteria.com) 上新提醒小工具

专为日本中古娃娃/周边专门店 **Dollyteria** (`https://www.dollyteria.com/`) 打造的定点上新与有货监控提醒小工具，完全使用 **Python** 开发。

---

## ✨ 核心功能亮点

1. **定点准时触发 (Scheduled Drop Trigger)**
   - Dollyteria 经常在晚间固定时刻批量上架新商品（如每日 `20:00:00` 或 `21:00:00`）。
   - 本工具支持秒级定时触发，到达指定时刻自动启动 **“爆发抢购模式 (Burst Mode)”**（每 1.5 ~ 2 秒高频轮询检测，持续 3 分钟），第一时间捕获刚上架的商品。
   - 非定点时段支持平缓定期巡检（如每 5 分钟），兼顾日常捡漏与防封禁。

2. **多重在库过滤与多词同时满足 (AND / OR 灵活模式)**
   - **多词同时满足 (AND 模式)**：支持设定多个关键词全部同时满足才触发（例如 `DearSD` + `はな`、`ブライス` + `OF`），搜索时自动将多个词合并为空格联合检索，过滤掉单一名词的干扰信息。
   - **满足其一即可 (OR 模式)**：适合同义词匹配（例如 `ブライス` 或 `Blythe`）。
   - **排除关键词**：如 `難あり`、`ジャンク` 等自动过滤瑕疵品，配合**价格区间筛选**。
   - **双重在库检验**：自动结合官方 `available=1` 在库筛选以及图片水印中 hex 编码的 `SoldOut 売切` 标识，确保**只提醒真正有货且能加购的商品**。

3. **智能去重与基准线 (Deduplication)**
   - 采用持久化状态缓存（`data/state.json`），已发现过的在售商品不会重复轰炸报警。
   - 支持**“一键收录当前库存为基准线”**，避免初次启动时将既有老库存全部当作新商品报警。

4. **即时多渠道提醒 (Multi-Channel Notifiers)**
   - 💻 **Windows 原生桌面弹窗 (Toast)**：右下角气泡横幅通知，**点击直接在浏览器中打开该商品详情页**。
   - 🔊 **系统提示音**：发现新商品时播放清脆提示音（Windows `winsound` / Web Audio 欢快多音阶）。
   - 🌐 **Web 实时流 (SSE)**：网页端保持实时长连接推送，无需手动刷新。
   - 👾 **Discord Webhook 提醒**：内置 Discord 官方 Webhook，将包含商品高清缩略图、日元价格、状态与官网直达链接的精美 Embed 卡片实时推送到指定的 Discord 频道。
   - 📧 **邮件通知 (SMTP)**：内置标准 SMTP 协议，支持 QQ、163、Gmail 等邮箱发送精美 HTML 图文清单。

5. **自动下单扩展接口 (Auto-Order Hook)**
   - 在 `hooks/auto_order.py` 中预留了完整的自动化加购与下单流程骨架。
   - 默认开启 **Dry-Run (仿真测试模式)**，安全可靠，为后续接入全自动抢购留好标准接口。

---

## 🚀 快速启动

### 方式一：双击启动 (推荐)
在项目根目录下，直接双击运行：
```text
run.bat
```
会自动启动服务并在默认浏览器中弹出打开 **Web 管理控制看板** (`http://127.0.0.1:5000`)。

---

### 方式二：命令行启动

1. **启动 Web 控制看板 (带后台监控调度)**：
   ```bash
   python main.py
   ```
   *若不需要自动打开浏览器，可附加 `--no-browser` 参数。*

2. **单次即时检测 (检测完毕打印结果后退出)**：
   ```bash
   python main.py --check
   ```

3. **纯终端后台监控模式 (CLI)**：
   ```bash
   python main.py --cli
   ```

---

## 📁 项目工程结构

```text
dollyteria/
├── Dockerfile                # Docker 镜像构建配置 (针对云端 / Linux 深度优化)
├── docker-compose.yml        # Docker Compose 生产与测试容器编排
├── deploy.sh                 # 腾讯云 Lighthouse 一键部署与运维管理脚本
├── .dockerignore             # Docker 构建忽略规则
├── config.json               # 配置文件 (规则、调度时间、通知开关)
├── run.bat                   # Windows 本地一键双击启动脚本
├── main.py                   # 程序主入口 (支持 Web / CLI / Check 单次)
├── requirements.txt          # Python 依赖清单
├── core/
│   ├── scraper.py            # Dollyteria 页面抓取与解析模块
│   ├── monitor.py            # 监控核心引擎 (规则匹配、在库过滤、去重)
│   └── scheduler.py          # 定时调度器 (定点准时触发与爆发模式)
├── notifiers/
│   ├── base.py               # 通知基类接口
│   ├── sound.py              # 声音提醒 (Windows winsound / 终端蜂鸣)
│   ├── desktop.py            # Windows 原生气泡通知 (点击直达详情页)
│   ├── discord.py            # Discord Webhook 富文本 Embed 推送模块
│   ├── email_notify.py       # SMTP HTML 邮件通知模块
│   └── manager.py            # 通知中心调度器
├── hooks/
│   └── auto_order.py         # 自动下单 / 自动加购扩展接口 (支持代金引换与店頭自提)
├── web/
│   ├── app.py                # Flask 后端 REST / SSE API
│   ├── templates/
│   │   └── index.html        # Web 控制看板 HTML
│   └── static/
│       ├── style.css         # 极简现代深色玻璃拟物风格样式
│       └── app.js            # 前端数据交互与实时更新逻辑
└── data/
    └── state.json            # 已记录商品 ID 与历史命中记录
```

---

## 🛠️ 配置说明与自定义

所有配置均可在网页看板中直观修改并实时保存，也可以直接编辑根目录下的 `config.json`：

```json
{
  "watches": [
    {
      "id": "w_blythe",
      "name": "Blythe 小布系列",
      "keywords": ["ブライス", "Blythe"],
      "exclude_keywords": ["難あり", "ジャンク"],
      "min_price": 0,
      "max_price": 0,
      "enabled": true,
      "match_mode": "all",
      "search_mode": "both"
    }
  ],
  "schedule": {
    "enabled": true,
    "drop_times": [
      "20:00:00",
      "21:00:00"
    ],
    "burst_duration_sec": 180,
    "burst_interval_sec": 2.0,
    "interval_minutes": 5,
    "interval_enabled": true
  },
  "notifications": {
    "sound_enabled": true,
    "desktop_enabled": true,
    "discord": {
      "enabled": true,
      "webhook_url": "https://discord.com/api/webhooks/xxxx/yyyy"
    },
    "email": {
      "enabled": true,
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "smtp_user": "your_email@gmail.com",
      "smtp_password": "16位应用密码",
      "receiver_email": "receiver@gmail.com",
      "use_ssl": true
    }
  },
  "auto_order": {
    "enabled": false,
    "dry_run": true,
    "payment_method": "cod",
    "username": "your_email@gmail.com",
    "password": "your_password"
  }
}
```

---

## 🚀 部署至腾讯云轻量应用服务器 (Tencent Cloud Lighthouse)

将本项目部署在 [腾讯云轻量应用服务器 (Lighthouse)](https://www.tencentcloud.com/product/lighthouse) 上，即可实现 **24 小时云端全天候无间断上新监控与抢购**，即使本地电脑关机也绝不漏单。

### 1. 为什么选择腾讯云 Lighthouse？
- **极速低延迟 (强烈推荐选择「中国香港」或「日本东京」地域)**：直连日本 Dollyteria 官网，探测与抢购延迟低至数十毫秒，远超本地宽带。
- **全自动化 Docker 容器编排**：一键拉取镜像与部署，隔离环境无污染。
- **时区精准对齐 (`Asia/Tokyo`)**：镜像已锁定日本东京时间 (JST)，使您在面板设置的 `20:00:00`、`21:00:00` 等时刻与 Dollyteria 官网毫秒级对齐。
- **数据永久持久化**：挂载宿主机目录 `./data:/app/data`，无论容器如何重启或拉取最新代码，您的监控规则、账号与历史记录永不丢失。

---

### 2. 极速部署四步走

#### 第 ① 步：准备服务器与放行防火墙端口
1. 在腾讯云 Lighthouse 控制台创建实例，**镜像建议选择：应用镜像 `Docker-CE`**（或系统镜像 `Ubuntu 22.04 LTS / Debian 12`）。
2. **放行端口 (重要)**：
   - 进入该 Lighthouse 实例详情 ➔ 点击 **【防火墙】** 标签页；
   - 点击 **【添加规则】** ➔ 协议选择 `TCP`，端口填写 `5000`，备注 `Dollyteria 看板` ➔ 点击确定。

#### 第 ② 步：登录服务器并拉取代码
通过 SSH 或腾讯云控制台的 **Web Shell (一键登录)** 连接到服务器终端：
```bash
# 1. 安装 git (如已有可跳过)
sudo apt-get update && sudo apt-get install -y git

# 2. 克隆项目仓库至服务器
git clone https://github.com/Yangyuchen11/dollyteria-watcher.git dollyteria
cd dollyteria
```

#### 第 ③ 步：一键运行服务
运行自带的一键部署脚本：
```bash
bash deploy.sh start
```
脚本会自动构建 Docker 镜像并在后台安全启动容器，同时把数据目录映射到 `./data`。

#### 第 ④ 步：访问 Web 管理看板
打开浏览器，访问：
```text
http://<您的Lighthouse公网IP>:5000
```
即可直接进入深色拟物控制看板！在「🔔 通知渠道」填入 Discord Webhook 或 Gmail，手机即可 24 小时接收实时图文上新推送。

---

### 3. 日常便捷运维命令

在服务器 `dollyteria` 目录下，直接使用自带脚本进行管理：
```bash
bash deploy.sh logs      # 查看后台实时检测与上新日志 (Ctrl+C 退出)
bash deploy.sh restart   # 重启服务
bash deploy.sh stop      # 停止服务
bash deploy.sh update    # 一键拉取 Git 最新代码并重新构建更新
bash deploy.sh status    # 查看容器当前运行状态与端口
```

---

## 💡 使用小技巧

1. **首次使用推荐操作**：
   - 首次打开 Web 看板后，点击右上角看板指标上的 **“收录当前库存”**。这样系统会先将店铺当前已有的大几千件库存记录下来，之后一旦有**新上架**或**新补货**的商品，就会立即精准报警。
2. **抢购黄金 3 分钟**：
   - Dollyteria 每次上新一般需要 1~3 分钟才能将全部商品上传完毕。系统已设置在命中预设时刻（如 `21:00:00`）时，自动进行 180 秒的高频抓取，确保每一批上传的商品都不会漏掉。
3. **多端协同提醒**：
   - 部署在腾讯云 Lighthouse 后，通过配置 **Discord Webhook** 与 **邮件 (SMTP)** 通知，即便出门在外，手机也能在商品上架的第一秒收到包含大图与直达链接的高级卡片推送！

