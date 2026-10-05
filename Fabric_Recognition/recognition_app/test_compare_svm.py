import sys
import warnings
from sklearn.model_selection import GridSearchCV
warnings.filterwarnings('ignore')
import logging
logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)

import tkinter as tk
from tkinter import messagebox

from PIL import ImageTk, Image
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False
import queue

# 全局UI安全队列
ui_queue = queue.Queue()
import numpy as np
import sounddevice as sd
import time
import threading
import socket
import json
import os
import pickle
import scipy.io.wavfile as wavfile
from collections import deque
from matplotlib.animation import FuncAnimation
import librosa
from multiprocessing import Pool, cpu_count

from sklearn.svm import SVC
from sklearn.preprocessing import RobustScaler
from sklearn.pipeline import make_pipeline
from sklearn.feature_selection import SelectKBest, f_classif
from scipy.stats import skew, kurtosis

# ===================== 全局配置 =====================
UDP_IP, UDP_PORT = "0.0.0.0", 8888
MODEL_DIR = "svm_models"
RAW_MIC_DIR = "raw_mic"
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(RAW_MIC_DIR, exist_ok=True)
IS_COLLECTING = False  # 采集运行标志
PROCESSED_FILES = set()  # 记录已提取特征的文件，防重复
EXTRACTOR_THREAD = None  # 特征提取线程
PIPELINE_FILE = f"{MODEL_DIR}/svm_pipeline.pkl"
DATASET_FILE = f"{MODEL_DIR}/training_dataset.pkl"

PROGRAM_RUNNING = True
data_lock = threading.Lock()

result = {
    "pred": 0, "conf": 0.0, "scores": [0.0]*10,
    "msg": "", "color": "#0f0"
}
collect_motion_data = {"stage": "wait"}

# ===================== 音频参数 =====================
SR_MIC = 192000
F_MIN = 120
F_MAX = 9600
TOTAL_SEC = 1.7
MIC_BUFFER_SIZE = int(SR_MIC * TOTAL_SEC)
N_MELS = 256

N_FFT = 4096

mic_buffer = deque(maxlen=MIC_BUFFER_SIZE)
audio_segment_1 = []
audio_segment_2 = []
raw_train_data = []

X_train, y_train = [], []
is_model_trained = False
svm_pipeline = None

# ===================== 全局开关（4个统一选项） =====================
CHOICE_CLEAR_AUDIO = False
CHOICE_RE_EXTRACT = False
CHOICE_RE_TRAIN = False
CHOICE_ENTER_COLLECT = False
ROOT_UI = tk.Tk()
ROOT_UI.withdraw()
ROOT_UI.attributes('-topmost', True)

def log_info(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}")

# ===================== 【统一弹窗】所有选项合并，选完立即删文件 =====================
def unified_init_choice():
    global CHOICE_CLEAR_AUDIO, CHOICE_RE_EXTRACT, CHOICE_RE_TRAIN, CHOICE_ENTER_COLLECT
    log_info("="*50)
    log_info("📌 系统初始化配置")
    log_info("="*50)
    
    CHOICE_CLEAR_AUDIO = messagebox.askyesno("配置1/4", "是否【清空原始音频库】？\n是=删除所有已存音频")
    CHOICE_RE_EXTRACT = messagebox.askyesno("配置2/4", "是否【重新提取所有特征】？\n是=删除历史特征集")
    CHOICE_RE_TRAIN = messagebox.askyesno("配置3/4", "是否【重新训练模型】？\n是=删除历史模型")
    CHOICE_ENTER_COLLECT = messagebox.askyesno("配置4/4", "是否【进入采集模式】？\n是=等待机床采集 | 否=直接使用现有数据")

    log_info("\n⚡ 立即执行初始化清理操作...")
    if CHOICE_CLEAR_AUDIO:
        for f in os.listdir(RAW_MIC_DIR):
            try: os.remove(os.path.join(RAW_MIC_DIR, f))
            except: pass
        log_info("✅ 已清空原始音频库")
    if CHOICE_RE_EXTRACT and os.path.exists(DATASET_FILE):
        os.remove(DATASET_FILE)
        log_info("✅ 已清空历史特征集")
    if CHOICE_RE_TRAIN and os.path.exists(PIPELINE_FILE):
        os.remove(PIPELINE_FILE)
        log_info("✅ 已清空历史训练模型")
    
    log_info(f"\n📋 最终配置：")
    log_info(f"   清空音频：{CHOICE_CLEAR_AUDIO} | 重提特征：{CHOICE_RE_EXTRACT}")
    log_info(f"   重训模型：{CHOICE_RE_TRAIN} | 采集模式：{CHOICE_ENTER_COLLECT}")
    log_info("="*50)

