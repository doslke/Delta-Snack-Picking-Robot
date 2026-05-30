# Delta Robot 零食自动售货系统

基于 Delta 机器人 + 视觉大模型的零食自动称重售货系统。机器人通过摄像头识别零食、PID 视觉伺服抓取、HX711 称重，结果实时同步到微信小程序完成支付。

## 系统架构

```
微信小程序 (wxapp/)
    ↕ 云函数 (getCartList / validateCart / completeOrder)
微信云开发数据库 (carts / orders / products)
    ↑
machineWeigh HTTP 触发器
    ↑
控制器 Python (controller/)
    ↕ TCP :8266
ESP32 固件 (firmware/delta_robot.ino)
    → 3× MG996R 舵机 + 气泵继电器 + HX711 称重
```

---

## 目录结构

```
src/
├── firmware/
│   └── delta_robot.ino       # ESP32 固件（FreeRTOS，逆运动学，TCP 协议）
├── controller/
│   ├── main.py               # 主入口，视觉检测 + 伺服循环
│   ├── vision.py             # Qwen-VL 调用 + ArUco 检测 + 绘图
│   ├── servo.py              # PID 视觉伺服 + 下降抓取序列
│   ├── robot.py              # TCP 客户端，封装固件协议
│   ├── camera.py             # 后台摄像头线程 + 畸变校正
│   ├── pid.py                # 2D PID 控制器
│   ├── cloud.py              # 推送称重结果到云函数
│   ├── config.py             # 所有可调参数
│   ├── inventory.json        # 在售商品名称列表（可选）
│   └── dot25.npz             # 相机内参文件（可选）
└── wxapp/
    ├── miniprogram/          # 小程序前端
    └── cloudfunctions/
        ├── machineWeigh/     # 机器上报称重（HTTP 触发器）
        ├── getCartList/      # 小程序轮询购物车
        ├── validateCart/     # 验证购物车 ID
        └── completeOrder/    # 支付完成，写订单并清空购物车
```

---

## 快速开始

### 1. 固件烧录（ESP32）

**依赖库**（Arduino Library Manager 安装）：
- `HX711 by bogde`

**烧录步骤**：

1. 用 Arduino IDE 打开 `firmware/delta_robot.ino`，选择开发板 `ESP32 Dev Module`，烧录。
2. 打开串口监视器（115200 baud），首次配置 WiFi：
   ```
   setwifi <你的SSID> <你的密码>
   ```
   凭据写入 NVS flash，重启后自动连接，串口会打印分配到的 IP 地址：
   ```
   [WiFi] IP: 192.168.1.xxx  TCP:8266
   ```
3. 记录该 IP，填入 `controller/config.py` 的 `ROBOT_IP`。

**硬件接线**：

| 信号 | ESP32 引脚 |
|------|-----------|
| 舵机 0（臂 0°） | D14 |
| 舵机 1（臂 120°） | D12 |
| 舵机 2（臂 240°） | D13 |
| 气泵继电器 | D26 |
| 气泵按钮 | D27 |
| HX711 DOUT | D33 |
| HX711 SCK | D32 |

**HX711 校准**：将已知重量的砝码放上称重台，调整 `firmware/delta_robot.ino` 中的 `HX711_CALIBRATION`（默认 `2280.0`）直到读数准确，重新烧录。

---

### 2. 控制器（Python）

**环境要求**：Python 3.10+

**安装依赖**：
```bash
pip install opencv-python opencv-contrib-python numpy pillow dashscope python-dotenv
```

**配置 API Key**：

复制 `.env.example` 为 `.env`，填入阿里云 DashScope API Key：
```bash
cp .env.example .env
# 编辑 .env，填入：
# DASHSCOPE_API_KEY=your-api-key-here
```

