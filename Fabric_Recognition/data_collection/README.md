# CNC 轨迹采集项目 README

## 1. 项目简介

这是一个 **CNC 平台 + 触觉传感器** 的自动化数据采集项目。项目的核心目标是：

1. 控制 CNC 按预设轨迹运动；
2. 在运动过程中同步采集触觉/力传感器数据；
3. 按类别、速度、轨迹编号、重复次数自动整理保存数据；
4. 支持断点续采、批量多区域采集、可选实时绘图。

当前主流程由 `data_collection.py` 调度，底层分别调用：

- `cnc_module.py`：负责 CNC 串口连接、G-code 发送、等待运动完成；
- `sensor_module.py`：负责传感器串口连接、配置、实时采样、保存数据、可选实时绘图；
- `data_collection.py`：负责组合轨迹、遍历速度/区域/重复次数，并协调 CNC 与传感器同步工作。

此外，项目中还包含：

- `sensor.py`：`sensor_module.py` 的简化版本，不带实时绘图和部分附加能力；
- `zero_all.py`：用于快速启动传感器并执行 CNC 回零；
- `mat.py`：用于数据可视化的测试脚本，但当前与最新数据格式并不完全匹配，使用前需要调整。

---

## 2. 推荐项目结构

建议将项目整理成如下结构（尤其注意 `data_collection.py` 中使用了 `from module import sensor_module, cnc_module`）：

```text
project/
├── module/
│   ├── cnc_module.py
│   └── sensor_module.py
├── data_collection.py
├── zero_all.py
├── sensor.py
├── mat.py
└── README.md
```

---

## 3. 依赖环境

### 3.1 Python 依赖

项目中使用到的主要库：

- `pyserial`
- `matplotlib`
- `threading`（标准库）
- `datetime`（标准库）
- `os`（标准库）
- `time`（标准库）
- `collections.deque`（标准库）
- `atexit`（标准库）

安装命令：

```bash
pip install pyserial matplotlib
```

### 3.2 硬件要求

- 一台支持 G-code/GRBL 的 CNC 控制器；
- 一个通过串口输出数据的触觉/力传感器；
- 正确配置的串口号，例如：
  - CNC：`COM4`
  - Sensor：`COM5`

---

## 4. 项目运行逻辑概览

完整运行流程如下：

1. 初始化 CNC 串口；
2. 尝试执行 `$H` 回零；
3. 设置 CNC 为绝对坐标模式 `G90`、毫米单位 `G21`；
4. CNC 移动到 `(X0, Y0, Z0)` 安全初始位置；
5. 根据区域坐标自动生成 5 类轨迹；
6. 按“区域组 -> 速度 -> 轨迹 -> 重复次数”依次执行任务；
7. 每次任务执行时：
   - 先移动到轨迹起点上方安全高度；
   - 启动传感器采集线程；
   - Z 轴下压到接触高度；
   - 沿指定轨迹运动；
   - Z 轴抬起；
   - 停止传感器采集并保存数据；
8. 所有任务完成后再次 `$H` 回零。

---

## 5. 各模块详细说明

## 5.1 `cnc_module.py`

### 模块作用

负责与 CNC 控制器建立串口通信，发送 G-code 指令，并在需要时等待机床运动完全结束。

### 主要全局变量

| 变量名 | 作用 |
|---|---|
| `cnc_connection` | 保存当前 CNC 串口连接对象 |
| `cnc_port` | CNC 串口号，默认 `COM4` |
| `cnc_baudrate` | CNC 波特率，默认 `115200` |

### 主要函数

#### 1）`initialize_cnc(port="COM4", baudrate=115200)`
初始化 CNC 串口连接。

- 建立串口连接；
- 等待设备软复位；
- 清空输入缓冲；
- 设置超时。

返回值：
- 成功返回 `True`
- 失败返回 `False`

#### 2）`execute_command(cmd_str)`
发送一条 G-code 或 GRBL 指令，并等待设备返回 `ok`。

