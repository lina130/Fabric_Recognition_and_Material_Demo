# Recognition app

Two desktop GUIs share one folder, one ``config.py``, one ``module/``, one ``photos/``
reference set and one ``cnn_models/`` directory:

| File | Classifier | Notes |
| --- | --- | --- |
| ``test_compare.pyw`` | **CNN** (PyTorch) | Main GUI; trains/fine-tunes and runs the CNN classifier. Launches ``data_collection.py`` in the same folder. |
| ``test_compare_svm.py`` | **SVM** (scikit-learn) | Reads ``raw_mic/``, extracts features, runs ``GridSearchCV`` and stores ``svm_models/svm_pipeline.pkl``. |
| ``data_collection.py`` | - | CNC/sensor capture helper spawned by the CNN GUI. |
| ``config.py`` | - | Shared plotting / audio configuration and global state. |
| ``sound_device.py`` | - | Audio-device helpers. |
| ``module/`` | - | ``cnc_module`` (G-code) and ``sensor_module`` (tactile sensor) drivers. |
| ``photos/`` | - | Reference photos per material (``N.jpg``); SVM-only variants kept as ``N_svm.jpg``. |
| ``cnn_models/`` | - | Trained CNN checkpoints and classifier weights (``*.pth``). |
| ``svm_models/`` | - | Trained SVM pipeline and cached training set (``*.pkl``). |

## Run

```bash
pip install -r ../requirements.txt
cd recognition_app
python test_compare.pyw       # CNN GUI   (or run_all.bat)
python test_compare_svm.py    # SVM GUI   (or run_all_svm.bat)
```

Both GUIs expect the tactile sensor to be streaming on UDP ``0.0.0.0:8888`` and, for
data collection, a CNC connected over serial.

> The SVM GUI needs the raw recordings in ``recognition_app/raw_mic/`` (not tracked, see
> ``../dataset/README.md``). The CNN GUI uses the tracked weights in ``cnn_models/``.