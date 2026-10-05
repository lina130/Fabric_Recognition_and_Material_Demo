import serial
import time
import re

# 全局变量来存储机床连接
cnc_connection = None
cnc_port = "COM8"
cnc_baudrate = 115200

def initialize_cnc(port="COM8", baudrate=115200):
    """初始化G-code发送器"""
    global cnc_connection, cnc_port, cnc_baudrate
    
    cnc_port = port
    cnc_baudrate = baudrate
    
    try:
        cnc_connection = serial.Serial(cnc_port, cnc_baudrate, timeout=0.1)
        print("--- 串口已连接，等待设备软复位... ---")
        time.sleep(2.0)
        cnc_connection.read_all()
        cnc_connection.timeout = 10
        print("--- 设备已准备就绪 ---")
        return True
    except Exception as e:
        print(f"❌ --- CNC初始化失败: {e} ---")
        return False

def execute_command(cmd_str):
    """发送指令并等待 'ok' 或处理错误"""
    global cnc_connection
    
    if not cnc_connection:
        raise RuntimeError("CNC连接未初始化")
    
    print(f"[G-code] >>> {cmd_str}")
    cnc_connection.write((cmd_str + '\n').encode())
    
    while True:
        line = cnc_connection.readline().decode(errors='ignore').strip()
        if not line:
            raise RuntimeError(f"等待 '{cmd_str}' 的响应超时！")
        
        print(f"[G-code] <<< {line}")
        if "ok" in line.lower():
            return
        if "error" in line.lower() or "alarm" in line.lower():
            if "11" in line:
                raise ConnectionAbortedError("ALARM:11 Homing Required")
            raise RuntimeError(f"设备返回错误: {line}")

def execute_command_and_wait_complete(cmd_str):
    """发送指令并等待机床完全停止运动"""
    global cnc_connection
    
    if not cnc_connection:
        raise RuntimeError("CNC连接未初始化")
    
    print(f"[G-code] >>> {cmd_str}")
    cnc_connection.write((cmd_str + '\n').encode())
    
    # 首先等待grbl接受命令并返回'ok'
    while True:
        line = cnc_connection.readline().decode(errors='ignore').strip()
        if not line:
            raise RuntimeError(f"等待 '{cmd_str}' 的 'ok' 响应超时！")
        
        print(f"[G-code] <<< {line}")
        if "ok" in line.lower():
            break
        if "error" in line.lower() or "alarm" in line.lower():
            raise RuntimeError(f"设备返回错误: {line}")
    
    # 命令已被接受，现在等待运动完成
    wait_for_idle()
    print(f"[机床状态] ✅ 运动完成，机床已停止")

def wait_for_idle():
    """【最终优化版】等待机床进入空闲状态，采用快速、主动的轮询"""
    global cnc_connection
    if not cnc_connection:
        raise RuntimeError("CNC连接未初始化")

    start_time = time.time()
    print("[机床状态] 等待运动完成...")
    
    # 在开始查询之前，先清空串口的输入缓冲区，避免读到旧的状态信息
    cnc_connection.read_all()

    while True:
        # 持续发送状态查询指令 '?'
        cnc_connection.write("?\n".encode())
        
        # 给予一个极短的延时，等待设备响应
        time.sleep(0.05)
        
        # 尝试读取响应
        response = cnc_connection.read_until(b'>').decode(errors='ignore')
        
        # 检查响应中是否包含 'Idle' 状态
        if '<Idle' in response:
            elapsed = time.time() - start_time
            print(f"[机床状态] 检测到空闲状态，等待结束。总耗时: {elapsed:.2f}秒")
            # 额外清空一下缓冲区，确保下一条指令的干净环境
            cnc_connection.read_all()
            break # 退出循环

        # 设置超时保护
        if time.time() - start_time > 240: # 最多等待240秒
            print("⚠️ [机床状态] 等待超时！")
            break

def close_cnc():
    """关闭CNC连接"""
    global cnc_connection
    if cnc_connection and cnc_connection.is_open:
        cnc_connection.close()
        print("\n--- 串口已关闭 ---")
        cnc_connection = None