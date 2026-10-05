import json
import queue
import time
import threading
from typing import Tuple
import numpy as np
from module import cnc_module
import socket
import tkinter as tk
from tkinter import messagebox, simpledialog
import serial.tools.list_ports
import tempfile
import subprocess
import sys
import os
import multiprocessing
multiprocessing.freeze_support()
multiprocessing.set_start_method('spawn', force=True)

# ✅ 全局只创建1次TK窗口，根治重复弹窗
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)

Coordinate = Tuple[float, float]

TEST_UDP_IP = "127.0.0.1"
TEST_UDP_PORT = 8888
send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
CURRENT_STAGE = "wait"

COMMAND_QUEUE = queue.Queue()
LISTEN_UDP_PORT = 9999 

MATERIAL_BATCH_COUNTS = {
    1: 0, 2: 0, 3: 0, 4: 0,
    5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0
}

FIXED_TRAJECTORY = ((0.0, -50.0), (-90.0, -50.0))

DEFAULT_SPEED = 3000
DEFAULT_Z_DOWN = -37.0
DEFAULT_Z_SAFE = 0.0
DEFAULT_RAPID_FEED = 5000
DEFAULT_Z_FEED = 5000

# ===================== 自动检测串口 (改用 GUI 弹窗) =====================
def select_serial_port():
    print("\n🔍 正在扫描系统可用串口...")
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        messagebox.showerror("错误", "未检测到可用串口！请检查机床 USB 数据线。")
        return None
    
    if len(ports) == 1:
        print(f"👉 仅检测到一个串口，自动选择: {ports[0].device}")
        return ports[0].device
        
    port_list = "\n".join([f"[{i}] {p.device} - {p.description}" for i, p in enumerate(ports)])
    choice = simpledialog.askinteger("选择机床串口", f"检测到多个串口，请输入编号(0-{len(ports)-1}):\n\n{port_list}", initialvalue=0)
    
    if choice is not None and 0 <= choice < len(ports):
        print(f"✅ 已选择机床串口: {ports[choice].device}")
        return ports[choice].device
    return None

# 🔥 新增：专门向上位机发送串口信息的 UDP 通道
def notify_serial_port(port_name):
    try:
        msg = json.dumps({"cmd": "serial_info", "port": port_name}).encode("utf-8")
        send_sock.sendto(msg, (TEST_UDP_IP, TEST_UDP_PORT))
    except: pass

def send_motion_signal(position_id: int, traj_id: int, motion_num: int, status: str):
    try:
        signal_data = {"position_id": position_id, "traj_id": traj_id, "motion_num": motion_num, "status": status, "stage": CURRENT_STAGE, "time": time.time()}
        send_sock.sendto(json.dumps(signal_data).encode("utf-8"), (TEST_UDP_IP, TEST_UDP_PORT))
        if "capture" in status: print(f"[UDP] 正在传输信号: {status}")
    except: pass

def listen_for_gui_commands():
    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_sock.bind(("0.0.0.0", LISTEN_UDP_PORT))
    print(f"\n🎧 联动开启：后台正在监听上位机指令 (端口: {LISTEN_UDP_PORT})...")
    while True:
        try:
            data, _ = listen_sock.recvfrom(1024)
            cmd = json.loads(data.decode('utf-8')).get("cmd")
            if cmd in ["start_detect_cycle", "homing"]:
                COMMAND_QUEUE.put(cmd)
        except: time.sleep(0.1)

def _run_double_trajectory(position_id, traj_id, start_pt, end_pt, batch_idx):
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

    time.sleep(2.2); send_motion_signal(position_id, traj_id, batch_idx, "capture_1")
    time.sleep(2.8); send_motion_signal(position_id, traj_id, batch_idx, "capture_2")
    time.sleep(2.2); send_motion_signal(position_id, traj_id, batch_idx, "capture_3")
    time.sleep(2.8); send_motion_signal(position_id, traj_id, batch_idx, "capture_4")
    
    move_thread.join()
    cnc_module.execute_command_and_wait_complete(f"G01 Z{DEFAULT_Z_SAFE} F{DEFAULT_Z_FEED}")
    time.sleep(0.2)

