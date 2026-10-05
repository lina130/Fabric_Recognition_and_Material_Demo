
import time
import serial
import serial.tools.list_ports
from module import cnc_module  # 这里只导入 cnc，不要 sensor！

def auto_find_cnc_port():
    """自动找 CNC(GRBL, 115200)"""
    for p in serial.tools.list_ports.comports():
        try:
            ser = serial.Serial(p.device, 115200, timeout=0.5)
            ser.write(b"?\n")
            time.sleep(0.1)
            res = ser.read(128).decode('utf-8', errors='ignore')
            ser.close()
            if '<' in res and ('Idle' in res or 'Run' in res or 'Alarm' in res):
                return p.device
        except:
            continue
    print("⚠️ 未自动识别到 CNC，使用默认 COM8")
    return "COM8"

if __name__ == "__main__":
    print("\n🔍 正在自动扫描串口...")
    cnc_port = auto_find_cnc_port()
    
    print(f"\n✅ CNC 端口: {cnc_port}")
    print("-" * 50)
    
    # 初始化CNC
    if not cnc_module.initialize_cnc(cnc_port, 115200):
        raise RuntimeError("CNC初始化失败")
    
    # 回零
    print("\n--- 开始执行回零 ($H) ---")
    cnc_module.execute_command("$H")
    print("✅ --- 回零成功！---")
    