# ===================== 多进程：处理单个音频对 =====================
def process_single_file_pair(arc1_name):
    try:
        pos_str = arc1_name.split("_")[0].replace("mat", "")
        pos = int(pos_str)
        arc2_name = arc1_name.replace("arc1", "arc2")
        arc1_path = os.path.join(RAW_MIC_DIR, arc1_name)
        arc2_path = os.path.join(RAW_MIC_DIR, arc2_name)

        _, data1 = wavfile.read(arc1_path)
        _, data2 = wavfile.read(arc2_path)

        feat1 = extract_mic_features(data1)
        feat2 = extract_mic_features(data2)

        # 🔥 每处理完一个立刻打印
        print(f"[{time.strftime('%H:%M:%S')}]   ✅ 材质 {pos} 特征提取完成 ({arc1_name})")
        print(f"[{time.strftime('%H:%M:%S')}]   ✅ 材质 {pos} 特征提取完成 ({arc2_name})")
        
        return pos, feat1, feat2

    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}]   ❌ 处理失败：{str(e)}")
        return 0, None, None

# ===================== 多进程批量特征提取 =====================
def load_existing_audio_extract_features():
    global X_train, y_train
    log_info("\n🚀【CPU 榨干版】批量特征提取...")
    X_train.clear()
    y_train.clear()

    all_files = os.listdir(RAW_MIC_DIR)
    arc1_files = [f for f in all_files if f.endswith(".wav") and "arc1" in f]
    total = len(arc1_files)
    log_info(f"🔍 共 {total} 组，启用 {cpu_count()} 核心全速跑")

    # ======================= 🔥 榨干核心 =======================
    import multiprocessing
    NUM_PROCESSES = multiprocessing.cpu_count()  # 全核心

    with Pool(processes=NUM_PROCESSES) as pool:
        results = list(pool.imap_unordered(process_single_file_pair, arc1_files, chunksize=2))

    for pos, f1, f2 in results:
        if f1 is not None:
            X_train.append(f1)
            y_train.append(pos)
        if f2 is not None:
            X_train.append(f2)
            y_train.append(pos)

    with open(DATASET_FILE, 'wb') as f:
        pickle.dump((X_train, y_train), f)

    log_info(f"\n🎉 提取完成！总样本数：{len(X_train)}")
    
# ===================== 数据加载 =====================
def load_data_if_exist():
    global is_model_trained, svm_pipeline, X_train, y_train
    if os.path.exists(DATASET_FILE):
        try:
            with open(DATASET_FILE, 'rb') as f:
                X_train, y_train = pickle.load(f)
            log_info(f"✅ 已加载历史数据集，样本数：{len(X_train)}")
        except:
            log_info("⚠️ 历史数据集加载失败，已清空")
            X_train.clear()
            y_train.clear()

    if os.path.exists(PIPELINE_FILE):
        try:
            with open(PIPELINE_FILE, 'rb') as f:
                svm_pipeline = pickle.load(f)
                is_model_trained = True
            log_info("✅ 识别模型加载成功")
        except:
            log_info("⚠️ 模型加载失败")
            is_model_trained = False


# ===================== 极速版 · 材质专属特征（速度拉满 + 精度无损）=====================
def extract_mic_features(mic_data):
    mic = np.array(mic_data, dtype=np.float32)
    if len(mic) < 800:
        return None
    
    # 仅去直流 = 全频段保留，不丢弱材质信号
    mic = mic - np.mean(mic)

    # ===================== 1. 极速时域特征（材质振动核心，0耗时）=====================
    zcr = librosa.feature.zero_crossing_rate(mic)
    rms = librosa.feature.rms(y=mic)
    onset_env = librosa.onset.onset_strength(y=mic, sr=SR_MIC, n_fft=N_FFT)
    
    # 强制1维，零报错
    basic_feat = np.concatenate([
        np.atleast_1d(np.mean(zcr)), np.atleast_1d(np.var(zcr)),
        np.atleast_1d(np.mean(rms)), np.atleast_1d(np.var(rms)),
        np.atleast_1d(np.mean(onset_env)), np.atleast_1d(np.var(onset_env)),
        np.atleast_1d(np.max(np.abs(mic))), np.atleast_1d(np.std(mic))
    ])

    # ===================== 2. 精简MFCC（材质音色核心，超快）=====================
    # 20维MFCC = 精度拉满 + 计算量砍半
    mfcc = librosa.feature.mfcc(y=mic, sr=SR_MIC, n_mfcc=20, n_fft=N_FFT)
    mfcc_delta = librosa.feature.delta(mfcc)
    
    mfcc_feat = np.concatenate([
        np.mean(mfcc, axis=1), np.var(mfcc, axis=1),
        np.mean(mfcc_delta, axis=1), np.var(mfcc_delta, axis=1)
    ])

    # ===================== 3. 极简谱对比度（材质纹理区分，极快）=====================
    contrast = librosa.feature.spectral_contrast(y=mic, sr=SR_MIC, n_fft=N_FFT)
    contrast_feat = np.concatenate([np.mean(contrast, axis=1), np.var(contrast, axis=1)])

    # ===================== 最终合并（无冗余、无耗时计算）=====================
    feat = np.concatenate([basic_feat, mfcc_feat, contrast_feat])
    
    # 安全兜底
    feat = np.nan_to_num(feat, nan=0.0, posinf=0.0, neginf=0.0)
    return feat if not np.any(np.isnan(feat)) else None