特点：
- 如果收到 `error` 或 `alarm`，会抛出异常；
- 如果检测到 `ALARM:11`，会抛出 `ConnectionAbortedError("ALARM:11 Homing Required")`。

适合发送：
- `$H`
- `$X`
- `G90`
- `G21`
- 不需要额外等待运动结束的普通配置命令

#### 3）`execute_command_and_wait_complete(cmd_str)`
发送运动指令，并在收到 `ok` 后继续等待机床真正进入 `Idle` 状态。

适合发送：
- `G01 X... Y... Z... F...`
- `G02 ...`

这是本项目里最关键的运动控制接口之一。

#### 4）`wait_for_idle()`
主动轮询机床状态：

- 反复发送 `?` 查询状态；
- 检查串口响应中是否出现 `<Idle`；
- 最多等待 240 秒。

#### 5）`close_cnc()`
关闭 CNC 串口连接。

### 使用建议

- 所有涉及物理运动的指令，尽量使用 `execute_command_and_wait_complete()`；
- 配置类指令（如 `$H`、`G90`、`G21`）可以用 `execute_command()`；
- 如果机床没有先回零，某些控制器会触发 `ALARM:11`。

---

## 5.2 `sensor_module.py`

### 模块作用

负责：

- 连接传感器串口；
- 发送初始化配置；
- 开启后台线程持续读取数据；
- 将三轴力数据实时保存到 TXT；
- 可选显示实时曲线；
- 程序退出时自动清理绘图窗口。

### 主要全局变量

| 变量名 | 作用 |
|---|---|
| `sensor_port` | 传感器串口号，默认 `COM5` |
| `sensor_baudrate` | 传感器波特率，默认 `115200` |
| `sensor_connection` | 当前串口连接对象 |
| `is_running` | 采集线程是否持续运行 |
| `sensor_thread` | 后台采集线程 |
| `data_count` | 当前采集到的数据条数 |
| `output_file_handler` | 当前输出文件句柄 |
| `output_filename` | 当前输出文件路径 |
| `plot_enabled` | 是否启用实时绘图 |
| `plot_thread` | 绘图线程 |
| `data_buffer_x/y/z` | 绘图用缓冲区，最多保留 500 个点 |

### 主要函数

#### 1）`create_output_file(...)`
创建输出文件并打开句柄。

生成目录结构：

```text
main_category/sub_category/v速度/traj轨迹号/r重复次数.txt
```

例如：

```text
fabric/b47/v500/traj1/r1.txt
```

支持的额外能力：

- 自动创建分层目录；
- 可选是否允许重名后缀；
- 可将 `metadata` 写入文件头（以 `# key: value` 形式）。

#### 2）`write_sensor_data_to_txt(parsed_values)`
把解析后的前三个力值写入 TXT。

当前每行格式是：

```text
fx fy fz
```

例如：

```text
0.15 -0.22 3.48
```

#### 3）`configure_sensor()`
配置传感器。

当前发送的配置命令是：

```text
100 Hz,2
```

如果以后需要改采样率或模式，优先修改这里。

#### 4）`parse_sensor_data(data_line)`
解析一行串口数据：

- 去除空白；
- 如果以 `d0` 开头则去掉前缀；
- 按空格分割并尝试转为浮点数；
- 至少提取到 3 个数值时返回列表。

#### 5）`is_contact_data(parsed_values)`
判断是否接触。

逻辑：若前三个数值都接近 0（绝对值小于 0.1），判定为基准状态；否则判定为接触状态。

#### 6）`sensor_read_loop()`
后台线程持续执行的数据读取循环。

主要功能：

- 从串口读取一行；
- 解析有效数据；
- 保存到 TXT；
- 更新实时绘图缓存；
- 按一定频率打印控制台状态。

#### 7）`start_sensor_reading(...)`
启动传感器读取，是外部最常用的入口。

调用后会：

1. 创建输出文件；
2. 必要时初始化绘图窗口；
3. 打开串口；
4. 清空缓存；
5. 配置传感器；
6. 启动后台采集线程。

#### 8）`stop_sensor_reading()`
停止采集线程，刷新并关闭输出文件，关闭串口连接。