DashScope API Key 在 [阿里云百炼控制台](https://bailian.console.aliyun.com/) 创建。控制器使用 `qwen3-vl-plus` 模型识别零食。

**配置机器人参数**（`controller/config.py`）：

```python
ROBOT_IP   = "192.168.1.xxx"   # 固件串口打印的 IP
ROBOT_PORT = 8266

CART_ID    = "CART001"         # 与小程序二维码一致
MACHINE_WEIGH_URL = "https://..."  # 云函数 HTTP 触发器 URL（见第 3 步）
```

**配置相机内参**（可选，提升精度）：

将相机标定结果保存为 `controller/dot25.npz`（包含 `mtx` 和 `dist` 字段）。未提供时跳过畸变校正。

**配置在售商品**（可选）：

编辑 `controller/inventory.json`，填入商品名称列表，VLM 识别时只会从列表中选取：
```json
["薯片", "辣条", "饼干", "糖果"]
```

**运行**：
```bash
cd src
python -m controller.main
# 可选参数：
#   --camera 1        指定摄像头索引（默认 0）
#   --no-robot        无机器人模式，仅测试视觉识别
#   --save            保存每次检测结果为 PNG
```

启动后按 `s` 开始自动扫描抓取，按 `q` 退出。

---

### 3. 微信云开发配置

#### 3.1 创建云开发环境

在微信开发者工具中打开 `wxapp/`，开通云开发，记录环境 ID。

#### 3.2 创建数据库集合

在云开发控制台 → 数据库，创建以下集合：

| 集合名 | 用途 |
|--------|------|
| `carts` | 购物车实时状态（机器写入，小程序读取） |
| `orders` | 历史订单记录 |
| `products` | 商品信息（名称、单价、图片） |
| `config` | 小程序配置（品牌 logo 等） |

**`products` 集合字段格式**：
```json
{
  "name": "薯片",
  "unitPrice": 15.80,
  "image": "cloud://xxx/snacks/chips.jpg"
}
```
`unitPrice` 单位为元/500g。

**`config` 集合**（可选，用于品牌 logo）：
```json
{ "key": "brandLogo", "value": "cloud://xxx/logo.png" }
```

#### 3.3 部署云函数

在微信开发者工具中，右键每个云函数目录 → **上传并部署（云端安装依赖）**：
- `machineWeigh`
- `getCartList`
- `validateCart`
- `completeOrder`

#### 3.4 开启 machineWeigh HTTP 触发器

1. 云开发控制台 → 云函数 → `machineWeigh` → **HTTP 触发**
2. 开启 URL 化，复制生成的 URL（形如 `https://xxx.ap-shanghai.tencentcloudapi.com/machineWeigh`）
3. 将该 URL 填入 `controller/config.py` 的 `MACHINE_WEIGH_URL`

#### 3.5 配置购物车二维码

每台机器对应一个购物车 ID（如 `CART001`），生成内容为该 ID 的二维码贴在机器上。格式要求：3-20 位字母、数字、下划线或连字符。

`controller/config.py` 中的 `CART_ID` 必须与二维码内容一致。

---

## 参数调优

### PID 视觉伺服（`controller/config.py`）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SERVO_KP` | 0.30 | 比例增益，过大会震荡 |
| `SERVO_KI` | 0.0 | 积分增益，用于消除稳态误差 |
| `SERVO_KD` | 0.1 | 微分增益，抑制超调 |
| `SERVO_TOL_PX` | 15 | 收敛判定阈值（像素） |
| `SERVO_MAX_ITER` | 25 | 最大迭代次数 |
| `SERVO_SETTLE_S` | 0.8 | 每步等待机械稳定时间（秒） |
| `CAMERA_OFFSET_U` | -70 | 相机水平安装偏移补偿（像素） |
| `CAMERA_OFFSET_V` | -40 | 相机垂直安装偏移补偿（像素） |

调整 `CAMERA_OFFSET_U/V`：让机器人移到已知位置，观察 ArUco 中心与目标点的偏差，按偏差方向修正。

### 工作空间（`controller/config.py` 和固件）

```python
WS = dict(x_min=-100, x_max=100, y_min=-100, y_max=100, z_min=50, z_max=280)
```

固件中有相同的安全限制，两者需保持一致。称重台位置 `WEIGH_X/Y/Z_MM` 在固件中被豁免工作空间检查，可超出上述范围。

---

## TCP 协议参考

控制器与固件通过 TCP :8266 通信，纯文本换行分隔。

| 发送 | 响应 | 说明 |
|------|------|------|
| `x.xx,y.yy,z.zz\n` | `[OK] x,y,z` / `[ERR] ...` | 移动到目标坐标 |
| `pos\n` | `[POS] x,y,z` + `[ANG] a0,a1,a2` | 查询当前位置 |
| `home\n` | `[OK] ...` | 归位 |
| `pump_on\n` | `[PUMP] ON` | 开气泵 |
| `pump_off\n` | `[PUMP] OFF` | 关气泵 |
| `weight\n` | `[WEIGHT] x.xx g` | 触发称重（约 1s 后返回） |
| `tare\n` | `[TARE] Done.` | 称重台归零 |
| `ping\n` | `[PONG]` | 心跳 |

连接建立后固件发送 `[HELLO] Delta Robot ready.`。按钮触发气泵时固件主动推送 `[PUMP] ON (button)`。
