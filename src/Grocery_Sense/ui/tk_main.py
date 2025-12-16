"""
grocery_sense.ui.tk_main

Basic Tkinter shell for Grocery Sense.

- Main window with buttons for common tasks:
    * Initialize DB
    * View Shopping List
    * View Meal Suggestions
    * Build Weekly Plan

- Each task opens a simple Toplevel window.
- A log/output panel at the bottom shows messages and errors for testers.

This is intentionally minimal and backend-focused. You can evolve the UI later.
"""

from __future__ import annotations

import traceback
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from typing import Optional

from grocery_sense.data.connection import get_connection
from grocery_sense.data.schema import initialize_database

from grocery_sense.data.items_repo import ItemsRepository
from grocery_sense.data.stores_repo import StoresRepository
from grocery_sense.data.shopping_list_repo import ShoppingListRepository

from grocery_sense.services.shopping_list_service import ShoppingListService
from grocery_sense.services.meal_suggestion_service import (
    MealSuggestionService,
    explain_suggested_meal,
)
from grocery_sense.services.weekly_planner_service import (
    WeeklyPlannerService,
    summarize_weekly_plan,
)


class GrocerySenseApp(tk.Tk):
    """
    Root Tkinter application for Grocery Sense.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("Grocery Sense - Prototype")
        self.geometry("900x600")

        # --- DB + services wiring -----------------------------------------
        initialize_database()
        self.conn = get_connection()

        self.stores_repo = StoresRepository(self.conn)
        self.items_repo = ItemsRepository(self.conn)
        self.sl_repo = ShoppingListRepository(self.conn)

        self.shopping_list_service = ShoppingListService(
            self.sl_repo,
            self.stores_repo,
        )
        self.meal_suggestion_service = MealSuggestionService(
            price_history_service=None  # wire real one later
        )
        self.weekly_planner_service = WeeklyPlannerService(
            meal_suggestion_service=self.meal_suggestion_service,
            shopping_list_service=self.shopping_list_service,
            items_repo=self.items_repo,
        )

        # --- Layout -------------------------------------------------------
        self._build_main_menu()
        self._build_log_panel()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_main_menu(self) -> None:
        frame = ttk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        ttk.Label(frame, text="Grocery Sense - Main Menu", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )

        row = 1

        ttk.Button(
            frame,
            text="1) Initialize / Verify Database",
            command=self._safe_call(self._handle_init_db),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="2) View Shopping List",
            command=self._safe_call(self._open_shopping_list_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="3) View Meal Suggestions",
            command=self._safe_call(self._open_meal_suggestions_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="4) Build Weekly Plan (and add to list)",
            command=self._safe_call(self._open_weekly_plan_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)
        row += 1

        ttk.Button(
            frame,
            text="Exit",
            command=self.destroy,
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=(10, 0))

    def _build_log_panel(self) -> None:
        """
        Bottom log/output panel where we print status and errors.
        """
        label = ttk.Label(self, text="Log / Output:")
        label.pack(side=tk.TOP, anchor="w", padx=10)

        self.log_text = ScrolledText(self, height=12, state=tk.NORMAL)
        self.log_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._log("Grocery Sense UI started.")

    # ------------------------------------------------------------------
    # Logging / error handling
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        """
        Append a line to the log panel and also print to stdout.
        """
        print(message)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def _log_exception(self, prefix: str, exc: BaseException) -> None:
        """
        Log an exception with traceback in the log panel.
        """
        self._log(f"[ERROR] {prefix}: {exc}")
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.log_text.insert(tk.END, tb + "\n")
        self.log_text.see(tk.END)

    def _safe_call(self, func):
        """
        Wrap a callback so that exceptions are caught and shown in the log panel,
        instead of silently killing the Tkinter event loop.
        """
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                self._log_exception(f"Exception in {func.__name__}", e)
        return wrapper

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _handle_init_db(self) -> None:
        """
        Button: Initialize / Verify DB schema.
        """
        initialize_database()
        self._log("Database schema initialized / verified.")

    def _open_shopping_list_window(self) -> None:
        """
        Button: Open a window listing active shopping list items.
        """
        win = tk.Toplevel(self)
        win.title("Shopping List")
        win.geometry("500x400")

        ttk.Label(win, text="Active Shopping List Items", font=("Segoe UI", 11, "bold")).pack(
            side=tk.TOP, anchor="w", padx=10, pady=10
        )

        listbox = tk.Listbox(win)
        listbox.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        def refresh():
            listbox.delete(0, tk.END)
            items = self.shopping_list_service.list_active_items(include_checked_off=True)
            if not items:
                listbox.insert(tk.END, "(no items)")
                return
            for it in items:
                status = "✓" if it.is_checked_off else " "
                line = (
                    f"[{status}] id={it.id}  {it.display_name}  "
                    f"x{it.quantity} {it.unit}  (store_id={it.planned_store_id})"
                )
                listbox.insert(tk.END, line)

        ttk.Button(win, text="Refresh", command=self._safe_call(refresh)).pack(
            side=tk.BOTTOM, pady=5
        )

        refresh()

    def _open_meal_suggestions_window(self) -> None:
        """
        Button: Open a window showing meal suggestions and explanations.
        """
        win = tk.Toplevel(self)
        win.title("Meal Suggestions")
        win.geometry("700x500")

        top_frame = ttk.Frame(win)
        top_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(
            top_frame,
            text="Meal Suggestions (backend only)",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w")

        # List of recipes on the left
        suggestions_box = tk.Listbox(top_frame, width=35)
        suggestions_box.grid(row=1, column=0, sticky="nsew", pady=(5, 0))

        # Explanation on the right
        explanation_text = ScrolledText(top_frame, width=50, height=20, state=tk.NORMAL)
        explanation_text.grid(row=1, column=1, sticky="nsew", padx=(10, 0), pady=(5, 0))

        top_frame.columnconfigure(0, weight=1)
        top_frame.columnconfigure(1, weight=2)
        top_frame.rowconfigure(1, weight=1)

        # Storage for current suggestions
        current_suggestions = []

        def load_suggestions():
            nonlocal current_suggestions
            suggestions_box.delete(0, tk.END)
            explanation_text.delete("1.0", tk.END)

            self._log("Loading meal suggestions (no explicit ingredient targets)...")
            current_suggestions = self.meal_suggestion_service.suggest_meals_for_week(
                target_ingredients=None,
                max_recipes=6,
                recently_used_recipe_ids=None,
            )

            if not current_suggestions:
                suggestions_box.insert(tk.END, "(no suggestions)")
                return

            for s in current_suggestions:
                name = s.recipe.get("name", "Unnamed recipe")
                suggestions_box.insert(tk.END, f"{name} (score {s.total_score:.2f})")

        def on_select(event):
            if not current_suggestions:
                return
            sel = suggestions_box.curselection()
            if not sel:
                return
            idx = sel[0]
            if idx >= len(current_suggestions):
                return
            s = current_suggestions[idx]
            explanation = explain_suggested_meal(s)
            explanation_text.delete("1.0", tk.END)
            explanation_text.insert(tk.END, explanation)

        suggestions_box.bind("<<ListboxSelect>>", self._safe_call(on_select))

        button_frame = ttk.Frame(win)
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 10))

        ttk.Button(button_frame, text="Reload Suggestions", command=self._safe_call(load_suggestions)).pack(
            side=tk.LEFT
        )

        load_suggestions()

    def _open_weekly_plan_window(self) -> None:
        """
        Button: Build a weekly plan, persist to shopping list, and show summary.
        """
        win = tk.Toplevel(self)
        win.title("Weekly Plan")
        win.geometry("700x500")

        ttk.Label(win, text="Weekly Plan (backend only)", font=("Segoe UI", 11, "bold")).pack(
            side=tk.TOP, anchor="w", padx=10, pady=10
        )

        summary_box = ScrolledText(win, state=tk.NORMAL)
        summary_box.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        def build_plan():
            summary_box.delete("1.0", tk.END)
            self._log("Building weekly plan (6 recipes, added to shopping list)...")

            plan = self.weekly_planner_service.build_weekly_plan(
                num_recipes=6,
                target_ingredients=None,
                recently_used_recipe_ids=None,
                persist_to_shopping_list=True,
                planned_store_id=None,
                added_by="weekly_planner_ui",
            )

            lines = summarize_weekly_plan(plan)
            for line in lines:
                summary_box.insert(tk.END, line + "\n")

            summary_box.insert(tk.END, "\nIngredients:\n")
            for ing in plan.planned_ingredients:
                summary_box.insert(
                    tk.END,
                    f" - {ing.name} (in {ing.approximate_count} recipes, item_id={ing.item_id})\n",
                )

        ttk.Button(win, text="Build Weekly Plan", command=self._safe_call(build_plan)).pack(
            side=tk.BOTTOM, pady=5
        )

        # Optionally build immediately on open:
        build_plan()


def main() -> None:
    app = GrocerySenseApp()
    app.mainloop()


if __name__ == "__main__":
    main()
