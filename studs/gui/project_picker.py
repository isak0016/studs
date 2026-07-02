"""GUI for creating, importing, and managing synced Ableton projects."""
from __future__ import annotations

import gzip
import io
import platform
import subprocess
import threading
import tkinter as tk
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import unquote
import xml.etree.ElementTree as ET

from studs.config import CONFIG_PATH, RCLONE_BIN, load_config, save_config
from studs.drive_api import remote_path_for_id, share_project_folder
from studs.sync import (
    GDRIVE_REMOTE_NAME,
    add_contributor,
    create_new_project,
    load_projects,
    pull_archive,
    push_archive,
    read_contributors,
    remote_archive_mtime,
    remote_archive_path_for,
    save_projects,
    upsert_project,
)
from studs.vst_scan.scanner import scan_installed_plugins

RCLONE_OAUTH_PORT = 53682  # rclone's default local OAuth redirect port


def _kill_stale_oauth_listener() -> None:
    """Frees rclone's OAuth callback port if a previous, never-completed
    GDrive Setup attempt left its local webserver running."""
    system = platform.system()
    if system == "Darwin" or system == "Linux":
        pids = subprocess.run(
            ["lsof", "-ti", f":{RCLONE_OAUTH_PORT}"], capture_output=True, text=True
        ).stdout.split()
        for pid in pids:
            name = subprocess.run(
                ["ps", "-p", pid, "-o", "comm="], capture_output=True, text=True
            ).stdout.strip()
            if "rclone" in name:
                subprocess.run(["kill", pid], capture_output=True)
    elif system == "Windows":
        netstat = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
        for line in netstat.splitlines():
            if f":{RCLONE_OAUTH_PORT} " not in line or "LISTENING" not in line:
                continue
            pid = line.split()[-1]
            tasklist = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True
            ).stdout
            if "rclone" in tasklist.lower():
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)


def ensure_config_exists() -> None:
    if not CONFIG_PATH.exists():
        save_config(
            {
                "projects": [],
                "username": "",
                "custom_vst_paths": {"vst2": [], "vst3": []},
            }
        )


def rclone_remote_exists(name: str) -> bool:
    if not name:
        return False
    result = subprocess.run([RCLONE_BIN, "listremotes"], capture_output=True, text=True)
    remotes = [r.strip().rstrip(":") for r in result.stdout.splitlines() if r.strip()]
    return name in remotes


def _looks_like_ableton_project(path: Path) -> bool:
    return any(path.rglob("*.als"))


def _parse_als_info(data: bytes) -> dict:
    root = ET.fromstring(gzip.decompress(data))
    tracks = root.find(".//Tracks")
    track_count = len(tracks) if tracks is not None else 0

    # Plugin devices store their name either directly (VST2's PlugName) or only
    # in the browser path breadcrumb (VST3), e.g. "view:X-Plugins#Vendor:Name".
    vst_names = set()
    for device in root.iter("PluginDevice"):
        browser_path = device.find(".//BrowserContentPath")
        if browser_path is not None and browser_path.get("Value"):
            after_hash = browser_path.get("Value").split("#")[-1]
            name = after_hash.split(":")[-1] if ":" in after_hash else after_hash
            vst_names.add(unquote(name))
            continue
        plug_name = device.find(".//PlugName")
        if plug_name is not None and plug_name.get("Value"):
            vst_names.add(plug_name.get("Value"))

    return {"track_count": track_count, "vst_names": sorted(vst_names)}


def _local_als_info(project_dir: Path) -> dict | None:
    candidates = sorted(project_dir.glob("*.als"))
    if not candidates:
        return None
    return _parse_als_info(candidates[0].read_bytes())


def _remote_als_info(remote_archive_path: str) -> dict | None:
    # Pulls the whole (small, compressed) archive into memory rather than
    # making a separate Drive API call per file inside it.
    cat = subprocess.run([RCLONE_BIN, "cat", remote_archive_path], capture_output=True)
    if cat.returncode != 0:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(cat.stdout)) as zf:
            als_names = sorted(n for n in zf.namelist() if n.endswith(".als"))
            if not als_names:
                return None
            return _parse_als_info(zf.read(als_names[0]))
    except zipfile.BadZipFile:
        return None


class ProjectPickerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("studs")
        root.resizable(False, False)

        self.projects: list[dict] = load_projects()
        config = load_config()
        self.username_var = tk.StringVar(value=config.get("username", ""))
        custom_paths = config.get("custom_vst_paths", {})
        self.custom_vst2_paths: list[str] = list(custom_paths.get("vst2", []))
        self.custom_vst3_paths: list[str] = list(custom_paths.get("vst3", []))

        self._default_bg = root.cget("bg")
        self.banner_frame = tk.Frame(root, bg=self._default_bg)
        self.banner_frame.pack(fill="x")
        self.banner_message_label = tk.Label(
            self.banner_frame, text="", anchor="w", padx=10, pady=6, bg=self._default_bg
        )
        self.banner_message_label.pack(side="left", fill="x", expand=True)
        self.banner_close_label = tk.Label(
            self.banner_frame, text="", padx=10, cursor="hand2", bg=self._default_bg
        )
        self.banner_close_label.pack(side="right")
        self.banner_close_label.bind("<Button-1>", lambda event: self._clear_banner())

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.new_tab = tk.Frame(self.notebook)
        self.import_tab = tk.Frame(self.notebook)
        self.manage_tab = tk.Frame(self.notebook)
        self.settings_tab = tk.Frame(self.notebook)
        self.notebook.add(self.new_tab, text="new project")
        self.notebook.add(self.import_tab, text="import project")
        self.notebook.add(self.manage_tab, text="manage projects")
        self.notebook.add(self.settings_tab, text="settings")

        self._build_new_tab()
        self._build_import_tab()
        self._build_manage_tab()
        self._build_settings_tab()

        self._update_new_tab_gate()
        if rclone_remote_exists(GDRIVE_REMOTE_NAME):
            self.notebook.select(self.manage_tab)
        else:
            self.notebook.select(self.settings_tab)
            self._banner(
                "before you can create new projects, go to settings and click "
                "'gdrive setup' to connect your google account.",
                "error",
            )

    # -- shared -----------------------------------------------------------

    def _banner(self, message: str, kind: str = "success") -> None:
        """Shows a colored banner at the top instead of a blocking popup. Stays
        until dismissed via its 'x' or replaced by the next banner."""
        colors = {"success": ("#2e7d32", "white"), "error": ("#c62828", "white")}
        bg, fg = colors.get(kind, colors["error"])
        self.banner_frame.config(bg=bg)
        self.banner_message_label.config(text=message, bg=bg, fg=fg)
        self.banner_close_label.config(text="✕", bg=bg, fg=fg)

    def _clear_banner(self) -> None:
        self.banner_frame.config(bg=self._default_bg)
        self.banner_message_label.config(text="", bg=self._default_bg)
        self.banner_close_label.config(text="", bg=self._default_bg)

    def _run_async(self, work_fn, on_done) -> None:
        """Runs work_fn() in a background thread so the UI stays responsive,
        then marshals on_done(result) back onto the main thread."""

        def runner():
            result = work_fn()
            self.root.after(0, lambda: on_done(result))

        threading.Thread(target=runner, daemon=True).start()

    def _async_push_archive(
        self,
        local_folder: str,
        remote_archive_path: str,
        status_label: tk.Label,
        on_success,
        button: tk.Button | None = None,
    ) -> None:
        """Zips the whole local folder and uploads it as one file — one Drive API
        call instead of one per file inside, which is what actually caused the
        rate-limit slowdowns with plain `rclone sync` on many-small-file projects."""
        status_label.config(text=f"packaging and uploading {local_folder}...", fg="gray")
        if button is not None:
            button.config(state="disabled")

        def work() -> dict:
            return push_archive(local_folder, remote_archive_path)

        def done(result: dict) -> None:
            if button is not None:
                button.config(state="normal")
            if result["ok"]:
                status_label.config(text="done.", fg="green")
                self._banner(f"pushed to {remote_archive_path}", "success")
                on_success(result["mtime"])
            else:
                status_label.config(text="failed.", fg="red")
                self._banner(f"push failed: {result['error'] or 'unknown error'}", "error")

        self._run_async(work, done)

    def _async_pull_archive(
        self,
        remote_archive_path: str,
        local_folder: str,
        status_label: tk.Label,
        on_success,
        button: tk.Button | None = None,
    ) -> None:
        status_label.config(text=f"downloading and unpacking to {local_folder}...", fg="gray")
        if button is not None:
            button.config(state="disabled")

        def work() -> dict:
            return pull_archive(remote_archive_path, local_folder)

        def done(result: dict) -> None:
            if button is not None:
                button.config(state="normal")
            if result["ok"]:
                status_label.config(text="done.", fg="green")
                self._banner(f"pulled from {remote_archive_path}", "success")
                on_success(result["mtime"])
            else:
                status_label.config(text="failed.", fg="red")
                self._banner(f"pull failed: {result['error'] or 'unknown error'}", "error")

        self._run_async(work, done)

    def _remember_project(
        self,
        path: str,
        code: str,
        role: str,
        last_pushed: str | None = None,
        last_pushed_by: str | None = None,
        display_name: str | None = None,
        synced_remote_mtime: str | None = None,
    ) -> None:
        self.projects = upsert_project(
            self.projects,
            path,
            code,
            role,
            last_pushed=last_pushed,
            last_pushed_by=last_pushed_by,
            display_name=display_name,
            synced_remote_mtime=synced_remote_mtime,
        )
        save_projects(self.projects)
        self._refresh_manage_list()

    def _share_folder(self, code: str, email: str, status_label: tk.Label) -> None:
        status_label.config(text=f"sharing with {email}...", fg="gray")
        self.root.update_idletasks()
        try:
            share_project_folder(GDRIVE_REMOTE_NAME, code, email)
        except RuntimeError as e:
            status_label.config(text="share failed.", fg="red")
            self._banner(f"share failed: {e}", "error")
            return
        status_label.config(text=f"shared with {email}.", fg="green")
        self._banner(f"{email} now has editor access to this project's folder.", "success")

    def _require_username(self) -> bool:
        if self.username_var.get().strip():
            return True
        self._banner("set your username in the settings tab first.", "error")
        return False

    def _update_new_tab_gate(self) -> None:
        configured = rclone_remote_exists(GDRIVE_REMOTE_NAME)
        self.notebook.tab(self.new_tab, state="normal" if configured else "disabled")

    # -- New Project tab ----------------------------------------------------

    def _build_new_tab(self) -> None:
        frame = self.new_tab
        self.new_path_var = tk.StringVar()
        self.new_code_var = tk.StringVar()  # set after push: the project's real Drive folder ID
        self.new_pushed = False

        tk.Label(
            frame,
            text="pick a local ableton project folder to start syncing:",
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        tk.Entry(frame, textvariable=self.new_path_var, width=50, state="readonly").grid(
            row=1, column=0, padx=(12, 4), pady=4, sticky="we"
        )
        ttk.Button(frame, text="browse...", command=self._new_browse).grid(
            row=1, column=1, padx=(4, 12), pady=4
        )

        self.new_status_label = tk.Label(frame, text="", fg="gray")
        self.new_status_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        tk.Label(frame, text="project name (optional — only changes how it's displayed in manage projects):").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 0)
        )
        self.new_display_name_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.new_display_name_var, width=50).grid(
            row=4, column=0, columnspan=2, padx=12, pady=4, sticky="we"
        )

        self.new_push_button = ttk.Button(
            frame, text="push to drive", width=16, command=self._new_push
        )
        self.new_push_button.grid(row=5, column=0, columnspan=2, pady=12)

        tk.Label(frame, text="sync code (appears after pushing — share it with your partner):").grid(
            row=6, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 0)
        )
        tk.Entry(frame, textvariable=self.new_code_var, width=50, state="readonly").grid(
            row=7, column=0, columnspan=2, padx=12, pady=4, sticky="we"
        )

        tk.Label(
            frame, text="partner's google account email (grants access to just this project):"
        ).grid(row=8, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 0))
        self.new_partner_email_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.new_partner_email_var, width=40).grid(
            row=9, column=0, padx=(12, 4), pady=4, sticky="we"
        )
        self.new_share_button = ttk.Button(frame, text="share with partner", command=self._new_share)
        self.new_share_button.grid(row=9, column=1, padx=(4, 12), pady=4)

        self._update_new_tab_buttons()

    def _update_new_tab_buttons(self) -> None:
        has_folder = bool(self.new_path_var.get()) and Path(self.new_path_var.get()).is_dir()
        self.new_push_button.config(state="normal" if has_folder else "disabled")
        self.new_share_button.config(state="normal" if self.new_pushed else "disabled")

    def _new_browse(self) -> None:
        selected = filedialog.askdirectory(
            title="select ableton project folder", initialdir=str(Path.home())
        )
        if not selected:
            return
        self.new_path_var.set(selected)
        self.new_pushed = False
        self.new_code_var.set("")
        self.new_display_name_var.set("")
        if _looks_like_ableton_project(Path(selected)):
            self.new_status_label.config(text="found .als project file(s).", fg="green")
        else:
            self.new_status_label.config(
                text="warning: no .als files found in this folder or its subfolders.",
                fg="#b8860b",
            )
        self._update_new_tab_buttons()

    def _new_push(self) -> None:
        path = self.new_path_var.get()
        if not path or not Path(path).is_dir():
            self._banner("please choose a folder first.", "error")
            return
        if not self._require_username():
            return

        add_contributor(Path(path), self.username_var.get().strip())

        self.new_status_label.config(text=f"packaging and uploading {path}...", fg="gray")
        self.new_push_button.config(state="disabled")
        username = self.username_var.get().strip()

        def work() -> dict:
            return create_new_project(path, username)

        self._run_async(work, lambda r: self._new_push_done(r, path))

    def _new_push_done(self, r: dict, path: str) -> None:
        if not r["ok"]:
            self.new_status_label.config(text="failed.", fg="red")
            self._banner(f"sync failed: {r['error'] or 'unknown error'}", "error")
            self._update_new_tab_buttons()
            return

        folder_id = r["sync_code"]
        self.new_status_label.config(text="done.", fg="green")
        self.new_code_var.set(folder_id)
        self.new_pushed = True
        self._remember_project(
            path,
            folder_id,
            "created",
            last_pushed=datetime.now(timezone.utc).isoformat(),
            last_pushed_by=self.username_var.get().strip(),
            display_name=self.new_display_name_var.get().strip() or None,
            synced_remote_mtime=r["mtime"],
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(folder_id)
        self._banner(
            f"pushed. sync code copied to clipboard: {folder_id} — "
            "send it to your partner, then share with partner below.",
            "success",
        )
        self._update_new_tab_buttons()

    def _new_share(self) -> None:
        code = self.new_code_var.get()
        email = self.new_partner_email_var.get().strip()
        if not self.new_pushed:
            self._banner("push this project to drive before sharing it.", "error")
            return
        if not email:
            self._banner("enter your partner's google account email.", "error")
            return
        self._share_folder(code, email, self.new_status_label)

    # -- Import Project tab ------------------------------------------------

    def _build_import_tab(self) -> None:
        frame = self.import_tab
        self.import_code_var = tk.StringVar()

        tk.Label(
            frame,
            text="enter the sync code your partner sent you:",
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        tk.Entry(frame, textvariable=self.import_code_var, width=50).grid(
            row=1, column=0, columnspan=2, padx=12, pady=4, sticky="we"
        )

        self.import_status_label = tk.Label(frame, text="", fg="gray")
        self.import_status_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        self.import_pull_button = ttk.Button(
            frame, text="pull from drive", width=16, command=self._import_pull
        )
        self.import_pull_button.grid(row=3, column=0, columnspan=2, pady=12)

    def _import_pull(self) -> None:
        code = self.import_code_var.get().strip()
        if not code:
            self._banner("enter a sync code first.", "error")
            return

        selected = filedialog.askdirectory(
            title="choose where to save this project locally", initialdir=str(Path.home())
        )
        if not selected:
            return

        def on_success(mtime) -> None:
            self._remember_project(selected, code, "imported", synced_remote_mtime=mtime)
            self.import_code_var.set("")

        self._async_pull_archive(
            remote_archive_path_for(code),
            selected,
            self.import_status_label,
            on_success,
            button=self.import_pull_button,
        )

    # -- Manage Projects tab ------------------------------------------------

    def _build_manage_tab(self) -> None:
        frame = self.manage_tab
        self.manage_stale: set[str] = set()  # paths where drive has a newer push than we have

        tk.Label(frame, text="your projects (select one):").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0)
        )
        self.manage_listbox = tk.Listbox(frame, height=8, width=60)
        self.manage_listbox.grid(row=1, column=0, columnspan=2, padx=12, pady=4, sticky="we")
        self.manage_listbox.bind("<<ListboxSelect>>", self._on_manage_select)

        self.manage_detail_label = tk.Label(frame, text="", justify="left", fg="gray")
        self.manage_detail_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        button_row = tk.Frame(frame)
        button_row.grid(row=3, column=0, columnspan=2, pady=8)
        self.manage_push_button = ttk.Button(
            button_row, text="push to drive", width=14, command=self._manage_push
        )
        self.manage_push_button.pack(side="left", padx=6)
        self.manage_pull_button = ttk.Button(
            button_row, text="pull from drive", width=14, command=self._manage_pull
        )
        self.manage_pull_button.pack(side="left", padx=6)
        ttk.Button(
            button_row, text="project info", command=self._manage_project_info
        ).pack(side="left", padx=6)

        self.manage_status_label = tk.Label(frame, text="", fg="gray")
        self.manage_status_label.grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        self.manage_info_text = tk.Text(
            frame,
            height=4,
            width=58,
            wrap="word",
            relief="flat",
            borderwidth=0,
            bg=self._default_bg,
            font=("TkDefaultFont", 10),
        )
        self.manage_info_text.grid(row=5, column=0, columnspan=2, sticky="we", padx=12, pady=4)
        self.manage_info_text.tag_configure("green", foreground="#2e7d32")
        self.manage_info_text.tag_configure("red", foreground="#c62828")
        self.manage_info_text.tag_configure("mismatch", foreground="#b8860b")
        self.manage_info_text.config(state="disabled")

        tk.Label(frame, text="add a collaborator's google email (access to just this project):").grid(
            row=6, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0)
        )
        self.manage_partner_email_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.manage_partner_email_var, width=40).grid(
            row=7, column=0, padx=(12, 4), pady=4, sticky="we"
        )
        ttk.Button(frame, text="share with partner", command=self._manage_share).grid(
            row=7, column=1, padx=(4, 12), pady=4
        )

        self._refresh_manage_list()

    def _render_manage_list(self) -> None:
        """Draws the listbox from what's already known — no network calls,
        safe to call anytime (e.g. right after a background freshness check)."""
        selected = self.manage_listbox.curselection()
        self.manage_listbox.delete(0, tk.END)
        for p in self.projects:
            name = p.get("display_name") or Path(p["path"]).name
            star = "* " if p["path"] in self.manage_stale else ""
            contributors = read_contributors(Path(p["path"])) if Path(p["path"]).is_dir() else []
            who = ", ".join(contributors) if contributors else "no contributors yet"
            self.manage_listbox.insert(tk.END, f"{star}{name}  —  {who}  ({p['role']})")
        if selected:
            self.manage_listbox.selection_set(selected[0])

    def _refresh_manage_list(self) -> None:
        self._render_manage_list()
        self._check_manage_freshness()

    def _check_manage_freshness(self) -> None:
        """Compares each project's stored synced_remote_mtime against Drive's
        current one — one lightweight metadata call per project, run in the
        background so opening/refreshing Manage Projects never blocks the UI."""
        projects_snapshot = list(self.projects)

        def work() -> set[str]:
            stale = set()
            for p in projects_snapshot:
                remote_mtime = remote_archive_mtime(remote_path_for_id(GDRIVE_REMOTE_NAME, p["sync_code"]))
                synced_mtime = p.get("synced_remote_mtime")
                if remote_mtime and (not synced_mtime or remote_mtime > synced_mtime):
                    stale.add(p["path"])
            return stale

        def done(stale: set[str]) -> None:
            self.manage_stale = stale
            self._render_manage_list()

        self._run_async(work, done)

    def _selected_manage_project(self) -> dict | None:
        selection = self.manage_listbox.curselection()
        if not selection:
            self._banner("select a project from the list first.", "error")
            return None
        return self.projects[selection[0]]

    def _on_manage_select(self, event=None) -> None:
        selection = self.manage_listbox.curselection()
        if not selection:
            return
        p = self.projects[selection[0]]
        self.manage_detail_label.config(text=f"path: {p['path']}\ncode: {p['sync_code']}")

    def _manage_push(self) -> None:
        p = self._selected_manage_project()
        if not p:
            return
        if not Path(p["path"]).is_dir():
            self._banner(f"{p['path']} no longer exists.", "error")
            return
        if not self._require_username():
            return
        add_contributor(Path(p["path"]), self.username_var.get().strip())

        def on_success(mtime) -> None:
            self._remember_project(
                p["path"],
                p["sync_code"],
                p["role"],
                last_pushed=datetime.now(timezone.utc).isoformat(),
                last_pushed_by=self.username_var.get().strip(),
                synced_remote_mtime=mtime,
            )

        self._async_push_archive(
            p["path"],
            remote_archive_path_for(p["sync_code"]),
            self.manage_status_label,
            on_success,
            button=self.manage_push_button,
        )

    def _manage_pull(self) -> None:
        p = self._selected_manage_project()
        if not p:
            return
        local_path = Path(p["path"])
        if local_path.is_dir() and any(local_path.iterdir()) and not messagebox.askyesno(
            "pull from drive",
            f"this overwrites local files in {local_path} with what's in drive.\n"
            "any local changes not yet synced will be lost. continue?",
        ):
            return
        local_path.mkdir(parents=True, exist_ok=True)

        def on_success(mtime) -> None:
            self._remember_project(p["path"], p["sync_code"], p["role"], synced_remote_mtime=mtime)

        self._async_pull_archive(
            remote_archive_path_for(p["sync_code"]),
            str(local_path),
            self.manage_status_label,
            on_success,
            button=self.manage_pull_button,
        )

    def _manage_project_info(self) -> None:
        p = self._selected_manage_project()
        if not p:
            return
        project_dir = Path(p["path"])
        local_info = _local_als_info(project_dir) if project_dir.is_dir() else None
        remote_info = _remote_als_info(remote_archive_path_for(p["sync_code"]))

        local_tracks = str(local_info["track_count"]) if local_info else "no .als found"
        remote_tracks = str(remote_info["track_count"]) if remote_info else "no .als found"
        mismatch = (
            local_info is not None
            and remote_info is not None
            and local_info["track_count"] != remote_info["track_count"]
        )

        vst_names = local_info["vst_names"] if local_info else []
        installed_names = {plugin.name.lower() for plugin in scan_installed_plugins()}

        last_pushed = p.get("last_pushed")
        if last_pushed:
            last_pushed_line = datetime.fromisoformat(last_pushed).astimezone().strftime("%Y-%m-%d %H:%M")
        else:
            last_pushed_line = "never"
        last_pushed_by = p.get("last_pushed_by")

        text = self.manage_info_text
        text.config(state="normal")
        text.delete("1.0", tk.END)

        text.insert(tk.END, "tracks — local: ", "mismatch" if mismatch else ())
        text.insert(tk.END, f"{local_tracks} | drive: {remote_tracks}\n", "mismatch" if mismatch else ())

        text.insert(tk.END, "vsts used: ")
        if vst_names:
            for i, name in enumerate(vst_names):
                # Ableton's own project metadata inconsistently includes/omits the
                # vendor prefix (e.g. "Pro-L 2" vs "FabFilter Pro-L 2"), so match
                # either way being a substring of the other rather than exact equality.
                name_l = name.lower()
                is_installed = any(
                    name_l in installed or installed in name_l for installed in installed_names
                )
                tag = "green" if is_installed else "red"
                text.insert(tk.END, name, tag)
                if i < len(vst_names) - 1:
                    text.insert(tk.END, ", ")
        else:
            text.insert(tk.END, "none found")
        text.insert(tk.END, "\n")

        text.insert(tk.END, f"last pushed to drive: {last_pushed_line}")
        if last_pushed_by:
            text.insert(tk.END, f" [{last_pushed_by}]")
        text.insert(tk.END, f"\ndrive folder id: {p['sync_code']}")

        text.config(state="disabled")

    def _manage_share(self) -> None:
        p = self._selected_manage_project()
        if not p:
            return
        email = self.manage_partner_email_var.get().strip()
        if not email:
            self._banner("enter the collaborator's google account email.", "error")
            return
        self._share_folder(p["sync_code"], email, self.manage_status_label)

    # -- Settings tab ---------------------------------------------------

    def _build_settings_tab(self) -> None:
        frame = self.settings_tab

        tk.Label(
            frame,
            text="connect your google drive account (opens a browser sign-in):",
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0))

        self.gdrive_setup_button = ttk.Button(
            frame, text="gdrive setup", width=16, command=self._settings_gdrive_setup
        )
        self.gdrive_setup_button.grid(row=1, column=0, columnspan=2, pady=4)
        self.remote_status_label = tk.Label(frame, text="", fg="gray")
        self.remote_status_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=12)

        tk.Label(frame, text="your username (shown to collaborators):").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 0)
        )
        tk.Entry(frame, textvariable=self.username_var, width=40).grid(
            row=4, column=0, columnspan=2, padx=12, pady=4, sticky="we"
        )

        tk.Label(
            frame, text="custom vst2 folders (only needed if you don't use standard install paths):"
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 0))
        self.vst2_listbox = tk.Listbox(frame, height=4, width=60)
        self.vst2_listbox.grid(row=6, column=0, columnspan=2, padx=12, pady=4, sticky="we")
        vst2_button_row = tk.Frame(frame)
        vst2_button_row.grid(row=7, column=0, columnspan=2)
        ttk.Button(vst2_button_row, text="add...", command=lambda: self._settings_add_path("vst2")).pack(
            side="left", padx=6
        )
        ttk.Button(
            vst2_button_row, text="remove selected", command=lambda: self._settings_remove_path("vst2")
        ).pack(side="left", padx=6)

        tk.Label(frame, text="custom vst3 folders:").grid(
            row=8, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 0)
        )
        self.vst3_listbox = tk.Listbox(frame, height=4, width=60)
        self.vst3_listbox.grid(row=9, column=0, columnspan=2, padx=12, pady=4, sticky="we")
        vst3_button_row = tk.Frame(frame)
        vst3_button_row.grid(row=10, column=0, columnspan=2)
        ttk.Button(vst3_button_row, text="add...", command=lambda: self._settings_add_path("vst3")).pack(
            side="left", padx=6
        )
        ttk.Button(
            vst3_button_row, text="remove selected", command=lambda: self._settings_remove_path("vst3")
        ).pack(side="left", padx=6)

        self.settings_status_label = tk.Label(frame, text="", fg="gray")
        self.settings_status_label.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)
        ttk.Button(frame, text="save settings", width=16, command=self._settings_save).grid(
            row=12, column=0, columnspan=2, pady=12
        )

        self._refresh_settings_lists()

    def _refresh_settings_lists(self) -> None:
        self.vst2_listbox.delete(0, tk.END)
        for p in self.custom_vst2_paths:
            self.vst2_listbox.insert(tk.END, p)
        self.vst3_listbox.delete(0, tk.END)
        for p in self.custom_vst3_paths:
            self.vst3_listbox.insert(tk.END, p)

    def _settings_add_path(self, fmt: str) -> None:
        selected = filedialog.askdirectory(
            title=f"select a custom {fmt} folder", initialdir=str(Path.home())
        )
        if not selected:
            return
        paths = self.custom_vst2_paths if fmt == "vst2" else self.custom_vst3_paths
        if selected not in paths:
            paths.append(selected)
        self._refresh_settings_lists()

    def _settings_remove_path(self, fmt: str) -> None:
        listbox = self.vst2_listbox if fmt == "vst2" else self.vst3_listbox
        paths = self.custom_vst2_paths if fmt == "vst2" else self.custom_vst3_paths
        selection = listbox.curselection()
        if not selection:
            return
        del paths[selection[0]]
        self._refresh_settings_lists()

    def _settings_gdrive_setup(self) -> None:
        self.remote_status_label.config(text="opening browser for google sign-in...", fg="gray")
        self.gdrive_setup_button.config(state="disabled")

        def work() -> subprocess.CompletedProcess:
            _kill_stale_oauth_listener()
            args = [RCLONE_BIN, "config", "create", GDRIVE_REMOTE_NAME, "drive", "scope=drive"]
            return subprocess.run(args, capture_output=True, text=True)

        self._run_async(work, self._settings_gdrive_setup_done)

    def _settings_gdrive_setup_done(self, result: subprocess.CompletedProcess) -> None:
        self.gdrive_setup_button.config(state="normal")
        if result.returncode == 0 and rclone_remote_exists(GDRIVE_REMOTE_NAME):
            self.remote_status_label.config(text="google drive connected.", fg="green")
            self._banner("google drive is set up.", "success")
        else:
            self.remote_status_label.config(text="setup failed.", fg="red")
            self._banner(f"gdrive setup failed: {result.stderr or 'unknown error'}", "error")
        self._update_new_tab_gate()

    def _settings_save(self) -> None:
        config = load_config()
        config["username"] = self.username_var.get().strip()
        config["custom_vst_paths"] = {"vst2": self.custom_vst2_paths, "vst3": self.custom_vst3_paths}
        save_config(config)
        self.settings_status_label.config(text="settings saved.", fg="green")


def main() -> None:
    ensure_config_exists()
    root = tk.Tk()
    ProjectPickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
