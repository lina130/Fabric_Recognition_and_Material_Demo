import numpy as np
import sounddevice as sd
import serial
import struct
import time
from collections import deque

# ===================== 配置（完全匹配你的E模式） =====================
SERIAL_PORT = 'COM10'
SERIAL_BAUDRATE = 921600
SEND_INIT_CMD = b'e'
SYNC_HEAD = b'\xAA\x55'
SAMPLE_PER_FRAME = 64
PCM_FORMAT = f'<{SAMPLE_PER_FRAME}h'

# 播放采样率（根据你的E模式实际采样率调整）
PLAY_SAMPLE_RATE = 48000

# 播放缓冲区
play_buffer = deque(maxlen=int(PLAY_SAMPLE_RATE * 0.5))

# ===================== 声卡播放回调 =====================
def audio_callback(outdata, frames, _, status):
    if status:
        print(status, flush=True)
    
    buffer_len = len(play_buffer)
    if buffer_len < frames:
        outdata[:] = np.zeros((frames, 1), dtype=np.float32)
        return
    
    # 取出数据并归一化到声卡标准格式[-1.0, 1.0]
    play_data = np.array([play_buffer.popleft() for _ in range(frames)], dtype=np.float32)
    play_data = play_data / 32768.0  # int16转float32
    outdata[:, 0] = play_data

# ===================== 主程序 =====================
def main():
    ser = None
    stream = None
    frame_count = 0  # 帧计数
    try:
        # 1. 打开串口
        ser = serial.Serial(SERIAL_PORT, SERIAL_BAUDRATE, timeout=0.1)
        print(f"✅ 串口 {SERIAL_PORT} 已打开，波特率 {SERIAL_BAUDRATE}")
        
        # 2. 发送初始指令 'e'
        ser.write(SEND_INIT_CMD)
        print(f"📤 已发送指令：{SEND_INIT_CMD.decode('ascii')}")
        
        # 3. 打开声卡播放流
        stream = sd.OutputStream(
            samplerate=PLAY_SAMPLE_RATE,
            channels=1,
            blocksize=1024,
            callback=audio_callback
        )
        stream.start()
        print("✅ 声卡播放已启动")
        print("🔊 实时数据单行刷新中，按 Ctrl+C 停止\n")

        # 4. 循环读串口、解析、播放、实时显示
        buffer = b''
        while True:
            data = ser.read(ser.in_waiting or 1024)
            if not data:
                time.sleep(0.001)
                continue
            
            buffer += data
            
            # 解析 E 模式帧
            while len(buffer) >= len(SYNC_HEAD) + SAMPLE_PER_FRAME * 2:
                head_pos = buffer.find(SYNC_HEAD)
                if head_pos == -1:
                    buffer = buffer[-1:]
                    break
                
                frame_end = head_pos + len(SYNC_HEAD) + SAMPLE_PER_FRAME * 2
                if len(buffer) < frame_end:
                    break
                
                # 提取 PCM 数据
                frame = buffer[head_pos:frame_end]
                buffer = buffer[frame_end:]
                pcm_raw = frame[len(SYNC_HEAD):]
                pcm_samples = struct.unpack(PCM_FORMAT, pcm_raw)
                
                # 送入播放缓冲区
                play_buffer.extend(pcm_samples)
                
                # ===================== 【核心：单行实时刷新数据】 =====================
                frame_count += 1
                peak_value = max(abs(s) for s in pcm_samples)  # 当前帧音频峰值
                first_3_sample = pcm_samples[:3]  # 前3个采样点
                # 单行刷新，不刷屏，永远只显示最新一条
                print(
                    f"🎵 帧号: {frame_count:6d} | 音频峰值: {peak_value:5d} | 前3采样: {first_3_sample}",
                    end='\r',
                    flush=True
                )

    except KeyboardInterrupt:
        print("\n\n🛑 已手动停止")
    except Exception as e:
        print(f"\n❌ 运行错误：{e}")
    finally:
        # 释放资源
        if stream is not None:
            stream.stop()
            stream.close()
        if ser is not None and ser.is_open:
            ser.close()
        print("✅ 串口、声卡资源已全部释放")

if __name__ == "__main__":
    main()