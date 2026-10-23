from filters._driving_uninterfered_accel import create_metric_class


def create_metric():
    return create_metric_class("60_90", "accel_build_time")()
