"""
Tkinter front-end for running the fixed master test-case file (master.csv)
on demand, under a version label you type each time.

Usage (from this folder's parent directory):
    python -m eval_automation.gui_run
"""
import csv
import json
import threading
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .auth_client import login
from .config import REPORTS_DIR
from .report_writer import write_report
from .runner import run_test_cases
from .scoring import warm_up_client

_HERE = Path(__file__).resolve().parent
_SETTINGS_FILE = _HERE / "settings.json"
_DEFAULT_MASTER_FILE = _HERE / "master.csv"

_LOCAL_URL = "http://localhost:8014"
_DEPLOYED_URL = "https://ca-tmt-dolly-backend-dev.politemeadow-bb00a646.centralus.azurecontainerapps.io/api"


def _load_settings() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            return json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_settings(settings: dict) -> None:
    _SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: "EvalRunnerApp") -> None:
        super().__init__(parent.root)
        self.title("Settings")
        self.resizable(False, False)
        self.parent = parent

        self.master_file = tk.StringVar(value=parent.master_file.get())

        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Master test file:").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(frame, textvariable=self.master_file, width=50)
        entry.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        ttk.Button(frame, text="Browse...", command=self._browse).grid(row=1, column=1, padx=(6, 0))

        button_row = ttk.Frame(frame)
        button_row.grid(row=2, column=0, columnspan=2, sticky="e")
        ttk.Button(button_row, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(button_row, text="Save", command=self._save).pack(side="right")

        self.transient(parent.root)
        self.grab_set()

    def _browse(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
        if path:
            self.master_file.set(path)

    def _save(self) -> None:
        self.parent.master_file.set(self.master_file.get())
        _save_settings({"master_file": self.master_file.get()})
        self.destroy()


class EvalRunnerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Dolly Eval Runner")
        root.geometry("560x420")

        settings = _load_settings()
        self.master_file = tk.StringVar(value=settings.get("master_file", str(_DEFAULT_MASTER_FILE)))
        self.target = tk.StringVar(value="deployed")
        self.version = tk.StringVar(value=datetime.now().strftime("run_%Y%m%d_%H%M%S"))

        pad = {"padx": 10, "pady": 6}

        top_bar = ttk.Frame(root)
        top_bar.pack(fill="x", **pad)
        ttk.Button(top_bar, text="Settings...", command=self._open_settings).pack(side="right")

        target_frame = ttk.Frame(root)
        target_frame.pack(fill="x", **pad)
        ttk.Label(target_frame, text="Target:").pack(side="left")
        ttk.Radiobutton(target_frame, text="Local", variable=self.target, value="local").pack(side="left", padx=6)
        ttk.Radiobutton(target_frame, text="Deployed", variable=self.target, value="deployed").pack(side="left")

        version_frame = ttk.Frame(root)
        version_frame.pack(fill="x", **pad)
        ttk.Label(version_frame, text="Version:").pack(side="left")
        ttk.Entry(version_frame, textvariable=self.version).pack(side="left", fill="x", expand=True, padx=6)

        self.run_button = ttk.Button(root, text="Run", command=self._on_run)
        self.run_button.pack(**pad)

        self.status_box = scrolledtext.ScrolledText(root, height=14, state="disabled")
        self.status_box.pack(fill="both", expand=True, **pad)

        # Warms up the judge client once, now, while the window is idle -
        # this process stays running between runs (unlike the old
        # once-per-run CLI script), so the first test case of the first run
        # no longer eats a cold-start connection-setup cost.
        threading.Thread(target=self._warm_up, daemon=True).start()

    def _warm_up(self) -> None:
        try:
            warm_up_client()
            self.root.after(0, self._log, "Judge client ready.")
        except Exception as exc:
            self.root.after(0, self._log, f"Judge client warm-up failed (will retry on first use): {exc}")

    def _open_settings(self) -> None:
        SettingsDialog(self)

    def _log(self, message: str) -> None:
        self.status_box.configure(state="normal")
        self.status_box.insert("end", message + "\n")
        self.status_box.see("end")
        self.status_box.configure(state="disabled")

    def _on_run(self) -> None:
        master_path = Path(self.master_file.get())
        version = self.version.get().strip()
        if not master_path.exists():
            messagebox.showerror("Eval Runner", f"File not found:\n{master_path}")
            return
        if not version:
            messagebox.showerror("Eval Runner", "Version can't be empty.")
            return

        self.run_button.configure(state="disabled")
        self._log(f"Starting run '{version}' against {self.target.get()}...")
        threading.Thread(target=self._run_worker, args=(master_path, version), daemon=True).start()

    def _run_worker(self, master_path: Path, version: str) -> None:
        base_url = _LOCAL_URL if self.target.get() == "local" else _DEPLOYED_URL
        try:
            self.root.after(0, self._log, f"Logging in to {base_url} ...")
            token = login(base_url)

            with open(master_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.root.after(0, self._log, f"Loaded {len(rows)} test case(s). Running...")

            results = run_test_cases(base_url, token, version, rows)

            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            report_path = REPORTS_DIR / f"{version}_report_{timestamp}.csv"
            write_report(results, report_path)

            errors = sum(1 for r in results if r.get("error"))
            self.root.after(0, self._log, f"Done - {len(results)} row(s), {errors} error(s).")
            self.root.after(0, self._log, f"Report: {report_path}")
        except Exception as exc:
            self.root.after(0, self._log, f"FAILED: {exc}")
        finally:
            self.root.after(0, lambda: self.run_button.configure(state="normal"))


def main() -> None:
    root = tk.Tk()
    EvalRunnerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
