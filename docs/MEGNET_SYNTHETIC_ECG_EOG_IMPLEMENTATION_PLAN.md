# MEGnet Synthetic ECG/EOG Integration Plan

## Goal

Integrate MEGnet-synthesized ECG/EOG references into the MEGqc ECG/EOG pipeline while preserving compatibility with existing outputs, enforcing consistent signal processing across all source types, and clearly marking synthetic provenance in derivative files and reports.

## 1) Confirmed behavior rules

### ECG source priority

- If a recorded ECG channel exists:
  1. recorded ECG first
  2. MNE reconstructed ECG second
  3. MEGnet synthetic ECG third
- If no recorded ECG channel exists:
  1. MEGnet synthetic ECG first
  2. MNE reconstructed ECG second

### EOG behavior

- For this phase, MEGnet synthetic EOG runs even if recorded EOG channels exist and pass checks.
- Synthetic EOG combines MEGnet class `1` (vEOG/blink) and class `3` (hEOG/saccade).

### Component selection strategy

- Use top-1 highest probability component(s) for now:
  - ECG: class `2` top-1
  - EOG: class `1` top-1 + class `3` top-1

### Data model and derivatives

- Do not insert synthetic channels into `Raw`.
- Keep synthetic signals as separate internal references.
- Include explicit source/provenance metadata in derivatives.

### Reporting requirement

- Reports must explicitly state `MEGnet-synthesized` whenever selected or evaluated.
- Reports must include component IDs/probabilities and method details.

## 2) Dependency policy (TensorFlow/MEGnet)

### Optional dependency mode (current decision)

- MEGqc attempts MEGnet import at runtime.
- If unavailable, pipeline does not hard-crash; it continues with remaining candidates.
- Report and metrics include explicit warning and availability state.

### Hard-fail mode (not used in phase-1)

- Missing TensorFlow/MEGnet would fail ECG/EOG metric or full run.

### Decision for this implementation

- Keep MEGnet optional for now.
- Add runtime messaging and report fields for availability state.

## 2.5) Current as-is recorded channel processing (baseline map)

This section documents how original recorded channels are processed today, before MEGnet integration, so insertion points and behavior changes are explicit.

### ECG recorded channel (as-is)

Primary flow in `MEGqc/meg_qc/calculation/metrics/ECG_EOG_meg_qc.py`:

1. `ECG_meg_qc(...)` starts ECG metric processing.
2. `get_ECG_data_choose_method(...)` attempts to use recorded ECG channel data when available.
3. ECG reference signal is preprocessed in the existing ECG path (filtering/conditioning used by the detector branch).
4. Event/peak detection is run on that processed reference signal.
5. Quality evaluation is applied (`check_3_conditions`, `check_mean_wave`).
6. If needed, reconstruction path is evaluated via `reconstruct_ecg_and_check(...)`.
7. Output fields are packaged for downstream metrics/reporting via `make_simple_metric_ECG_EOG(...)`.

### EOG recorded channel (as-is)

Primary flow in `MEGqc/meg_qc/calculation/metrics/ECG_EOG_meg_qc.py`:

1. `EOG_meg_qc(...)` starts EOG metric processing.
2. `get_EOG_data(...)` loads recorded EOG channel candidate(s) when available.
3. EOG signal is preprocessed in the existing EOG path (filtering/conditioning used by the detector branch).
4. Event/peak detection is run on the processed EOG reference signal.
5. Quality evaluation is applied (`check_3_conditions`, `check_mean_wave`).
6. Output fields are packaged for downstream metrics/reporting via `make_simple_metric_ECG_EOG(...)`.

### Current insertion boundary for MEGnet

MEGnet candidate generation should be inserted at the candidate-building stage, before final source selection and before downstream artifact/correlation computations:

- ECG insertion: inside/adjacent to `get_ECG_data_choose_method(...)` candidate creation, alongside recorded and MNE reconstructed candidates.
- EOG insertion: inside/adjacent to `get_EOG_data(...)` candidate creation, alongside recorded candidates.

### Processing requirement for synthetic parity

To ensure effective estimation and fair comparison, MEGnet synthetic candidates must pass through the same evaluator stages as recorded channels:

1. common preprocessing
2. common event detection
3. common quality checks (`check_3_conditions`, `check_mean_wave`)
4. normalized result packaging with provenance metadata

This parity is implemented by the unified reference signal evaluator in Section 3.

## 3) Core architecture update: unified reference signal evaluator

Create a shared evaluator used by both ECG and EOG that runs each candidate source through the same processing chain.

### Processing chain (applies to each candidate)

1. Input source signal + provenance (`recorded`, `mne_reconstructed`, `megnet_synthesized`)
2. Apply common preprocessing
3. Detect events with one method
4. Run quality checks:
   - `check_3_conditions`
   - `check_mean_wave`
5. Return normalized evaluation payload

### Why this change

- Removes source-dependent bias caused by mixed detection paths.
- Ensures recorded/reconstructed/synthetic references are compared under identical conditions.
- Enables stable candidate ranking/selection logic while preserving auditability.

## 4) Result object model (single selected + all candidates)

Each ECG/EOG branch receives one container object with:

- one `selected` evaluation (used for downstream analysis)
- full `candidates` evaluations (stored for traceability/reporting)

### Proposed shape

```python
reference_eval_result = {
    "selected_source": "recorded|mne_reconstructed|megnet_synthesized|none",
    "selected": {
        "signal": np.ndarray,
        "event_indexes": np.ndarray,
        "n_events": int,
        "events_rate_per_min": float,
        "quality": {
            "three_conditions": {
                "similar_ampl": bool,
                "no_breaks": bool,
                "no_bursts": bool,
                "overall_good": bool,
            },
            "mean_wave_shape_ok": bool,
            "overall_pass": bool,
        },
        "method": {
            "preprocessing": dict,
            "event_detector": str,
        },
        "provenance": {
            "signal_source": str,
            "is_synthetic": bool,
            "component_ids": list,
            "component_probs": list,
            "details": str,
        },
    },
    "candidates": {
        "recorded": dict | None,
        "mne_reconstructed": dict | None,
        "megnet_synthesized": dict | None,
    },
    "selection_reason": str,
}
```

