# MMT Unified Mining

The standalone ABS activation metric (`abs_activation_count`) has been removed
from mining and reporting. ABS-correlated ACC exit (`acc_exit_after_abs`) and
its required ABS signal remain enabled.

Bad Case extraction reports display Chinese metric names consistent with comparison reports
in the metric-name column. The existing metric-key column, extraction-rule keys, CSV filenames
and summary-report identifiers remain unchanged.
Mining summary reports also include a Chinese metric-name column immediately after the
original metric-name column; code identifiers and statistics remain unchanged.
All 12 driving-uninterfered-acceleration metrics are excluded from Bad Case
extraction reports, regardless of extraction rules. Their calculations, CSV
outputs, summary statistics and comparison reports remain unchanged.

## Bad Case signal plots

Signal plots default to enabled (`"bad_case_signal_plots": true`), including
configs or presets without this field. Set it to `false` or disable **signal plots**
in the unified app's MMT form to opt out. Explicitly disabled presets stay disabled.
The option is saved in
presets and is also available as `信号图` in the Feishu advanced parameters.
The MMT Python environment needs Pillow in addition to the existing numpy,
h5py and openpyxl dependencies. Dry runs and disabled Bad Case extraction
do not draw plots.

The existing `bad_case_report_<timestamp>.xlsx` gains image and plotting-status
columns. Each selected **metric/folder** row contains an embedded signal
overview, not an individual event or threshold-evidence plot: the source CSV
can aggregate multiple events into one row. CAN plots show available core
signals and the metric's required signals, including sustained jerk computed
with the mining algorithm. Explicit CAN `[Event start/end]` intervals in
relative seconds are shaded; other event timestamps are not guessed.
Topic plots use the same comprehensive-frame preprocessing as mining, showing
speed, wheel/slope/total acceleration, total sustained jerk, ACC/override,
curvature control and front-target signals where available. Planning/geometric
following candidates are shown separately, with target IDs.

Plots reuse the pipeline's H5 selection, next-folder eligibility and continuation
window. CAN time is relative to the primary H5 start; Topic time is relative to
the primary folder's first UNP timestamp. Missing signals or insufficient
samples are identified in the status column. A missing/unreadable source or
other plotting error is logged and recorded in the affected row; it does not
become an empty success image or discard the other report rows.

Full-size PNGs remain in `<report-stem>_signal_plots` next to the report.
Embedded images remain viewable when the XLSX is copied or included in the
existing result ZIP/Feishu delivery; separate PNGs and raw data are not added
to the ZIP. Plotting increases runtime and report size. Existing reports are
not modified automatically; rerun mining with plotting enabled.

## Driver override truncates scenes

All topic scene CPIs (curve, follow start, follow stop including its stable
distance window, red light, stop-and-go, traffic-light stop-and-go and slope)
use `longitudinal_preprocessor.is_acc_controlling`: `NP_State.f_Np_on` and no
driver override (`throttle_override`, falling back to `throttle_pedal_percent > 0`).
An override frame breaks the scene. The CAN MPIs `acc_rollback_count` and
`acc_lost_arbitration` also exclude frames where
`PPEI_Propulsion_General_Data_1_0C2_M.IAccPdlOvrrdAtv == 1`. Case counts can
be lower than in earlier versions. A truncated prefix is retained only when it
already meets the scene's existing completion and minimum-length criteria;
override-frame speed or stationary state cannot complete follow-stop or
stop-and-go scenes.

## Curve and stop-and-go TTC gate

Green-light start comfort jerk has no minimum: only maximum jerk >2.5 m/s3
is a Bad Case. Values <=2.5 m/s3 have zero severity; scene selection and
jerk calculation are unchanged.

The three curve-control, four stop-and-go, two red-light stop, five green-light
start and three slope CPI metrics share the forward-target gate: no target is
allowed; otherwise the nearest in-lane target must have distance >20 m and
`crash_risk_ttc` >8 s. Equality fails, as do missing/NaN TTC for an existing target.
Green-light starts check this both at trigger and throughout recording; a
violation discards an unfinished recording without restarting it mid-drive.
Completed scenes remain eligible. Red-light recovery searches cannot bridge
a failed target gate. Truncated scenes still require their original completion
and minimum-length conditions. Measurement thresholds and J6B remain unchanged.

CAN set-speed reach uses this same >20 m / >8 s gate throughout its minimum
8-second scene. It matches Fusion frames to CAN timestamps within 300 ms,
including concatenated context folders. Missing/unmatched Fusion is not treated
as an empty road; missing data is reported explicitly when no matching data exists.
The position-based candidate considers only available targets ahead (`x > 0`) within the lateral
corridor `-1.5 <= y <= 1.5 m`, including cut-ins after they enter it. Adjacent-lane
targets outside this corridor do not block the scene unless selected by planning.

