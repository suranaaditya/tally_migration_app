"""TallySlim — tkinter GUI wrapper around ``preprocessor.preprocess``.

Usage:
    python -m tools.tally_slim.gui

Deliverable for bookkeepers: PyInstaller bundles this module into
``TallySlim.exe`` (see ``build.py``).  Double-click workflow:

  1. Browse to the full Tally XML export
  2. Output path auto-populates to ``<stem>_slim.xml`` in same folder
  3. Click Convert
  4. Pre-scan (~5 s on 220 MB) counts entities for the progress bar
  5. Main conversion with live progress ticks
  6. Completion dialog reports sizes + log path

Threading: preprocessing runs on a daemon ``threading.Thread``.  All UI
updates marshal back to the Tk main loop via ``root.after(0, ...)`` —
tkinter is not thread-safe so direct widget mutation from the worker
thread would crash on Windows.
"""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from rgi_migration.parsers.tally_tag_whitelist import WHITELIST_VERSION
from tools.tally_slim import __version__ as TOOL_VERSION
from tools.tally_slim.preprocessor import (
    InputError,
    RunSummary,
    StrictValidationError,
    count_tallymessages,
    preprocess,
)


def _default_output_path(input_path: Path) -> Path:
    """``ghrcacs_masters.xml`` → ``ghrcacs_masters_slim.xml`` (same folder).

    Idempotent: if the input already ends in ``_slim``, don't double-suffix.
    """
    stem = input_path.stem
    if stem.endswith("_slim"):
        stem = stem[:-5]
    return input_path.with_name(f"{stem}_slim.xml")