#### 9）`init_plot()` / `plot_loop()` / `close_plot()`
用于实时绘图。

如果 `enable_plot=True`，会打开一个 matplotlib 窗口，实时显示三轴力曲线。

### `start_sensor_reading()` 重要参数说明

| 参数名 | 作用 | 默认值 | 建议 |
|---|---|---:|---|
| `port` | 传感器串口号 | `COM5` | 根据设备管理器修改 |
| `baudrate` | 传感器波特率 | `115200` | 与硬件一致 |
| `main_category` | 主类别文件夹 | `m1` | 例如材料大类 |
| `sub_category` | 子类别文件夹 | `b1` | 例如样本编号 |
| `velocity` | 当前速度标签 | `500` | 会写入 `v500` 目录 |
| `trajectory_id` | 轨迹编号 | `1` | 会写入 `traj1` 目录 |
| `repeat_num` | 重复次数编号 | `1` | 会写入 `r1.txt` |
| `enable_plot` | 是否显示实时绘图 | `True`/`False` | 调试时开，批量采集时建议关 |
| `allow_suffix` | 文件已存在时是否自动加后缀 | `True` | 批量采集建议由主流程控制 |
| `metadata` | 文件头元数据 | `None` | 建议保留，方便追溯 |

---

## 5.3 `sensor.py`

### 模块作用

这是 `sensor_module.py` 的简化版本，保留了：

- 串口连接；
- 传感器配置；
- 数据读取；
- TXT 保存；
- 接触/基准状态判定；
- 控制台输出。

### 与 `sensor_module.py` 的区别

`sensor.py` 缺少以下能力：

- 实时绘图；
- `metadata` 文件头写入；
- `allow_suffix=False` 时直接阻止覆盖的控制；
- 部分更细化的可视化与清理机制。

### 是否还需要保留

如果你的主流程已经全面切换到 `sensor_module.py`，那么：

- `sensor.py` 可以视为历史版本或备用版本；
- 后续维护建议统一以 `sensor_module.py` 为主，避免两套逻辑同时修改造成不一致。

---

## 5.4 `data_collection.py`

### 模块作用

这是整个项目的**主控脚本**，负责：

- 设定默认采集区域；
- 设定默认速度列表；
- 自动生成轨迹；
- 调用 CNC 和传感器模块执行批量采集；
- 实现断点续采；
- 自动组织输出目录。

### 关键类型定义

```python
Coordinate = Tuple[float, float]
```

表示二维坐标 `(x, y)`。

### 默认区域坐标 `DEFAULT_COORD_GROUPS`

```python
DEFAULT_COORD_GROUPS = [
    ((-10.0, 0.0), (-70.0, -60.0)),
    ((-110.0, 0.0), (-170.0, -60.0)),
    ((-10.0, -100.0), (-70.0, -160.0)),
    ((-110.0, -100.0), (-170.0, -160.0)),
]
```

每一组区域由两个点组成：

```python
((start_x, start_y), (end_x, end_y))
```

你可以把它理解为：

- `start`：区域的一个角点；
- `end`：区域对角点；
- 主程序会基于这两个点自动生成多种轨迹。

### 默认参数

| 常量名 | 默认值 | 作用 |
|---|---:|---|
| `DEFAULT_SPEED_LIST` | `[500, 1000, 1500, 2000, 2500]` | 批量采集速度列表 |
| `DEFAULT_REPEAT_PER_TRAJECTORY` | `5` | 每条轨迹重复次数 |
| `DEFAULT_MAIN_CATEGORY` | `"fabirc"` | 主类别目录名（注意这里代码里是 `fabirc`） |
| `DEFAULT_SUB_CATEGORY` | `"b"` | 默认子类别 |
| `DEFAULT_GROUP_SUB_CATEGORIES` | `['b47','b48','b49','b50']` | 每组区域单独子目录 |
| `DEFAULT_Z_DOWN` | `-17.0` | 接触/下压高度 |
| `DEFAULT_Z_SAFE` | `0.0` | 安全抬刀高度 |
| `DEFAULT_SENSOR_PORT` | `COM5` | 传感器串口 |
| `DEFAULT_SENSOR_BAUD` | `115200` | 传感器波特率 |
| `DEFAULT_CNC_PORT` | `COM4` | CNC 串口 |
| `DEFAULT_CNC_BAUD` | `115200` | CNC 波特率 |
| `DEFAULT_ENABLE_PLOT` | `False` | 是否开启实时绘图 |
| `DEFAULT_RAPID_FEED` | `5000` | 空移速度 |
| `DEFAULT_Z_FEED` | `500` | Z 轴进给速度 |

