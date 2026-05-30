import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# ==================== 基础配置 ====================
JSON_OUTPUT = "snack_output.json"
MAX_WORKERS = 10

# ==================== 主窗口 ====================
app = ttk.Window(
    title="零食数据分发终端",
    themename="cyborg",   # 深色主题
    size=(960, 660),
    resizable=(False, False),
)
app.place_window_center()

style = ttk.Style()
style.configure("TLabel", font=("微软雅黑", 11))
style.configure("TButton", font=("微软雅黑", 11))
style.configure("TEntry", font=("微软雅黑", 11))
style.configure("dim.TLabel", font=("微软雅黑", 10), foreground="#6C7086")

# ==================== 变量 ====================
csv_var   = ttk.StringVar()
robot_var = ttk.StringVar()
snack_data: list = []
robot_list: list = []

# ==================== 布局 ====================
root = ttk.Frame(app, padding=(20, 16))
root.pack(fill=BOTH, expand=YES)

# 标题
ttk.Label(root, text="零食数据分发终端",
          font=("微软雅黑", 20, "bold"), bootstyle="info").pack(anchor=W)
ttk.Separator(root).pack(fill=X, pady=(8, 12))

# 文件选择区
file_frame = ttk.LabelFrame(root, text=" 数据文件 ", padding=(12, 8), bootstyle="secondary")
file_frame.pack(fill=X, pady=(0, 12))

def _file_row(parent, label, var, filetypes):
    row = ttk.Frame(parent)
    row.pack(fill=X, pady=4)
    ttk.Label(row, text=label, width=16).pack(side=LEFT)
    ttk.Entry(row, textvariable=var, width=52).pack(side=LEFT, padx=(0, 8))
    def browse():
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            var.set(path)
    ttk.Button(row, text="浏览", bootstyle="outline-secondary",
               width=8, command=browse).pack(side=LEFT)

_file_row(file_frame, "商品 CSV",
          csv_var,   [("CSV 文件", "*.csv"), ("所有文件", "*.*")])
_file_row(file_frame, "机器人 JSON",
          robot_var, [("JSON 文件", "*.json"), ("所有文件", "*.*")])

# 按钮行
btn_frame = ttk.Frame(root)
btn_frame.pack(fill=X, pady=(0, 12))

btn_load = ttk.Button(btn_frame, text="加载数据", bootstyle="success",
                      width=14, command=lambda: on_load())
btn_send = ttk.Button(btn_frame, text="批量下发", bootstyle="primary",
                      width=14, command=lambda: on_send())
btn_exit = ttk.Button(btn_frame, text="退出", bootstyle="danger",
                      width=10, command=app.destroy)

btn_load.pack(side=LEFT)
btn_send.pack(side=LEFT, padx=(12, 0))
btn_exit.pack(side=RIGHT)

# 日志区
ttk.Separator(root).pack(fill=X, pady=(0, 8))
ttk.Label(root, text="运行日志", bootstyle="secondary",
          font=("微软雅黑", 10)).pack(anchor=W)

log_box = ScrolledText(root, height=18, font=("Consolas", 10), state=DISABLED,
                       bg="#1a1a2e", fg="#CDD6F4", insertbackground="#CDD6F4",
                       relief="flat", borderwidth=0)
log_box.pack(fill=BOTH, expand=YES, pady=(4, 8))

# 状态栏
status_var = ttk.StringVar(value="就绪")
ttk.Label(root, textvariable=status_var, style="dim.TLabel").pack(anchor=W)


# ==================== 日志 ====================
def log(msg: str):
    log_box["state"] = NORMAL
    log_box.insert(END, msg + "\n")
    log_box.see(END)
    log_box["state"] = DISABLED
    status_var.set(msg[:100])
    app.update_idletasks()


