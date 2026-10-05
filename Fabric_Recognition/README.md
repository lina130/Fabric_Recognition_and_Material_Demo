# Algorithm of Tactile Digitalization (材质识别)

A tactile-sensor + high-speed-microphone pipeline that recognises the **material** placed
on a sensor by analysing the acoustic fingerprint produced while a CNC tool drags across
it. Two classifiers are provided: a classic **SVM** pipeline and a **CNN** (PyTorch)
pipeline.

## Layout

| Folder | Contents |
| --- | --- |
| [`recognition_app/`](recognition_app/) | The two desktop recognition GUIs and their trained models. |
| [`data_collection/`](data_collection/) | CNC-trajectory driven automatic data collection. |
| [`calibration_tools/AgileReach-Tools-S1/`](calibration_tools/AgileReach-Tools-S1/) | Sensor calibration and raw-signal analysis tooling. |
| [`dataset/`](dataset/) | Where the raw microphone recordings live (tracked out of git, see its README). |

## Hardware

- An Infineon-style tactile/acoustic sensor front-end (UDP data on port `8888`).
- A high-speed microphone sampled at 192 kHz.
- A CNC platform driven over a serial G-code link.

## Quick start

```bash
pip install -r requirements.txt
cd recognition_app
python test_compare.pyw     # CNN recognition GUI
python test_compare_svm.py  # SVM recognition GUI
```

See each sub-folder README for details.