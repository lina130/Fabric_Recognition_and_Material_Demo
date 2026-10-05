import sys
import warnings
warnings.filterwarnings('ignore')
import multiprocessing
multiprocessing.freeze_support()
multiprocessing.set_start_method('spawn', force=True)

# 🌟 新增 QSizePolicy
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QFrame, QCheckBox, QDialog, QTextEdit, QSizePolicy)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QPixmap, QColor, QTextCursor

import queue
import numpy as np
import sounddevice as sd
import time
import threading
import socket
import json
import os
import glob
from concurrent.futures import ThreadPoolExecutor
import subprocess
import atexit

import scipy.io.wavfile as wavfile
from scipy.signal import butter, lfilter
from collections import deque
from sklearn.metrics import classification_report
from tqdm import tqdm

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from torch.optim import AdamW
from torch.cuda.amp import GradScaler, autocast
import torchaudio
import torchaudio.transforms as T
import gc

from tkinter import messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
torch.backends.cudnn.benchmark = True
torch.backends.cuda.matmul.allow_tf32 = True

cnc_process = None 

class EmittingStream(QObject):
    textWritten = pyqtSignal(str)
    def write(self, text): self.textWritten.emit(str(text))
    def flush(self): pass

class Config:
    FORCE_CONTINUE_TRAINING = False
    DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    
    UDP_IP, UDP_PORT = "0.0.0.0", 8888 
    TARGET_UDP_IP, TARGET_UDP_PORT = "127.0.0.1", 9999 
    
    # 🌟 使用 os.path.join 将路径拼接，这在 Windows/Linux 下通用
    MODEL_DIR = os.path.join(BASE_DIR, "cnn_models")
    RAW_MIC_DIR = os.path.join(BASE_DIR, "raw_mic")
    
    MODEL_FILE = os.path.join(MODEL_DIR, "material_classifier.pth")
    SUB_MODEL_FILE = os.path.join(MODEL_DIR, "sub_classifier.pth")
    
    # 🌟 你的 photos 文件夹应该放在 exe 同目录下
    PHOTO_BASE_PATH = os.path.join(BASE_DIR, "photos")
    
    # 🌟 你的数据采集脚本应该放在 exe 同目录下的 testt 文件夹里
    # 新的正确路径
    @staticmethod
    def get_cnc_command():
        """获取下位机程序的调用命令，自动区分开发环境和打包环境"""
        if getattr(sys, 'frozen', False):
            # 打包后：调用同目录下的data_collection.exe
            exe_dir = os.path.dirname(sys.executable)
            return [os.path.join(exe_dir, "data_collection.exe")]
        else:
            # 开发环境：调用Python脚本
            script_path = os.path.join(BASE_DIR, "data_collection.py")
            return [sys.executable, script_path]
    SR_MIC = 192000; TOTAL_SEC = 1.7; MIC_BUFFER_SIZE = int(SR_MIC * TOTAL_SEC)
    NUM_SLICES = 4; SLICE_BUFFER_SIZE = int(MIC_BUFFER_SIZE / NUM_SLICES) 
    N_FFT = 4096; HOP_LENGTH = 512; F_MIN = 1200; F_MAX = 16000; N_MELS = 128
    SPEC_HEIGHT = N_MELS; SPEC_WIDTH = int(np.ceil(SLICE_BUFFER_SIZE / HOP_LENGTH))
    INPUT_CHANNELS = 1; BATCH_SIZE = 32; NUM_WORKERS = 0; MAX_EPOCHS = 200       
    EARLY_STOP_PATIENCE = 25; MAX_LR = 3e-3; NUM_CLASSES = 10; ENABLE_EXPERT_MODEL = 1
    CONFUSING_CLASSES = [1, 7, 10]

os.makedirs(Config.MODEL_DIR, exist_ok=True)
os.makedirs(Config.RAW_MIC_DIR, exist_ok=True)

PROGRAM_RUNNING = True
data_lock = threading.Lock()
ui_queue = queue.Queue()
serial_queue = queue.Queue() # 🔥 新增：专门接收底层传上来的串口信息
inference_pool = ThreadPoolExecutor(max_workers=4)