UNP `leadone_info.object_id` supplies an additional, independent following candidate.
It is matched to the available Fusion object with the same `track_id`, using UNP's
Fusion `metaTimeMs`. This planning-selected candidate is not restricted by the
position candidate's lateral corridor. Different IDs coexist; equal IDs count once.
Distance, velocity, acceleration, length and `crash_risk_ttc` always come from the
same object; `leadone_info.time_distance` is not TTC. All existing target-clearance
gates require **both** candidates to pass their distance/TTC thresholds. A selected
ID missing from matched Fusion blocks clearance and emits a warning.

The seven follow-start/follow-stop metrics build and measure scenes independently
for each candidate ID, splitting on target loss rather than switching objects.
The target ID must remain unchanged from scene trigger through completion,
including the front-start detection window. Missing/invalid target frames break
continuity; a returning ID cannot bridge the gap. A replacement ID must satisfy
the scene's entry and minimum-length requirements again.
The stable follow-stop distance observation continues to track the same ID for
the full two-second observation window. Simultaneous scenes for different IDs
are separate cases. Uninterfered acceleration and unreasonable-low-speed exclusions
also recognize the planning-selected target; their other thresholds are unchanged.
Following measurement logs include `target_id` to distinguish simultaneous cases.
CAN set-speed reach uses the matching UNP frame (within 300 ms) and its Fusion
reference time when available. Legacy data without UNP retains position-only
screening with an explicit warning.

## Confirmed steady slope scenes

All three slope CPI metrics share `slope_scene_utils.find_steady_slope_segments`.
Retain the normal NP/no-near-front/moving/slope/curvature-limit/red-light/data gates,
but require a finite positive UNP SetSpeed constant within `1e-6` of the candidate's
first value. Topic `cruise_velocity` and chassis `vehicle_speed_average` are m/s:
speed must remain within SetSpeed +/- `2/3.6 m/s` for a full second to enter.
The confirming sample starts the measured segment; the preceding dwell is excluded.
Every measured segment must last at least 5 seconds (time-based, not frame-count-based).
No near front object is allowed; if a forward object exists, its distance must be strictly
greater than 20 m and its `crash_risk_ttc` strictly greater than 8 s. The nearest
forward object within the lane corridor and any distinct planning-selected leadone
object are checked independently; equality to either threshold fails.

Once entered, speed outside the entry band is still included until an original
base condition fails or SetSpeed changes. Then terminate immediately and require
a fresh one-second dwell. Remove the old SetSpeed-crossing requirement and
20-frame slope-recovery exception. All three measurements use the whole confirmed
segment, without additional dashboard-speed cropping. Their existing formulas,
units and Bad Case thresholds remain unchanged (including dashboard full-range
speed fluctuation). Preserve primary-file ownership across concatenated context.

## Stable follow-stop distance

Follow-stop distance retains the existing scene entry and inclusive [3, 4] m
criteria, but no longer uses the first stationary sample. After that sample at
`t0`, observe the full [t0, t0+2 s] interval and take the median distance over
[t0+1 s, t0+2 s]. Each sample is converted to bumper clearance first:
`closest_obj_x - closest_obj_len / 2 - 3.8` (meters).

Throughout observation, the ego vehicle must remain stationary with NP on,
the same available closest target must remain present, and its absolute ground
velocity must be <=0.2 m/s. Fusion preprocessing now retains `closest_obj_id`
from `track_id` to verify identity; missing IDs are not guessed from position.
Missing/invalid/stale matched data, a target switch/loss, vehicle movement,
an incomplete 2 s interval or a sampling gap >0.2 s excludes the scene from
the valid total Case count. The final second needs at least 3 distinct fusion
timestamps; repeated references to the same fusion timestamp count once.
The first sample at/after the right endpoint confirms coverage and state, but
samples strictly beyond the endpoint are not included in the median.

Exclusions and reasons are preserved in metric details. Successful measurements
record the median, stable-window endpoints and sample count. Existing continuous
cross-done context can complete the observation; no fallback to the first stop
sample or a shortened window is used. "Stable" means the final-second median;
no additional variance/range limit is imposed.

## Sustained jerk calculation

