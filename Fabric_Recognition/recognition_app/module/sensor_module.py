import serial
import time
import threading
from datetime import datetime
import os
import matplotlib
matplotlib.use('TkAgg')  # 使用TkAgg后端，支持多线程
import matplotlib.pyplot as plt
from collections import deque
import atexit

# 全局变量来存储传感器状态
sensor_port = "COM5"
sensor_baudrate = 460800
sensor_connection = None
is_running = False
sensor_thread = None
data_count = 0

output_file_handler = None # 直接保存文件句柄
output_filename = "" # 保存文件名

# 实时绘图相关变量
plot_enabled = False
plot_thread = None
fig = None
ax = None
lines = []
data_buffer_x = deque(maxlen=500)  # 最多显示500个数据点
data_buffer_y = deque(maxlen=500)
data_buffer_z = deque(maxlen=500)
plot_lock = threading.Lock()
plot_running = False

def create_output_file(main_category, sub_category, velocity, trajectory_id, repeat_num, allow_suffix=True, metadata=None):
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
    
    # 如果文件已存在
    if os.path.exists(filename):
        if allow_suffix:
            base_name = os.path.join(folder_path, f"r{repeat_num}")
            counter = 1
            while os.path.exists(f"{base_name}_({counter}).txt"):
                counter += 1
            filename = f"{base_name}_({counter}).txt"
        else:
            raise FileExistsError(f"目标文件已存在: {filename}")
    
    try:
        # 直接打开文件并保持句柄,使用行缓冲模式
        output_file_handler = open(filename, 'w', encoding='utf-8', buffering=1)
        output_filename = filename
        if metadata:
            for key, value in metadata.items():
                output_file_handler.write(f"# {key}: {value}\n")
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
    """配置传感器（工程师版：什么都不发，只清空缓冲区 + 全日志 + 预读取验证）"""
    global sensor_connection
    from datetime import datetime
    
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"\n{'='*80}")
    print(f"[INFO ] [{ts}] ========== 开始初始化传感器（工程师原版逻辑） ==========")
    print(f"{'='*80}")

    try:
        # --- 1. 检查串口连接状态 ---
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if not sensor_connection or not sensor_connection.is_open:
            print(f"[ERROR] [{ts}] 串口未连接或已断开！无法初始化")
            return
        
        print(f"[INFO ] [{ts}] 1/4 串口状态检查通过: {sensor_connection.port} @ {sensor_connection.baudrate}")

        # --- 2. 检查并清空输入缓冲区 ---
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        in_waiting_before = sensor_connection.in_waiting
        print(f"[INFO ] [{ts}] 2/4 清空缓冲区前: 积压 {in_waiting_before} 字节")
        
        sensor_connection.reset_input_buffer()
        time.sleep(0.1)  # 给一点时间让清空生效
        
        in_waiting_after = sensor_connection.in_waiting
        print(f"[INFO ] [{ts}] 2/4 清空缓冲区后: 剩余 {in_waiting_after} 字节")
        
        if in_waiting_after > 0:
            print(f"[WARN ] [{ts}] 2/4 警告：清空后仍有数据残留，可能传感器正在高速输出")

        # --- 3. 等待 0.5 秒，看有没有新数据进来（验证传感器是否在吐数） ---
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[INFO ] [{ts}] 3/4 等待 0.5 秒，验证传感器数据流...")
        time.sleep(0.5)
        
        in_waiting_test = sensor_connection.in_waiting
        if in_waiting_test > 0:
            print(f"[INFO ] [{ts}] 3/4 ✅ 验证通过！检测到 {in_waiting_test} 字节新数据，传感器正在正常输出")
        else:
            print(f"[WARN ] [{ts}] 3/4 ⚠️  警告：未检测到新数据流入！请检查：")
            print(f"[WARN ] [{ts}]     1. 传感器是否上电？")
            print(f"[WARN ] [{ts}]     2. 波特率是否正确（当前是 {sensor_connection.baudrate}）？")
            print(f"[WARN ] [{ts}]     3. TX/RX 是否接反？")

        # --- 4. 预读几行看看格式对不对（可选调试） ---
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[INFO ] [{ts}] 4/4 预读取 2 行验证数据格式...")
        
        preview_lines = []
        for i in range(2):
            if sensor_connection.in_waiting:
                line = sensor_connection.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    preview_lines.append(line)
                    print(f"[DEBUG] [{ts}]   预读行 {i+1}: {repr(line)}")
        
        if preview_lines:
            # 简单检查一下格式
            sample_line = preview_lines[0]
            if 'ax=' in sample_line and 'ay=' in sample_line and 'az=' in sample_line:
                print(f"[INFO ] [{ts}] 4/4 ✅ 数据格式验证通过！包含 ax=, ay=, az=")
            else:
                print(f"[WARN ] [{ts}] 4/4 ⚠️  数据格式可能不对，未检测到 ax=, ay=, az=")
        else:
            print(f"[WARN ] [{ts}] 4/4 未能预读到有效数据行")

        # --- 完成 ---
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"\n{'='*80}")
        print(f"[INFO ] [{ts}] ========== 传感器初始化完成 ==========")
        print(f"{'='*80}\n")

    except Exception as e:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"\n{'='*80}")
        print(f"[ERROR] [{ts}] ========== 传感器初始化异常 ==========")
        print(f"[ERROR] [{ts}] 错误信息: {str(e)}")
        print(f"{'='*80}\n")