# ==================== CSV 解析 ====================
def parse_snack_csv(csv_path: str):
    try:
        for enc in ("utf-8-sig", "gbk", "gb18030", "latin-1"):
            try:
                df = pd.read_csv(csv_path, encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return False, "无法识别文件编码，请将 CSV 另存为 UTF-8 格式"

        required = [
            "sku", "name", "short_name", "category", "price", "weight",
            "length", "width", "height", "z_pick", "z_up", "grab_type",
            "place", "keywords", "remark",
        ]
        for col in required:
            if col not in df.columns:
                return False, f"缺少字段：{col}"

        goods = []
        for row_num, (_, row) in enumerate(df.iterrows(), start=2):
            try:
                goods.append({
                    "sku":        str(row["sku"]).strip(),
                    "name":       str(row["name"]).strip(),
                    "short_name": str(row["short_name"]).strip(),
                    "category":   str(row["category"]).strip(),
                    "price":      round(float(row["price"]), 2),
                    "weight":     round(float(row["weight"]), 2),
                    "size_mm": {
                        "length": int(row["length"]),
                        "width":  int(row["width"]),
                        "height": int(row["height"]),
                    },
                    "z_pick":     int(row["z_pick"]),
                    "z_up":       int(row["z_up"]),
                    "grab_type":  str(row["grab_type"]).strip(),
                    "place":      str(row["place"]).strip(),
                    "keywords":   [k.strip() for k in str(row["keywords"]).split(",")],
                    "remark":     str(row["remark"]).strip(),
                })
            except Exception as e:
                log(f"  ⚠️ 第 {row_num} 行跳过：{e}")
                continue

        if not goods:
            return False, "没有可用的商品数据，所有行均解析失败"

        names = [g["name"] for g in goods]
        with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
            json.dump(names, f, ensure_ascii=False, indent=2)

        return True, goods

    except Exception as e:
        return False, f"解析失败：{e}"


# ==================== 加载机器人列表 ====================
def load_robot_list(json_path: str):
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            robots = json.load(f)
        if not isinstance(robots, list):
            return False, "机器人数据必须是数组"
        for r in robots:
            if "ip" not in r or "port" not in r:
                return False, "缺少 ip 或 port 字段"
        return True, robots
    except Exception as e:
        return False, f"读取失败：{e}"


# ==================== 下发 ====================
def send_single_robot(robot: dict, data: list) -> str:
    ip, port = robot["ip"], robot["port"]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(6)
        s.connect((ip, port))
        s.sendall((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"))
        s.recv(2048)
        s.close()
        return f"✅ {ip}:{port} 下发成功"
    except Exception as e:
        return f"❌ {ip}:{port} 下发失败：{e}"


def batch_send(robots: list, data: list):
    log(f"\n开始向 {len(robots)} 台机器人分发...")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for res in ex.map(lambda r: send_single_robot(r, data), robots):
            log(res)
            if "✅" in res:
                ok += 1
            else:
                fail += 1
    log(f"\n{'─' * 48}")
    log(f"完成 | 成功：{ok}  失败：{fail}")
    messagebox.showinfo("分发完成", f"成功：{ok} 台\n失败：{fail} 台")


# ==================== 事件处理 ====================
def on_load():
    global snack_data, robot_list
    csv_path   = csv_var.get().strip()
    robot_path = robot_var.get().strip()
    if not csv_path or not robot_path:
        messagebox.showerror("错误", "请选择两个文件！")
        return

    log("─" * 48)
    log("正在解析商品数据...")

    ok, result = parse_snack_csv(csv_path)
    if not ok:
        log(result)
        messagebox.showerror("解析失败", result)
        return

    ok2, robots = load_robot_list(robot_path)
    if not ok2:
        log(robots)
        messagebox.showerror("机器人文件错误", robots)
        return

    snack_data = result
    robot_list = robots
    log(f"✅ 解析成功，已导出 {JSON_OUTPUT}")
    log(f"✅ 商品 {len(snack_data)} 个，机器人 {len(robot_list)} 台")
    log("商品列表：")
    for i, item in enumerate(snack_data, 1):
        log(f"  {i:>3}. {item['name']}")
    log("─" * 48)
    messagebox.showinfo("加载成功", f"商品：{len(snack_data)} 个\n机器人：{len(robot_list)} 台")


def on_send():
    if not snack_data or not robot_list:
        messagebox.showerror("无法下发", "请先加载数据！")
        return
    threading.Thread(target=batch_send, args=(robot_list, snack_data), daemon=True).start()


app.mainloop()
