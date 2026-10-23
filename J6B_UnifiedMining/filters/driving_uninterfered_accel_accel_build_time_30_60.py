from filters._driving_uninterfered_accel import create_metric_class


def create_metric():
    return create_metric_class("30_60", "accel_build_time")()
