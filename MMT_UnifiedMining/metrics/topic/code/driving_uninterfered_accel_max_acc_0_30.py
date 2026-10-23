from _driving_uninterfered_accel_scene import run_metric

METRIC_CATEGORY = "CPI"


def __run_haoran_data_run__(frames):
    return run_metric(frames, "0_30", "max_acc")
