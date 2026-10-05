import serial
import time
import threading
from datetime import datetime
import os

# 全局变量来存储传感器状态
sensor_port = "COM5"
sensor_baudrate = 460800
sensor_connection = None
is_running = False
sensor_thread = None
data_count = 0

output_file_handler = None # 直接保存文件句柄
output_filename = "" # 保存文件名

def create_output_file(main_category, sub_category, velocity, trajectory_id, repeat_num):
    """创建并打开一个TXT文件用于数据记录,使用分层文件夹结构"""
    global output_file_handler, output_filename
    
    # 创建分层文件夹结构: m1/b1/v500/traj1/
    folder_path = os.path.join(main_category, sub_category, f"v{velocity}", f"traj{trajectory_id}")
    
    # 如果文件夹不存在,则创建
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_path}")
    
    # 文件命名格式: r{重复次数}.txt
    filename = os.path.join(folder_path, f"r{repeat_num}.txt")
    
    # 如果文件已存在,添加额外的序号后缀
    if os.path.exists(filename):
        base_name = os.path.join(folder_path, f"r{repeat_num}")
        counter = 1
        while os.path.exists(f"{base_name}_({counter}).txt"):
            counter += 1
        filename = f"{base_name}_({counter}).txt"
    
    try:
        # 直接打开文件并保持句柄,使用行缓冲模式
        output_file_handler = open(filename, 'w', encoding='utf-8', buffering=1)
        output_filename = filename
        print(f"✅ 传感器数据将实时保存到TXT文件: {filename}")
        return True
    except Exception as e:
        print(f"❌ 创建TXT输出文件失败: {e}")
        output_file_handler = None
        return False

def write_sensor_data_to_txt(parsed_values):
    """将解析后的三个力数据写入TXT文件(每行格式: f1 f2 f3)"""
    global output_file_handler
    if output_file_handler and parsed_values and len(parsed_values) >= 3:
        try:
            # 只保存前三个力数据,用空格分隔
            line = f"{parsed_values[0]:.2f} {parsed_values[1]:.2f} {parsed_values[2]:.2f}\n"
            output_file_handler.write(line)
            output_file_handler.flush()  # 立即刷新到磁盘
        except Exception as e:
            print(f"⚠️ 写入TXT文件失败: {e}")

def configure_sensor():
    """配置触觉传感器参数"""
    global sensor_connection
    try:
        print("--- 开始配置触觉传感器 ---")
        config_commands = ["100 Hz,2"]
        for cmd in config_commands:
            print(f"[传感器配置] 发送: {cmd}")
            sensor_connection.write((cmd + '\n').encode())
            time.sleep(0.2)
        time.sleep(1)
        sensor_connection.read_all()
        print("✅ --- 触觉传感器配置完成 ---")
    except Exception as e:
        print(f"⚠️ 传感器配置失败: {e}")

def parse_sensor_data(data_line):
    """解析传感器数据行,返回数值列表"""
    try:
        data_line = data_line.strip()
        if not data_line: 
            return None
        
        # 移除 'd0' 前缀(如果存在)
        if data_line.startswith('d0'):
            data_line = data_line[2:].strip()
        
        # 按空格分割
        parts = data_line.split()
        numbers = [float(p) for p in parts if p and p.replace('.', '').replace('-', '').isdigit()]
        
        if len(numbers) >= 3:
            return numbers
        return None
    except Exception as e:
        return None

def is_contact_data(parsed_values):
    """判断是否为接触数据"""
    if not parsed_values or len(parsed_values) < 3: 
        return False
    return not all(abs(val) < 0.1 for val in parsed_values[:3])

def sensor_read_loop():
    """持续读取传感器数据的循环"""
    global sensor_connection, is_running, data_count
    
    print("✅ --- 传感器数据监控开始 ---")
    
    while is_running:
        try:
            if sensor_connection and sensor_connection.is_open:
                line = sensor_connection.readline().decode(errors='ignore').strip()
                
                if line:
                    timestamp_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    
                    # 解析数据
                    parsed_values = parse_sensor_data(line)
                    
                    if parsed_values:
                        data_count += 1
                        # 写入文件(只写三个力数据)
                        write_sensor_data_to_txt(parsed_values)
                        
                        # 控制台美化输出
                        values_str = " ".join([f"{v:7.2f}" for v in parsed_values[:3]])
                        if is_contact_data(parsed_values):
                            print(f" [{timestamp_str}] ✅接触: [{values_str}] (#{data_count})")
                        else:
                            print(f" [{timestamp_str}] 🔵基准: [{values_str}] (#{data_count})")
                    else:
                        # 非数据行,可能是配置信息
                        if line and not line.startswith('d0'):
                            print(f" [{timestamp_str}] {line}")
                else:
                    time.sleep(0.01)
            else:
                break
        except Exception as e:
            print(f"⚠️ [传感器错误] {e}")
            time.sleep(0.1)
    
    print(f"✅ [传感器状态] 数据监控结束 (总共收集 {data_count} 条数据)")

def start_sensor_reading(port="COM5", baudrate=115200, main_category="m1", sub_category="b1", velocity=500, trajectory_id=1, repeat_num=1):
    """开始读取传感器数据"""
    global sensor_port, sensor_baudrate, sensor_connection, is_running, sensor_thread, data_count
    
    sensor_port = port
    sensor_baudrate = baudrate
    data_count = 0  # 重置计数
    
    try:
        if not create_output_file(main_category, sub_category, velocity, trajectory_id, repeat_num):
            print("⚠️ 警告:无法创建输出文件,数据将只在控制台显示")
        
        sensor_connection = serial.Serial(sensor_port, sensor_baudrate, timeout=1.0)
        print(f"✅ --- 触觉传感器端口已连接到 {sensor_port} ---")
        
        time.sleep(1.0)
        sensor_connection.read_all()
        
        configure_sensor()
        
        is_running = True
        sensor_thread = threading.Thread(target=sensor_read_loop, daemon=True)
        sensor_thread.start()
        
        print("✅ --- 传感器监控线程已启动 ---")
        
    except Exception as e:
        print(f"❌ 传感器启动失败: {e}")
        is_running = False

def stop_sensor_reading():
    """停止传感器读取并关闭文件"""
    global is_running, sensor_connection, sensor_thread, output_file_handler
    
    print("--- 正在停止传感器监控 ---")
    is_running = False
    
    if sensor_thread and sensor_thread.is_alive():
        sensor_thread.join(timeout=2)
    
    # 关闭文件句柄
    if output_file_handler:
        try:
            output_file_handler.flush()  # 确保所有数据写入
            output_file_handler.close()
            print(f"✅ TXT数据文件 '{output_filename}' 已成功保存,共{data_count}条记录")
            output_file_handler = None
        except Exception as e:
            print(f"⚠️ 关闭TXT文件时出错: {e}")

    if sensor_connection and sensor_connection.is_open:
        sensor_connection.close()
        print("✅ --- 传感器串口连接已关闭 ---")