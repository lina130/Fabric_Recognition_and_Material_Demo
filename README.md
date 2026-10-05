# Fabric Recognition & Material Demo

This repository collects **two independent projects** that were previously scattered in
separate, deeply-nested folders. They are deliberately kept apart - each one lives in its
own top-level directory with its own README, so neither can be confused with the other.

| Directory | Project | What it is |
| --- | --- | --- |
| [`Fabric_Recognition/`](Fabric_Recognition/) | Algorithm of Tactile Digitalization | Tactile-sensor + high-speed-microphone pipeline for **material/fabric recognition** (SVM and CNN variants) plus the CNC calibration tooling. |
| [`material_Demo/`](material_Demo/) | CNC material demo | Desktop PyInstaller demos (`main_demo.py`, `cnc_demo.py`) that drive the same sensor/CNC modules for a live material demo. |

Both projects talk to the same kind of tactile sensor + microphone front-end, which is why
they are shipped together, but they are **not** a single program: build and run them
separately.

## Repository layout

```
.
├── Fabric_Recognition/          # recognition / training project
│   ├── recognition_app/         # PyTk GUIs (CNN + SVM) + trained models
│   ├── data_collection/         # CNC trajectory data-collection tool
│   ├── calibration_tools/       # AgileReach-Tools-S1 calibration / analysis
│   └── dataset/                 # (documented) raw recordings - not tracked
└── material_Demo/               # standalone PyInstaller demo applications
```

## Notes on the cleanup

The original `Fabric_Recognition` tree contained a redundant double nesting
(`Algorithm-of-Tactile-Digitalization/cnn/Algorithm-of-Tactile-Digitalization/...`) and two
copies of the shared recognition / data-collection code. In this repository:

- the nesting was flattened;
- the two recognition GUIs (SVM `test_compare.py` and CNN `test_compare.pyw`) were merged
  into one `recognition_app/` folder that shares one `config.py`, `module/`, `photos/` and
  `cnn_models/`;
- the CNC data-collection code was merged into a single `data_collection/` folder (the
  superseded copy is kept as `data_collection_legacy.py`);
- a broken hard-coded absolute path in the SVM app was replaced with a relative one;
- build outputs, caches and the ~1 GB of raw microphone recordings are excluded from git
  (see [`Fabric_Recognition/dataset/README.md`](Fabric_Recognition/dataset/README.md)).

No source file was dropped; only duplicate copies were collapsed.

## License

Released under the MIT License - see [`LICENSE`](LICENSE).