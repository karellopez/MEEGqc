# Writing a MEEGqc plugin

A plugin is a separate Python package that adds to MEEGqc without being part of
it. MEEGqc finds installed plugins by themselves; users install and remove them
with `meegqc-plugins` or the GUI's **Plugins** dialog. MEEGqc's own install
stays small, and a plugin brings its own dependencies.

This folder holds the plugins maintained with MEEGqc:

| Folder | Package | What it adds |
|---|---|---|
| `template/` | (not published) | The smallest working plugin, to start a new one. |

MEGnet (ECG/EOG references for MEG recordings) is a plugin too, the complete
example to read: it is part of MEEGqc itself, in `meg_qc/plugins/megnet/`, and
only the libraries it needs are installed separately (the `megnet` extra). A
plugin of your own is a separate package, like `template/`.

## What a plugin can do (plugin API 1)

- **Own a settings section.** One `[SECTION]` in `settings.ini`, with its
  defaults and comments. MEEGqc shows it in the settings editor (the comments
  become tooltips), writes it with `get-meegqc-config`, saves it with each run's
  results, and passes the user's values to the plugin.
- **Supply ECG and EOG reference signals.** For recordings without a usable ECG
  or EOG channel, or to compare with one. MEEGqc runs its usual analysis on the
  signal, writes the results as the plugin's own derivatives, and the reports
  show them under the plugin's name.

Anything else needs a new API version: open an issue first.

## Layout

```
meegqc-yourplugin/
  pyproject.toml
  README.md
  src/meegqc_yourplugin/
    __init__.py        plugin(): the entry point
    settings.ini       the default [YOURPLUGIN] section
  tests/
```

`pyproject.toml` registers the entry point and depends on MEEGqc:

```toml
[project]
name = "meegqc-yourplugin"
dependencies = ["meg_qc>=X.Y", "your-heavy-library"]

[project.entry-points."meegqc.plugins"]
yourplugin = "meegqc_yourplugin:plugin"

[tool.setuptools.package-data]
meegqc_yourplugin = ["settings.ini"]
```

Use as `meg_qc>=` the first MEEGqc release with the plugin API.

## The entry point

`plugin()` takes no arguments and returns a `meg_qc.plugins.Plugin`:

```python
from importlib import resources
from meg_qc.plugins import PLUGIN_API_VERSION, Plugin

def plugin() -> Plugin:
    return Plugin(
        name="yourplugin",              # lowercase letters and digits
        label="YourPlugin",             # shown to people
        version="0.1.0",
        summary="What it adds, and for which recordings.",
        api_version=PLUGIN_API_VERSION,
        settings_section="YOURPLUGIN",
        default_settings=resources.files("meegqc_yourplugin")
            .joinpath("settings.ini").read_text(encoding="utf-8"),
        parse_settings=parse_settings,  # section (strings) -> params dict
        reference_provider=YourProvider(),
    )
```

Keep the module that defines `plugin()` light. MEEGqc imports it at startup
and every time it lists plugins, so import heavy libraries (torch, keras, ...)
only inside `compute`.

## Rules

1. **The name is permanent.** It names the derivatives
   (`desc-ECGchannel<name>`, `desc-ECGs<name>`) and the settings source, so
   changing it orphans every output already written.
2. **Never break MEEGqc.** A plugin that raises, has a wrong API version or a
   bad name is listed as "not loaded" with the reason, and MEEGqc carries on.
   Do not rely on that: catch what you can and return a result that says why.
3. **Say why, in a sentence a user can act on.** `applies_to` returns `None` or
   a reason such as `"it needs at least 105 s of data; this recording is 60.0 s"`.
   MEEGqc prints it in the recording's log and in the ECG/EOG report text.
4. **Check before you run.** `applies_to` is called first and must be cheap.
   Refuse recordings your method was not built for, rather than returning a
   plausible result for them. MEGnet's `applies.py` refuses systems it was never
   trained on, too-short recordings, and missing sensor positions.
5. **Do not modify the recording.** `raw` is shared with the other metrics.
   Copy it if you need to filter it. Intermediate files go in `work_dir`.
6. **One value per sample.** A reference signal is a 1-D array at the
   recording's sampling rate. MEEGqc stretches a signal of another length to
   fit, but say so in your docs if you resample.
7. **Settings use the format of `settings.ini`.** Write one comment line before
   each key: `#key (type) - What it does. Unit: Hz. Default: 40`. The settings
   editor shows that line as the key's tooltip.
8. **Heavy dependencies belong to the plugin**, never to MEEGqc. Name their
   size in the summary (MEGnet: "Installs PyTorch and Keras").
9. **No global side effects at import.** Set environment variables or patch
   libraries inside `compute`, once (see `meg_qc/plugins/megnet/run.py`).

## Reference signals

A reference provider has two methods:

```python
from meg_qc.plugins import ReferenceResult, ReferenceSignal

class YourProvider:
    def applies_to(self, raw, params) -> str | None:
        ...                              # None, or why not

    def compute(self, raw, params, *, data_path, work_dir) -> ReferenceResult:
        ecg = ReferenceSignal(signal, component_ids=[...], component_probs=[...])
        return ReferenceResult("ok", ecg=ecg, eog=None)
```

Its settings section must contain these standard keys. MEEGqc reads them
itself:

| Key | Values | Meaning |
|---|---|---|
| `ecg`, `eog` | `off`, `fallback`, `independent`, `both` | `fallback`: replaces a recorded or reconstructed channel that failed its checks. `independent`: analysed and reported beside it. |
| `ecg_lowpass_h_freq`, `eog_lowpass_h_freq` | Hz, `0` = none | Low-pass applied before peaks are detected. |

What MEEGqc writes for an `independent` reference, and for one that replaced
the channel:

- `desc-ECGchannel<name>` / `desc-EOGchannel<name>`: the reference, with
  `signal_source = <name>`, `signal_source_label = <label>` and your component
  ids and probabilities;
- `desc-ECGs<name>` / `desc-EOGs<name>`: every channel ranked against it.

The subject report gives the plugin its own tab, and the dataset reports count
how many recordings used it.

## Testing

- Unit tests in `tests/` that need neither MEEGqc's test data nor your heavy
  dependencies: what `applies_to` accepts and refuses, settings parsing, the
  `Plugin` object (MEGnet's: `tests/test_megnet_plugin_code.py`).
- Real-data tests belong in MEEGqc's suite and skip when the plugin is not
  installed (`tests/realdata/test_megnet_plugin.py`).
- MEEGqc's own `tests/realdata/test_plugin_reference.py` shows a fake provider
  going through the whole calculation and report.

## Installing during development

```bash
meegqc-plugins install ./plugins/meegqc-yourplugin
meegqc-plugins list
meegqc-plugins remove yourplugin
```

## Publishing

Publish the package on PyPI. To make `meegqc-plugins install yourplugin` and the
Plugins dialog offer it by name, add it to `meg_qc/plugins/catalog.py` in a pull
request.

## API versions

`PLUGIN_API_VERSION` changes only when a change would break existing plugins.
New optional fields on `Plugin` do not change it. A plugin written for another
version is listed as "not loaded", with both version numbers.
