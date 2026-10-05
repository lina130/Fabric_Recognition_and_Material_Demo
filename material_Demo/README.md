# Material Demo

Standalone desktop demonstration apps for the tactile / acoustic material-recognition
hardware. This folder is **independent** of `../Fabric_Recognition/` - it shares the same
kind of sensor but is its own program.

## Apps

| File | UI | Purpose |
| --- | --- | --- |
| `main_demo.py` | PyQt5 | Main live demo window: acquires audio, runs the CNN material classifier and shows the recognised material. |
| `cnc_demo.py` | Tkinter | Drives the CNC platform along a fixed trajectory for the demo. |

## Supporting files

- `module/` - `cnc_module` and `sensor_module` drivers (shared design with the
  recognition project).
- `cnn_models/` - trained CNN classifier weights (`*.pth`).
- `photos/` - reference photos per material (`1.jpg` .. `10.jpg`).
- `*.spec` - PyInstaller specs used to build the standalone executables.

## Run

```bash
pip install PyQt5 pyserial sounddevice numpy torch torchaudio scikit-learn scipy
python main_demo.py
python cnc_demo.py
```

## Build standalone executables

```bash
pyinstaller main_demo.spec
pyinstaller cnc_demo.spec
```

`build/` and `dist/` are not tracked.