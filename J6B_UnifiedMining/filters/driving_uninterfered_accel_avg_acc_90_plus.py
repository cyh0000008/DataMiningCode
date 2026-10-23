from filters._driving_uninterfered_accel import create_metric_class


def create_metric():
    return create_metric_class("90_plus", "avg_acc")()