def parse_sensor_data(data_line):
    """解析传感器数据（1:1复制工程师原版逻辑）"""
    try:
        data_line = data_line.strip()
        
        # 【工程师逻辑】必须同时包含 ax=, ay=, az=
        if not data_line or 'ax=' not in data_line or 'ay=' not in data_line or 'az=' not in data_line:
            return None

        ax = ay = az = 0.0
        
        # 【工程师逻辑】按逗号分割，按等号取值
        for seg in data_line.split(','):
            seg = seg.strip()
            if seg.startswith('ax='):
                ax = float(seg.split('=')[1])
            elif seg.startswith('ay='):
                ay = float(seg.split('=')[1])
            elif seg.startswith('az='):
                az = float(seg.split('=')[1])

        # 返回三轴数据
        return [ax, ay, az]

    except Exception:
        # 【工程师逻辑】解析失败直接跳过，不报错
        return None
    
def is_contact_data(parsed_values):
    """判断是否为接触数据"""
    if not parsed_values or len(parsed_values) < 3: 
        return False
    return not all(abs(val) < 0.1 for val in parsed_values[:3])

def update_plot_data(fx, fy, fz):
    """更新绘图数据缓冲区"""
    global data_buffer_x, data_buffer_y, data_buffer_z
    with plot_lock:
        data_buffer_x.append(fx)
        data_buffer_y.append(fy)
        data_buffer_z.append(fz)

def plot_loop():
    """绘图线程循环"""
    global fig, ax, lines, plot_running, data_buffer_x, data_buffer_y, data_buffer_z
    
    try:
        # 创建图形窗口
        fig = plt.figure(figsize=(10, 6))
        ax = fig.add_subplot(111)
        
        # 创建三条线
        line_x, = ax.plot([], [], 'r-', label='Force X', linewidth=1.5)
        line_y, = ax.plot([], [], 'g-', label='Force Y', linewidth=1.5)
        line_z, = ax.plot([], [], 'b-', label='Force Z', linewidth=1.5)
        lines = [line_x, line_y, line_z]
        
        ax.set_xlabel('Sample Count')
        ax.set_ylabel('Force (N)')
        ax.set_title('Real-time Tactile Sensor Data')
        ax.legend(loc='upper right')
        ax.grid(True, alpha=0.3)
        ax.set_xlim(0, 500)
        ax.set_ylim(-10, 10)
        
        plt.tight_layout()
        plt.show(block=False)
        plt.pause(0.1)
        
        print("✅ --- 实时绘图窗口已初始化 ---")
        
        # 持续更新图形
        while plot_running:
            try:
                with plot_lock:
                    if len(data_buffer_x) > 0:
                        x_data = list(range(len(data_buffer_x)))
                        
                        # 更新三条线的数据
                        lines[0].set_data(x_data, list(data_buffer_x))
                        lines[1].set_data(x_data, list(data_buffer_y))
                        lines[2].set_data(x_data, list(data_buffer_z))
                        
                        # 动态调整X轴范围
                        if len(data_buffer_x) > 0:
                            ax.set_xlim(0, max(500, len(data_buffer_x)))
                        
                        # 自动调整Y轴范围
                        all_data = list(data_buffer_x) + list(data_buffer_y) + list(data_buffer_z)
                        if all_data:
                            y_min = min(all_data) - 1
                            y_max = max(all_data) + 1
                            ax.set_ylim(y_min, y_max)
                        
                        # 刷新图形
                        fig.canvas.draw_idle()
                        fig.canvas.flush_events()
                
                plt.pause(0.1)  # 更新频率：每100ms
                
            except Exception as e:
                if plot_running:  # 只在运行中报错
                    print(f"⚠️ 更新绘图失败: {e}")
                break
        
    except Exception as e:
        print(f"⚠️ 绘图窗口初始化失败: {e}")
    finally:
        # 安全关闭图形
        try:
            if fig is not None:
                plt.close(fig)
        except:
            pass