> 注意：`DEFAULT_MAIN_CATEGORY = "fabirc"` 很可能是 `fabric` 的拼写笔误。如果你想规范数据目录名称，建议改成你实际需要的名称。

### 关键辅助函数说明

#### 1）`_expected_output_path(...)`
根据目录规则生成目标输出文件路径，用于断点续采和文件存在性判断。

#### 2）`_normalize_repeats(repeats)`
统一处理重复次数参数。

支持：
- 直接传 `int`
- 传 `tuple/list`

如果传元组或列表，只取第一个元素。

#### 3）`_circle_points(center, radius)`
根据圆心与半径生成圆轨迹的起点和半圈终点。

#### 4）`_build_trajectories(start, end, radius=30.0)`
**这是定义轨迹的核心函数。**

它会根据区域的起点和终点，生成 5 条轨迹：

##### 轨迹 1：横竖（正向）
```text
start -> (end_x, start_y) -> end
```

##### 轨迹 2：对角线（正向）
```text
start -> end
```

##### 轨迹 3：横竖（反向）
```text
end -> (end_x, start_y) -> start
```

##### 轨迹 4：对角线（反向）
```text
end -> start
```

##### 轨迹 5：圆轨迹
- 圆心：区域中心；
- 半径：默认 `30.0`；
- 使用两段 `G02` 顺时针圆弧拼成整圆。

### 轨迹内部数据结构

轨迹由字典定义，典型格式如下：

#### 直线轨迹
```python
{
    "traj_id": 1,
    "path_type": "lines",
    "start": (sx, sy),
    "waypoints": [(ex, sy), (ex, ey)],
}
```

#### 圆轨迹
```python
{
    "traj_id": 5,
    "path_type": "circle",
    "center": (cx, cy),
    "start": circle_start,
    "mid": circle_mid,
}
```

### 轨迹执行函数

#### `_run_line_trajectory(...)`
执行折线/直线类轨迹：

1. CNC 快速移动到起点上方安全高度；
2. 启动传感器采集；
3. Z 轴下压到 `z_down`；
4. 依次经过 `waypoints`；
5. Z 轴抬回 `z_safe`；
6. 停止采集并保存数据。

#### `_run_circle_trajectory(...)`
执行圆轨迹：

1. CNC 移到圆起点上方；
2. 启动传感器采集；
3. Z 轴下压；
4. 用两段 `G02` 走完整圆；
5. Z 轴抬起；
6. 停止采集并保存。

### 主函数 `run_multi_groups(...)`

这是整个系统最重要的入口函数。

#### 参数说明（非常重要）

| 参数名 | 作用 | 建议说明 |
|---|---|---|
| `coord_groups` | 多组区域坐标 | 每组是 `((x1,y1),(x2,y2))` |
| `speed_list` | 速度列表 | 单位通常是 mm/min |
| `repeat_per_trajectory` | 每条轨迹重复次数 | 可传 `int` 或 `(n,)` |
| `main_category` | 主类目录名 | 如材料类型 |
| `sub_category` | 基础子类名 | 与 `append_group_suffix` 配合使用 |
| `append_group_suffix` | 是否自动给每组附加 `_g1/_g2...` | 批量多组但未显式传分组名时很有用 |
| `group_sub_categories` | 每组单独目录名 | 长度必须与区域组数量一致 |
| `resume_enabled` | 是否启用断点续采 | 建议大批量采集时开启 |
| `z_down` | 接触高度 | 最敏感参数之一，直接影响接触深度 |
| `z_safe` | 安全高度 | 非接触移动高度 |
| `sensor_port` | 传感器串口 | 如 `COM5` |
| `sensor_baud` | 传感器波特率 | 通常 `115200` |
| `cnc_port` | CNC 串口 | 如 `COM4` |
| `cnc_baud` | CNC 波特率 | 通常 `115200` |
| `enable_plot` | 是否实时绘图 | 调试时开，正式批采建议关 |
| `rapid_feed` | 空移速度 | 到起点/抬刀后的移动速度 |
| `z_feed` | Z 轴上下速度 | 建议不要过大 |

