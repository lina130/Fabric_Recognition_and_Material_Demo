import matplotlib.pyplot as plt
import datetime

#data_file = ''C:\\Users\\liyib\\\\fabirc\\b31\\v500\\traj1\\r1.txt'
data_file = 'C:\\Users\\liyib\\Desktop\\平台控制代码\\fabirc\\b47\\v500\\traj1\\r1.txt'

# 用于存储解析后的数据
timestamps = []
forces_x = []
forces_y = []
forces_z = []

try:
    with open(data_file, 'r') as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) == 2:
                timestamp_str = parts[0]
                force_str = parts[1].strip().split(' ')

                try:
                    # 解析时间戳
                    dt_object = datetime.datetime.fromisoformat(timestamp_str)
                    timestamps.append(dt_object)

                    # 解析力数据
                    forces_x.append(float(force_str[0]))
                    forces_y.append(float(force_str[1]))
                    forces_z.append(float(force_str[2]))
                except ValueError as e:
                    print(f"跳过无效行 (时间戳或力数据): {line.strip()} - 错误: {e}")
            else:
                print(f"跳过格式不正确的行: {line.strip()}")

except FileNotFoundError:
    print(f"错误: 文件 '{data_file}' 未找到。请确保文件存在。")
    exit()

if not timestamps:
    print("没有有效数据可绘制。请检查您的数据文件。")
    exit()

# 创建图和3D子图
fig = plt.figure(figsize=(10, 8))
ax = fig.add_subplot(111, projection='3d')

# 绘制三维折线图
ax.plot(forces_x, forces_y, forces_z, marker='o', linestyle='-', color='b')

# 设置轴标签
ax.set_xlabel('Force X')
ax.set_ylabel('Force Y')
ax.set_zlabel('Force Z')
ax.set_title('3D Force Data Over Time')

# 为了在图例中显示时间，我们可以创建一个简单的颜色映射或在工具提示中显示
# 这里我们仅仅绘制折线，时间信息可以通过x轴的索引间接表示，或者在其他图中单独显示。

# 显示图表
plt.tight_layout()
plt.show()

# 如果你还需要一个2D图来显示每个分量随时间的变化，可以添加：
plt.figure(figsize=(12, 6))
plt.plot(timestamps, forces_x, label='Force X', marker='.', linestyle='-')
plt.plot(timestamps, forces_y, label='Force Y', marker='.', linestyle='-')
plt.plot(timestamps, forces_z, label='Force Z', marker='.', linestyle='-')
plt.xlabel('Time')
plt.ylabel('Force Value')
plt.title('Force Components Over Time')
plt.legend()
plt.grid(True)
plt.tight_layout()
plt.show()