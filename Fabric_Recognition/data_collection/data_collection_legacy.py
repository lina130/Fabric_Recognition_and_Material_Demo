import json
import queue
import time
import threading
from typing import Tuple
import numpy as np
from module import cnc_module
import socket
import tkinter as tk
from tkinter import messagebox

# ✅【唯一修改】全局只创建1次TK窗口，根治重复弹窗
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
# 就在你原有全局变量后面加这1行

Coordinate = Tuple[float, float]

# ===================== 网络与模式配置 =====================
TEST_UDP_IP = "127.0.0.1"
TEST_UDP_PORT = 8888
send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
CURRENT_STAGE = "wait"

# 🔥 10个材质采集配置
MATERIAL_BATCH_COUNTS = {
    1: 0, 2: 0, 3: 0, 4: 0,
    5: 0, 6: 0, 7: 0, 8: 0, 9: 0, 10: 0
}

FIXED_TRAJECTORY = ((0.0, -50.0), (-90.0, -50.0))

# ===================== 机床运动参数 =====================
DEFAULT_SPEED = 3000
DEFAULT_Z_DOWN = -25.0
DEFAULT_Z_SAFE = 0.0
DEFAULT_RAPID_FEED = 5000
DEFAULT_Z_FEED = 5000

# ===================== UDP信号发送（完全不动） =====================
def send_motion_signal(position_id: int, traj_id: int, motion_num: int, status: str):
    signal_data = {
        "position_id": position_id,
        "traj_id": traj_id,
        "motion_num": motion_num,
        "status": status,
        "stage": CURRENT_STAGE,
        "time": time.time()
    }
    try:
        send_msg = json.dumps(signal_data).encode("utf-8")
        send_sock.sendto(send_msg, (TEST_UDP_IP, TEST_UDP_PORT))
        
        target_count = MATERIAL_BATCH_COUNTS.get(position_id, 1) if CURRENT_STAGE == 'train' else 1
        if status in ["capture_1", "capture_2"]:
            print(f"[UDP] 材质:{position_id} | 进度:{motion_num}/{target_count} | {status}")
    except Exception as e:
        print(f"UDP发送失败: {e}")

# ===================== 机床轨迹运动（完全不动） =====================
def _run_line_trajectory(position_id, traj_id, start_pt, end_pt, batch_idx):
    cx = (start_pt[0] + end_pt[0]) / 2.0
    cy = (start_pt[1] + end_pt[1]) / 2.0
    dist = np.hypot(end_pt[0]-start_pt[0], end_pt[1]-start_pt[1])
    radius = dist / 2 * 0.8

    circle_start = (cx + radius, cy)
    circle_mid = (cx - radius, cy)
    
    cnc_module.execute_command_and_wait_complete("G80 G90 G17 G40")
    cnc_module.execute_command_and_wait_complete(f"G01 X{circle_start[0]:.3f} Y{circle_start[1]:.3f}")
    cnc_module.execute_command_and_wait_complete(f"G01 Z{DEFAULT_Z_DOWN} F{DEFAULT_Z_FEED}")
    time.sleep(0.1)

    arc1 = f"G02 X{circle_mid[0]:.1f} Y{circle_mid[1]:.1f} I{-radius:.1f} J0.000 F{DEFAULT_SPEED}"
    arc2 = f"G02 X{circle_start[0]:.1f} Y{circle_start[1]:.1f} I{radius:.1f} J0.000 F{DEFAULT_SPEED}"

    def full_circle_move():
        cnc_module.execute_command_and_wait_complete(arc1)
        time.sleep(0.1)
        cnc_module.execute_command_and_wait_complete(arc2)

    move_thread = threading.Thread(target=full_circle_move, daemon=True)
    move_thread.start()

    time.sleep(2.2)
    send_motion_signal(position_id, traj_id, batch_idx, "capture_1")
    time.sleep(2.8)
    send_motion_signal(position_id, traj_id, batch_idx, "capture_2")
    move_thread.join()
    
    cnc_module.execute_command_and_wait_complete(f"G01 Z{DEFAULT_Z_SAFE} F{DEFAULT_Z_FEED}")
    time.sleep(0.2)

# ===================== 机床回零（完全不动） =====================
def safe_homing():
    print("\n🏠 回零中...")
    cnc_module.execute_command("$H")
    time.sleep(3)
    cnc_module.execute_command_and_wait_complete("G01 X0 Y0 Z0 F5000")
    print("✅ 回零完成")

# ===================== 主流程（完全不动） =====================
def run_batch_collection():
    global CURRENT_STAGE
    try:
        print("\n" + "="*50)
        print("🤖 基石架构机床控制端已启动...")
        print("="*50)

        # 初始化机床
        cnc_module.initialize_cnc("COM13", 115200)
        cnc_module.execute_command("G90 G21")
        safe_homing()
        
        # 训练采集流程
        CURRENT_STAGE = "train"
        send_motion_signal(0, 0, 0, "train_start")
        
        for material_id in range(1, 11):
            target_count = MATERIAL_BATCH_COUNTS[material_id]
            if target_count <= 0:
                continue
                
            print(f"\n==================================================")
            print(f"🔔 请更换为【材质 {material_id}】")
            print(f"💡 计划采集 {target_count} 次")
            input("换好后按回车开始采集...")
            
            for i in range(1, target_count + 1):
                print(f"-> 采集中 材质:{material_id} 第{i}/{target_count}次")
                _run_line_trajectory(material_id, 1, FIXED_TRAJECTORY[0], FIXED_TRAJECTORY[1], i)
        
        print("\n✅ 所有材质采集完成！触发AI训练...")
        send_motion_signal(0, 0, 0, "train_done")
        time.sleep(5)
        print("\n✅ AI训练完成，进入识别模式")

        # 识别流程
        CURRENT_STAGE = "detect"
        print("\n🔍 材质识别循环已启动")
        
        while True:
            print(f"\n==================================================")
            input("按回车执行下一次识别...")
            _run_line_trajectory(1, 1, FIXED_TRAJECTORY[0], FIXED_TRAJECTORY[1], 1)
            time.sleep(1) 
            
    except Exception as e:
        print(f"运行错误：{str(e)}")
    finally:
        cnc_module.close_cnc()
        send_sock.close()
        print("\n🔌 机床已断开连接")

if __name__ == "__main__":
    run_batch_collection()