### `run_multi_groups()` 的内部执行逻辑

1. 初始化 CNC；
2. 尝试 `$H` 回零；
3. 若回零失败，尝试 `$X` 解锁；
4. 设置绝对坐标与毫米单位；
5. 回到 `(0,0,0)`；
6. 为每个区域生成 5 条轨迹；
7. 对每个速度、每条轨迹、每次重复建立任务计划；
8. 若启用断点续采，则从第一个缺失文件开始；
9. 若发现目标文件已存在：
   - `resume_enabled=True` 时会删除旧文件并重采；
   - `resume_enabled=False` 时直接跳过；
10. 执行任务；
11. 所有任务完成后再次 `$H` 回零；
12. `finally` 中确保关闭传感器和 CNC。

### `run_default_four_groups(...)`
这是对 `run_multi_groups(...)` 的封装，默认使用 `DEFAULT_COORD_GROUPS`。

适合快速启动默认四个区域采集。

### `__main__` 默认入口

当前脚本直接执行时，会使用：

- 默认四组区域；
- 默认 5 个速度；
- 每条轨迹重复 5 次；
- 默认分类参数；
- 默认串口参数；
- 默认 Z 轴设置。

---

## 5.5 `zero_all.py`

### 模块作用

这是一个快速测试/辅助脚本，用来：

1. 启动传感器读取；
2. 初始化 CNC；
3. 执行 `$H` 回零。

### 适合场景

- 单独检查 CNC 是否可以正常回零；
- 检查传感器是否能正常连通；
- 做系统上电后的快速准备。

### 注意点

脚本里传感器端口写的是 `COM3`：

```python
sensor_module.start_sensor_reading("COM3", 115200)
```

而主采集脚本默认是 `COM5`。如果你的设备实际在 `COM5`，这里需要同步修改。

---

## 5.6 `mat.py`

### 模块作用

用于读取保存的数据并绘图，包含：

- 三维轨迹图；
- 三轴力随时间变化的二维图。

### 当前存在的问题

这个脚本目前假设输入文件每行格式是：

```text
timestamp, fx fy fz
```

但 `sensor_module.py` / `sensor.py` 当前实际保存格式是：

```text
fx fy fz
```

并且 `sensor_module.py` 还可能在文件头写入：

```text
# key: value
```

所以 `mat.py` **不能直接无修改地读取当前新格式文件**。使用前至少需要做如下适配：

1. 跳过以 `#` 开头的元数据行；
2. 按空格而不是逗号解析力数据；
3. 如果没有时间戳，就用样本序号替代时间轴。

---

## 6. 数据输出格式说明

### 6.1 目录结构

数据按照如下层级保存：

```text
main_category/
└── sub_category/
    └── v速度/
        └── traj轨迹号/
            └── r重复次数.txt
```

例如：

```text
fabric/
└── b47/
    └── v500/
        └── traj1/
            └── r1.txt
```

### 6.2 文件内容示例

如果传入了 `metadata`，文件开头可能是：

```text
# main_category: fabric
# sub_category: b47
# speed_mm_per_min: 500
# trajectory_id: 1
# repeat_num: 1
0.13 0.02 1.45
0.14 0.01 1.48
0.18 0.03 1.52
```

其中真正的数据行始终是：

```text
fx fy fz
```

---

## 7. 如何修改参数

这一节是最实用的部分，建议你后续改实验时优先参考。

### 7.1 修改串口

在 `data_collection.py` 中修改默认值：