def init_plot():
    """初始化实时绘图窗口（在独立线程中运行）"""
    global plot_enabled, plot_thread, plot_running
    
    if not plot_enabled:
        plot_running = True
        plot_thread = threading.Thread(target=plot_loop, daemon=True)
        plot_thread.start()
        plot_enabled = True
        time.sleep(1.5)  # 等待窗口初始化

def sensor_read_loop():
    """持续读取传感器数据的循环（全日志版）"""
    global sensor_connection, is_running, data_count
    from datetime import datetime

    print(f"\n[INFO ] [{datetime.now().strftime('%H:%M:%S')}] ========== 传感器数据监控线程启动 ==========")
    
    print_counter = 0
    print_interval = 50  # 每50条打印一次状态汇总
    last_data_time = time.time()
    timeout_warning_printed = False

    while is_running:
        try:
            # --- 检查 1：串口是否还开着 ---
            if not sensor_connection or not sensor_connection.is_open:
                print(f"[ERROR] [{datetime.now().strftime('%H:%M:%S')}] 串口连接已断开！")
                break

            # --- 检查 2：缓冲区是否有数据 ---
            in_waiting = sensor_connection.in_waiting
            if in_waiting > 0:
                last_data_time = time.time()
                timeout_warning_printed = False
                
                # 读取一行
                line = sensor_connection.readline().decode('utf-8', errors='ignore')
                
                # 解析
                parsed_values = parse_sensor_data(line)
                
                if parsed_values:
                    data_count += 1
                    write_sensor_data_to_txt(parsed_values)
                    
                    if plot_enabled:
                        update_plot_data(parsed_values[0], parsed_values[1], parsed_values[2])

                    # 状态汇总打印
                    print_counter += 1
                    if print_counter >= print_interval:
                        ts = datetime.now().strftime("%H:%M:%S")
                        print(f"[INFO ] [{ts}] 状态汇总 | 总收集: {data_count} | 缓冲区: {in_waiting}字节")
                        print_counter = 0
            else:
                # --- 检查 3：数据超时警告 ---
                time_since_last_data = time.time() - last_data_time
                if time_since_last_data > 2.0 and not timeout_warning_printed:
                    print(f"[WARN ] [{datetime.now().strftime('%H:%M:%S')}] 数据超时！已 {time_since_last_data:.1f} 秒未收到数据")
                    timeout_warning_printed = True
                
                time.sleep(0.001)

        except Exception as e:
            print(f"[ERROR] [{datetime.now().strftime('%H:%M:%S')}] 读取循环异常: {str(e)}")
            time.sleep(0.1)
    
    print(f"\n[INFO ] [{datetime.now().strftime('%H:%M:%S')}] ========== 传感器数据监控结束 (总计: {data_count} 条) ==========\n")


def start_sensor_reading(port="COM5", baudrate=115200, main_category="m1", sub_category="b1", velocity=500, trajectory_id=1, repeat_num=1, enable_plot=True, allow_suffix=True, metadata=None):
    """开始读取传感器数据"""
    global sensor_port, sensor_baudrate, sensor_connection, is_running, sensor_thread, data_count
    global data_buffer_x, data_buffer_y, data_buffer_z
    
    sensor_port = port
    sensor_baudrate = baudrate
    data_count = 0  # 重置计数
    
    # 清空数据缓冲区
    data_buffer_x.clear()
    data_buffer_y.clear()
    data_buffer_z.clear()
    
    try:
        if not create_output_file(main_category, sub_category, velocity, trajectory_id, repeat_num, allow_suffix=allow_suffix, metadata=metadata):
            print("⚠️ 警告:无法创建输出文件,数据将只在控制台显示")
        
        # 初始化绘图窗口（只初始化一次）
        if enable_plot and not plot_enabled:
            init_plot()
        
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

def close_plot():
    """关闭绘图窗口"""
    global fig, plot_enabled, plot_running, plot_thread
    
    plot_running = False
    
    if plot_thread and plot_thread.is_alive():
        plot_thread.join(timeout=1)
    
    # 给一点时间让线程完全退出
    time.sleep(0.2)
    
    if plot_enabled:
        try:
            # 强制关闭所有matplotlib窗口
            plt.close('all')
            plot_enabled = False
            print("✅ --- 绘图窗口已关闭 ---")
        except Exception as e:
            pass  # 忽略关闭时的错误

# 注册退出时的清理函数
def cleanup_on_exit():
    """程序退出时的清理"""
    global plot_running
    plot_running = False
    try:
        plt.close('all')
    except:
        pass

atexit.register(cleanup_on_exit)
