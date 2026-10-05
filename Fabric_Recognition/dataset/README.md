# Dataset

The raw microphone recordings used to train the classifiers live here.

They are **intentionally not tracked in git** because the local copy is about **1 GB**
(836 WAV files, each ~1.3 MB, sampled at 192 kHz). Keeping them out of git keeps the
repository small and cloneable; the code, the feature extractors and the trained models
are still fully tracked.

## Expected layout

```
Fabric_Recognition/
└── recognition_app/
    └── raw_mic/           # <-- place the recordings here to retrain the SVM
        mat1_<timestamp>_arc1.wav
        mat1_<timestamp>_arc2.wav
        ...
```

The SVM application (`recognition_app/test_compare_svm.py`) creates and reads
`raw_mic/` relative to its own working directory, so copying the recordings into
`recognition_app/raw_mic/` is enough to re-extract features and retrain.

The filenames encode the class and the two arc passes:

```
mat{1..10}_{timestamp}_arc{1,2}.wav
        ^ class id        ^ first / second arc pass
```