```python
DEFAULT_SENSOR_PORT = "COM5"
DEFAULT_CNC_PORT = "COM4"
```

或者直接在调用 `run_multi_groups()` 时传入：

```python
run_multi_groups(
    ...,
    sensor_port="COM6",
    cnc_port="COM3",
)
```

---

### 7.2 修改速度列表

修改：

```python
DEFAULT_SPEED_LIST = [500, 1000, 1500, 2000, 2500]
```

例如改为：

```python
DEFAULT_SPEED_LIST = [300, 600, 900]
```

也可以运行时直接传：

```python
run_multi_groups(
    ...,
    speed_list=[300, 600, 900],
)
```

---

### 7.3 修改每条轨迹重复次数

修改：

```python
DEFAULT_REPEAT_PER_TRAJECTORY = 5
```

例如改成每条轨迹重复 3 次：

```python
DEFAULT_REPEAT_PER_TRAJECTORY = 3
```

也可以：

```python
run_multi_groups(
    ...,
    repeat_per_trajectory=3,
)
```

---

### 7.4 修改 Z 轴高度

这两个参数非常重要：

```python
DEFAULT_Z_DOWN = -17.0
DEFAULT_Z_SAFE = 0.0
```

含义：

- `z_down`：接触测量时的 Z 高度；
- `z_safe`：空移时的安全高度。

如果压得太深：
- 可能损伤样品或传感器；
- 力值会异常偏大。

如果压得太浅：
- 可能接触不稳定；
- 数据表现为接近 0。

建议先手动调试确认安全高度，再批量运行。

---

### 7.5 修改分类目录

修改：

```python
DEFAULT_MAIN_CATEGORY = "fabirc"
DEFAULT_SUB_CATEGORY = "b"
DEFAULT_GROUP_SUB_CATEGORIES = ["b47", "b48", "b49", "b50"]
```

例如：

```python
DEFAULT_MAIN_CATEGORY = "fabric"
DEFAULT_SUB_CATEGORY = "cotton"
DEFAULT_GROUP_SUB_CATEGORIES = ["sample01", "sample02", "sample03", "sample04"]
```

---

### 7.6 修改实时绘图开关

默认关闭：

```python
DEFAULT_ENABLE_PLOT = False
```

调试时可改成：

```python
DEFAULT_ENABLE_PLOT = True
```

注意：
- 批量长时间采集时不建议开启；
- 会增加图形线程和 UI 开销。

---

### 7.7 修改空移速度和 Z 轴速度

```python
DEFAULT_RAPID_FEED = 5000
DEFAULT_Z_FEED = 500
```

含义：

- `rapid_feed`：到起点、安全高度移动的速度；
- `z_feed`：Z 轴下压/抬起速度。

建议：
- `rapid_feed` 可相对较快；
- `z_feed` 不要过高，避免冲击传感器或样品。

---

## 8. 轨迹应该如何修改（重点）

这是你最关心、后续最常需要改的部分。

### 8.1 当前轨迹生成入口在哪里？

所有轨迹都在：

```python
_build_trajectories(start, end, radius=30.0)
```

里面定义。

也就是说：

- 想改轨迹形状，主要改这个函数；
- 想改轨迹数量，也主要改这个函数；
- 想改圆的半径，改这里的 `radius`；
- 想增加新的轨迹类型，除了改这里，还要改 `run_multi_groups()` 的执行分支。

---

### 8.2 当前 5 条轨迹的含义

假设区域：

```python
start = (sx, sy)
end   = (ex, ey)
```

则当前轨迹为：

#### 轨迹1：横竖正向
```text
(sx, sy) -> (ex, sy) -> (ex, ey)
```

#### 轨迹2：对角正向
```text
(sx, sy) -> (ex, ey)
```

#### 轨迹3：横竖反向
```text
(ex, ey) -> (ex, sy) -> (sx, sy)
```

#### 轨迹4：对角反向
```text
(ex, ey) -> (sx, sy)
```

#### 轨迹5：中心圆
- 圆心：
```python
cx = (sx + ex) / 2
cy = (sy + ey) / 2
```
- 半径默认为 `30.0`