def run_batch_collection():
    global CURRENT_STAGE
    try:
        print("🤖 下位机驱动进程已启动...")
        target_com_port = select_serial_port()
        if not target_com_port: return
            
        # 🔥 获取到串口后，立刻通过 UDP 汇报给前端 UI 显示
        notify_serial_port(target_com_port)
            
        print(f"⚙️ 正在连接机床 ({target_com_port})...")
        if not cnc_module.initialize_cnc(target_com_port, 115200):
            messagebox.showerror("错误", f"CNC串口初始化失败: {target_com_port}")
            return

        # 先归零，再切换到绝对坐标和毫米单位，避免 homing 未完成时直接发送 G90/G21
        print("🏠 机床首次复位回零中...")
        cnc_module.execute_command("$H")
        time.sleep(3)
        cnc_module.execute_command("G90 G21")
        cnc_module.execute_command_and_wait_complete("G01 X0 Y0 Z0 F5000")
        print("✅ 回零完成")
        
        CURRENT_STAGE = "train"
        send_motion_signal(0, 0, 0, "train_start")
        
        for material_id in range(1, 11):
            target_count = MATERIAL_BATCH_COUNTS[material_id]
            if target_count <= 0: continue
            messagebox.showinfo("采集中", f"请更换为【材质 {material_id}】\n换好后点击确定，机床将立刻动作！")
            for i in range(1, target_count + 1):
                print(f"-> 采集中 材质:{material_id} 第{i}/{target_count}次")
                _run_double_trajectory(material_id, 1, FIXED_TRAJECTORY[0], FIXED_TRAJECTORY[1], i)
        
        print("✅ 采集完毕！发送训练指令并等待...")
        send_motion_signal(0, 0, 0, "train_done")
        time.sleep(2)

        CURRENT_STAGE = "detect"
        print("🔍 已进入【识别模式】等待上位机下发指令...")
        
        threading.Thread(target=listen_for_gui_commands, daemon=True).start()
        
        while True:
            try:
                cmd = COMMAND_QUEUE.get(timeout=0.5)
                if cmd == "start_detect_cycle":
                    print("⚡ 收到 GUI 触发信号，电机开始运动！")
                    _run_double_trajectory(1, 1, FIXED_TRAJECTORY[0], FIXED_TRAJECTORY[1], 1)
                elif cmd == "homing":
                    print("\n🛑 收到归零指令！准备进行串口资源切换...")
                    # 1. 彻底关闭当前串口占用
                    cnc_module.close_cnc()
                    time.sleep(1.5) # 给操作系统 1.5 秒时间完全释放 COM 口
                    
                    # 2. 生成独立回零脚本，直接接收串口号作为参数，不再重新扫描
                    homing_script = f"""
import sys
import time
from module import cnc_module
port = sys.argv[1] # 接收父进程传来的端口号
print(f"🚀 [独立进程] 正在连接串口: {{port}}")
if cnc_module.initialize_cnc(port, 115200):
    print("--- 开始执行回零 ($H) ---")
    cnc_module.execute_command("$H")
    time.sleep(3)
    cnc_module.execute_command_and_wait_complete("G01 X0 Y0 Z0 F5000")
    print("✅ --- 回零成功！---")
    cnc_module.close_cnc()
else:
    print("❌ [独立进程] 串口连接失败")
"""
                    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".py", encoding="utf-8") as f:
                        f.write(homing_script)
                        tmp_path = f.name
                        
                    print(f"🚀 启动独立进程执行归零 (串口: {target_com_port})...")
                    # 传递 target_com_port 作为参数
                    subprocess.run([sys.executable, tmp_path, target_com_port])
                    os.remove(tmp_path)
                    
                    # 3. 独立进程结束后，主进程重连
                    print("🔄 归零进程退出。主进程重连串口中...")
                    time.sleep(1)
                    retry_count = 0
                    while retry_count < 3:
                        if cnc_module.initialize_cnc(target_com_port, 115200):
                            cnc_module.execute_command("G90 G21")
                            print("✅ 主进程重新接管完成，系统恢复就绪！")
                            break
                        else:
                            print(f"⚠️ 重连失败，正在重试 ({retry_count+1}/3)...")
                            time.sleep(1)
                            retry_count += 1
            
            except Exception as e: print(f"运行错误：{str(e)}")
    finally:
            cnc_module.close_cnc()
            send_sock.close()

if __name__ == "__main__":
    run_batch_collection()