class TallySlimApp:
    """Main application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(
            f"TallySlim v{TOOL_VERSION} (whitelist v{WHITELIST_VERSION})"
        )
        self.root.resizable(False, False)
        self.root.geometry("600x400")

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value="Ready — select an input file to begin"
        )
        self.progress_var = tk.DoubleVar(value=0.0)

        self._is_running = False
        self._build_ui()

        # Trigger convert-button enable/disable when either path changes.
        self.input_var.trace_add("write", lambda *_: self._update_convert_button())
        self.output_var.trace_add("write", lambda *_: self._update_convert_button())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        # Row 0/1 — title + version subtitle
        ttk.Label(
            frame,
            text="RGI Tally XML Preprocessor",
            font=("Segoe UI", 14, "bold"),
        ).grid(row=0, column=0, columnspan=3, pady=(0, 2))
        ttk.Label(
            frame,
            text=f"v{TOOL_VERSION}  -  whitelist v{WHITELIST_VERSION}",
            font=("Segoe UI", 9),
            foreground="#666",
        ).grid(row=1, column=0, columnspan=3, pady=(0, 16))

        # Row 2 — input
        ttk.Label(frame, text="Input file:").grid(
            row=2, column=0, sticky="w", pady=4
        )
        ttk.Entry(
            frame,
            textvariable=self.input_var,
            width=50,
            state="readonly",
        ).grid(row=2, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(
            frame, text="Browse...", command=self._pick_input,
        ).grid(row=2, column=2, pady=4)

        # Row 3 — output
        ttk.Label(frame, text="Output file:").grid(
            row=3, column=0, sticky="w", pady=4
        )
        ttk.Entry(
            frame, textvariable=self.output_var, width=50,
        ).grid(row=3, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(
            frame, text="Browse...", command=self._pick_output,
        ).grid(row=3, column=2, pady=4)

        # Row 4 — progress bar
        self.progress = ttk.Progressbar(
            frame,
            orient="horizontal",
            mode="determinate",
            variable=self.progress_var,
            maximum=100,
            length=500,
        )
        self.progress.grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(16, 4)
        )

        # Row 5 — status text
        ttk.Label(
            frame, textvariable=self.status_var, foreground="#555",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 16))

        # Row 6 — Convert / Quit
        btns = ttk.Frame(frame)
        btns.grid(row=6, column=0, columnspan=3, pady=8)
        self.convert_btn = ttk.Button(
            btns, text="Convert", command=self._on_convert, state="disabled",
        )
        self.convert_btn.pack(side="left", padx=8)
        ttk.Button(btns, text="Quit", command=self.root.destroy).pack(
            side="left", padx=8,
        )

        frame.columnconfigure(1, weight=1)

    # ------------------------------------------------------------------
    # File-picker handlers
    # ------------------------------------------------------------------

    def _pick_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Tally XML export",
            filetypes=[("Tally XML files", "*.xml"), ("All files", "*.*")],
        )
        if not path:
            return
        in_path = Path(path)
        self.input_var.set(str(in_path))
        self.output_var.set(str(_default_output_path(in_path)))

    def _pick_output(self) -> None:
        current = Path(self.output_var.get()) if self.output_var.get() else None
        path = filedialog.asksaveasfilename(
            title="Save slim XML as",
            defaultextension=".xml",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
            initialdir=str(current.parent) if current else None,
            initialfile=current.name if current else None,
        )
        if path:
            self.output_var.set(path)

    def _update_convert_button(self) -> None:
        if self._is_running:
            return
        state = (
            "normal"
            if self.input_var.get() and self.output_var.get()
            else "disabled"
        )
        self.convert_btn.config(state=state)

    # ------------------------------------------------------------------
    # Conversion orchestration
    # ------------------------------------------------------------------

    def _on_convert(self) -> None:
        if self._is_running:
            return
        input_path = Path(self.input_var.get())
        output_path = Path(self.output_var.get())

        if not input_path.exists():
            messagebox.showerror(
                "Input file missing",
                f"The selected input file doesn't exist:\n{input_path}",
            )
            return

        self._is_running = True
        self.convert_btn.config(state="disabled")
        self.progress_var.set(0.0)
        self.status_var.set("Scanning...")

        threading.Thread(
            target=self._worker,
            args=(input_path, output_path),
            daemon=True,
        ).start()

    def _worker(self, input_path: Path, output_path: Path) -> None:
        """Runs on the worker thread — never touches widgets directly."""
        try:
            # Phase 1 — pre-scan for progress denominator
            total = count_tallymessages(input_path)
            self._post(lambda: self.progress.config(maximum=max(total, 1)))
            self._post(
                lambda: self.status_var.set(f"Processing 0 of {total:,}...")
            )

            # Phase 2 — main conversion with throttled progress callbacks
            def on_progress(processed: int) -> None:
                self._post(lambda p=processed: self.progress_var.set(p))
                self._post(
                    lambda p=processed: self.status_var.set(
                        f"Processing {p:,} of {total:,}..."
                    )
                )

            summary = preprocess(
                input_path,
                output_path,
                strict=True,
                progress_callback=on_progress,
            )

            # Ensure bar hits 100% even if last tick landed short.
            self._post(lambda: self.progress_var.set(max(total, 1)))
            self._post(lambda: self.status_var.set("Done"))
            self._post(lambda s=summary, o=output_path: self._on_success(s, o))
        except InputError as exc:
            self._post(
                lambda e=exc, o=output_path: self._on_error(
                    f"Input error:\n\n{e}", o
                )
            )
        except StrictValidationError as exc:
            self._post(
                lambda e=exc, o=output_path: self._on_error(
                    f"The slim XML did not round-trip through the parser:\n\n{e}\n\n"
                    f"This is a serious issue — please send the log file to support.",
                    o,
                )
            )
        except Exception as exc:
            self._post(
                lambda e=exc, o=output_path: self._on_error(
                    f"Unexpected error ({type(e).__name__}):\n\n{e}", o
                )
            )
        finally:
            self._post(self._reset_running)

    def _post(self, fn) -> None:                  # noqa: ANN001 — callable
        """Marshal a zero-arg callback to the Tk main loop."""
        self.root.after(0, fn)

    def _reset_running(self) -> None:
        self._is_running = False
        self._update_convert_button()

    # ------------------------------------------------------------------
    # Completion / failure dialogs (always called on main thread)
    # ------------------------------------------------------------------

    def _on_success(self, summary: RunSummary, output_path: Path) -> None:
        log_path = Path(str(output_path) + ".log")
        in_mb = summary.input_size / 1024 / 1024
        out_mb = summary.output_size / 1024 / 1024
        pct = (
            100.0 * (1.0 - summary.output_size / summary.input_size)
            if summary.input_size > 0 else 0.0
        )
        messagebox.showinfo(
            "Conversion complete",
            f"Slim XML written to:\n{output_path}\n\n"
            f"Size: {in_mb:.1f} MB -> {out_mb:.1f} MB "
            f"({pct:.1f}% reduction)\n"
            f"Time: {summary.elapsed_seconds:.1f}s\n"
            f"Validation: {summary.strict_result}\n\n"
            f"Log written to:\n{log_path}",
        )

    def _on_error(self, message: str, output_path: Path) -> None:
        log_path = Path(str(output_path) + ".log")
        self.status_var.set("Failed - see dialog for details")
        self.progress_var.set(0.0)
        messagebox.showerror(
            "Conversion failed",
            f"{message}\n\nFull details in log:\n{log_path}",
        )


def main() -> None:
    root = tk.Tk()
    TallySlimApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