---

### 8.3 修改圆轨迹半径

当前：

```python
def _build_trajectories(start, end, radius: float = 30.0):
```

如果想改成半径 20：

```python
def _build_trajectories(start, end, radius: float = 20.0):
```

或者在调用时显式传入：

```python
trajs = _build_trajectories(start, end, radius=20.0)
```

> 注意：圆半径必须与你的区域大小、材料尺寸、夹具空间相匹配，否则会超出区域或撞机。

---

### 8.4 修改某条折线路径

比如你想把轨迹 1 从：

```python
"waypoints": [(ex, sy), (ex, ey)]
```

改成先纵向后横向：

```python
"waypoints": [(sx, ey), (ex, ey)]
```

那么轨迹就变成：

```text
(sx, sy) -> (sx, ey) -> (ex, ey)
```

只要改这一段即可。

---

### 8.5 增加一个新的折线轨迹

例如增加“Z 字形”轨迹，可以在 `_build_trajectories()` 返回列表中新增：

```python
{
    "traj_id": 6,
    "path_type": "lines",
    "start": (sx, sy),
    "waypoints": [
        (ex, sy),
        (sx, ey),
        (ex, ey),
    ],
}
```

含义是：

```text
(sx, sy) -> (ex, sy) -> (sx, ey) -> (ex, ey)
```

因为它的 `path_type` 仍然是 `"lines"`，所以 **不需要修改执行器**，主程序会自动走 `_run_line_trajectory()`。

---

### 8.6 增加新的圆弧/曲线轨迹

如果你想新增的轨迹不是普通折线，而是新的曲线类型，比如：

- 椭圆；
- 螺旋；
- S 曲线；
- 多段圆弧；

那么光在 `_build_trajectories()` 里加定义还不够，还需要：

1. 给它一个新的 `path_type`，比如 `"ellipse"`；
2. 在 `run_multi_groups()` 里增加对应的分支；
3. 新写一个执行函数，例如 `_run_ellipse_trajectory(...)`；
4. 在执行函数里拼出对应 G-code。

例如伪代码：

```python
if traj["path_type"] == "lines":
    _run_line_trajectory(...)
elif traj["path_type"] == "circle":
    _run_circle_trajectory(...)
elif traj["path_type"] == "ellipse":
    _run_ellipse_trajectory(...)
```

---

### 8.7 修改轨迹编号规则

当前轨迹编号通过 `traj_id` 控制，例如：

```python
"traj_id": 1
```

它会直接决定输出目录名：

```text
traj1/
traj2/
traj3/
...
```

所以如果你新增或调整轨迹顺序，请保证：

- `traj_id` 唯一；
- 目录编号和你的实验记录表一致；
- 后续训练/分析脚本也按相同编号理解轨迹。

---

### 8.8 修改采集区域而不是修改轨迹

很多时候你不是想改轨迹形状，而是想让同一组轨迹在不同位置执行。这时应该改：

```python
DEFAULT_COORD_GROUPS
```

例如改成两组区域：

```python
DEFAULT_COORD_GROUPS = [
    ((0.0, 0.0), (-50.0, -50.0)),
    ((-60.0, 0.0), (-110.0, -50.0)),
]
```

这样轨迹 1~5 的生成逻辑不变，只是应用在新的区域范围上。

---

### 8.9 如果想让每个区域使用不同轨迹，怎么做？

当前代码默认：
- 每个区域都使用同一套 `_build_trajectories()` 生成规则。

如果你要实现“不同区域不同轨迹”，推荐两种方法：

#### 方法 A：在 `_build_trajectories()` 中根据区域条件判断

例如：

```python
def _build_trajectories(start, end, radius=30.0):
    if start == (-10.0, 0.0):
        return [...第一套轨迹...]
    else:
        return [...第二套轨迹...]
```

#### 方法 B：把轨迹生成函数参数化

例如新增参数 `pattern_type`：

```python
def _build_trajectories(start, end, radius=30.0, pattern_type="default"):
    if pattern_type == "default":
        ...
    elif pattern_type == "zigzag":
        ...
```

