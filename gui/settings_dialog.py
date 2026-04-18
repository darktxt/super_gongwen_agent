from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .env_config import GUI_CONFIG_KEYS, read_gui_config_values, write_gui_config_values


class SettingsDialog:
    def __init__(
        self,
        master: tk.Misc,
        *,
        on_apply: Callable[[dict[str, str]], None],
    ) -> None:
        self._master = master
        self._on_apply = on_apply
        self._window = tk.Toplevel(master)
        self._window.title("设置")
        self._window.resizable(False, False)
        self._window.transient(master)
        self._window.grab_set()

        self._show_api_key = tk.BooleanVar(value=False)
        self._values = read_gui_config_values()
        self._entries: dict[str, ttk.Entry] = {}
        self._string_vars = {
            key: tk.StringVar(value=self._values.get(key, "")) for key in GUI_CONFIG_KEYS
        }

        self._build()
        self._window.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self) -> None:
        container = ttk.Frame(self._window, padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(1, weight=1)

        field_labels = {
            "LITELLM_MODEL": "LITELLM_MODEL（必填）",
            "LITELLM_API_KEY": "LITELLM_API_KEY（按需）",
            "LITELLM_BASE_URL": "LITELLM_BASE_URL（按需）",
        }

        for row_index, key in enumerate(GUI_CONFIG_KEYS):
            ttk.Label(container, text=field_labels[key]).grid(
                row=row_index,
                column=0,
                sticky="w",
                padx=(0, 12),
                pady=6,
            )
            entry = ttk.Entry(
                container,
                textvariable=self._string_vars[key],
                width=42,
                show="*" if key == "LITELLM_API_KEY" else "",
            )
            entry.grid(row=row_index, column=1, sticky="ew", pady=6)
            self._entries[key] = entry

            if key == "LITELLM_API_KEY":
                toggle = ttk.Checkbutton(
                    container,
                    text="显示",
                    variable=self._show_api_key,
                    command=self._toggle_api_key_visibility,
                )
                toggle.grid(row=row_index, column=2, sticky="w", padx=(8, 0))

        button_row = ttk.Frame(container)
        button_row.grid(row=len(GUI_CONFIG_KEYS), column=0, columnspan=3, sticky="e", pady=(14, 0))

        ttk.Button(button_row, text="保存", command=self._save_only).grid(
            row=0,
            column=0,
            padx=(0, 8),
        )
        ttk.Button(button_row, text="保存并应用", command=self._save_and_apply).grid(
            row=0,
            column=1,
            padx=(0, 8),
        )
        ttk.Button(button_row, text="关闭", command=self.close).grid(row=0, column=2)

    def _toggle_api_key_visibility(self) -> None:
        entry = self._entries["LITELLM_API_KEY"]
        entry.configure(show="" if self._show_api_key.get() else "*")

    def _collect_values(self) -> dict[str, str]:
        return {key: self._string_vars[key].get().strip() for key in GUI_CONFIG_KEYS}

    def _save_only(self) -> None:
        values = self._collect_values()
        path = write_gui_config_values(values)
        messagebox.showinfo("设置", f"已保存到 {path.name}", parent=self._window)

    def _save_and_apply(self) -> None:
        values = self._collect_values()
        write_gui_config_values(values)
        self._on_apply(values)
        messagebox.showinfo("设置", "已保存并应用", parent=self._window)
        self.close()

    def close(self) -> None:
        self._window.grab_release()
        self._window.destroy()