All acceleration-derived jerk uses the same algorithm as J6B: a centered **0.3 s**
arithmetic mean of acceleration, followed by a centered **0.3 s** least-squares
linear fit against actual timestamps. The fitted slope is jerk in m/s3. There is
no adjacent/three-point difference and no additional jerk smoothing.

`jerk_utils.py` is local to MMT so this project can still be deployed independently.
It is used by all seven Topic jerk CPI implementations, Topic/CAN ACC jerkiness
and heavy braking, dedicated ACC jerkiness scenes, APA jerkiness and gear-switch
jerkiness. Existing signal selection, slope compensation, thresholds and event
duration criteria are unchanged. High-pass acceleration analysis, torque rates
and unrelated acceleration/speed statistics retain their existing filters.

Each fit needs at least three finite samples spanning at least 0.15 s; unsupported
points are NaN, not zero, and do not qualify as MPI jerk candidates. CPI helpers
warn and return no measurement when there is no supported fit. Timestamp arrays
must be finite and strictly increasing. Topic CPI adapters retain sorting,
last-value timestamp deduplication and millisecond output, with seconds used
internally. File/scene edges use truncated windows.

Legacy sample-count smoothing arguments remain in callable signatures for
compatibility with default calls (`5`); non-default overrides now raise an
explicit error instead of silently changing or ignoring the fixed time windows.
Result parameter dictionaries report the effective windows in seconds and the
regression method. This is a windowed trend, not a new requirement for 0.3 s
of continuous threshold exceedance. Re-mine both Base and Target with the new
algorithm for version comparisons; historical values are not automatically updated.

## Vehicle powertrain

Set the required root-level `powertrain_type` in `config.json` for the vehicle being mined:

```json
"powertrain_type": "PHEV"
```

Select `ICE`, `PHEV` or `BEV`. Mining stops with a configuration error if the value is missing or invalid. The selected type is passed to both Topic and CAN metrics. The current `config.json` is set to `PHEV`. Each type has independent threshold values: ICE retains its old criteria, while PHEV and BEV each initially retain the old combined PHEV/BEV criteria. Legacy `PHEV/BEV` configs emit a warning and migrate to `PHEV`; select `BEV` explicitly for a battery-electric vehicle.

## Template-aligned MMT criteria

Powertrain-specific criteria use the selected type for both Bad Case checks and severity calculations:

| Metric | ICE | PHEV | BEV |
|---|---:|---:|---:|
| Stop-and-go maximum jerk | <= 4 m/s3 | <= 3.5 m/s3 | <= 3.5 m/s3 |
| Initial speed build-up time | 1.0-3.2 s | 1.0-2.8 s | 1.0-2.8 s |
| Stop-and-go acceleration build-up time (no minimum) | <=4.0 s | <=3.5 s | <=3.5 s |
| Slope steady-speed fluctuation | <= 0.6 m/s | <= 0.3 m/s | <= 0.3 m/s |
| Follow-start response latency | <= 2.8 s | <= 2.5 s | <= 2.5 s |
| Red-light stop comfort jerk | >= -2.3 m/s3 | >= -2.0 m/s3 | >= -2.0 m/s3 |
| Green-light start response latency | <= 1.7 s | <= 1.5 s | <= 1.5 s |
| ACC start jerkiness scene window | 3 s | 1 s | 1 s |

Other updated template criteria include stop-and-go maximum acceleration (1.0-2.7 m/s2), follow-stop distance (3-4 m), follow-stop TTC (>= 4.5 s), follow-start comfort jerk (<=1.5 m/s3, no minimum), red-light stop distance (1.5-3.5 m), and green-light maximum acceleration (1.0-2.3 m/s2). Green-light response latency and maximum acceleration are separate metrics: each has its own Bad Case count and severity score, based only on its own threshold.

## Uninterfered driving acceleration

The follow-stop fluctuation metric has been removed from mining and comparison reports.
Control shake is also excluded from comparison reports (MMT did not implement this metric).

For all initial-speed strata and ICE/PHEV/BEV, each valid event's maximum acceleration
must lie in the inclusive interval [1.5, 2.5] m/s2. Values outside this interval count
as Bad Cases. Severity is the exceedance in m/s2 divided by 1 m/s2, multiplied by 100.
The maximum-acceleration metrics use the normal CPI total-case / bad-case report format.
Average acceleration must be strictly >1.5 m/s2 and acceleration build-up time strictly
<3.5 s; equality is a Bad Case. All three metrics use normal CPI reporting for every
powertrain and speed stratum. Severity uses exceedance divided by a unit baseline of 1,
multiplied by 100; Bad Cases exactly on a strict boundary have zero exceedance severity.
Scene qualification is unchanged.
