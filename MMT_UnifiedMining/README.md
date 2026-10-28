# MMT Unified Mining

Bad Case extraction reports display Chinese metric names consistent with comparison reports
in the metric-name column. The existing metric-key column, extraction-rule keys, CSV filenames
and summary-report identifiers remain unchanged.
Mining summary reports also include a Chinese metric-name column immediately after the
original metric-name column; code identifiers and statistics remain unchanged.
All 12 driving-uninterfered-acceleration metrics are excluded from Bad Case
extraction reports, regardless of extraction rules. Their calculations, CSV
outputs, summary statistics and comparison reports remain unchanged.

## Curve and stop-and-go TTC gate

The three curve-control CPI metrics and four stop-and-go CPI metrics use a
4.5 s TTC scene-selection threshold on every frame (previously 3.5 s).
An available positive TTC below 4.5 s breaks the candidate segment; equality
is allowed. Existing missing/zero-TTC handling and all other scene conditions
remain unchanged. This does not change jerk or acceleration build-up thresholds.

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
| Stop-and-go acceleration build-up time | 1.0-4.0 s | 1.0-3.5 s | 1.0-3.5 s |
| Slope steady-speed fluctuation | <= 0.6 m/s | <= 0.3 m/s | <= 0.3 m/s |
| Follow-start response latency | <= 2.8 s | <= 2.5 s | <= 2.5 s |
| Red-light stop comfort jerk | >= -2.3 m/s3 | >= -2.0 m/s3 | >= -2.0 m/s3 |
| Green-light start response latency | <= 1.7 s | <= 1.5 s | <= 1.5 s |
| ACC start jerkiness scene window | 3 s | 1 s | 1 s |

Other updated template criteria include stop-and-go maximum acceleration (1.0-2.7 m/s2), follow-stop distance (3-4 m), follow-stop TTC (>= 4.5 s), follow-start comfort jerk (1.0-1.5 m/s3), red-light stop distance (1.5-3.5 m), and green-light maximum acceleration (1.0-2.3 m/s2). Green-light response latency and maximum acceleration are separate metrics: each has its own Bad Case count and severity score, based only on its own threshold.

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
