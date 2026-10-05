import json
import time
import threading
import numpy as np
from module import cnc_module
import socket
import tkinter as tk
from tkinter import messagebox, simpledialog
import serial.tools.list_ports
import sys

root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)

TEST_UDP_IP, TEST_UDP_PORT = "127.0.0.1", 8888
send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
LISTEN_UDP_PORT = 9999 

FIXED_TRAJECTORY = ((0.0, -50.0), (-90.0, -50.0))
DEFAULT_SPEED = 3000
DEFAULT_Z_DOWN = -37.0
DEFAULT_Z_SAFE = 0.0
DEFAULT_Z_FEED = 5000

def select_serial_port():
    print("\n🔍 正在扫描系统可用串口...")
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        messagebox.showerror("错误", "未检测到可用串口！请检查机床 USB 数据线。")
        return None
    if len(ports) == 1: return ports[0].device
        
    port_list = "\n".join([f"[{i}] {p.device} - {p.description}" for i, p in enumerate(ports)])
    choice = simpledialog.askinteger("选择机床串口", f"检测到多个串口，请输入编号(0-{len(ports)-1}):\n\n{port_list}", initialvalue=0)
    if choice is not None and 0 <= choice < len(ports): return ports[choice].device
    return None

def notify_serial_port(port_name):
    try: send_sock.sendto(json.dumps({"cmd": "serial_info", "port": port_name}).encode("utf-8"), (TEST_UDP_IP, TEST_UDP_PORT))
    except: pass

def send_motion_signal(status: str):
    try:
        send_sock.sendto(json.dumps({"status": status}).encode("utf-8"), (TEST_UDP_IP, TEST_UDP_PORT))
        print(f"[UDP] 发送信号: {status}")
    except: pass

def _run_double_trajectory(start_pt, end_pt):
    cx, cy = (start_pt[0] + end_pt[0]) / 2.0, (start_pt[1] + end_pt[1]) / 2.0
    dist = np.hypot(end_pt[0]-start_pt[0], end_pt[1]-start_pt[1])
    radius = dist / 2 * 0.8
    circle_start, circle_mid = (cx + radius, cy), (cx - radius, cy)
    
    cnc_module.execute_command_and_wait_complete("G80 G90 G17 G40")
    cnc_module.execute_command_and_wait_complete(f"G01 X{circle_start[0]:.3f} Y{circle_start[1]:.3f}")
    cnc_module.execute_command_and_wait_complete(f"G01 Z{DEFAULT_Z_DOWN} F{DEFAULT_Z_FEED}")
    time.sleep(0.1)

    arc1 = f"G02 X{circle_mid[0]:.1f} Y{circle_mid[1]:.1f} I{-radius:.1f} J0.000 F{DEFAULT_SPEED}"
    arc2 = f"G02 X{circle_start[0]:.1f} Y{circle_start[1]:.1f} I{radius:.1f} J0.000 F{DEFAULT_SPEED}"

    def double_circle_move():
        for _ in range(2):
            cnc_module.execute_command_and_wait_complete(arc1); time.sleep(0.1)
            cnc_module.execute_command_and_wait_complete(arc2); time.sleep(0.1)

    move_thread = threading.Thread(target=double_circle_move, daemon=True); move_thread.start()

    time.sleep(2.2); send_motion_signal("capture_1")
    time.sleep(2.8); send_motion_signal("capture_2")
    time.sleep(2.2); send_motion_signal("capture_3")
    time.sleep(2.8); send_motion_signal("capture_4")
    
    move_thread.join()
    cnc_module.execute_command_and_wait_complete(f"G01 Z{DEFAULT_Z_SAFE} F{DEFAULT_Z_FEED}")
    time.sleep(0.2)

def run_demo_mode():
    target_com_port = select_serial_port()
    if not target_com_port: return
    notify_serial_port(target_com_port)
        
    print(f"⚙️ 正在连接机床 ({target_com_port})...")
    cnc_module.initialize_cnc(target_com_port, 115200)
    cnc_module.execute_command("G90 G21")
    
    print("🏠 机床复位回零中...")
    cnc_module.execute_command("$H")
    time.sleep(3)
    cnc_module.execute_command_and_wait_complete("G01 X0 Y0 Z0 F5000")
    print("✅ 回零完成，进入识别模式监听！")

    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_sock.bind(("0.0.0.0", LISTEN_UDP_PORT))
    
    while True:
        try:
            data, _ = listen_sock.recvfrom(1024)
            cmd = json.loads(data.decode('utf-8')).get("cmd")
            if cmd == "start_detect_cycle":
                print("⚡ 收到 GUI 触发信号，电机开始运动！")
                _run_double_trajectory(FIXED_TRAJECTORY[0], FIXED_TRAJECTORY[1])
        except Exception as e:
            print(f"运行错误：{e}")
            time.sleep(0.1)

if __name__ == "__main__":
    try:
        run_demo_mode()
    finally:
        cnc_module.close_cnc()
        send_sock.close()