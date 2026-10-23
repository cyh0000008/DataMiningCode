from filters._driving_uninterfered_accel import create_metric_class


def create_metric():
    return create_metric_class("0_30", "max_acc")()
