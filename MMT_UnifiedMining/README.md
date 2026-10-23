# MMT Unified Mining

## Vehicle powertrain

Set the required root-level `powertrain_type` in `config.json` for the vehicle being mined:

```json
"powertrain_type": "PHEV/BEV"
```

The only accepted values are `PHEV/BEV` and `ICE`. Mining stops with a configuration error if the value is missing or invalid. The selected type is passed to both Topic and CAN metrics. The current `config.json` is set to `PHEV/BEV`; update it when mining an ICE vehicle.

## Template-aligned MMT criteria

Powertrain-specific criteria use the selected type for both Bad Case checks and severity calculations:

| Metric | PHEV/BEV | ICE |
|---|---:|---:|
| Stop-and-go maximum jerk | <= 3.5 m/s3 | <= 4 m/s3 |
| Initial speed build-up time | 1.0-2.8 s | 1.0-3.2 s |
| Stop-and-go acceleration build-up time | 1.0-3.5 s | 1.0-4.0 s |
| Slope steady-speed fluctuation | <= 0.3 m/s | <= 0.6 m/s |
| Follow-start response latency | <= 2.5 s | <= 2.8 s |
| Red-light stop comfort jerk | >= -2.0 m/s3 | >= -2.3 m/s3 |
| Green-light start response latency | <= 1.5 s | <= 1.7 s |
| ACC start jerkiness scene window | 1 s | 3 s |

Other updated template criteria include stop-and-go maximum acceleration (1.0-2.7 m/s2), follow-stop distance (3-4 m), follow-stop TTC (>= 4.5 s), follow-start comfort jerk (1.0-1.5 m/s3), red-light stop distance (1.5-3.5 m), and green-light maximum acceleration (1.0-2.3 m/s2). Green-light response latency and maximum acceleration are separate metrics: each has its own Bad Case count and severity score, based only on its own threshold.
