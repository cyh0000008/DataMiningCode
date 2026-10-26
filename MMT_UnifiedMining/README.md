# MMT Unified Mining

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

For all initial-speed strata and ICE/PHEV/BEV, each valid event's maximum acceleration
must lie in the inclusive interval [1.5, 2.5] m/s2. Values outside this interval count
as Bad Cases. Severity is the exceedance in m/s2 divided by 1 m/s2, multiplied by 100.
The maximum-acceleration metrics use the normal CPI total-case / bad-case report format.
Average acceleration must be strictly >1.5 m/s2 and acceleration build-up time strictly
<3.5 s; equality is a Bad Case. All three metrics use normal CPI reporting for every
powertrain and speed stratum. Severity uses exceedance divided by a unit baseline of 1,
multiplied by 100; Bad Cases exactly on a strict boundary have zero exceedance severity.
Scene qualification is unchanged.
