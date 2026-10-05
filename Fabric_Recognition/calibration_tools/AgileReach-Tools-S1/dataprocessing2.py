import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.widgets import SpanSelector
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import tkinter as tk
from tkinter import filedialog, messagebox
import numpy as np

# Define global variables
selected_data = []
canvas = None
fig = None
span = None
toolbar = None

def save_data():
    if selected_data:
        save_file_path = filedialog.asksaveasfilename(defaultextension=".xlsx", 
                                                      filetypes=[("Excel files", "*.xlsx")])
        if save_file_path:
            selected_df = pd.DataFrame({
                'Number': selected_data[0]['Number'],
                'Channel 1': selected_data[0]['Channel 1'],
                'Channel 2': selected_data[0]['Channel 2'],
                'Channel 3': selected_data[0]['Channel 3']
            })
            selected_df.to_excel(save_file_path, index=False)
            status_label1.config(text=f"Data saved to {save_file_path}")
        else:
            status_label1.config(text="Save operation cancelled")
    else:
        messagebox.showwarning("No data selected", "Please select a range of data first")

def onselect(xmin, xmax):
    global Friction
    global Flatness
    selected_indices = np.arange(int(xmin), int(xmax) + 1)
    selected_Channel1 = y1[selected_indices]
    selected_Channel2 = y2[selected_indices]
    selected_Channel3 = y3[selected_indices]
    
    Average_Channel_1 = np.mean(selected_Channel1**2)
    Average_Channel_2 = np.mean(selected_Channel2**2)
    Friction = np.sqrt(Average_Channel_1 + Average_Channel_2)
    Flatness = np.std(selected_Channel3)
    
    global selected_data
    selected_data = [{
        'Number': selected_indices,
        'Channel 1': selected_Channel1,
        'Channel 2': selected_Channel2,
        'Channel 3': selected_Channel3
    }]
    
    status_label1.config(text=f"Selected data from index {int(xmin)} to {int(xmax)}")
    status_label2.config(text=f"Friction coefficient = {Friction:.1f}")
    status_label3.config(text=f"Flatness coefficient = {Flatness:.1f}")
    
    # Force the canvas to redraw to show the selection area
    canvas.draw_idle()

def open_file():
    global df, y1, y2, y3, fig, canvas, span, toolbar

    # Clear previous figures and toolbars
    if canvas is not None:
        canvas.get_tk_widget().pack_forget()
    if toolbar is not None:
        toolbar.pack_forget()

    file_path = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx")])
    
    if file_path:
        df = pd.read_excel(file_path)
        y1 = df.iloc[:, 1].values
        y2 = df.iloc[:, 2].values
        y3 = df.iloc[:, 3].values
        
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(y1, label='Channel 1')
        ax.plot(y2, label='Channel 2')
        ax.plot(y3, label='Channel 3')
        ax.legend()
        
        # Embed the plot into Tkinter
        canvas = FigureCanvasTkAgg(fig, master=root)
        canvas.draw()
        canvas.get_tk_widget().pack(pady=10)
        
        toolbar = NavigationToolbar2Tk(canvas, root)
        toolbar.update()
        toolbar.pack()
        
        # Create the SpanSelector
        span = SpanSelector(ax, onselect, 'horizontal', useblit=False,
                            props=dict(alpha=0.5, facecolor='red'))
        
        status_label1.config(text=f"File {file_path} opened successfully")
    else:
        status_label1.config(text="File open operation cancelled")

# Create Tkinter GUI
root = tk.Tk()
root.title("Data Selector and Saver")

# Open file button
open_file_button = tk.Button(root, text="Open File", command=open_file)
open_file_button.pack(pady=10)

# Save button
save_button = tk.Button(root, text="Save Selected Data", command=save_data)
save_button.pack(pady=10)

# Status labels
status_label1 = tk.Label(root, text="No file selected yet")
status_label1.pack(pady=10)

status_label2 = tk.Label(root, text="Friction coefficient = xxx")
status_label2.pack(pady=10)

status_label3 = tk.Label(root, text="Flatness coefficient = xxx")
status_label3.pack(pady=10)

# Run the Tkinter main loop
root.mainloop()