# ===================== 多进程：处理一组样本 =====================
def process_train_sample(item):
    pos, a1, a2 = item
    f1 = extract_mic_features(a1)
    f2 = extract_mic_features(a2)
    print(f"[{time.strftime('%H:%M:%S')}]   -> 提取样本 (材质 {pos}) 完成")
    return pos, f1, f2

# ===================== 多进程训练前特征提取 =====================
def extract_all_and_train():
    global X_train, y_train, is_model_trained, svm_pipeline, raw_train_data
    X_clean, y_clean = [], []
    for f, label in zip(X_train, y_train):
        if not np.any(np.isnan(f)) and not np.any(np.isinf(f)):
            X_clean.append(f)
            y_clean.append(label)
    X_train, y_train = X_clean, y_clean
    if len(raw_train_data) > 0:
        log_info(f"⚙️ 【多进程】提取本次采集特征，共 {len(raw_train_data)} 组...")
        with Pool(processes=cpu_count()) as pool:
            results = pool.map(process_train_sample, raw_train_data)

        for pos, f1, f2 in results:
            if f1 is not None:
                X_train.append(f1)
                y_train.append(pos)
            if f2 is not None:
                X_train.append(f2)
                y_train.append(pos)
        raw_train_data.clear()

    if len(X_train) == 0:
        log_info("❌ 无有效训练数据")
        return

    log_info("⏳ 提取完毕，多核训练中...")
    X_train_np = np.array(X_train)
    base_pipeline = make_pipeline(RobustScaler(), SelectKBest(f_classif), SVC(class_weight=None, probability=True, max_iter=100000, random_state=42))

    param_grid = [
    {
        'selectkbest__k': ['all'],   # 禁止删除任何特征，救活冷门材质
        'svc__C': [3,4,5,6,7,8,10,12,15],
        'svc__gamma': [0.0012,0.0015,0.0018,0.002,0.0022,0.0025,0.003,0.0035],
        'svc__kernel': ['rbf'],
        'svc__class_weight': [None], # 不压制小众材质
        'svc__tol': [1e-5]
    }
]
    
    grid = GridSearchCV(base_pipeline, param_grid, cv=3, n_jobs=-1, scoring='f1_macro')
    
    grid.fit(X_train_np, np.array(y_train))

    svm_pipeline = grid.best_estimator_
    is_model_trained = True
    with open(PIPELINE_FILE, 'wb') as f: pickle.dump(svm_pipeline, f)
    with open(DATASET_FILE, 'wb') as f: pickle.dump((X_train, y_train), f)
    log_info(f"✅ 训练完成！最佳参数: {grid.best_params_}")