然后在任务构造阶段为不同区域传不同模式。

> 从长期维护角度，**方法 B 更清晰**。

---

### 8.10 轨迹修改后的调试建议

每次改轨迹后，强烈建议按以下顺序测试：

1. 先把 `z_down` 提高（例如接近 `z_safe`），做空跑测试；
2. 先只保留一个区域、一条轨迹、一次重复；
3. 开启 `enable_plot=False`，减少干扰；
4. 先观察 CNC 是否按预期走位；
5. 确认没有越界、撞夹具、超行程后，再恢复正常下压高度。

---

## 9. 典型使用方式

### 9.1 直接运行默认采集

```bash
python data_collection.py
```

这会按照默认参数执行完整批量采集。

---

### 9.2 在代码中自定义调用

```python
from data_collection import run_multi_groups

run_multi_groups(
    coord_groups=[
        ((-10.0, 0.0), (-70.0, -60.0)),
        ((-110.0, 0.0), (-170.0, -60.0)),
    ],
    speed_list=[500, 1000],
    repeat_per_trajectory=2,
    main_category="fabric",
    sub_category="b",
    group_sub_categories=["sample1", "sample2"],
    resume_enabled=True,
    z_down=-17.0,
    z_safe=0.0,
    sensor_port="COM5",
    sensor_baud=115200,
    cnc_port="COM4",
    cnc_baud=115200,
    enable_plot=False,
    rapid_feed=5000,
    z_feed=500,
)
```

---

## 10. 常见问题与注意事项

### 10.1 `ALARM:11 Homing Required`
表示设备要求先回零。

解决方式：
- 先执行 `$H`；
- 若有必要，再执行 `$X` 解锁；
- 确保当前位置安全。

### 10.2 传感器没有数据
排查：

1. 串口号是否正确；
2. 波特率是否正确；
3. 传感器配置命令是否正确；
4. `parse_sensor_data()` 是否与设备输出格式一致。

### 10.3 图形窗口卡住或关闭异常
如果启用了绘图：
- 确认本机支持 `TkAgg`；
- 批量采集时建议关闭绘图；
- 必要时只在单次调试时开启。

### 10.4 `mat.py` 读不出数据
原因通常是：
- 当前输出格式与脚本假设格式不一致；
- 文件头有 `# metadata`；
- 没有时间戳字段。

需要改 `mat.py` 的解析逻辑。

### 10.5 断点续采行为要理解清楚
当前逻辑下：

- 如果 `resume_enabled=True`，程序会从第一个缺失任务开始；
- 对于目标任务，如果文件已存在，会尝试删除旧文件并重采；
- 如果 `resume_enabled=False`，已存在文件会直接跳过。

这点很关键，避免误删或误覆盖已有数据。

---

## 11. 建议的后续优化方向

如果后续你还要继续扩展项目，建议优先考虑这些方向：

1. **统一模块命名**：去掉 `(1)` 后缀，固定成标准模块名；
2. **统一数据格式**：让采集脚本和分析脚本使用同一种输出结构；
3. **增加轨迹配置文件**：把轨迹从代码里抽离到 JSON/YAML，更方便切换实验方案；
4. **增加日志系统**：把关键运行状态保存到日志文件；
5. **增加异常恢复机制**：如串口掉线自动重连；
6. **增加可视化校验模式**：在正式运行前绘制轨迹示意图，确认不会越界。

---

## 12. 一句话总结

如果你只记住这个项目的三个核心点，可以记成：

- `cnc_module.py`：负责“怎么动”；
- `sensor_module.py`：负责“怎么采”；
- `data_collection.py`：负责“按什么轨迹、什么速度、采多少次”。

而你后续最常修改的内容，通常集中在：

1. `DEFAULT_COORD_GROUPS`（改采集区域）
2. `_build_trajectories()`（改轨迹形状）
3. `DEFAULT_SPEED_LIST` / `repeat_per_trajectory`（改实验参数）
4. `z_down` / `z_safe`（改接触方式）

