"""Run the same mining queue without a desktop, controlled by the bound Feishu user."""

from __future__ import annotations

import argparse
import secrets
import sys

from PySide6.QtCore import QCoreApplication, QTimer

from unified_app.feishu_bot import FeishuBridge
from unified_app.job_manager import JobManager
from unified_app.paths import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", action="store_true", help="生成绑定码并等待本人私聊")
    parser.add_argument("--bind-code", default="", help="使用指定的一次性绑定码")
    args = parser.parse_args()
    app = QCoreApplication(sys.argv[:1])
    manager = JobManager(max_concurrent=load_settings().max_concurrent_jobs)
    code = args.bind_code or (secrets.token_hex(4) if args.bind else "")
    bridge = FeishuBridge(manager, bind_code=code)
    bridge.log.connect(lambda message: print(message, flush=True))
    if code:
        print("请向飞书机器人私聊发送：绑定挖掘助手 " + code, flush=True)
    try:
        if not bridge.start():
            print("尚未启用飞书助手，请先运行 --bind。", file=sys.stderr)
            return 2
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    app.aboutToQuit.connect(bridge.stop)
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(500)
    import signal
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