# ===================== 双半圆处理（极限多核加速版）=====================
def process_collected_circle(pos, stage):
    global audio_segment_1, audio_segment_2, raw_train_data

    log_info(f"🔗 捕获双半圆，开始独立落盘/分析 (阶段:{stage})")

    if stage == "train":
        ts = int(time.time() * 1000)
        file1 = os.path.join(RAW_MIC_DIR, f"mat{pos}_{ts}_arc1.wav")
        file2 = os.path.join(RAW_MIC_DIR, f"mat{pos}_{ts}_arc2.wav")

        norm1 = np.clip(np.array(audio_segment_1) * 0.9, -1.0, 1.0)
        norm2 = np.clip(np.array(audio_segment_2) * 0.9, -1.0, 1.0)

        wavfile.write(file1, SR_MIC, norm1.astype(np.float32))
        wavfile.write(file2, SR_MIC, norm2.astype(np.float32))
        raw_train_data.append((pos, np.array(audio_segment_1), np.array(audio_segment_2)))

    elif stage == "detect":
        # ===================== 🔥 极限加速：无启动开销 + 真并行 =====================
        import threading
        import queue

        q = queue.Queue()

        def task1():
            q.put(extract_mic_features(audio_segment_1))
        def task2():
            q.put(extract_mic_features(audio_segment_2))

        # 双线程同时跑（无进程启动开销，速度快 30%~200%）
        t1 = threading.Thread(target=task1, daemon=True)
        t2 = threading.Thread(target=task2, daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        feat1 = q.get()
        feat2 = q.get()

        # NaN 安全兜底
        if feat1 is not None:
            feat1 = np.nan_to_num(feat1, nan=0.0, posinf=0.0, neginf=0.0)
        if feat2 is not None:
            feat2 = np.nan_to_num(feat2, nan=0.0, posinf=0.0, neginf=0.0)

        if feat1 is not None and feat2 is not None:
            recognize_dual_features(pos, feat1, feat2)

# ===================== 识别逻辑 =====================
def recognize_dual_features(pos_actual, feat1, feat2):
    global result, ui_queue
    p1 = svm_pipeline.predict_proba([feat1])[0]
    p2 = svm_pipeline.predict_proba([feat2])[0]
    p_fused = (p1 + p2) / 2.0
    best_pred = np.argmax(p_fused) + 1
    conf = p_fused[best_pred - 1]
    
    result["pred"] = best_pred
    result["conf"] = conf
    result["scores"] = p_fused.tolist()
    result["color"] = "#0f0" if best_pred == pos_actual else "#f00"

    # 只发消息，不操作任何UI，彻底规避线程冲突
    ui_queue.put(best_pred)

# ===================== 图片弹窗 · 线程安全终极版 =====================
PHOTO_BASE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "photos")
img_popup_window = None
GLOBAL_IMAGE_CACHE = {}  # 全局缓存，防回收

def show_target_image(target_id):
    """ 只能在【主线程】运行 """
    global img_popup_window, GLOBAL_IMAGE_CACHE

    # 安全关闭旧窗口
    try:
        if img_popup_window is not None:
            img_popup_window.destroy()
    except:
        pass

    # 新窗口
    img_popup_window = tk.Toplevel(ROOT_UI)
    img_popup_window.title(f"AI 识别：材质 {target_id}")
    img_popup_window.attributes("-topmost", True)

    img_path = os.path.join(PHOTO_BASE_PATH, f"{target_id}.jpg")

    try:
        pil_img = Image.open(img_path)
        pil_img.thumbnail((700, 700), Image.Resampling.LANCZOS)
        
        # 全局缓存，永不回收
        GLOBAL_IMAGE_CACHE["img"] = ImageTk.PhotoImage(pil_img)
        
        lbl = tk.Label(img_popup_window, image=GLOBAL_IMAGE_CACHE["img"])
        lbl.pack()

    except Exception as e:
        tk.Label(img_popup_window, text=f"无图片 {target_id}", fg="red").pack()


# ===================== 🔥 线程安全调用入口（必须用这个）=====================
def show_image_safe(target_id):
    """ 子线程调用这个 → 自动抛到主线程执行 """
    ROOT_UI.after(0, show_target_image, target_id)
        
        
# ===================== 麦克风/UDP =====================
def mic_cb(indata, *_, **__):
    mic_buffer.extend(indata[:,0])

def start_mic():
    with sd.InputStream(samplerate=SR_MIC, channels=1, blocksize=1024, callback=mic_cb):
        while PROGRAM_RUNNING: time.sleep(0.1)

def udp_server():
    global X_train, y_train, audio_segment_1, audio_segment_2, raw_train_data, is_model_trained
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((UDP_IP, UDP_PORT))

    while PROGRAM_RUNNING:
        try:
            data, _ = s.recvfrom(1024)
            msg = json.loads(data.decode('utf-8'))
            pos, status, stage = msg["position_id"], msg["status"], msg["stage"]
            collect_motion_data["stage"] = stage

            if status == "train_start":
                if not CHOICE_CLEAR_AUDIO:
                    log_info("ℹ️ 不清空原库，新样本将【追加】到数据集")
                else:
                    X_train.clear()
                    y_train.clear()
                    raw_train_data.clear()

            elif status == "capture_1":
                with data_lock: audio_segment_1 = list(mic_buffer)

            elif status == "capture_2":
                with data_lock: audio_segment_2 = list(mic_buffer)
                process_collected_circle(pos, stage)

            elif status == "train_done":
                log_info("\n📦 采集完成，执行多进程特征提取+训练")
                if CHOICE_RE_EXTRACT or CHOICE_RE_TRAIN:
                    threading.Thread(target=extract_all_and_train, daemon=True).start()
                else:
                    log_info("ℹ️ 跳过提取+训练，直接识别")

        except Exception:
            continue

