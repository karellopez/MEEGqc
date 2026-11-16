# MEGnet for MEEGqc

A MEEGqc plugin. It adds MEGnet's heartbeat and eye-movement components to
MEEGqc's ECG and EOG metrics, as reference signals.

[MEGnet](https://github.com/nih-megcore/MegNet) (Treacher et al., NeuroImage
2021) is a neural network that labels the independent components (ICA) of a MEG
recording as heartbeat, blink, eye movement or other. This gives MEEGqc an ECG
and an EOG reference for recordings without a usable ECG or EOG channel. It can
also be analysed beside a recorded channel, to compare the two.

## Install

From the MEEGqc GUI: **Plugins**, then **Install** next to MEGnet. Or:

```bash
meegqc-plugins install megnet
```

This installs `meegqc-megnet` and `megnet-neuro`, which bring PyTorch and Keras
(about 1 GB). On Linux, pip installs the CUDA build of PyTorch (several GB)
unless the CPU build is installed first:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

The model weights are downloaded from HuggingFace the first time MEGnet runs.

## Which recordings

MEGnet runs only where it was trained, and says why it skips any other
recording:

- MEG from MEGIN (Vectorview, TRIUX), CTF, 4D/BTi or KIT systems. Not OPM, not
  EEG.
- At least 20 good magnetometer channels (or CTF/KIT axial gradiometers) with
  sensor positions.
- At least 105 s long: MEGnet classifies two 60 s windows that overlap by 15 s.
- Sampled at 250 Hz or more.

## Settings

The `[MEGNET]` section of MEEGqc's settings (**Global settings...** in the GUI):

| Key | Default | Meaning |
|---|---|---|
| `ecg`, `eog` | `independent` | `off`; `fallback` (replaces a recorded or reconstructed channel that failed its checks); `independent` (analysed beside it); `both` |
| `ecg_lowpass_h_freq`, `eog_lowpass_h_freq` | `40`, `20` | Low-pass applied to MEGnet's component before peaks are detected, in Hz; `0` = none |
| `probability_threshold` | `0.9` | With `combine_components`: components of a class at least this probable are averaged |
| `combine_components` | `False` | Average several components of a class, weighted by probability |
| `max_components_per_class` | `1` | At most this many, when combining |
| `megnet_ecg_class`, `megnet_eog_primary_class`, `megnet_eog_secondary_class` | `2`, `1`, `3` | MEGnet's class numbers |

`MEGQC_MEGNET_GPU=1` runs MEGnet on the GPU. By default it runs on the CPU,
because many recordings in parallel each reserving GPU memory ran out of it.

## Outputs

MEEGqc writes MEGnet's references as `desc-ECGchannelmegnet` and
`desc-EOGchannelmegnet`, and every channel ranked against them as
`desc-ECGsmegnet` and `desc-EOGsmegnet`. The subject report shows them in a
MEGnet tab, with the components used.

## Tests

```bash
pytest plugins/meegqc-megnet/tests     # needs neither MEGnet nor test data
```

Real-data tests are in MEEGqc's suite (`tests/realdata/test_megnet_plugin.py`)
and run when this plugin is installed.