## 5) Files and functions to update

### Core ECG/EOG logic

- `MEGqc/meg_qc/calculation/metrics/ECG_EOG_meg_qc.py`
  - add shared evaluator helpers (preprocess + detect + quality + packaging)
  - update `get_ECG_data_choose_method`
  - update `reconstruct_ecg_and_check` (candidate builder role)
  - update `get_EOG_data`
  - update `ECG_meg_qc`
  - update `EOG_meg_qc`
  - update `make_simple_metric_ECG_EOG`

### New MEGnet adapter

- `MEGqc/meg_qc/calculation/metrics/megnet_synthetic_ref.py`
  - guarded import of MEGnet/TensorFlow
  - extract top-1 class components
  - build synthetic signals and initial metadata
  - no direct mutation of `Raw`

### Config parsing

- `MEGqc/meg_qc/calculation/initial_meg_qc.py`
  - `get_all_config_params` for new ECG/EOG MEGnet keys

### Config defaults and docs

- `MEGqc/meg_qc/settings/settings.ini`
- `MEGqc/docs/source/settings_ini.rst`

### Plot/report rendering

- `MEGqc/meg_qc/plotting/universal_plots.py`
- `MEGqc/meg_qc/plotting/meg_qc_plots.py`
- `MEGqc/meg_qc/plotting/universal_html_report.py` (if needed)

### Summary compatibility

- `MEGqc/meg_qc/calculation/metrics/summary_report_GQI.py`
- `MEGqc/meg_qc/calculation/meg_qc_pipeline.py` (flattening, schema extension)

## 6) Source and metadata schema updates

### ECG/EOG derivatives

Extend `ECGchannel` and `EOGchannel` outputs with fields such as:

- `signal_source` (`recorded`, `mne_reconstructed`, `megnet_synthesized`)
- `is_synthetic` (`True`/`False`)
- `synthetic_component_ids`
- `synthetic_component_probs`
- `synthetic_details`
- `candidate_sources_evaluated`
- `selected_source`
- `selection_reason`

Keep `recorded_or_reconstructed` for backward compatibility; allow `megnet_synthetic` value.

### Simple metrics and report text

- include explicit fallback chain and selected source
- include candidate summary and MEGnet availability warnings
- include component IDs/probabilities when synthetic is involved

## 7) Config additions (phase-1 defaults)

Under `[ECG]` and `[EOG]`:

- `use_megnet_fallback = True`
- `megnet_optional_dependency = True`
- `megnet_component_strategy = top1`
- `megnet_ecg_class = 2`
- `megnet_eog_classes = 1,3`
- `megnet_eog_force_use = True`

For unified evaluator controls (new):

- `reference_preproc_mode = unified`
- `reference_event_detector = unified`
- `reference_apply_quality_checks = True`

Parser defaults must keep legacy configs valid.

## 8) Implementation sequence

1. Freeze/document current recorded-channel baseline map (Section 2.5) to validate parity after refactor.
2. Add `megnet_synthetic_ref.py` adapter with guarded imports and normalized outputs.
3. Add shared evaluator in `ECG_EOG_meg_qc.py`:
   - common preprocessing
   - common event detection
   - quality checks
   - normalized candidate result object
4. Refactor ECG candidate generation:
   - recorded candidate
   - reconstructed candidate
   - MEGnet candidate
5. Apply ECG selection logic using agreed priority rules and candidate quality outcomes.
6. Refactor EOG candidate generation:
   - recorded candidate (if present)
   - MEGnet candidate always attempted in this phase
7. Apply EOG selection logic for current phase behavior.
8. Pass selected evaluation to downstream artifact/correlation path; keep all candidates for metadata/reporting.
9. Extend derivative tables and simple metric/report content with provenance and candidate details.
10. Add config keys, parser support, and docs.
11. Add/adjust tests.

## 9) Test plan

### Unit tests

- unified evaluator returns same schema for recorded/reconstructed/synthetic
- common preprocessing and detector are applied to all candidates
- quality checks run for all candidate types
- ECG priority with and without recorded channel
- EOG synthetic candidate attempted even when recorded exists
- top-1 component selection for class `2` and classes `1,3`
- optional dependency path when MEGnet/TensorFlow import fails

### Integration tests

- dataset with recorded ECG/EOG
- dataset missing ECG/EOG
- environment with MEGnet unavailable
- verify derivatives include selected source + candidate provenance
- verify report text includes `MEGnet-synthesized` and component details

## 10) Acceptance criteria

- ECG selection order matches agreed rules exactly.
- EOG synthetic path runs as agreed for this phase.
- Recorded/reconstructed/synthetic references use the same preprocessing and event detection path.
- Quality checks are applied consistently across candidates.
- Baseline recorded-channel processing path is explicitly documented and traceable to insertion points.
- No synthetic channels are added to `Raw`.
- Derivative files clearly indicate selected source and candidate provenance.
- Reports explicitly state `MEGnet-synthesized` with component IDs/details when relevant.
- Pipeline remains operational when MEGnet is missing (optional mode).

## 11) Open items for next iteration

- add user switch for selection policy (recorded-first vs synthetic-first)
- optional top-k or weighted component strategy
- candidate serialization format (TSV JSON field vs dedicated derivative JSON)
- caching ICA/classification artifacts for runtime efficiency