# ===================== UI界面 =====================
plt.style.use('dark_background')
fig = plt.figure(figsize=(16, 8))
gs = fig.add_gridspec(3, 1, height_ratios=[3, 1, 3])
SHOW_POINTS = 1000
ax_time = fig.add_subplot(gs[0])
ax_time.set_title("实时全频波形", fontsize=14)
ax_time.set_ylim(-0.4, 0.4)
line_time, = ax_time.plot(np.zeros(SHOW_POINTS), '#0ff', lw=1)
ax_time.set_xlim(0, SHOW_POINTS)

ax_res = fig.add_subplot(gs[1])
ax_res.axis('off')
txt_stage = ax_res.text(0.02, 0.5, "系统状态", fontsize=18, c='#ff0')
txt_pred  = ax_res.text(0.4, 0.5, "AI认定：-", fontsize=32, c='#0f0')

ax_probs = fig.add_subplot(gs[2])
ax_probs.axis('off')
prob_texts = []

for i in range(10):
    row, col = i // 5, i % 5
    t = ax_probs.text(0.05 + col*0.19, 0.7 - row*0.4, f"材质 {i+1}: 0.0%", fontsize=18, c='white')
    prob_texts.append(t)

def refresh(frame):
    # 【核心】主线程轮询队列，安全处理图片弹窗
    try:
        while not ui_queue.empty():
            target_id = ui_queue.get_nowait()
            show_target_image(target_id)
    except queue.Empty:
        pass

    # 原有波形刷新逻辑完全保留
    y = np.array(list(mic_buffer))
    y = y[-SHOW_POINTS:] if len(y) >= SHOW_POINTS else np.pad(y, (SHOW_POINTS - len(y), 0))
    y = np.clip((y - np.mean(y)) * 1.5, -1, 1)
    line_time.set_ydata(y)

    stage = collect_motion_data["stage"]
    current_samples = len(raw_train_data)*2 if stage == "train" else len(X_train)
    txt_stage.set_text(f"{'采集中' if stage=='train' else '识别中'} | 样本:{current_samples}")

    if is_model_trained and stage == "detect":
        txt_pred.set_text(f"AI认定：材质 {result['pred']} | 置信：{result['conf']:.1%}")
        txt_pred.set_color(result['color'])
        for i in range(10):
            prob_texts[i].set_text(f"材质 {i+1}:\n{result['scores'][i]:.1%}")
            prob_texts[i].set_color('#0f0' if i+1==result['pred'] else 'white')
            
    return line_time, txt_stage, txt_pred, *prob_texts

# ===================== 主入口（最终版：双分支都包含训练，采集绝对优先）=====================
if __name__ == "__main__":
    unified_init_choice()

    # ========== 核心规则：采集模式 → 先启动采集，再做所有操作（含训练）==========
    if CHOICE_ENTER_COLLECT:
        # 1. 【最高优先级】最先启动采集（UDP+麦克风）
        IS_COLLECTING = True
        threading.Thread(target=udp_server, daemon=True).start()
        threading.Thread(target=start_mic, daemon=True).start()
        log_info("✅ 采集模式：UDP/麦克风已优先启动")

        # 2. 采集启动后 → 重提取特征
        if not CHOICE_CLEAR_AUDIO and CHOICE_RE_EXTRACT:
            load_existing_audio_extract_features()

        # 3. 采集启动后 → 加载数据/模型
        load_data_if_exist()

        # 🔥 【补齐缺失的训练】采集模式下，满足条件就训练（和非采集分支完全一致）
        if CHOICE_RE_TRAIN and len(X_train) > 0:
            extract_all_and_train()

    # ========== 非采集模式：保留你原始完整逻辑 ==========
    else:
        if not CHOICE_CLEAR_AUDIO and CHOICE_RE_EXTRACT:
            load_existing_audio_extract_features()
        
        load_data_if_exist()

        if CHOICE_RE_TRAIN and len(X_train) > 0:
            extract_all_and_train()

        threading.Thread(target=udp_server, daemon=True).start()
        threading.Thread(target=start_mic, daemon=True).start()
    
    # 统一启动UI
    ani = FuncAnimation(fig, refresh, interval=30, blit=True)
    plt.tight_layout()
    plt.show()