result = {"pred": 0, "conf": 0.0, "scores": [0.0]*10, "color": "#0f0", "slice_preds": [], "slice_confs": [], "sub_used": False}
collect_motion_data = {"stage": "wait"}
train_status = {"is_training": False, "msg": "训练状态：空闲"}
mic_buffer = deque(maxlen=Config.MIC_BUFFER_SIZE)
audio_segments = [[] for _ in range(4)]
is_model_trained = False
cnn_model = None
is_sub_model_trained = False
sub_cnn_model = None

CHOICE = {"clear_audio": False, "re_extract": False, "re_train": False, "re_train_sub": False, "enter_collect": False}

def log_info(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
def clear_gpu_cache(): gc.collect(); torch.cuda.empty_cache()
def get_total_samples(): return len(glob.glob(os.path.join(Config.RAW_MIC_DIR, "*_spec.pt")))

# --------------------------
# 🎵 AI 模型逻辑 (保持不变)
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

class FocalLoss(nn.Module):
    def __init__(self, alpha=None, gamma=2.0):
        super().__init__()
        self.alpha, self.gamma = alpha, gamma
    def forward(self, inputs, targets):
        ce_loss = F.cross_entropy(inputs, targets, weight=self.alpha, reduction='none')
        return ((1 - torch.exp(-ce_loss)) ** self.gamma * ce_loss).mean()

class SpecAugmentGPU(nn.Module):
    def __init__(self):
        super().__init__()
        self.time_mask = T.TimeMasking(time_mask_param=10)
        self.freq_mask = T.FrequencyMasking(freq_mask_param=20)
    def forward(self, spec):
        if self.training:
            if torch.rand(1) < 0.5: spec = self.time_mask(spec)
            if torch.rand(1) < 0.5: spec = self.freq_mask(spec)
        return spec

class MemoryMaterialDataset(Dataset):
    def __init__(self, data_dir=Config.RAW_MIC_DIR, transform=None, target_classes=None):
        self.transform, self.specs, self.labels = transform, [], []
        files = glob.glob(os.path.join(data_dir, "*_spec.pt"))
        for f in files:
            try:
                pos = int(os.path.basename(f).split("_")[0].replace("mat", ""))
                spec = torch.load(f, weights_only=True)
                if target_classes is None: self.specs.append(spec); self.labels.append(pos - 1)
                elif pos in target_classes: self.specs.append(spec); self.labels.append(target_classes.index(pos))
            except: pass
    def __len__(self): return len(self.labels)
    def __getitem__(self, idx): return self.transform(self.specs[idx]) if self.transform else self.specs[idx], torch.tensor(self.labels[idx], dtype=torch.long)

def clean_old_checkpoints(keep_last=3):
    checkpoints = []
    for f in os.listdir(Config.MODEL_DIR):
        if f.startswith("checkpoint_epoch_") and f.endswith(".pth"):
            try: checkpoints.append((int(f.split("_")[-1].split(".")[0]), os.path.join(Config.MODEL_DIR, f)))
            except: continue
    if len(checkpoints) <= keep_last: return
    checkpoints.sort()
    for epoch, path in checkpoints[:-keep_last]:
        try: os.remove(path)
        except: pass

def _train_single_model(is_sub_model=False):
    global train_status
    clear_gpu_cache()
    model_name = "专家子模型" if is_sub_model else "全局主模型"
    save_path = Config.SUB_MODEL_FILE if is_sub_model else Config.MODEL_FILE
    num_classes = len(Config.CONFUSING_CLASSES) if is_sub_model else Config.NUM_CLASSES
    target_classes = Config.CONFUSING_CLASSES if is_sub_model else None

    full_dataset = MemoryMaterialDataset(transform=SpecAugmentGPU(), target_classes=target_classes)
    if len(full_dataset) < 10: return False

    train_size = int(0.8 * len(full_dataset))
    train_dataset, val_dataset = torch.utils.data.random_split(full_dataset, [train_size, len(full_dataset) - train_size])
    train_loader = DataLoader(train_dataset, batch_size=min(Config.BATCH_SIZE, train_size), shuffle=True, pin_memory=True, num_workers=0)
    val_loader = DataLoader(val_dataset, batch_size=Config.BATCH_SIZE, shuffle=False, pin_memory=True, num_workers=0)
    
    model = MaterialCNN(num_classes=num_classes).to(Config.DEVICE)
    optimizer = AdamW(model.parameters(), lr=Config.MAX_LR, weight_decay=1e-2)
    criterion, scaler, best_val_f1, early_stop_counter, has_decayed_lr, accumulation_steps = FocalLoss(), GradScaler(), 0.0, 0, False, 1

    for epoch in range(1, Config.MAX_EPOCHS + 1):
        model.train()
        train_status["msg"] = f"{model_name} 训练中 | Epoch: {epoch}"
        train_pbar = tqdm(train_loader, desc=f"{model_name} Ep {epoch}", leave=False, mininterval=1.0)
        for param in model.parameters(): param.grad = None 
        for i, (specs, labels) in enumerate(train_pbar):
            specs, labels = specs.to(Config.DEVICE, non_blocking=True), labels.to(Config.DEVICE, non_blocking=True)
            lam = np.random.beta(0.2, 0.2)
            index = torch.randperm(specs.size(0)).cuda()
            mixed_specs = lam * specs + (1 - lam) * specs[index]
            with autocast():
                outputs = model(mixed_specs)
                loss = (lam * criterion(outputs, labels) + (1 - lam) * criterion(outputs, labels[index])) / accumulation_steps
            scaler.scale(loss).backward()
            if (i + 1) % accumulation_steps == 0 or (i + 1) == len(train_loader):
                scaler.step(optimizer); scaler.update()
                for param in model.parameters(): param.grad = None
            
        model.eval()
        all_preds, all_labels_val = [], []
        with torch.no_grad():
            for specs, labels in val_loader:
                specs, labels = specs.to(Config.DEVICE, non_blocking=True), labels.to(Config.DEVICE, non_blocking=True)
                with autocast(): outputs = model(specs)
                all_preds.extend(torch.max(outputs, 1)[1].cpu().numpy())
                all_labels_val.extend(labels.cpu().numpy())

        macro_f1 = classification_report(all_labels_val, all_preds, output_dict=True, zero_division=0)['macro avg']['f1-score']
        if macro_f1 > 0.80 and not has_decayed_lr:
            has_decayed_lr = True
            for pg in optimizer.param_groups: pg['lr'] *= 0.1

        if macro_f1 > best_val_f1:
            best_val_f1 = macro_f1
            torch.save(model.state_dict(), save_path)
            early_stop_counter = 0
        else:
            early_stop_counter += 1
            if early_stop_counter >= Config.EARLY_STOP_PATIENCE: break

        if not is_sub_model and epoch % 5 == 0:
            torch.save(model.state_dict(), f"{Config.MODEL_DIR}/checkpoint_epoch_{epoch}.pth")
            clean_old_checkpoints(keep_last=2)
    return True

def train_pipeline():
    global is_model_trained, is_sub_model_trained, cnn_model, sub_cnn_model, train_status
    if train_status.get("is_training", False): return
    try:
        train_status["is_training"] = True
        if CHOICE.get("re_train", False) and _train_single_model(is_sub_model=False):
            cnn_model = MaterialCNN(num_classes=Config.NUM_CLASSES).to(Config.DEVICE)
            cnn_model.load_state_dict(torch.load(Config.MODEL_FILE))
            cnn_model.eval(); is_model_trained = True
        if CHOICE.get("re_train_sub", False) and Config.ENABLE_EXPERT_MODEL == 1 and len(Config.CONFUSING_CLASSES) > 0 and _train_single_model(is_sub_model=True):
            sub_cnn_model = MaterialCNN(num_classes=len(Config.CONFUSING_CLASSES)).to(Config.DEVICE)
            sub_cnn_model.load_state_dict(torch.load(Config.SUB_MODEL_FILE))
            sub_cnn_model.eval(); is_sub_model_trained = True
    finally:
        train_status["is_training"], train_status["msg"] = False, "训练空闲"

def recognize_16_slices(pos_actual, specs):
    global result, ui_queue
    if not is_model_trained or cnn_model is None: return
    specs_tensor = torch.stack(specs).to(Config.DEVICE, non_blocking=True)
    with torch.no_grad():
        with autocast(): outputs = cnn_model(specs_tensor) 
        probs = F.softmax(outputs, dim=1)
    
    sorted_probs, _ = torch.sort(probs, dim=0) 
    trimmed_probs = sorted_probs[1:-1, :] if len(specs) > 2 else sorted_probs
    p_fused = torch.mean(trimmed_probs, dim=0) 
    p_fused = p_fused / torch.sum(p_fused)     
    best_pred = torch.argmax(p_fused).item() + 1
    
    if Config.ENABLE_EXPERT_MODEL == 1 and best_pred in Config.CONFUSING_CLASSES and is_sub_model_trained and sub_cnn_model is not None:
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

def async_process_audio_16_slices(pos, stage, segments):
    try:
        sliced_specs = []
        for idx, seg in enumerate(segments):
            arr = np.array(seg, dtype=np.float32)
            chunk_size = len(arr) // Config.NUM_SLICES
            if stage == "train":
                ts = int(time.time() * 1000)
                norm = np.clip(arr*0.9, -1, 1).astype(np.float32)
                wavfile.write(os.path.join(Config.RAW_MIC_DIR, f"mat{pos}_{ts}_arc{idx+1}.wav"), Config.SR_MIC, norm)
            for i in range(Config.NUM_SLICES):
                slice_data = arr[i * chunk_size : (i + 1) * chunk_size]
                spec = audio_to_spectrogram_sliced(slice_data)
                sliced_specs.append(spec)
                if stage == "train":
                    torch.save(spec, os.path.join(Config.RAW_MIC_DIR, f"mat{pos}_{ts}_arc{idx+1}_slice{i+1}_spec.pt"))
        if stage == "detect": recognize_16_slices(pos, sliced_specs)
    except: pass

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
            
            # 🔥 捕获底层发来的专属串口同步信息
            if msg.get("cmd") == "serial_info":
                serial_queue.put(msg.get("port"))
                continue
                
            pos, status, stage = msg.get("position_id"), msg.get("status"), msg.get("stage")
            collect_motion_data["stage"] = stage

            if status == "train_start" and CHOICE["clear_audio"]:
                for f in glob.glob(os.path.join(Config.RAW_MIC_DIR, "*")): os.remove(f)
            elif status == "capture_1":
                with data_lock: audio_segments[0] = list(mic_buffer)
            elif status == "capture_2":
                with data_lock: audio_segments[1] = list(mic_buffer)
            elif status == "capture_3":
                with data_lock: audio_segments[2] = list(mic_buffer)
            elif status == "capture_4":
                with data_lock: audio_segments[3] = list(mic_buffer)
                inference_pool.submit(async_process_audio_16_slices, pos, stage, [list(a) for a in audio_segments])
            elif status == "train_done":
                if CHOICE.get("re_train", False) or CHOICE.get("re_train_sub", False):
                    threading.Thread(target=train_pipeline, daemon=True).start()
        except Exception: continue

def load_data_if_exist():
    global is_model_trained, cnn_model, is_sub_model_trained, sub_cnn_model
    if os.path.exists(Config.MODEL_FILE):
        try:
            cnn_model = MaterialCNN(num_classes=Config.NUM_CLASSES).to(Config.DEVICE)
            cnn_model.load_state_dict(torch.load(Config.MODEL_FILE))
            cnn_model.eval(); is_model_trained = True
        except: pass
    if Config.ENABLE_EXPERT_MODEL == 1 and os.path.exists(Config.SUB_MODEL_FILE):
        try:
            sub_cnn_model = MaterialCNN(num_classes=len(Config.CONFUSING_CLASSES)).to(Config.DEVICE)
            sub_cnn_model.load_state_dict(torch.load(Config.SUB_MODEL_FILE))
            sub_cnn_model.eval(); is_sub_model_trained = True
        except: pass

# =====================================================================
# 🎨 8. 现代化 GUI 部分
# =====================================================================

class StartupConfigDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("系统初始化配置")
        # 🌟 1. 窗口加宽到 680，彻底解决右侧文字被切掉的问题！
        self.setFixedSize(680, 620) 
        
        check_svg_base64 = "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIzIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwb2x5bGluZSBwb2ludHM9IjIwIDYgOSAxNyA0IDEyIi8+PC9zdmc+"

        # 🌟 2. 使用通配符 * 强制所有控件使用等线/雅黑，加大倒角
        self.setStyleSheet(f"""
            * {{
                font-family: 'Microsoft YaHei', 'DengXian', 'PingFang SC', sans-serif;
            }}
            QDialog {{ 
                background-color: #ffffff; 
            }}
            QLabel {{ 
                font-size: 26px; 
                font-weight: bold; 
                color: #1c1c1c; 
                margin-bottom: 20px; 
                padding-bottom: 5px; 
            }}
            QCheckBox {{ 
                font-size: 23px; 
                color: #333333; 
                spacing: 20px; 
                padding: 12px; 
                border-radius: 12px; 
            }}
            QCheckBox:hover {{ 
                background-color: #f4f5f7; 
            }}
            
            /* 🌟 3. 勾选框放大，倒角增加到 10px，非常圆润 */
            QCheckBox::indicator {{ 
                width: 32px; 
                height: 32px; 
                border-radius: 12px; 
                border: 2px solid #d9d9d9; 
                background-color: #ffffff;
            }}
            QCheckBox::indicator:hover {{ border: 2px solid #1677ff; }}
            QCheckBox::indicator:checked {{ 
                background-color: #52c41a; 
                border: 2px solid #52c41a; 
                image: url(data:image/svg+xml;base64,{check_svg_base64});
            }}
            
            /* 🌟 4. 底部按钮倒角增加到 16px，更具现代感 */
            QPushButton {{ 
                background-color: #1677ff; 
                color: #ffffff; 
                font-weight: bold; 
                font-size: 26px; 
                border-radius: 16px; 
                min-height: 65px; 
                max-height: 65px; 
                padding: 10px 0; 
                margin-top: 25px; 
            }}
            QPushButton:hover {{ background-color: #4096ff; }}
            QPushButton:pressed {{ background-color: #0958d9; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(50, 40, 50, 40)
        layout.setSpacing(12)
        
        layout.addWidget(QLabel("🚀 请选择系统启动模式："))
        
        self.cb1 = QCheckBox("1. 清空历史原始音频与特征库")
        self.cb2 = QCheckBox("2. 从本地 Wav 重新提取特征 (旧数据兼容)")
        self.cb3 = QCheckBox("3. 重新训练【全局主模型】(丢弃旧模型)")
        self.cb4 = QCheckBox("4. 重新训练【专家子模型】(针对难点材质)")
        self.cb5 = QCheckBox("5. 启动后立刻进入采集模式")

        for cb in [self.cb1, self.cb2, self.cb3, self.cb4, self.cb5]: 
            cb.setCursor(Qt.PointingHandCursor)
            layout.addWidget(cb)
            
        layout.addStretch()
        
        btn_confirm = QPushButton("确认配置并启动系统")
        btn_confirm.setCursor(Qt.PointingHandCursor)
        btn_confirm.clicked.connect(self.accept)
        layout.addWidget(btn_confirm)

    def get_choices(self):
        return {
            "clear_audio": self.cb1.isChecked(), 
            "re_extract": self.cb2.isChecked(), 
            "re_train": self.cb3.isChecked(), 
            "re_train_sub": self.cb4.isChecked(), 
            "enter_collect": self.cb5.isChecked()
        }
    


class ModernDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("布料识别")
        self.resize(1400, 900)
        self.current_img_path = None

        # 🌟 QSS: 调整后的工业风格，更圆润，字体更大
        self.setStyleSheet("""
            QMainWindow { background-color: #f0f2f5; }
            QWidget { font-family: 'Microsoft YaHei', sans-serif; }
            
            QFrame[cssClass="Card"] { background-color: #ffffff; border-radius: 20px; border: 1px solid #e0e4e8; }
            
            QLabel[cssClass="CardTitle"] { font-size: 26px; font-weight: bold; color: #1c1c1c; padding-bottom: 5px; }
            QLabel[cssClass="InfoText"] { font-size: 26px; color: #595959; margin: 5px 0; }
            
            QLabel#ResultText { font-size: 100px; font-weight: bold; color: #1677ff; }
            QLabel#ImageDisplay { background-color: #ffffff; border-radius: 12px; }
            
            QPushButton#TriggerBtn { background-color: #1677ff; color: white; font-size: 28px; font-weight: bold; border-radius: 12px; padding: 25px; }
            /* 🔄 重启按钮：实心琥珀色 (表示系统级操作，警示但不刺眼) */
            QPushButton#RestartBtn { 
                background-color: #faad14; color: white; font-size: 28px; font-weight: bold; 
                border-radius: 12px; padding: 25px; margin-top: 5px;
            }
        """)

        main_widget = QWidget(); self.setCentralWidget(main_widget)
        root = QVBoxLayout(main_widget); root.setContentsMargins(20, 20, 20, 20)
        
        # 顶部：指示灯 + 标题
        header = QHBoxLayout(); header.setContentsMargins(0, 0, 0, 10)
        self.status_dot = QLabel(); self.status_dot.setFixedSize(24, 24); self.status_dot.setStyleSheet("background-color: #52c41a; border-radius: 12px;")
        title = QLabel("智能工业材质感知系统"); title.setStyleSheet("font-size: 32px; font-weight: bold;")
        header.addWidget(self.status_dot); header.addWidget(title); header.addStretch()
        root.addLayout(header)

        # 内容区
        content = QHBoxLayout(); content.setSpacing(20)
        
        # 左侧三个卡片
        left_layout = QVBoxLayout(); left_layout.setSpacing(20)
        
        c1 = QFrame(); c1.setProperty("cssClass", "Card"); l1 = QVBoxLayout(c1)
        t1 = QLabel("🔌 设备连接"); t1.setProperty("cssClass", "CardTitle")
        self.port_lbl = QLabel("串口：等待下位机..."); self.port_lbl.setProperty("cssClass", "InfoText")
        self.baud_lbl = QLabel("波特率：115200"); self.baud_lbl.setProperty("cssClass", "InfoText")
        l1.addWidget(t1); l1.addWidget(self.port_lbl); l1.addWidget(self.baud_lbl); l1.addStretch()

        c2 = QFrame(); c2.setProperty("cssClass", "Card"); l2 = QVBoxLayout(c2)
        t2 = QLabel("📊 系统状态"); t2.setProperty("cssClass", "CardTitle")
        self.status_lbl = QLabel("就绪"); self.status_lbl.setProperty("cssClass", "InfoText")
        l2.addWidget(t2); l2.addWidget(self.status_lbl); l2.addStretch()

        c3 = QFrame(); c3.setProperty("cssClass", "Card"); l3 = QVBoxLayout(c3)
        t3 = QLabel("🛠️ 操作面板"); t3.setProperty("cssClass", "CardTitle")
        self.restart_btn = QPushButton("🔄 重启系统"); self.restart_btn.setObjectName("RestartBtn")
        self.restart_btn.clicked.connect(self.restart_app)
        self.trigger_btn = QPushButton("🚀 启动识别"); self.trigger_btn.setObjectName("TriggerBtn")
        self.trigger_btn.clicked.connect(self.trigger_motor_detection)
        l3.addWidget(t3); l3.addSpacing(10); l3.addWidget(self.restart_btn); l3.addWidget(self.trigger_btn)

        # 🌟 关键：给这三个卡片加 stretch 权重，它们会自动平分左侧高度
        left_layout.addWidget(c1, stretch=1); left_layout.addWidget(c2, stretch=1); left_layout.addWidget(c3, stretch=2)
        
        # 右侧显示
        right_panel = QFrame(); right_panel.setProperty("cssClass", "Card")
        r_layout = QVBoxLayout(right_panel)
        self.result_lbl = QLabel(""); self.result_lbl.setObjectName("ResultText"); self.result_lbl.setAlignment(Qt.AlignCenter)
        self.img_lbl = QLabel(""); self.img_lbl.setObjectName("ImageDisplay")
        self.img_lbl.setAlignment(Qt.AlignCenter) # 确保 Label 本身的文字/图片居中
        self.img_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        r_layout.addWidget(self.result_lbl); r_layout.addWidget(self.img_lbl, stretch=1)
        
        content.addLayout(left_layout, stretch=1); content.addWidget(right_panel, stretch=3)
        root.addLayout(content, stretch=4)
        
        # 终端
        self.console = QTextEdit(); self.console.setStyleSheet("font-size: 16px; border-radius: 12px; padding: 10px;")
        root.addWidget(self.console, stretch=1)
        

        sys.stdout = EmittingStream()
        sys.stdout.textWritten.connect(self.append_log)
        sys.stderr = EmittingStream()
        sys.stderr.textWritten.connect(self.append_log)

        self.timer = QTimer(); self.timer.timeout.connect(self.update_ui_from_queue); self.timer.start(100)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 🌟 无论虚线框怎么随窗口拉伸，图片都能永远完美等比铺满
        if self.current_img_path and os.path.exists(self.current_img_path):
            pixmap = QPixmap(self.current_img_path)
            self.img_lbl.setPixmap(pixmap.scaled(self.img_lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def blink_status_dot(self):
        """发送信号时，指示灯变为明黄色闪烁，随后恢复常绿"""
        self.status_dot.setStyleSheet("background-color: #fadb14; border-radius: 10px;")
        QTimer.singleShot(300, lambda: self.status_dot.setStyleSheet("background-color: #52c41a; border-radius: 10px;"))

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
            log_info(f"✅ 已向底层控制系统下发: [{cmd_str}] 指令")
        except Exception as e: log_info(f"❌ 指令下发失败: {str(e)}")

    def trigger_motor_detection(self):
        self.result_lbl.setText("识别中..."); self.result_lbl.setStyleSheet("color: #faad14; font-size: 50px;")
        self.img_lbl.clear(); self.img_lbl.setText("等待图像...")
        self.send_udp_cmd("start_detect_cycle")

    def trigger_homing(self):
        log_info("🏠 触发独立进程归零程序...")
        self.send_udp_cmd("homing")

    def update_ui_from_queue(self):
        # 🔥 获取专属通道发来的真实串口名称
        while not serial_queue.empty():
            port = serial_queue.get_nowait()
            self.port_lbl.setText(f"串口：{port}")
            self.port_lbl.setStyleSheet("color: #1677ff; font-weight: bold; font-size: 25px;")
            
        if train_status["is_training"]: self.status_lbl.setText(train_status["msg"]); self.trigger_btn.setEnabled(False); self.trigger_btn.setText("训练中...")
        else: self.status_lbl.setText(f"就绪 | 样本库: {get_total_samples()}"); self.trigger_btn.setEnabled(True); self.trigger_btn.setText("🚀 启动识别")
        
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
    global cnc_process, CHOICE
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # 🌟 加上这两行，强制应用程序的底座字体！
    from PyQt5.QtGui import QFont
    app.setFont(QFont("Microsoft YaHei", 12))
    if not Config.FORCE_CONTINUE_TRAINING:
        dialog = StartupConfigDialog()
        if dialog.exec_() == QDialog.Accepted: CHOICE.update(dialog.get_choices())
        else: sys.exit(0)
    else: CHOICE.update({"clear_audio": False, "re_extract": False, "re_train": True, "re_train_sub": True, "enter_collect": False})
    
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        # ✅ 这里自动选择调用脚本还是exe
        cnc_command = Config.get_cnc_command()
        print(f"🚀 正在启动下位机: {' '.join(cnc_command)}")
        
        cnc_process = subprocess.Popen(
            cnc_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8', 
            bufsize=1,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        def read_cnc_output():
            while cnc_process and cnc_process.poll() is None:
                line = cnc_process.stdout.readline()
                if line: print(f"[下位机] {line.strip()}")
        threading.Thread(target=read_cnc_output, daemon=True).start()
    except Exception as e:
        print(f"❌ 挂载机床驱动失败: {e}")
        messagebox.showerror("致命错误", f"无法启动下位机程序：{str(e)}\n请确保data_collection.exe与主程序在同一目录")

    if CHOICE["clear_audio"]:
        for f in glob.glob(os.path.join(Config.RAW_MIC_DIR, "*")):
            try: os.remove(f)
            except: pass
    if CHOICE["re_train"] and os.path.exists(Config.MODEL_FILE): os.remove(Config.MODEL_FILE)
    if CHOICE.get("re_train_sub", False) and os.path.exists(Config.SUB_MODEL_FILE): os.remove(Config.SUB_MODEL_FILE)

    threading.Thread(target=udp_server, daemon=True).start()
    threading.Thread(target=start_mic, daemon=True).start()
    load_data_if_exist()
    if (CHOICE.get("re_train", False) or CHOICE.get("re_train_sub", False)) and get_total_samples() > 0: threading.Thread(target=train_pipeline, daemon=True).start()

    window = ModernDashboard()
    window.showMaximized() 
    sys.exit(app.exec_())


def run_cnc_process_in_thread():
    """在独立线程中运行下位机CNC代码"""
    import data_collection
    data_collection.run_batch_collection()

@atexit.register
def cleanup():
    if cnc_process: cnc_process.terminate()

if __name__ == "__main__":
    start_app()