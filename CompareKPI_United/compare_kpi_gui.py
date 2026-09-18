#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tkinter UI for generating KPI comparison reports."""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import traceback
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

from mpi_cpi_compare import (
    DEFAULT_REPORT_DIR,
    DEFAULT_TEMPLATE_DIR,
    REPORT_PROFILE_J6B,
    REPORT_PROFILE_MMT,
    build_report,
    profile_dir_name,
)


APP_TITLE = "MPI/CPI 汇总对比报告生成工具"
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]+')


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        project_root = exe_dir.parent
        if exe_dir.name.lower() == "dist" and (project_root / DEFAULT_TEMPLATE_DIR).exists():
            return project_root
        return exe_dir
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled).resolve()
    return app_dir()


def template_path_for(profile: str) -> Path:
    return resource_dir() / DEFAULT_TEMPLATE_DIR / f"template_{profile_dir_name(profile)}.xlsx"


def output_dir() -> Path:
    return app_dir() / DEFAULT_REPORT_DIR


def blank_to_none(value: str) -> str | None:
    value = value.strip()
    return value or None


def optional_path(value: str) -> Path | None:
    value = value.strip().strip('"')
    return Path(value) if value else None


def empty_base_sentinel() -> Path:
    return app_dir() / ".ui_empty_base"


def validate_output_name(name: str) -> str | None:
    name = name.strip()
    if not name:
        return None
    name = Path(name).name
    if INVALID_FILENAME_CHARS.search(name):
        raise ValueError("输出文件名不能包含这些字符：< > : \" / \\ | ? *")
    if not name.lower().endswith(".xlsx"):
        name += ".xlsx"
    return name


class CompareKpiApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("900x680")
        self.root.minsize(820, 620)

        self.profile = StringVar(value=REPORT_PROFILE_MMT)
        self.base_path = StringVar(value="")
        self.target_path = StringVar(value=str(app_dir() / "target" / REPORT_PROFILE_MMT))
        self.output_name = StringVar(value="")
        self.include_details = BooleanVar(value=False)

        self.base_vehicle = StringVar(value="")
        self.base_date = StringVar(value="")
        self.base_version = StringVar(value="")
        self.target_vehicle = StringVar(value="")
        self.target_date = StringVar(value="")
        self.target_version = StringVar(value="")

        self.status = StringVar(value="准备就绪")
        self.log_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_output: Path | None = None

        self._build_ui()
        self._poll_queue()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)

        title = ttk.Label(outer, text=APP_TITLE, font=("Microsoft YaHei UI", 15, "bold"))
        title.grid(row=0, column=0, sticky="w")

        config = ttk.LabelFrame(outer, text="报告配置", padding=12)
        config.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        config.columnconfigure(1, weight=1)
        config.columnconfigure(4, weight=1)

        ttk.Label(config, text="格式").grid(row=0, column=0, sticky="w")
        profile_box = ttk.Combobox(
            config,
            textvariable=self.profile,
            values=(REPORT_PROFILE_J6B, REPORT_PROFILE_MMT),
            state="readonly",
            width=12,
        )
        profile_box.grid(row=0, column=1, sticky="w", padx=(8, 24))
        profile_box.bind("<<ComboboxSelected>>", lambda _event: self._profile_changed())

        ttk.Label(config, text="固定输出路径").grid(row=0, column=2, sticky="w")
        ttk.Label(config, text=str(output_dir())).grid(row=0, column=3, columnspan=2, sticky="w", padx=(8, 0))

        ttk.Label(config, text="输出文件名").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(config, textvariable=self.output_name).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=(8, 8), pady=(10, 0)
        )
        ttk.Label(config, text="留空则自动命名").grid(row=1, column=4, sticky="w", pady=(10, 0))

        ttk.Checkbutton(config, text="生成信号对照表", variable=self.include_details).grid(
            row=2, column=1, sticky="w", padx=(8, 0), pady=(10, 0)
        )

        paths = ttk.LabelFrame(outer, text="选择报告", padding=12)
        paths.grid(row=2, column=0, sticky="ew", pady=8)
        paths.columnconfigure(1, weight=1)

        self._path_row(paths, 0, "base", self.base_path, optional=True)
        self._path_row(paths, 1, "target", self.target_path, optional=False)

        overrides = ttk.LabelFrame(outer, text="表头覆盖（留空则按报告默认信息）", padding=12)
        overrides.grid(row=3, column=0, sticky="ew", pady=8)
        for col in range(1, 7, 2):
            overrides.columnconfigure(col, weight=1)

        self._override_row(overrides, 0, "base车型", self.base_vehicle, "base日期", self.base_date, "base版本", self.base_version)
        self._override_row(
            overrides,
            1,
            "target车型",
            self.target_vehicle,
            "target日期",
            self.target_date,
            "target版本",
            self.target_version,
        )

        actions = ttk.Frame(outer)
        actions.grid(row=4, column=0, sticky="ew", pady=(8, 10))
        actions.columnconfigure(3, weight=1)
        self.generate_button = ttk.Button(actions, text="生成对比报告", command=self._start_generate)
        self.generate_button.grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="打开输出文件夹", command=self._open_output_dir).grid(row=0, column=1, padx=(10, 0))
        ttk.Button(actions, text="打开生成文件", command=self._open_last_output).grid(row=0, column=2, padx=(10, 0))
        ttk.Label(actions, textvariable=self.status).grid(row=0, column=3, sticky="e")

        log_frame = ttk.LabelFrame(outer, text="运行日志", padding=8)
        log_frame.grid(row=5, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = self._make_log_widget(log_frame)
        self._log("base 可以留空；target 必须选择文件或文件夹。")
        self._log("输出固定写入：%s" % output_dir())

    def _path_row(self, parent: ttk.Frame, row: int, label: str, variable: StringVar, optional: bool) -> None:
        ttk.Label(parent, text=f"{label}报告").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(parent, text="选文件", command=lambda: self._select_file(variable)).grid(row=row, column=2, pady=4)
        ttk.Button(parent, text="选文件夹", command=lambda: self._select_folder(variable)).grid(
            row=row, column=3, padx=(6, 0), pady=4
        )
        if optional:
            ttk.Button(parent, text="清空", command=lambda: variable.set("")).grid(row=row, column=4, padx=(6, 0), pady=4)

    def _override_row(
        self,
        parent: ttk.Frame,
        row: int,
        label_a: str,
        var_a: StringVar,
        label_b: str,
        var_b: StringVar,
        label_c: str,
        var_c: StringVar,
    ) -> None:
        ttk.Label(parent, text=label_a).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var_a).grid(row=row, column=1, sticky="ew", padx=(8, 18), pady=4)
        ttk.Label(parent, text=label_b).grid(row=row, column=2, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var_b).grid(row=row, column=3, sticky="ew", padx=(8, 18), pady=4)
        ttk.Label(parent, text=label_c).grid(row=row, column=4, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=var_c).grid(row=row, column=5, sticky="ew", padx=(8, 0), pady=4)

    def _make_log_widget(self, parent: ttk.Frame):
        import tkinter as tk

        text = tk.Text(parent, height=10, wrap="word", state="disabled", font=("Consolas", 10))
        y_scroll = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=y_scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        return text

    def _profile_changed(self) -> None:
        default_target = app_dir() / "target" / self.profile.get()
        if not self.target_path.get().strip() or "target" in self.target_path.get():
            self.target_path.set(str(default_target))
        self._log("已选择格式：%s" % self.profile.get())

    def _select_file(self, variable: StringVar) -> None:
        path = filedialog.askopenfilename(
            title="选择报告文件",
            filetypes=(("Excel 文件", "*.xlsx"), ("所有文件", "*.*")),
        )
        if path:
            variable.set(path)

    def _select_folder(self, variable: StringVar) -> None:
        path = filedialog.askdirectory(title="选择报告文件夹")
        if path:
            variable.set(path)

    def _start_generate(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_TITLE, "正在生成中，请稍等。")
            return

        try:
            options = self._collect_options()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self.generate_button.configure(state="disabled")
        self.status.set("正在生成...")
        self._log("开始生成对比报告...")
        self.worker = threading.Thread(target=self._run_generate, args=(options,), daemon=True)
        self.worker.start()

    def _collect_options(self) -> dict[str, object]:
        profile = self.profile.get().strip()
        template = template_path_for(profile)
        if not template.exists():
            raise FileNotFoundError(f"模板不存在：{template}")

        target = optional_path(self.target_path.get())
        if target is None:
            raise ValueError("请选择 target 报告文件或文件夹。")
        if not target.exists():
            raise FileNotFoundError(f"target 路径不存在：{target}")

        base = optional_path(self.base_path.get())
        if base is not None and not base.exists():
            raise FileNotFoundError(f"base 路径不存在：{base}")
        if base is None:
            base = empty_base_sentinel()

        output_name = validate_output_name(self.output_name.get())
        output_path = output_dir() / output_name if output_name else None

        return {
            "root": app_dir(),
            "template": template,
            "output": output_path,
            "base_path": base,
            "target_path": target,
            "include_details": bool(self.include_details.get()),
            "report_profile": profile,
            "base_vehicle": blank_to_none(self.base_vehicle.get()),
            "target_vehicle": blank_to_none(self.target_vehicle.get()),
            "base_date": blank_to_none(self.base_date.get()),
            "target_date": blank_to_none(self.target_date.get()),
            "base_version": blank_to_none(self.base_version.get()),
            "target_version": blank_to_none(self.target_version.get()),
        }

    def _run_generate(self, options: dict[str, object]) -> None:
        try:
            output = build_report(**options)  # type: ignore[arg-type]
        except Exception:
            self.log_queue.put(("error", traceback.format_exc()))
        else:
            self.log_queue.put(("done", str(output)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "done":
                    self.last_output = Path(payload or "")
                    self.status.set("生成完成")
                    self._log("已生成：%s" % self.last_output)
                    messagebox.showinfo(APP_TITLE, "已生成：\n%s" % self.last_output)
                    self.generate_button.configure(state="normal")
                elif kind == "error":
                    self.status.set("生成失败")
                    self._log(payload or "生成失败")
                    messagebox.showerror(APP_TITLE, payload or "生成失败")
                    self.generate_button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _open_output_dir(self) -> None:
        output_dir().mkdir(parents=True, exist_ok=True)
        os.startfile(output_dir())  # type: ignore[attr-defined]

    def _open_last_output(self) -> None:
        if not self.last_output or not self.last_output.exists():
            messagebox.showinfo(APP_TITLE, "还没有可打开的生成文件。")
            return
        os.startfile(self.last_output)  # type: ignore[attr-defined]


def main() -> None:
    root = Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    CompareKpiApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
