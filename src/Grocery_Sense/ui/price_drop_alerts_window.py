from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable, Any

from Grocery_Sense.services.price_drop_alert_service import PriceDropAlertService


def _fmt_money(v: Any) -> str:
    try:
        if v is None:
            return "n/a"
        return f"${float(v):,.2f}"
    except Exception:
        return "n/a"


class PriceDropAlertsWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk, *, log: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent)
        self.title("Price Drop Alerts")
        self.geometry("1100x640")

        self._log = log or (lambda msg: None)
        self.svc = PriceDropAlertService()

        self.threshold_var = tk.DoubleVar(value=20.0)
        self.min_samples_var = tk.IntVar(value=3)
        self.receipts_scan_days_var = tk.IntVar(value=7)

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Price Drop Alerts", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)

        controls = ttk.Frame(self, padding=(10, 0, 10, 10))
        controls.pack(fill=tk.X)

        ttk.Label(controls, text="Threshold (% drop):").pack(side=tk.LEFT)
        ttk.Entry(controls, textvariable=self.threshold_var, width=6).pack(side=tk.LEFT, padx=(6, 14))

        ttk.Label(controls, text="Min samples:").pack(side=tk.LEFT)
        ttk.Entry(controls, textvariable=self.min_samples_var, width=4).pack(side=tk.LEFT, padx=(6, 14))

        ttk.Label(controls, text="Scan receipts from last (days):").pack(side=tk.LEFT)
        ttk.Entry(controls, textvariable=self.receipts_scan_days_var, width=4).pack(side=tk.LEFT, padx=(6, 14))

        ttk.Button(controls, text="Refresh", command=self.refresh).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(controls, text="Scan Recent Receipts", command=self.scan_recent).pack(side=tk.RIGHT)

        mid = ttk.Frame(self, padding=(10, 0, 10, 10))
        mid.pack(fill=tk.BOTH, expand=True)

        cols = ("id", "date", "item", "store", "observed", "avg30", "drop", "samples", "receipt", "basis")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings")
        for c, label, w, anchor in [
            ("id", "ID", 60, "center"),
            ("date", "Observed Date", 110, "center"),
            ("item", "Item", 260, "w"),
            ("store", "Store", 190, "w"),
            ("observed", "Observed", 90, "e"),
            ("avg30", "Avg (30d)", 90, "e"),
            ("drop", "Drop %", 80, "e"),
            ("samples", "Samples", 70, "center"),
            ("receipt", "Receipt ID", 80, "center"),
            ("basis", "Basis", 70, "center"),
        ]:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor=anchor)

        yscroll = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill=tk.X)

        ttk.Button(bottom, text="Dismiss Selected", command=self.dismiss_selected).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Dismiss All", command=self.dismiss_all).pack(side=tk.LEFT, padx=(8, 0))

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.RIGHT)

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        alerts = self.svc.list_open_alerts(limit=400)

        for a in alerts:
            drop = a.get("percent_drop")
            self.tree.insert(
                "",
                "end",
                iid=str(a["id"]),
                values=(
                    a["id"],
                    a.get("observed_date") or "",
                    a.get("item_name") or "",
                    a.get("store_name") or "",
                    _fmt_money(a.get("observed_unit_price")),
                    _fmt_money(a.get("avg_price")),
                    "" if drop is None else f"{float(drop):.1f}%",
                    a.get("sample_count") or 0,
                    a.get("receipt_id") or "",
                    a.get("basis") or "",
                ),
            )

        self.status_var.set(f"Open alerts: {len(alerts)}")
        self._log(f"[Alerts] Loaded {len(alerts)} open alerts.")

    def scan_recent(self) -> None:
        try:
            res = self.svc.detect_for_recent_receipts(
                receipts_days_back=int(self.receipts_scan_days_var.get() or 7),
                threshold_percent=float(self.threshold_var.get() or 20.0),
                window_days=30,
                min_samples=int(self.min_samples_var.get() or 3),
            )
        except Exception as e:
            messagebox.showerror("Scan failed", str(e))
            return

        self._log(f"[Alerts] Scan complete: created={res.created_count}, skipped={res.skipped_count}")
        self.refresh()

    def dismiss_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select an alert first.")
            return
        for iid in sel:
            try:
                self.svc.dismiss_alert(int(iid))
            except Exception:
                pass
        self.refresh()

    def dismiss_all(self) -> None:
        if not messagebox.askyesno("Dismiss All", "Dismiss ALL open alerts?"):
            return
        self.svc.dismiss_all()
        self.refresh()


def open_price_drop_alerts_window(parent: tk.Tk, log: Optional[Callable[[str], None]] = None) -> None:
    PriceDropAlertsWindow(parent, log=log)
