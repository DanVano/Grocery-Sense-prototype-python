from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

from Grocery_Sense.data.repositories import stores_repo


class StoreSettingsWindow(tk.Toplevel):
    """Let the user choose which stores they shop at and mark favourites."""

    def __init__(self, master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master)
        self.title("Store Settings")
        self.geometry("680x480")
        self.minsize(560, 360)
        self._log = log or (lambda _: None)
        self._stores = []
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        ttk.Label(
            root,
            text="Choose which stores you shop at and mark favourites.",
            foreground="#444",
        ).pack(anchor="w", pady=(0, 8))

        cols = ("name", "shop_here", "favourite", "priority")
        self.tree = ttk.Treeview(root, columns=cols, show="headings", height=14, selectmode="browse")
        self.tree.heading("name", text="Store")
        self.tree.heading("shop_here", text="Shop here")
        self.tree.heading("favourite", text="Favourite ★")
        self.tree.heading("priority", text="Priority")
        self.tree.column("name", width=300, anchor="w")
        self.tree.column("shop_here", width=90, anchor="center")
        self.tree.column("favourite", width=100, anchor="center")
        self.tree.column("priority", width=80, anchor="center")

        vsb = ttk.Scrollbar(root, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        tree_frame = ttk.Frame(root)
        tree_frame.pack(fill="both", expand=True)
        self.tree.pack(side="left", fill="both", expand=True, in_=tree_frame)
        vsb.pack(side="right", fill="y", in_=tree_frame)

        btn_row = ttk.Frame(root)
        btn_row.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row, text="Toggle Shop Here", command=self._toggle_shop_here).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Toggle Favourite", command=self._toggle_favourite).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Refresh", command=self._refresh).pack(side="left")
        ttk.Button(btn_row, text="Close", command=self.destroy).pack(side="right")

        note = ttk.Label(
            root,
            text="Only 'Shop here' stores are considered by the Basket Optimizer.",
            foreground="#666",
        )
        note.pack(anchor="w", pady=(6, 0))

    def _refresh(self) -> None:
        self._stores = stores_repo.list_stores(order_by_priority=False)
        self.tree.delete(*self.tree.get_children())
        for s in self._stores:
            self.tree.insert("", "end", iid=str(s.id), values=(
                s.name,
                "✓" if s.shop_here else "✗",
                "★" if s.is_favorite else "—",
                s.priority if s.priority else "—",
            ))

    def _selected_store(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a store", "Select a store first.", parent=self)
            return None
        store_id = int(sel[0])
        return next((s for s in self._stores if s.id == store_id), None)

    def _toggle_shop_here(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        try:
            stores_repo.set_store_shop_here(store.id, not store.shop_here)
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self)
            return
        self._log(f"[StoreSettings] {'Enabled' if not store.shop_here else 'Disabled'} shop_here for {store.name}")
        self._refresh()

    def _toggle_favourite(self) -> None:
        store = self._selected_store()
        if store is None:
            return
        try:
            stores_repo.set_store_favorite(store.id, not store.is_favorite)
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self)
            return
        self._log(f"[StoreSettings] {'Starred' if not store.is_favorite else 'Unstarred'} {store.name}")
        self._refresh()


def open_store_settings_window(
    master: Optional[tk.Misc] = None, *, log: Optional[Callable[[str], None]] = None
) -> StoreSettingsWindow:
    return StoreSettingsWindow(master, log=log)
