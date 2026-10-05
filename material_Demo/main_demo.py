import sys
import warnings
warnings.filterwarnings('ignore')
import multiprocessing
multiprocessing.freeze_support()
multiprocessing.set_start_method('spawn', force=True)

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QFrame, QTextEdit, QSizePolicy)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QPixmap, QTextCursor

import queue
import numpy as np
import sounddevice as sd
import time
import threading
import socket
import json
import os
from concurrent.futures import ThreadPoolExecutor
import subprocess
import atexit

from scipy.signal import butter, lfilter
from collections import deque

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio
from torch.cuda.amp import autocast

# 替换原本的 BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    # 打包成 exe 后，获取 exe 所在的真实外部目录
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # 开发环境下，获取 py 脚本所在的目录
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


torch.backends.cudnn.benchmark = True
torch.backends.cuda.matmul.allow_tf32 = True

class EmittingStream(QObject):
    textWritten = pyqtSignal(str)
    def write(self, text): self.textWritten.emit(str(text))
    def flush(self): pass

class Config:
    DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    UDP_IP, UDP_PORT = "0.0.0.0", 8888 
    TARGET_UDP_IP, TARGET_UDP_PORT = "127.0.0.1", 9999 
    
    MODEL_DIR = os.path.join(BASE_DIR, "cnn_models")
    MODEL_FILE = os.path.join(MODEL_DIR, "material_classifier.pth")
    SUB_MODEL_FILE = os.path.join(MODEL_DIR, "sub_classifier.pth")
    PHOTO_BASE_PATH = os.path.join(BASE_DIR, "photos")
    
    @staticmethod
    def get_cnc_command():
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            return [os.path.join(exe_dir, "cnc_demo.exe")]
        else:
            return [sys.executable, os.path.join(BASE_DIR, "cnc_demo.py")]

    SR_MIC = 192000; TOTAL_SEC = 1.7; MIC_BUFFER_SIZE = int(SR_MIC * TOTAL_SEC)
    NUM_SLICES = 4; SLICE_BUFFER_SIZE = int(MIC_BUFFER_SIZE / NUM_SLICES) 
    N_FFT = 4096; HOP_LENGTH = 512; F_MIN = 1200; F_MAX = 16000; N_MELS = 128
    INPUT_CHANNELS = 1; NUM_CLASSES = 10; ENABLE_EXPERT_MODEL = 1
    CONFUSING_CLASSES = [1, 7, 10]

PROGRAM_RUNNING = True
data_lock = threading.Lock()
ui_queue = queue.Queue()
serial_queue = queue.Queue()
inference_pool = ThreadPoolExecutor(max_workers=4)

mic_buffer = deque(maxlen=Config.MIC_BUFFER_SIZE)
audio_segments = [[] for _ in range(4)]
cnn_model = None
sub_cnn_model = None
cnc_process = None

