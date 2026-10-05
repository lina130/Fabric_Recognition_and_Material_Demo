import sys
import warnings
warnings.filterwarnings('ignore')
import logging
logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)

if sys.platform.startswith('win'):
    import ctypes
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except: pass

import matplotlib
matplotlib.use('TkAgg')
matplotlib.rcParams['axes.unicode_minus'] = False
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei','Microsoft YaHei']

import numpy as np
import sounddevice as sd
import time
import threading
import socket
import json
import os
from collections import deque
from matplotlib.widgets import Slider
from scipy.fft import rfft, rfftfreq, irfft
from matplotlib.animation import FuncAnimation
import librosa

# 全局系统配置
UDP_IP, UDP_PORT = "0.0.0.0", 8888
SAVE_ROOT = "sensor_data"
TRAIN_DIR = "train_features"
os.makedirs(TRAIN_DIR, exist_ok=True)
os.makedirs(SAVE_ROOT, exist_ok=True)

PROGRAM_RUNNING = True
MIC_DISABLED = False
data_lock = threading.Lock()

# 识别相关全局变量
train_features = {}
result = {"pred": 0, "conf": 0.0, "scores": [0,0,0,0]}
current_recognition = {"position_id":0,"is_match":False,"confidence":0.0,"mic_match":0.0}
collect_motion_data = {"position_id":1,"traj_id":0,"motion_num":0,"status":"idle","stage":"train"}

def log_info(msg): print(f"[{time.strftime('%H:%M:%S')}] INFO | {msg}")
def log_error(msg): print(f"[{time.strftime('%H:%M:%S')}] ERRO | {msg}")

# ===================== 核心硬件参数（英飞凌专用） =====================
SR_MIC = 192000          # 采样率 192kHz
F_MIN = 1500             # 起始频率 1.5kHz
F_MAX = 80000            # 截止频率 80kHz（80k以上不看）
WINDOW_SEC = 0.5
MIC_BUFFER_SIZE = int(SR_MIC * WINDOW_SEC)

# 噪声配置
NOISE_SAMPLE_SEC = 2.0
NOISE_BUFFER_SIZE = int(SR_MIC * NOISE_SAMPLE_SEC)
mic_buffer = deque(maxlen=MIC_BUFFER_SIZE)
noise_buffer = deque(maxlen=NOISE_BUFFER_SIZE)
is_collecting_noise = False

# 控制参数
params = {"gain":1.2, "noise_gate":0.005}