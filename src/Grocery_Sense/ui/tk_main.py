"""
Grocery_Sense.ui.tk_main

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

from Grocery_Sense.data.schema import initialize_database

from Grocery_Sense.services.shopping_list_service import ShoppingListService
from Grocery_Sense.services.meal_suggestion_service import (
    MealSuggestionService,
    explain_suggested_meal,
)
from Grocery_Sense.services.weekly_planner_service import (
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

        # Services (repositories are used internally by the services / repo modules)
        self.shopping_list_service = ShoppingListService()
        self.meal_suggestion_service = MealSuggestionService(
            price_history_service=None  # wire real one later
        )
        self.weekly_planner_service = WeeklyPlannerService(
            meal_suggestion_service=self.meal_suggestion_service,
            shopping_list_service=self.shopping_list_service,
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
            text="4) Build Weekly Plan",
            command=self._safe_call(self._open_weekly_plan_window),
            width=35,
        ).grid(row=row, column=0, sticky="w", pady=2)

    def _build_log_panel(self) -> None:
        self.log_box = ScrolledText(self, state=tk.NORMAL, height=10)
        self.log_box.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, padx=10, pady=10)
        self._log("Log initialized.")

    def _log(self, message: str) -> None:
        self.log_box.insert(tk.END, message + "\n")
        self.log_box.see(tk.END)

    def _log_exception(self, prefix: str, exc: BaseException) -> None:
        self._log(prefix)
        tb = traceback.format_exc()
        self._log(tb)

    def _safe_call(self, func):
        def wrapper():
            try:
                func()
            except Exception as e:
                self._log_exception("ERROR:", e)
        return wrapper

    # ------------------------------------------------------------------
    # Handlers / windows
    # ------------------------------------------------------------------

    def _handle_init_db(self) -> None:
        initialize_database()
        self._log("Database schema initialized / verified.")

    def _open_shopping_list_window(self) -> None:
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
            items = self.shopping_list_service.get_active_items(include_checked_off=True)
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

        listbox = tk.Listbox(top_frame, width=35)
        listbox.grid(row=1, column=0, sticky="nsw", pady=10)

        details = ScrolledText(top_frame, state=tk.NORMAL)
        details.grid(row=1, column=1, sticky="nsew", padx=(10, 0), pady=10)

        top_frame.grid_columnconfigure(1, weight=1)
        top_frame.grid_rowconfigure(1, weight=1)

        suggestions = self.meal_suggestion_service.suggest_meals_for_week(max_recipes=10)

        for s in suggestions:
            name = s.recipe.get("name") or s.recipe.get("title") or "Recipe"
            listbox.insert(tk.END, name)

        def on_select(evt):
            idxs = listbox.curselection()
            if not idxs:
                return
            s = suggestions[idxs[0]]
            details.delete("1.0", tk.END)
            details.insert(tk.END, explain_suggested_meal(s))

        listbox.bind("<<ListboxSelect>>", on_select)

    def _open_weekly_plan_window(self) -> None:
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

        build_plan()


def main() -> None:
    app = GrocerySenseApp()
    app.mainloop()


if __name__ == "__main__":
    main()