def log_info(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# --------------------------
# AI 模型网络结构 (保持不变，仅用于加载权重)
# --------------------------
def butter_bandpass(lowcut, highcut, fs, order=5):
    nyq = 0.5 * fs
    b, a = butter(order, [lowcut/nyq, highcut/nyq], btype='band')
    return b, a

def audio_to_spectrogram_sliced(audio_slice):
    audio = np.array(audio_slice, dtype=np.float32)
    if len(audio) < Config.SLICE_BUFFER_SIZE: audio = np.pad(audio, (0, Config.SLICE_BUFFER_SIZE - len(audio)))
    else: audio = audio[:Config.SLICE_BUFFER_SIZE]
    b, a = butter_bandpass(Config.F_MIN, Config.F_MAX, Config.SR_MIC, order=5)
    audio = lfilter(b, a, audio)
    audio = audio - np.mean(audio)
    mel_transform = torchaudio.transforms.MelSpectrogram(sample_rate=Config.SR_MIC, n_fft=Config.N_FFT, hop_length=Config.HOP_LENGTH, n_mels=Config.N_MELS, center=True)
    mel_spec = torchaudio.transforms.AmplitudeToDB()(mel_transform(torch.from_numpy(audio).float()))
    return ((mel_spec - mel_spec.mean()) / (mel_spec.std() + 1e-8)).unsqueeze(0)

class ECABlock(nn.Module):
    def __init__(self, channels, gamma=2, b=1):
        super().__init__()
        t = int(abs((np.log2(channels) + b) / gamma))
        k = t if t % 2 else t + 1
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.conv = nn.Conv1d(1, 1, kernel_size=k, padding=k//2, bias=False)
        self.sigmoid = nn.Sigmoid()
    def forward(self, x): return x * self.sigmoid(self.conv(self.avg_pool(x).squeeze(-1).transpose(-1, -2)).transpose(-1, -2).unsqueeze(-1)).expand_as(x)

class MBConvBlock(nn.Module):
    def __init__(self, in_channels, out_channels, stride=1, expand_ratio=4):
        super().__init__()
        hidden_dim = in_channels * expand_ratio
        self.use_res_connect = stride == 1 and in_channels == out_channels
        layers = []
        if expand_ratio != 1: layers.extend([nn.Conv2d(in_channels, hidden_dim, 1, 1, 0, bias=False), nn.BatchNorm2d(hidden_dim), nn.SiLU(inplace=True)])
        layers.extend([nn.Conv2d(hidden_dim, hidden_dim, 3, stride, 1, groups=hidden_dim, bias=False), nn.BatchNorm2d(hidden_dim), nn.SiLU(inplace=True), nn.Conv2d(hidden_dim, out_channels, 1, 1, 0, bias=False), nn.BatchNorm2d(out_channels)])
        self.conv = nn.Sequential(*layers)
        self.eca = ECABlock(out_channels)
    def forward(self, x):
        out = self.eca(self.conv(x))
        return x + out if self.use_res_connect else out

class MaterialCNN(nn.Module):
    def __init__(self, num_classes, in_channels=Config.INPUT_CHANNELS):
        super().__init__()
        self.in_channels = 24
        self.stem = nn.Sequential(nn.Conv2d(in_channels, 24, 3, stride=2, padding=1, bias=False), nn.BatchNorm2d(24), nn.SiLU(inplace=True))
        self.blocks = nn.Sequential(self._make_layer(24, 1, 1, expand_ratio=1), self._make_layer(32, 2, 2, expand_ratio=4), self._make_layer(64, 2, 2, expand_ratio=4), self._make_layer(128, 2, 2, expand_ratio=4))
        self.head = nn.Sequential(nn.Conv2d(128, 256, 1, 1, 0, bias=False), nn.BatchNorm2d(256), nn.SiLU(inplace=True), nn.AdaptiveAvgPool2d((1, 1)), nn.Flatten(), nn.Dropout(0.3), nn.Linear(256, num_classes))
    def _make_layer(self, out_channels, num_blocks, stride, expand_ratio):
        strides = [stride] + [1]*(num_blocks-1)
        layers = []
        for s in strides: layers.append(MBConvBlock(self.in_channels, out_channels, s, expand_ratio)); self.in_channels = out_channels
        return nn.Sequential(*layers)
    def forward(self, x): return self.head(self.blocks(self.stem(x)))

def load_models():
    global cnn_model, sub_cnn_model
    try:
        cnn_model = MaterialCNN(num_classes=Config.NUM_CLASSES).to(Config.DEVICE)
        cnn_model.load_state_dict(torch.load(Config.MODEL_FILE, map_location=Config.DEVICE))
        cnn_model.eval()
        log_info("✅ 全局主模型加载成功")
    except Exception as e: log_info(f"❌ 主模型加载失败: {e}")

    if Config.ENABLE_EXPERT_MODEL == 1:
        try:
            sub_cnn_model = MaterialCNN(num_classes=len(Config.CONFUSING_CLASSES)).to(Config.DEVICE)
            sub_cnn_model.load_state_dict(torch.load(Config.SUB_MODEL_FILE, map_location=Config.DEVICE))
            sub_cnn_model.eval()
            log_info("✅ 专家子模型加载成功")
        except: log_info("⚠️ 未检测到专家子模型，跳过加载")

def recognize_16_slices(specs):
    global ui_queue
    if cnn_model is None: return
    specs_tensor = torch.stack(specs).to(Config.DEVICE, non_blocking=True)
    with torch.no_grad():
        with autocast(): outputs = cnn_model(specs_tensor) 
        probs = F.softmax(outputs, dim=1)
    
    sorted_probs, _ = torch.sort(probs, dim=0) 
    trimmed_probs = sorted_probs[1:-1, :] if len(specs) > 2 else sorted_probs
    p_fused = torch.mean(trimmed_probs, dim=0) 
    p_fused = p_fused / torch.sum(p_fused)     
    best_pred = torch.argmax(p_fused).item() + 1
    
    if Config.ENABLE_EXPERT_MODEL == 1 and best_pred in Config.CONFUSING_CLASSES and sub_cnn_model is not None:
        with torch.no_grad():
            with autocast(): sub_outputs = sub_cnn_model(specs_tensor)
            sub_probs = F.softmax(sub_outputs, dim=1)
        sub_sorted, _ = torch.sort(sub_probs, dim=0)
        sub_trimmed = sub_sorted[1:-1, :] if len(specs) > 2 else sub_sorted
        sub_fused = torch.mean(sub_trimmed, dim=0)
        sub_fused = sub_fused / torch.sum(sub_fused)
        sub_best_idx = torch.argmax(sub_fused).item()
        best_pred = Config.CONFUSING_CLASSES[sub_best_idx]
    
    ui_queue.put(best_pred)

def async_process_audio(segments):
    try:
        sliced_specs = []
        for seg in segments:
            arr = np.array(seg, dtype=np.float32)
            chunk_size = len(arr) // Config.NUM_SLICES
            for i in range(Config.NUM_SLICES):
                slice_data = arr[i * chunk_size : (i + 1) * chunk_size]
                spec = audio_to_spectrogram_sliced(slice_data)
                sliced_specs.append(spec)
        recognize_16_slices(sliced_specs)
    except Exception as e: log_info(f"推理错误: {e}")

def mic_cb(indata, *_, **__): mic_buffer.extend(indata[:,0])
def start_mic():
    with sd.InputStream(samplerate=Config.SR_MIC, channels=1, blocksize=1024, callback=mic_cb):
        while PROGRAM_RUNNING: time.sleep(0.1)

def udp_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((Config.UDP_IP, Config.UDP_PORT))
    while PROGRAM_RUNNING:
        try:
            data, _ = s.recvfrom(4096)
            msg = json.loads(data.decode('utf-8'))
            
            if msg.get("cmd") == "serial_info":
                serial_queue.put(msg.get("port"))
                continue
                
            status = msg.get("status")
            if status == "capture_1":
                with data_lock: audio_segments[0] = list(mic_buffer)
            elif status == "capture_2":
                with data_lock: audio_segments[1] = list(mic_buffer)
            elif status == "capture_3":
                with data_lock: audio_segments[2] = list(mic_buffer)
            elif status == "capture_4":
                with data_lock: audio_segments[3] = list(mic_buffer)
                inference_pool.submit(async_process_audio, [list(a) for a in audio_segments])
        except Exception: continue

class ModernDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("材质感知系统 - Demo版")
        self.resize(1400, 900)
        self.current_img_path = None

        self.setStyleSheet("""
            QMainWindow { background-color: #f0f2f5; }
            QWidget { font-family: 'Microsoft YaHei', sans-serif; }
            QFrame[cssClass="Card"] { background-color: #ffffff; border-radius: 20px; border: 1px solid #e0e4e8; }
            QLabel[cssClass="CardTitle"] { font-size: 26px; font-weight: bold; color: #1c1c1c; padding-bottom: 5px; }
            QLabel[cssClass="InfoText"] { font-size: 26px; color: #595959; margin: 5px 0; }
            QLabel#ResultText { font-size: 100px; font-weight: bold; color: #1677ff; }
            QLabel#ImageDisplay { background-color: #ffffff; border-radius: 12px; }
            QPushButton#TriggerBtn { background-color: #1677ff; color: white; font-size: 28px; font-weight: bold; border-radius: 12px; padding: 25px; }
            QPushButton#RestartBtn { background-color: #faad14; color: white; font-size: 28px; font-weight: bold; border-radius: 12px; padding: 25px; margin-top: 5px; }
        """)

        main_widget = QWidget(); self.setCentralWidget(main_widget)
        root = QVBoxLayout(main_widget); root.setContentsMargins(20, 20, 20, 20)
        
        header = QHBoxLayout(); header.setContentsMargins(0, 0, 0, 10)
        self.status_dot = QLabel(); self.status_dot.setFixedSize(24, 24); self.status_dot.setStyleSheet("background-color: #52c41a; border-radius: 12px;")
        title = QLabel("智能工业材质感知系统 Demo"); title.setStyleSheet("font-size: 32px; font-weight: bold;")
        header.addWidget(self.status_dot); header.addWidget(title); header.addStretch()
        root.addLayout(header)

        content = QHBoxLayout(); content.setSpacing(20)
        left_layout = QVBoxLayout(); left_layout.setSpacing(20)
        
        c1 = QFrame(); c1.setProperty("cssClass", "Card"); l1 = QVBoxLayout(c1)
        t1 = QLabel("🔌 设备连接"); t1.setProperty("cssClass", "CardTitle")
        self.port_lbl = QLabel("串口：等待下位机..."); self.port_lbl.setProperty("cssClass", "InfoText")
        l1.addWidget(t1); l1.addWidget(self.port_lbl); l1.addStretch()

        c3 = QFrame(); c3.setProperty("cssClass", "Card"); l3 = QVBoxLayout(c3)
        t3 = QLabel("🛠️ 操作面板"); t3.setProperty("cssClass", "CardTitle")
        self.restart_btn = QPushButton("🔄 重启系统"); self.restart_btn.setObjectName("RestartBtn")
        self.restart_btn.clicked.connect(self.restart_app)
        self.trigger_btn = QPushButton("🚀 启动识别"); self.trigger_btn.setObjectName("TriggerBtn")
        self.trigger_btn.clicked.connect(self.trigger_motor_detection)
        l3.addWidget(t3); l3.addSpacing(10); l3.addWidget(self.restart_btn); l3.addWidget(self.trigger_btn)

        left_layout.addWidget(c1, stretch=1); left_layout.addWidget(c3, stretch=2)
        
        right_panel = QFrame(); right_panel.setProperty("cssClass", "Card")
        r_layout = QVBoxLayout(right_panel)
        self.result_lbl = QLabel(""); self.result_lbl.setObjectName("ResultText"); self.result_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl = QLabel(""); self.img_lbl.setObjectName("ImageDisplay"); self.img_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        r_layout.addWidget(self.result_lbl); r_layout.addWidget(self.img_lbl, stretch=1)
        
        content.addLayout(left_layout, stretch=1); content.addWidget(right_panel, stretch=3)
        root.addLayout(content, stretch=4)
        
        self.console = QTextEdit(); self.console.setStyleSheet("font-size: 16px; border-radius: 12px; padding: 10px;"); self.console.setReadOnly(True)
        root.addWidget(self.console, stretch=1)

        sys.stdout = EmittingStream(); sys.stdout.textWritten.connect(self.append_log)
        sys.stderr = EmittingStream(); sys.stderr.textWritten.connect(self.append_log)

        self.timer = QTimer(); self.timer.timeout.connect(self.update_ui_from_queue); self.timer.start(100)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_img_path and os.path.exists(self.current_img_path):
            pixmap = QPixmap(self.current_img_path)
            self.img_lbl.setPixmap(pixmap.scaled(self.img_lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def blink_status_dot(self):
        self.status_dot.setStyleSheet("background-color: #fadb14; border-radius: 12px;")
        QTimer.singleShot(300, lambda: self.status_dot.setStyleSheet("background-color: #52c41a; border-radius: 12px;"))

    def append_log(self, text):
        if text == '\r': return
        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text.replace('\r', ''))
        self.console.setTextCursor(cursor)
        self.console.ensureCursorVisible()

    def restart_app(self):
        time.sleep(0.5)
        subprocess.Popen([sys.executable] + sys.argv)
        QApplication.quit()

    def send_udp_cmd(self, cmd_str):
        self.blink_status_dot()
        try:
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sender.sendto(json.dumps({"cmd": cmd_str}).encode('utf-8'), (Config.TARGET_UDP_IP, Config.TARGET_UDP_PORT))
            sender.close()
            log_info(f"✅ 下发指令: [{cmd_str}]")
        except Exception as e: log_info(f"❌ 指令失败: {e}")

    def trigger_motor_detection(self):
        self.result_lbl.setText("采音识别中..."); self.result_lbl.setStyleSheet("color: #faad14; font-size: 50px;")
        self.img_lbl.clear(); self.img_lbl.setText("等待图像...")
        self.send_udp_cmd("start_detect_cycle")

    def update_ui_from_queue(self):
        while not serial_queue.empty():
            port = serial_queue.get_nowait()
            self.port_lbl.setText(f"串口：{port}")
            self.port_lbl.setStyleSheet("color: #1677ff; font-weight: bold; font-size: 25px;")
            
        while not ui_queue.empty():
            pred_class = ui_queue.get_nowait()
            self.result_lbl.setText(f"材质 {pred_class}"); self.result_lbl.setStyleSheet("color: #52c41a; font-size: 100px;")
            img_path = os.path.join(Config.PHOTO_BASE_PATH, f"{pred_class}.jpg")
            self.current_img_path = img_path 
            if os.path.exists(img_path): 
                pixmap = QPixmap(img_path)
                self.img_lbl.setPixmap(pixmap.scaled(self.img_lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else: 
                self.img_lbl.clear(); self.img_lbl.setText(f"缺图: {pred_class}.jpg")

def start_app():
    global cnc_process
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 12))
    
    threading.Thread(target=udp_server, daemon=True).start()
    threading.Thread(target=start_mic, daemon=True).start()
    load_models()

    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        cnc_command = Config.get_cnc_command()
        
        cnc_process = subprocess.Popen(
            cnc_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', bufsize=1, startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        def read_cnc_output():
            while cnc_process and cnc_process.poll() is None:
                line = cnc_process.stdout.readline()
                if line: print(f"[下位机] {line.strip()}")
        threading.Thread(target=read_cnc_output, daemon=True).start()
    except Exception as e: print(f"❌ 挂载机床驱动失败: {e}")

    window = ModernDashboard()
    window.showMaximized() 
    sys.exit(app.exec_())

@atexit.register
def cleanup():
    if cnc_process: cnc_process.terminate()

if __name__ == "__main__":
    start_app()