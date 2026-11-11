import json
import os
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

# List of default apps. Apps added via script will be saved only in the JSON file.
# Example of how to set a default app:
# ("app name", "app-package")
DEFAULT_APPS = []

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "adb_gui_manager.json"
WINDOWS = os.name == "nt"
DEFAULT_ADB_NAME = "adb.exe" if WINDOWS else "adb"


# Base modal dialog window centered relative to its parent window.
class BaseCenteredDialog(tk.Toplevel):

    # Initialize the modal dialog, make it non-resizable and grab input focus.
    def __init__(self, master, title, width=430, height=180):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.result = None
        self._center_to_parent(width, height)
        self.bind("<Escape>", lambda e: self.destroy())

    # Compute and apply the dialog geometry so it appears centered over the parent window.
    def _center_to_parent(self, width, height):
        self.update_idletasks()
        parent_x = self.master.winfo_rootx()
        parent_y = self.master.winfo_rooty()
        parent_w = self.master.winfo_width()
        parent_h = self.master.winfo_height()
        x = parent_x + max((parent_w - width) // 2, 0)
        y = parent_y + max((parent_h - height) // 2, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")


# Dialog for entering a new app's display name and package name to add it to the list.
class AddAppDialog(BaseCenteredDialog):

    # Build the form fields (name, package) and action buttons for adding an app.
    def __init__(self, master):
        super().__init__(master, "Add app", 470, 190)
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="App name:").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        self.name_var = tk.StringVar()
        name_entry = ttk.Entry(frame, textvariable=self.name_var, width=40)
        name_entry.grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(frame, text="Package name:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        self.package_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.package_var, width=40).grid(row=1, column=1, sticky="ew", pady=(0, 8))

        actions = ttk.Frame(frame)
        actions.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="Save", command=self.on_save).pack(side="right")

        self.bind("<Return>", lambda e: self.on_save())
        self.after(80, name_entry.focus_set)

    # Validate input fields and store the result tuple (name, package) before closing.
    def on_save(self):
        name = self.name_var.get().strip()
        package = self.package_var.get().strip()
        if not name or not package:
            messagebox.showwarning("Missing data", "Add both the app name and the package name.", parent=self)
            return
        self.result = (name, package)
        self.destroy()


# Dialog listing existing apps so the user can select one to delete.
class RemoveAppDialog(BaseCenteredDialog):

    # Populate the listbox with existing apps and set up delete/cancel actions.
    def __init__(self, master, apps):
        super().__init__(master, "Delete app", 430, 300)
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Select the app you want to delete:").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.listbox = tk.Listbox(frame, height=10, exportselection=False)
        self.listbox.grid(row=1, column=0, sticky="nsew")
        for app in apps:
            self.listbox.insert("end", f"{app['name']} | {app['package']}")
        if apps:
            self.listbox.selection_set(0)
            self.listbox.activate(0)

        actions = ttk.Frame(frame)
        actions.grid(row=2, column=0, sticky="e", pady=(10, 0))
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="Delete", command=self.on_delete).pack(side="right")

        self.bind("<Return>", lambda e: self.on_delete())
        self.after(80, self.listbox.focus_set)

    # Confirm a selection exists and store the selected index before closing.
    def on_delete(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("Missing selection", "Select an app to delete.", parent=self)
            return
        self.result = sel[0]
        self.destroy()


class AdbGui(tk.Tk):

    # Main application window: initialize state, load config/apps, build the UI,
    # and schedule startup tasks (adb/scrcpy detection, device refresh).
    def __init__(self):
        super().__init__()
        self.title("ADB Manager")
        self.geometry("1420x770")
        self.minsize(1550, 660)
        self.configure(bg="#f3f4f6")

        self.config_data = self._load_config()
        self.apps = self._load_apps()
        self.apk_paths = {app['name']: tk.StringVar(value="") for app in self.apps}
        self.devices = {}
        self.device_options = []
        self.selected_device = tk.StringVar(value="")
        self.device_summary = tk.StringVar(value="No devices detected")
        self.device_status = tk.StringVar(value="No device selected")
        self.adb_path = ""
        self.scrcpy_path = None
        self.adb_status = tk.StringVar(value="ADB: not configured")
        self.scrcpy_status = tk.StringVar(value="scrcpy: inactive")
        self.scrcpy_process = None
        self.scrcpy_device_serial = None
        self.active_scroll_target = None
        self.app_rows = []

        self.columns = [
            {"title": "App", "ratio": 0.13, "min": 120},
            {"title": "Package", "ratio": 0.23, "min": 210},
            {"title": "APK / ZIP Path", "ratio": 0.28, "min": 220, "max": 430},
            {"title": "Select file", "ratio": 0.13, "min": 130},
            {"title": "Actions", "ratio": 0.23, "min": 320},
        ]

        self._configure_style()
        self._build_header()
        self._build_toolbar()
        self._build_main_area()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.after(100, self._startup_tasks)
        self.after(150, self._apply_column_widths)

    # Configure ttk widget styles (colors, fonts, padding) for a consistent theme.
    def _configure_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Header.TFrame", background="#1f2937")
        style.configure("HeaderTitle.TLabel", background="#1f2937", foreground="#ffffff", font=("Segoe UI", 16, "bold"))
        style.configure("HeaderSub.TLabel", background="#1f2937", foreground="#d1d5db", font=("Segoe UI", 9))
        style.configure("Section.TLabelframe", background="#ffffff", borderwidth=1, relief="solid")
        style.configure("Section.TLabelframe.Label", background="#ffffff", foreground="#111827", font=("Segoe UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 9, "bold"))
        style.configure("HeadCell.TLabel", background="#e5e7eb", foreground="#111827", font=("Segoe UI", 9, "bold"), padding=(8, 7))
        style.configure("Cell.TLabel", background="#ffffff", foreground="#111827", font=("Segoe UI", 9), padding=(8, 8))
        style.configure("MutedCell.TLabel", background="#ffffff", foreground="#4b5563", font=("Segoe UI", 9), padding=(8, 8))

    # Build the top header bar with the app title and description.
    def _build_header(self):
        header = ttk.Frame(self, style="Header.TFrame", padding=(16, 12))
        header.pack(fill="x")
        left = ttk.Frame(header, style="Header.TFrame")
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="ADB Manager", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(left, text="Script for managing ADB commands.", style="HeaderSub.TLabel").pack(anchor="w", pady=(2, 0))

    # Build the toolbar with device selector, adb/scrcpy buttons, and status labels.
    def _build_toolbar(self):
        outer = ttk.Frame(self, padding=(10, 10, 10, 0), style="Card.TFrame")
        outer.pack(fill="x")
        top = ttk.LabelFrame(outer, text="Devices and Tools", style="Section.TLabelframe", padding=10)
        top.pack(fill="x")

        row1 = ttk.Frame(top, style="Card.TFrame")
        row1.pack(fill="x")
        ttk.Label(row1, text="Device:").pack(side="left", padx=(0, 6))
        self.device_combo = ttk.Combobox(row1, textvariable=self.selected_device, width=42, state="readonly", values=[])
        self.device_combo.pack(side="left", padx=(0, 8))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        ttk.Button(row1, text="Update device", style="Primary.TButton", command=self.refresh_devices).pack(side="left", padx=4)
        ttk.Button(row1, text="Find ADB", command=self.find_adb_manual).pack(side="left", padx=4)
        ttk.Button(row1, text="Open scrcpy", style="Primary.TButton", command=self.open_scrcpy).pack(side="left", padx=4)
        ttk.Button(row1, text="Find scrcpy", command=self.find_scrcpy_manual).pack(side="left", padx=4)
        ttk.Button(row1, text="Add app", command=self.add_app_dialog).pack(side="left", padx=4)
        ttk.Button(row1, text="Delete app", command=self.remove_app_dialog).pack(side="left", padx=4)
        ttk.Button(row1, text="Force-stop ALL", command=self.force_stop_all).pack(side="left", padx=4)
        ttk.Button(row1, text="Reboot", command=self.reboot_device).pack(side="left", padx=4)
        ttk.Button(row1, text="Restart ADB", command=self.restart_adb_server).pack(side="left", padx=4)
        ttk.Button(row1, text="Android Settings", command=self.open_android_settings).pack(side="left", padx=4)
        ttk.Button(row1, text="Clear logs", command=self.clear_log).pack(side="right", padx=4)

        row2 = ttk.Frame(top, style="Card.TFrame")
        row2.pack(fill="x", pady=(10, 0))
        tk.Label(row2, textvariable=self.device_summary, bg="#eef2ff", fg="#1e3a8a", padx=10, pady=6, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))
        tk.Label(row2, textvariable=self.device_status, bg="#eff6ff", fg="#1d4ed8", padx=10, pady=6, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))
        tk.Label(row2, textvariable=self.adb_status, bg="#fef3c7", fg="#92400e", padx=10, pady=6, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))
        tk.Label(row2, textvariable=self.scrcpy_status, bg="#ecfdf5", fg="#065f46", padx=10, pady=6, font=("Segoe UI", 9, "bold")).pack(side="left")

    # Build the main content area containing the app table and the log panel.
    def _build_main_area(self):
        main_wrap = ttk.Frame(self, padding=(10, 8, 10, 10), style="Card.TFrame")
        main_wrap.pack(fill="both", expand=True)
        main_wrap.rowconfigure(0, weight=3)
        main_wrap.rowconfigure(1, weight=2)
        main_wrap.columnconfigure(0, weight=1)
        self._build_app_section(main_wrap)
        self._build_log_section(main_wrap)
        self.bind("<Configure>", self._on_window_resize)
        self.bind_all("<MouseWheel>", self._on_global_mousewheel)

    # Build the scrollable applications table (header + canvas with rows).
    def _build_app_section(self, parent):
        section = ttk.LabelFrame(parent, text="Applications", style="Section.TLabelframe", padding=10)
        section.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        section.rowconfigure(2, weight=1)
        section.columnconfigure(0, weight=1)
        ttk.Label(section, text="Display the apps in alphabetical order for performing operations.", foreground="#6b7280").grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.app_header = ttk.Frame(section, style="Card.TFrame")
        self.app_header.grid(row=1, column=0, sticky="ew")
        self._build_app_header()

        self.app_scroll_host = tk.Frame(section, bg="#0f172a", bd=0, highlightthickness=0)
        self.app_scroll_host.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        self.app_scroll_host.grid_rowconfigure(0, weight=1)
        self.app_scroll_host.grid_columnconfigure(0, weight=1)

        self.app_canvas = tk.Canvas(self.app_scroll_host, bg="#ffffff", highlightthickness=0, bd=0)
        self.app_canvas.grid(row=0, column=0, sticky="nsew")
        self.app_scrollbar = tk.Scrollbar(self.app_scroll_host, orient="vertical", command=self.app_canvas.yview, bg="#0f172a", troughcolor="#0f172a", activebackground="#334155", highlightthickness=0, relief="flat", width=12)
        self.app_scrollbar.grid(row=0, column=1, sticky="ns")
        self.app_canvas.configure(yscrollcommand=self.app_scrollbar.set)

        self.app_body = ttk.Frame(self.app_canvas, style="Card.TFrame")
        self.app_window = self.app_canvas.create_window((0, 0), window=self.app_body, anchor="nw")
        self.app_body.bind("<Configure>", self._on_app_body_configure)
        self.app_canvas.bind("<Configure>", self._on_app_canvas_configure)
        self.app_canvas.bind("<Enter>", lambda e: self._set_active_scroll_target("apps"))
        self.app_canvas.bind("<Leave>", lambda e: self._set_active_scroll_target(None))
        self.refresh_app_table()

    # Build the read-only scrolled text widget used as the operations log.
    def _build_log_section(self, parent):
        section = ttk.LabelFrame(parent, text="Operations Log", style="Section.TLabelframe", padding=10)
        section.grid(row=1, column=0, sticky="nsew")
        section.rowconfigure(1, weight=1)
        section.columnconfigure(0, weight=1)
        ttk.Label(section, text="Dedicated independent log area. Text cannot be edited.", foreground="#6b7280").grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.log_widget = scrolledtext.ScrolledText(section, wrap="word", font=("Consolas", 10), bg="#0f172a", fg="#e5e7eb", insertbackground="#ffffff", relief="flat", padx=10, pady=10, state="disabled")
        self.log_widget.grid(row=1, column=0, sticky="nsew")
        self.log_widget.bind("<Enter>", lambda e: self._set_active_scroll_target("log"))
        self.log_widget.bind("<Leave>", lambda e: self._set_active_scroll_target(None))
        self.log_widget.bind("<Key>", lambda e: "break")

    # Load persisted settings (adb/scrcpy path, last device, app list) from JSON file.
    def _load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    # Persist current settings (adb/scrcpy path, last device, app list) to JSON file.
    def _save_config(self):
        self.config_data["adb_path"] = self.adb_path or ""
        self.config_data["scrcpy_path"] = self.scrcpy_path or ""
        self.config_data["last_device"] = self.selected_device.get()
        self.config_data["apps"] = self.apps
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            self.log(f"Unable to save configuration: {exc}")

    # Load the app list from config, falling back to DEFAULT_APPS if missing/invalid.
    def _load_apps(self):
        raw = self.config_data.get("apps")
        if isinstance(raw, list) and raw:
            cleaned = []
            for item in raw:
                if isinstance(item, dict) and item.get("name") and item.get("package"):
                    cleaned.append({"name": item["name"], "package": item["package"]})
            if cleaned:
                return cleaned
        apps = [{"name": name, "package": package} for name, package in DEFAULT_APPS]
        self.config_data["apps"] = apps
        return apps

    # Return the app list sorted alphabetically by display name.
    def get_sorted_apps(self):
        return sorted(self.apps, key=lambda x: x['name'].lower())

    # Open the AddAppDialog, validate uniqueness, and append the new app to the list.
    def add_app_dialog(self):
        dialog = AddAppDialog(self)
        self.wait_window(dialog)
        if not dialog.result:
            return
        name, package = dialog.result
        if any(app['name'].lower() == name.lower() for app in self.apps):
            messagebox.showwarning("Duplicate", "An app with this name already exists.", parent=self)
            return
        if any(app['package'].lower() == package.lower() for app in self.apps):
            messagebox.showwarning("Duplicate", "An app with this package name already exists.", parent=self)
            return
        self.apps.append({"name": name, "package": package})
        self.apk_paths.setdefault(name, tk.StringVar(value=""))
        self._save_config()
        self.refresh_app_table()
        self.log(f"App added: {name} ({package})")

    # Open the RemoveAppDialog, confirm, and remove the selected app from the list.
    def remove_app_dialog(self):
        sorted_apps = self.get_sorted_apps()
        if not sorted_apps:
            messagebox.showinfo("Delete app", "There are no apps to delete.", parent=self)
            return
        dialog = RemoveAppDialog(self, sorted_apps)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        target = sorted_apps[dialog.result]
        if messagebox.askyesno("Confirm", f"Delete the app {target['name']}?", parent=self):
            self.apps = [app for app in self.apps if not (app['name'] == target['name'] and app['package'] == target['package'])]
            self.apk_paths.pop(target['name'], None)
            self._save_config()
            self.refresh_app_table()
            self.log(f"Deleted app: {target['name']} ({target['package']})")

    # Rebuild all app rows in the scrollable table from the current app list.
    def refresh_app_table(self):
        for child in self.app_body.winfo_children():
            child.destroy()

        self.app_rows = []
        apps = self.get_sorted_apps()

        for idx, app in enumerate(apps):
            self.apk_paths.setdefault(app["name"], tk.StringVar(value=""))
            row = self._add_app_row(idx, app["name"], app["package"])
            self.app_rows.append(row)

            if idx < len(apps) - 1:
                sep = ttk.Separator(self.app_body, orient="horizontal")
                sep.grid(row=idx * 2 + 1, column=0, sticky="ew", pady=(0, 4))

    # Recalculate the canvas scroll region based on content and viewport height.
    def refresh_app_scrollregion(self):
        bbox = self.app_canvas.bbox("all")
        if not bbox:
            self.app_canvas.configure(scrollregion=(0, 0, 0, 0))
            return

        x1, y1, x2, y2 = bbox
        content_height = max(y2 - y1, 1)
        canvas_height = max(self.app_canvas.winfo_height(), 1)
        final_height = max(content_height, canvas_height)

        self.app_canvas.configure(scrollregion=(0, 0, x2, final_height))

    # Handle resize events for the app body/canvas/window to keep layout responsive.
    def _on_app_body_configure(self, _event=None):
        self.after_idle(self.refresh_app_scrollregion)

    # Track which scrollable widget (apps table or log) currently has mouse focus.
    def _set_active_scroll_target(self, target):
        self.active_scroll_target = target

    # Route mouse wheel events to the app table or the log widget depending on hover.
    def _on_global_mousewheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget == self.app_canvas:
                bbox = self.app_canvas.bbox("all")
                if bbox and (bbox[3] - bbox[1]) > self.app_canvas.winfo_height():
                    self.app_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return
            if widget == self.log_widget:
                self.log_widget.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return
            widget = widget.master

    # Check whether a widget is a descendant of a given ancestor widget in the tree.
    def _is_descendant(self, widget, ancestor):
        while widget is not None:
            if widget == ancestor:
                return True
            widget = widget.master
        return False

    # Handle resize events for the app body/canvas/window to keep layout responsive.
    def _on_window_resize(self, _event=None):
        self.after_idle(self._apply_column_widths)
        self.after_idle(self.refresh_app_scrollregion)

    # Handle resize events for the app body/canvas/window to keep layout responsive.
    def _on_app_canvas_configure(self, event):
        self.app_canvas.itemconfigure(self.app_window, width=event.width)
        self.after_idle(self._apply_column_widths)
        self.after_idle(self.refresh_app_scrollregion)

    # Compute column widths proportionally, respecting min/max constraints and distributing extra space.
    def _calculate_widths(self):
        total_width = max(self.app_canvas.winfo_width() - 12, 1)
        mins = [col['min'] for col in self.columns]
        sum_mins = sum(mins)

        if total_width < sum_mins:
            scale = total_width / sum_mins
            widths = [max(int(m * scale), 40) for m in mins]
            return widths

        widths = []
        for col in self.columns:
            width = max(int(total_width * col['ratio']), col['min'])
            if 'max' in col:
                width = min(width, col['max'])
            widths.append(width)
        used = sum(widths)
        extra = max(total_width - used, 0)
        distribution_order = [4, 3, 1, 0]
        distribution_weights = {4: 0.55, 3: 0.20, 1: 0.15, 0: 0.10}
        assigned = 0
        for idx in distribution_order[:-1]:
            add = int(extra * distribution_weights[idx])
            widths[idx] += add
            assigned += add
        widths[distribution_order[-1]] += max(extra - assigned, 0)
        return widths

    # Apply computed column widths to the header and all app rows.
    def _apply_column_widths(self):
        widths = self._calculate_widths()
        for col, width in enumerate(widths):
            self.app_header.grid_columnconfigure(col, minsize=width, weight=0)
        for row in self.app_rows:
            for col, width in enumerate(widths):
                row.grid_columnconfigure(col, minsize=width, weight=0)
            row.grid_columnconfigure(2, weight=1)

    # Render the column header labels for the applications table.
    def _build_app_header(self):
        for child in self.app_header.winfo_children():
            child.destroy()
        for idx, col in enumerate(self.columns):
            lbl = ttk.Label(self.app_header, text=col['title'], style="HeadCell.TLabel", anchor="w")
            lbl.grid(row=0, column=idx, sticky="ew", padx=(0, 6) if idx < len(self.columns) - 1 else 0)

    # Build a single table row with app name, package, path entry, browse button, and action buttons.
    def _add_app_row(self, row_index, name, package):
        row = ttk.Frame(self.app_body, style="Card.TFrame")
        row.grid(row=row_index * 2, column=0, sticky="ew", pady=3)

        ttk.Label(row, text=name, style="Cell.TLabel", anchor="w").grid(
            row=0, column=0, sticky="nsew", padx=(0, 6)
        )
        ttk.Label(row, text=package, style="MutedCell.TLabel", anchor="w").grid(
            row=0, column=1, sticky="nsew", padx=(0, 6)
        )

        entry_wrap = ttk.Frame(row, style="Card.TFrame")
        entry_wrap.grid(row=0, column=2, sticky="nsew", padx=(0, 6))
        entry_wrap.columnconfigure(0, weight=1)
        ttk.Entry(entry_wrap, textvariable=self.apk_paths[name], font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="ew", ipady=5
        )

        browse_wrap = ttk.Frame(row, style="Card.TFrame")
        browse_wrap.grid(row=0, column=3, sticky="nsew", padx=(0, 6))
        browse_wrap.columnconfigure(0, weight=1)
        ttk.Button(browse_wrap, text="Browse...", command=lambda n=name: self.browse_apk(n)).grid(
            row=0, column=0, sticky="ew"
        )

        actions_wrap = ttk.Frame(row, style="Card.TFrame")
        actions_wrap.grid(row=0, column=4, sticky="nsew")
        for idx in range(3):
            actions_wrap.columnconfigure(idx, weight=1)

        ttk.Button(actions_wrap, text="Install", command=lambda n=name, p=package: self.install_app(n, p)).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(actions_wrap, text="Force-stop", command=lambda p=package: self.force_stop(p)).grid(
            row=0, column=1, sticky="ew", padx=2
        )
        ttk.Button(actions_wrap, text="Uninstall", command=lambda p=package: self.uninstall(p)).grid(
            row=0, column=2, sticky="ew", padx=(4, 0)
        )

        return row

    # Thread-safe append of a message line to the read-only log widget.
    def log(self, msg):
        def _append():
            if not self.winfo_exists():
                return
            self.log_widget.configure(state="normal")
            self.log_widget.insert("end", str(msg) + "\n")
            self.log_widget.see("end")
            self.log_widget.configure(state="disabled")
        self.after(0, _append)

    # Clear all text from the log widget.
    def clear_log(self):
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

    # Extract the device serial number from the combobox selection string.
    def get_selected_serial(self):
        value = self.selected_device.get().strip()
        if not value:
            return None
        return value.split(" | ", 1)[0].strip()

    # Validate that a device is selected and currently online before proceeding.
    def ensure_device_selected(self):
        serial = self.get_selected_serial()
        if not serial:
            messagebox.showwarning("Device", "Select a device from the list.")
            return None
        info = self.devices.get(serial)
        if not info:
            messagebox.showwarning("Device", "The selected device is no longer available. Refresh the list.")
            return None
        if info["state"] != "device":
            messagebox.showwarning("Device", f"The {serial} device is not online: status = {info['state']}")
            return None
        return serial

    # Execute an adb command synchronously, logging output/errors, with timeout handling.
    def run_adb(self, args, serial=None, timeout=120, log_command=True):
        adb_executable = self.get_adb_command()
        cmd = [adb_executable]
        if serial:
            cmd += ["-s", serial]
        cmd += list(args)
        if log_command:
            self.log("> " + " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            if out:
                self.log(out)
            if err:
                self.log(err)
            return result.returncode == 0, out, err
        except FileNotFoundError:
            self.log("ERROR: ADB executable not found. Set ADB_PATH or add adb to your PATH.")
            return False, "", "adb not found"
        except subprocess.TimeoutExpired:
            self.log("ERROR: ADB command timeout.")
            return False, "", "timeout"
        except Exception as exc:
            self.log(f"ERROR adb: {exc}")
            return False, "", str(exc)

    # Run an adb command in a background thread to avoid blocking the UI.
    def run_adb_async(self, args, serial=None, timeout=120):
        threading.Thread(target=self.run_adb, args=(args, serial, timeout, True), daemon=True).start()

    # Trigger an adb server restart (kill-server + start-server) in a background
    # thread, then refresh the device list once the server is back up.
    def restart_adb_server(self):
        self.log("Restart ADB server request (kill-server + start-server)...")
        threading.Thread(target=self._restart_adb_server_worker, daemon=True).start()

    # Background worker: runs kill-server and start-server sequentially (no
    # specific device serial needed, these commands are global to the adb server),
    # then triggers a device list refresh back on the main thread.
    def _restart_adb_server_worker(self):
        self.run_adb(["kill-server"], log_command=True)
        self.run_adb(["start-server"], log_command=True)
        self.after(0, lambda: self.refresh_devices(log_result=True))

    # Parse the output of "adb devices -l" into a dict of serial -> {state, details}.
    def parse_devices_output(self, output):
        devices = {}
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices attached"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            state = parts[1]
            details = " ".join(parts[2:]) if len(parts) > 2 else ""
            devices[serial] = {"state": state, "details": details}
        return devices

    # Query connected devices via adb, update the device list/combobox, and log changes.
    def refresh_devices(self, log_result=True):
        ok, out, err = self.run_adb(["devices", "-l"], timeout=30, log_command=log_result)
        if not ok and not out:
            self._update_adb_status(False)
            self.devices = {}
            self.device_options = []
            self.device_combo["values"] = []
            self.selected_device.set("")
            self.device_summary.set("ADB is not available or there is no output")
            self.device_status.set("No device selected")
            return
        self._update_adb_status(True)
        previous_devices = self.devices.copy()
        self.devices = self.parse_devices_output(out)
        self.device_options = []
        for serial, info in self.devices.items():
            label = f"{serial} | {info['state']}"
            if info['details']:
                label += f" | {info['details']}"
            self.device_options.append(label)
        self.device_combo["values"] = self.device_options
        previous_serial = self.config_data.get("last_device") or self.get_selected_serial()
        if previous_serial and previous_serial in self.devices:
            for label in self.device_options:
                if label.startswith(previous_serial + " |"):
                    self.selected_device.set(label)
                    break
        elif self.device_options and not self.selected_device.get():
            self.selected_device.set(self.device_options[0])
        online = sum(1 for d in self.devices.values() if d['state'] == 'device')
        offline = sum(1 for d in self.devices.values() if d['state'] != 'device')
        self.device_summary.set(f"Total devices: {len(self.devices)} | online: {online} | not ready: {offline}")
        self._update_selected_device_status()
        if log_result:
            changed = []
            for serial, info in self.devices.items():
                old = previous_devices.get(serial)
                if old != info:
                    changed.append(f"- {serial}: {info['state']} {info['details']}")
            removed = [serial for serial in previous_devices if serial not in self.devices]
            if changed or removed:
                self.log("Update device:")
                for row in changed:
                    self.log(row)
                for serial in removed:
                    self.log(f"- {serial}: removed")
            elif not self.devices:
                self.log("No devices detected.")

    # Update the status label text based on the currently selected device's state.
    def _update_selected_device_status(self):
        serial = self.get_selected_serial()
        if not serial:
            self.device_status.set("No device selected")
            return
        info = self.devices.get(serial)
        if not info:
            self.device_status.set(f"Selected: {serial} | status: not available")
            return
        status_map = {"device": "online", "offline": "offline", "unauthorized": "unauthorized"}
        state = status_map.get(info['state'], info['state'])
        self.device_status.set(f"Selected: {serial} | status: {state}")

    # Handle device selection change: update status label and persist the choice.
    def _on_device_selected(self, _event=None):
        self._update_selected_device_status()
        self._save_config()

    # Perform initial detection of adb/scrcpy and load the device list at app startup.
    def _startup_tasks(self):
        self.adb_path = self.find_adb()
        self._update_adb_status()
        if self.adb_path:
            self.log(f"ADB found: {self.adb_path}")
        else:
            self.log("ADB was not found automatically. Use 'Find ADB'.")
        self.scrcpy_path = self.find_scrcpy()
        if self.scrcpy_path:
            self.scrcpy_status.set("scrcpy ready")
            self.log(f"scrcpy founded: {self.scrcpy_path}")
        else:
            self.scrcpy_status.set("scrcpy not found")
            self.log("scrcpy was not found automatically.")
        self.refresh_devices(log_result=False)
        self._save_config()

    # Open a file picker to select an APK or ZIP file for a given app.
    def browse_apk(self, app_name):
        path = filedialog.askopenfilename(title=f"Select APK or ZIP for {app_name}", filetypes=[("APK o ZIP", "*.apk *.zip"), ("All files", "*.*")])
        if path:
            self.apk_paths[app_name].set(path)

    # Extract an APK file from a ZIP archive, preferring one matching the package hint.
    def _extract_apk_from_zip(self, zip_path, package_hint=""):
        try:
            with zipfile.ZipFile(zip_path, "r") as zfile:
                apk_names = [n for n in zfile.namelist() if n.lower().endswith(".apk")]
                if not apk_names:
                    self.log(f"No .apk files found inside {zip_path}")
                    return None
                chosen = apk_names[0]
                if package_hint:
                    matches = [n for n in apk_names if package_hint.split(".")[-1].lower() in n.lower()]
                    if matches:
                        chosen = matches[0]
                tmpdir = tempfile.mkdtemp(prefix="adb_gui_extract_")
                extracted_path = zfile.extract(chosen, tmpdir)
                self.log(f"Excerpt {chosen} in {extracted_path}")
                return extracted_path
        except zipfile.BadZipFile:
            self.log(f"ERROR: {zip_path} is not a valid ZIP file.")
            return None
        except Exception as exc:
            self.log(f"ZIP extraction error: {exc}")
            return None

    # Resolve a raw path (file, ZIP, or folder) into a concrete installable APK path.
    def _resolve_apk_path(self, raw_path, package):
        if not raw_path:
            self.log("ERROR: no APK/ZIP path selected.")
            return None
        if os.path.isdir(raw_path):
            candidates = []
            for root, _, files in os.walk(raw_path):
                for filename in files:
                    full = os.path.join(root, filename)
                    if filename.lower().endswith(".apk"):
                        candidates.append(full)
                    elif filename.lower().endswith(".zip"):
                        extracted = self._extract_apk_from_zip(full, package)
                        if extracted:
                            candidates.append(extracted)
            if not candidates:
                self.log(f"No APKs found in the folder {raw_path}")
                return None
            return candidates[0]
        if raw_path.lower().endswith(".zip"):
            return self._extract_apk_from_zip(raw_path, package)
        if raw_path.lower().endswith(".apk"):
            return raw_path
        self.log("ERROR: invalid path (must be .apk, .zip, or a folder).")
        return None

    # Resolve the APK path and asynchronously run "adb install" on the selected device.
    def install_app(self, app_name, package):
        serial = self.ensure_device_selected()
        if not serial:
            return
        raw_path = self.apk_paths[app_name].get().strip()
        apk_path = self._resolve_apk_path(raw_path, package)
        if not apk_path:
            return
        self.run_adb_async(["install", "-r", "-g", "-t", apk_path], serial=serial)

    # Asynchronously force-stop a single package on the selected device.
    def force_stop(self, package):
        serial = self.ensure_device_selected()
        if not serial:
            return
        self.run_adb_async(["shell", "am", "force-stop", package], serial=serial)

    # Asynchronously force-stop every app in the managed list on the selected device.
    def force_stop_all(self):
        serial = self.ensure_device_selected()
        if not serial:
            return
        for app in self.apps:
            self.run_adb_async(["shell", "am", "force-stop", app['package']], serial=serial)

    # Confirm and asynchronously reboot the selected device via adb.
    def reboot_device(self):
        serial = self.ensure_device_selected()
        if not serial:
            return
        if messagebox.askyesno("Confirm", f"Reboot device {serial}?", parent=self):
            self.run_adb_async(["reboot"], serial=serial)

    # Open the Android Settings screen on the selected device.
    def open_android_settings(self):
        serial = self.ensure_device_selected()
        if not serial:
            return
        self.run_adb_async(["shell", "am", "start", "-a", "android.settings.SETTINGS"], serial=serial)

    # Confirm and asynchronously uninstall a package from the selected device.
    def uninstall(self, package):
        serial = self.ensure_device_selected()
        if not serial:
            return
        if messagebox.askyesno("Confirm", f"Uninstall {package} from the selected device?", parent=self):
            self.run_adb_async(["uninstall", package], serial=serial)

    # Update the ADB status label (ready / invalid path / not configured).
    def _update_adb_status(self, available=None):
        if available is None:
            available = bool(self.adb_path and os.path.isfile(self.adb_path))
        if available:
            self.adb_status.set("ADB: ready")
        elif self.adb_path:
            self.adb_status.set("ADB: path invalid")
        else:
            self.adb_status.set("ADB: not configured")

    # Return the configured adb executable path, or fall back to the default command name.
    def get_adb_command(self):
        return self.adb_path if self.adb_path and os.path.isfile(self.adb_path) else DEFAULT_ADB_NAME

    # Search common system locations and PATH for the adb executable.
    def find_adb(self):
        configured = self.config_data.get("adb_path", "")
        if configured and os.path.isfile(configured):
            return configured

        candidate_names = ["adb.exe"] if WINDOWS else ["adb"]
        search_dirs = []
        exe_dir = Path(__file__).resolve().parent
        search_dirs.extend([exe_dir, Path.cwd()])

        for env_part in os.environ.get("PATH", "").split(os.pathsep):
            if env_part.strip():
                search_dirs.append(Path(env_part.strip()))

        home = Path.home()
        if WINDOWS:
            for key in ["ProgramFiles", "ProgramFiles(x86)", "LocalAppData"]:
                val = os.environ.get(key)
                if val:
                    search_dirs.append(Path(val))
            search_dirs.extend([
                home / "Desktop",
                home / "Downloads",
                Path("C:/platform-tools"),
                Path("C:/adb"),
                Path("C:/tools"),
            ])
        else:
            search_dirs.extend([Path("/usr/bin"), Path("/usr/local/bin"), home / ".local" / "bin"])

        seen = set()
        unique_dirs = []
        for d in search_dirs:
            try:
                resolved = str(d)
            except Exception:
                continue
            if resolved not in seen:
                seen.add(resolved)
                unique_dirs.append(d)

        for directory in unique_dirs:
            for name in candidate_names:
                direct = directory / name
                if direct.is_file():
                    self.adb_path = str(direct)
                    self._save_config()
                    return self.adb_path
        return ""

    # Let the user manually pick the adb executable via a file dialog.
    def find_adb_manual(self):
        selected = filedialog.askopenfilename(
            title="Select adb",
            filetypes=[("ADB executable", "adb.exe" if WINDOWS else "adb"), ("All files", "*.*")],
        )
        if not selected:
            return
        if not os.path.isfile(selected):
            messagebox.showerror("ADB", "The selected file does not exist.", parent=self)
            return
        name_ok = os.path.basename(selected).lower() in {"adb", "adb.exe"}
        if not name_ok:
            if not messagebox.askyesno("ADB", "The selected file is not named adb/adb.exe. Use it anyway?", parent=self):
                return
        self.adb_path = selected
        self._update_adb_status(True)
        self._save_config()
        self.log(f"ADB set manually: {selected}")
        self.refresh_devices(log_result=False)

    # Search common system locations and PATH for the scrcpy executable.
    def find_scrcpy(self):
        configured = self.config_data.get("scrcpy_path", "")
        if configured and os.path.isfile(configured):
            return configured
        candidate_names = ["scrcpy.exe"] if WINDOWS else ["scrcpy"]
        search_dirs = []
        exe_dir = Path(__file__).resolve().parent
        search_dirs.extend([exe_dir, Path.cwd()])
        for env_part in os.environ.get("PATH", "").split(os.pathsep):
            if env_part.strip():
                search_dirs.append(Path(env_part.strip()))
        home = Path.home()
        if WINDOWS:
            for key in ["ProgramFiles", "ProgramFiles(x86)", "LocalAppData"]:
                val = os.environ.get(key)
                if val:
                    search_dirs.append(Path(val))
            search_dirs.extend([home / "Desktop", home / "Downloads", home / "AppData" / "Local", Path("C:/scrcpy"), Path("C:/tools"), Path("C:/adb")])
        else:
            search_dirs.extend([Path("/usr/bin"), Path("/usr/local/bin"), home / ".local" / "bin"])
        seen = set()
        unique_dirs = []
        for d in search_dirs:
            try:
                resolved = str(d)
            except Exception:
                continue
            if resolved not in seen:
                seen.add(resolved)
                unique_dirs.append(d)
        for directory in unique_dirs:
            for name in candidate_names:
                direct = directory / name
                if direct.is_file():
                    self.scrcpy_path = str(direct)
                    self._save_config()
                    return self.scrcpy_path
        return None

    # Let the user manually pick the scrcpy executable via a file dialog.
    def find_scrcpy_manual(self):
        path = filedialog.askopenfilename(title="Select scrcpy", filetypes=[("scrcpy", "scrcpy.exe" if WINDOWS else "scrcpy"), ("All files", "*.*")])
        if path:
            self.scrcpy_path = path
            self.scrcpy_status.set("scrcpy ready")
            self._save_config()
            self.log(f"scrcpy set manually: {path}")

    # Launch scrcpy for the selected device, or report if already running or missing.
    def open_scrcpy(self):
        serial = self.ensure_device_selected()
        if not serial:
            return
        if self.scrcpy_process and self.scrcpy_process.poll() is None:
            self.scrcpy_status.set(f"scrcpy is running on {self.scrcpy_device_serial}")
            messagebox.showinfo("scrcpy", "scrcpy is already running on the selected device.", parent=self)
            return
        if not self.scrcpy_path or not os.path.isfile(self.scrcpy_path):
            self.scrcpy_path = self.find_scrcpy()
        if not self.scrcpy_path:
            messagebox.showerror("scrcpy", "not found. Use “Find scrcpy” to set it up manually.", parent=self)
            self.scrcpy_status.set("scrcpy not found")
            return
        cmd = [self.scrcpy_path, "-s", serial]
        self.log("> " + " ".join(cmd))
        try:
            self.scrcpy_process = subprocess.Popen(cmd)
            self.scrcpy_device_serial = serial
            self.scrcpy_status.set(f"scrcpy is running on {serial}")
            self._save_config()
            threading.Thread(target=self._watch_scrcpy_process, daemon=True).start()
        except Exception as exc:
            messagebox.showerror("scrcpy", f"Unable to start scrcpy:\n{exc}", parent=self)
            self.log(f"ERROR scrcpy: {exc}")

    # Background thread that waits for the scrcpy process to exit and updates status accordingly.
    def _watch_scrcpy_process(self):
        process = self.scrcpy_process
        serial = self.scrcpy_device_serial
        if not process:
            return
        process.wait()
        def _update_after_close():
            if self.scrcpy_process is process:
                self.scrcpy_process = None
                self.scrcpy_device_serial = None
                self.scrcpy_status.set("scrcpy is not active")
                if serial:
                    self.log(f"scrcpy closed on {serial}")
                else:
                    self.log("scrcpy closed")
        self.after(0, _update_after_close)

    # Terminate any running scrcpy process, save config, and close the application.
    def on_close(self):
        if self.scrcpy_process and self.scrcpy_process.poll() is None:
            try:
                self.scrcpy_process.terminate()
            except Exception:
                pass
        self._save_config()
        self.destroy()


if __name__ == "__main__":
    app = AdbGui()
    app.mainloop()
