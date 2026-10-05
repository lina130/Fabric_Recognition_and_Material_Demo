import serial
import time

PORT = "COM5"
BAUD = 460800

print(f"正在连接 {PORT} @ {BAUD} ...")
ser = serial.Serial(PORT, BAUD, timeout=1)
time.sleep(1)
ser.flush()
print("✅ 连接成功")

# 1. 先清空缓冲区
print("\n--- 1. 清空缓冲区 ---")
ser.read_all()
time.sleep(0.2)

# 2. 发 'C' 指令
print("\n--- 2. 发送指令 'C' ---")
ser.write(b"C\n")
time.sleep(0.5)

# 3. 连续读 5 秒，打印【原始字节】和【尝试解码】
print("\n--- 3. 开始读 5 秒原始数据 ---")
start_time = time.time()
line_count = 0
while time.time() - start_time < 5:
    # 方式 A：尝试读一行（如果是文本协议）
    try:
        line_txt = ser.readline().decode('utf-8', errors='ignore').strip()
        if line_txt:
            line_count += 1
            print(f"[文本行 {line_count}] {repr(line_txt)}")
    except:
        pass

    # 方式 B：读所有可用字节（如果是二进制协议）
    raw_bytes = ser.read_all()
    if raw_bytes:
        print(f"[原始字节] 长度={len(raw_bytes)} | 内容={raw_bytes.hex()} | 尝试解码={raw_bytes.decode('utf-8', errors='ignore')}")

    time.sleep(0.01)

ser.close()
print("\n--- 测试结束 ---")