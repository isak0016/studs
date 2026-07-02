"""GUI for creating, importing, and managing synced Ableton projects."""
from __future__ import annotations

import gzip
import json
import platform
import secrets
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import xml.etree.ElementTree as ET

from studs.config import CONFIG_PATH, load_config, save_config
from studs.drive_api import folder_id_by_name, remote_path_for_id, share_project_folder

MAX_PROJECTS = 20
CONTRIBUTORS_FILENAME = ".studs_contributors.json"
GDRIVE_REMOTE_NAME = "studs_gdrive"
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
    result = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True)
    remotes = [r.strip().rstrip(":") for r in result.stdout.splitlines() if r.strip()]
    return name in remotes


def load_projects() -> list[dict]:
    return load_config().get("projects", [])


def save_projects(projects: list[dict]) -> None:
    config = load_config()
    config["projects"] = projects
    save_config(config)


def upsert_project(projects: list[dict], path: str, sync_code: str, role: str) -> list[dict]:
    projects = [p for p in projects if p["path"] != path]
    projects.insert(0, {"path": path, "sync_code": sync_code, "role": role})
    return projects[:MAX_PROJECTS]


def _read_contributors(project_dir: Path) -> list[str]:
    f = project_dir / CONTRIBUTORS_FILENAME
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _add_contributor(project_dir: Path, username: str) -> None:
    if not username:
        return
    contributors = _read_contributors(project_dir)
    if username not in contributors:
        contributors.append(username)
        (project_dir / CONTRIBUTORS_FILENAME).write_text(json.dumps(contributors, indent=2))


def _looks_like_ableton_project(path: Path) -> bool:
    return any(path.rglob("*.als"))


def _count_tracks_in_als_bytes(data: bytes) -> int:
    root = ET.fromstring(gzip.decompress(data))
    tracks = root.find(".//Tracks")
    return len(tracks) if tracks is not None else 0


def _local_track_count(project_dir: Path) -> int | None:
    candidates = sorted(project_dir.glob("*.als"))
    if not candidates:
        return None
    return _count_tracks_in_als_bytes(candidates[0].read_bytes())


def _remote_track_count(remote_path: str) -> int | None:
    listing = subprocess.run(
        ["rclone", "lsf", remote_path, "--include", "*.als"],
        capture_output=True,
        text=True,
    )
    filenames = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
    if not filenames:
        return None
    cat = subprocess.run(["rclone", "cat", f"{remote_path}/{filenames[0]}"], capture_output=True)
    if cat.returncode != 0:
        return None
    return _count_tracks_in_als_bytes(cat.stdout)


class ProjectPickerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Studs")
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
        self.notebook.add(self.new_tab, text="New Project")
        self.notebook.add(self.import_tab, text="Import Project")
        self.notebook.add(self.manage_tab, text="Manage Projects")
        self.notebook.add(self.settings_tab, text="Settings")

        self._build_new_tab()
        self._build_import_tab()
        self._build_manage_tab()
        self._build_settings_tab()

        self._update_new_tab_gate()
        if not rclone_remote_exists(GDRIVE_REMOTE_NAME):
            self._banner(
                "Before you can create new projects, go to Settings and click "
                "'GDrive Setup' to connect your Google account.",
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

    def _async_sync(
        self,
        src: str,
        dst: str,
        status_label: tk.Label,
        on_success,
        button: tk.Button | None = None,
    ) -> None:
        status_label.config(text=f"Working: {src} -> {dst}...", fg="gray")
        if button is not None:
            button.config(state="disabled")

        def work() -> subprocess.CompletedProcess:
            return subprocess.run(
                ["rclone", "sync", src, dst, "--progress"], capture_output=True, text=True
            )

        def done(result: subprocess.CompletedProcess) -> None:
            if button is not None:
                button.config(state="normal")
            if result.returncode == 0:
                status_label.config(text="Done.", fg="green")
                self._banner(f"Synced: {src} -> {dst}", "success")
                on_success()
            else:
                status_label.config(text="Failed.", fg="red")
                self._banner(f"Sync failed: {result.stderr or 'Unknown error'}", "error")

        self._run_async(work, done)

    def _remember_project(self, path: str, code: str, role: str) -> None:
        self.projects = upsert_project(self.projects, path, code, role)
        save_projects(self.projects)
        self._refresh_manage_list()

    def _share_folder(self, code: str, email: str, status_label: tk.Label) -> None:
        status_label.config(text=f"Sharing with {email}...", fg="gray")
        self.root.update_idletasks()
        try:
            share_project_folder(GDRIVE_REMOTE_NAME, code, email)
        except RuntimeError as e:
            status_label.config(text="Share failed.", fg="red")
            self._banner(f"Share failed: {e}", "error")
            return
        status_label.config(text=f"Shared with {email}.", fg="green")
        self._banner(f"{email} now has editor access to this project's folder.", "success")

    def _require_username(self) -> bool:
        if self.username_var.get().strip():
            return True
        self._banner("Set your username in the Settings tab first.", "error")
        return False

    def _remote_path_for(self, code: str) -> str:
        return remote_path_for_id(GDRIVE_REMOTE_NAME, code)

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
            text="Pick a local Ableton project folder to start syncing:",
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        tk.Entry(frame, textvariable=self.new_path_var, width=50, state="readonly").grid(
            row=1, column=0, padx=(12, 4), pady=4, sticky="we"
        )
        tk.Button(frame, text="Browse...", command=self._new_browse).grid(
            row=1, column=1, padx=(4, 12), pady=4
        )

        self.new_status_label = tk.Label(frame, text="", fg="gray")
        self.new_status_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        self.new_push_button = tk.Button(
            frame, text="Push to Drive", width=16, command=self._new_push
        )
        self.new_push_button.grid(row=3, column=0, columnspan=2, pady=12)

        tk.Label(frame, text="Sync code (appears after pushing — share it with your partner):").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 0)
        )
        tk.Entry(frame, textvariable=self.new_code_var, width=50, state="readonly").grid(
            row=5, column=0, columnspan=2, padx=12, pady=4, sticky="we"
        )

        tk.Label(
            frame, text="Partner's Google account email (grants access to just this project):"
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 0))
        self.new_partner_email_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.new_partner_email_var, width=40).grid(
            row=7, column=0, padx=(12, 4), pady=4, sticky="we"
        )
        self.new_share_button = tk.Button(frame, text="Share with Partner", command=self._new_share)
        self.new_share_button.grid(row=7, column=1, padx=(4, 12), pady=4)

        self._update_new_tab_buttons()

    def _update_new_tab_buttons(self) -> None:
        has_folder = bool(self.new_path_var.get()) and Path(self.new_path_var.get()).is_dir()
        self.new_push_button.config(state="normal" if has_folder else "disabled")
        self.new_share_button.config(state="normal" if self.new_pushed else "disabled")

    def _new_browse(self) -> None:
        selected = filedialog.askdirectory(
            title="Select Ableton Project Folder", initialdir=str(Path.home())
        )
        if not selected:
            return
        self.new_path_var.set(selected)
        self.new_pushed = False
        self.new_code_var.set("")
        if _looks_like_ableton_project(Path(selected)):
            self.new_status_label.config(text="Found .als project file(s).", fg="green")
        else:
            self.new_status_label.config(
                text="Warning: no .als files found in this folder or its subfolders.",
                fg="#b8860b",
            )
        self._update_new_tab_buttons()

    def _new_push(self) -> None:
        path = self.new_path_var.get()
        if not path or not Path(path).is_dir():
            self._banner("Please choose a folder first.", "error")
            return
        if not self._require_username():
            return

        # Push into a fresh, throwaway-named folder — the name itself is never used
        # again; what matters is the real Drive folder ID, resolved right after.
        temp_name = secrets.token_hex(8)
        remote_dst = f"{GDRIVE_REMOTE_NAME}:studs/{temp_name}"
        _add_contributor(Path(path), self.username_var.get().strip())

        self.new_status_label.config(text=f"Working: {path} -> {remote_dst}...", fg="gray")
        self.new_push_button.config(state="disabled")

        def work() -> dict:
            result = subprocess.run(
                ["rclone", "sync", path, remote_dst, "--progress"], capture_output=True, text=True
            )
            if result.returncode != 0:
                return {"ok": False, "error": result.stderr}
            return {"ok": True, "folder_id": folder_id_by_name(GDRIVE_REMOTE_NAME, "studs", temp_name)}

        self._run_async(work, lambda r: self._new_push_done(r, path))

    def _new_push_done(self, r: dict, path: str) -> None:
        if not r["ok"]:
            self.new_status_label.config(text="Failed.", fg="red")
            self._banner(f"Sync failed: {r['error'] or 'Unknown error'}", "error")
            self._update_new_tab_buttons()
            return

        folder_id = r["folder_id"]
        if not folder_id:
            self.new_status_label.config(text="Push succeeded but ID lookup failed.", fg="red")
            self._banner(
                "Push succeeded but its Drive folder ID couldn't be resolved. Try Push again.",
                "error",
            )
            self._update_new_tab_buttons()
            return

        self.new_status_label.config(text="Done.", fg="green")
        self.new_code_var.set(folder_id)
        self.new_pushed = True
        self._remember_project(path, folder_id, "created")
        self.root.clipboard_clear()
        self.root.clipboard_append(folder_id)
        self._banner(
            f"Pushed. Sync code copied to clipboard: {folder_id} — "
            "send it to your partner, then Share with Partner below.",
            "success",
        )
        self._update_new_tab_buttons()

    def _new_share(self) -> None:
        code = self.new_code_var.get()
        email = self.new_partner_email_var.get().strip()
        if not self.new_pushed:
            self._banner("Push this project to Drive before sharing it.", "error")
            return
        if not email:
            self._banner("Enter your partner's Google account email.", "error")
            return
        self._share_folder(code, email, self.new_status_label)

    # -- Import Project tab ------------------------------------------------

    def _build_import_tab(self) -> None:
        frame = self.import_tab
        self.import_code_var = tk.StringVar()

        tk.Label(
            frame,
            text="Enter the sync code your partner sent you:",
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=8)

        tk.Entry(frame, textvariable=self.import_code_var, width=50).grid(
            row=1, column=0, columnspan=2, padx=12, pady=4, sticky="we"
        )

        self.import_status_label = tk.Label(frame, text="", fg="gray")
        self.import_status_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        self.import_pull_button = tk.Button(
            frame, text="Pull from Drive", width=16, command=self._import_pull
        )
        self.import_pull_button.grid(row=3, column=0, columnspan=2, pady=12)

    def _import_pull(self) -> None:
        code = self.import_code_var.get().strip()
        if not code:
            self._banner("Enter a sync code first.", "error")
            return

        selected = filedialog.askdirectory(
            title="Choose where to save this project locally", initialdir=str(Path.home())
        )
        if not selected:
            return

        def on_success() -> None:
            self._remember_project(selected, code, "imported")
            self.import_code_var.set("")

        self._async_sync(
            self._remote_path_for(code),
            selected,
            self.import_status_label,
            on_success,
            button=self.import_pull_button,
        )

    # -- Manage Projects tab ------------------------------------------------

    def _build_manage_tab(self) -> None:
        frame = self.manage_tab

        tk.Label(frame, text="Your projects (select one):").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0)
        )
        self.manage_listbox = tk.Listbox(frame, height=8, width=60)
        self.manage_listbox.grid(row=1, column=0, columnspan=2, padx=12, pady=4, sticky="we")
        self.manage_listbox.bind("<<ListboxSelect>>", self._on_manage_select)

        self.manage_detail_label = tk.Label(frame, text="", justify="left", fg="gray")
        self.manage_detail_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        button_row = tk.Frame(frame)
        button_row.grid(row=3, column=0, columnspan=2, pady=8)
        self.manage_push_button = tk.Button(
            button_row, text="Push to Drive", width=14, command=self._manage_push
        )
        self.manage_push_button.pack(side="left", padx=6)
        self.manage_pull_button = tk.Button(
            button_row, text="Pull from Drive", width=14, command=self._manage_pull
        )
        self.manage_pull_button.pack(side="left", padx=6)
        tk.Button(
            button_row, text="Check Track Counts", command=self._manage_check_tracks
        ).pack(side="left", padx=6)

        self.manage_status_label = tk.Label(frame, text="", fg="gray")
        self.manage_status_label.grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        tk.Label(frame, text="Add a collaborator's Google email (access to just this project):").grid(
            row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0)
        )
        self.manage_partner_email_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.manage_partner_email_var, width=40).grid(
            row=6, column=0, padx=(12, 4), pady=4, sticky="we"
        )
        tk.Button(frame, text="Share with Partner", command=self._manage_share).grid(
            row=6, column=1, padx=(4, 12), pady=4
        )

        self._refresh_manage_list()

    def _refresh_manage_list(self) -> None:
        self.manage_listbox.delete(0, tk.END)
        for p in self.projects:
            contributors = _read_contributors(Path(p["path"])) if Path(p["path"]).is_dir() else []
            who = ", ".join(contributors) if contributors else "no contributors yet"
            self.manage_listbox.insert(
                tk.END, f"{Path(p['path']).name}  [{p['sync_code']}]  ({p['role']}) — {who}"
            )

    def _selected_manage_project(self) -> dict | None:
        selection = self.manage_listbox.curselection()
        if not selection:
            self._banner("Select a project from the list first.", "error")
            return None
        return self.projects[selection[0]]

    def _on_manage_select(self, event=None) -> None:
        selection = self.manage_listbox.curselection()
        if not selection:
            return
        p = self.projects[selection[0]]
        self.manage_detail_label.config(text=f"Path: {p['path']}\nCode: {p['sync_code']}")

    def _manage_push(self) -> None:
        p = self._selected_manage_project()
        if not p:
            return
        if not Path(p["path"]).is_dir():
            self._banner(f"{p['path']} no longer exists.", "error")
            return
        if not self._require_username():
            return
        _add_contributor(Path(p["path"]), self.username_var.get().strip())
        self._async_sync(
            p["path"],
            self._remote_path_for(p["sync_code"]),
            self.manage_status_label,
            self._refresh_manage_list,
            button=self.manage_push_button,
        )

    def _manage_pull(self) -> None:
        p = self._selected_manage_project()
        if not p:
            return
        local_path = Path(p["path"])
        if local_path.is_dir() and any(local_path.iterdir()) and not messagebox.askyesno(
            "Pull from Drive",
            f"This overwrites local files in {local_path} with what's in Drive.\n"
            "Any local changes not yet synced will be lost. Continue?",
        ):
            return
        local_path.mkdir(parents=True, exist_ok=True)
        self._async_sync(
            self._remote_path_for(p["sync_code"]),
            str(local_path),
            self.manage_status_label,
            self._refresh_manage_list,
            button=self.manage_pull_button,
        )

    def _manage_check_tracks(self) -> None:
        p = self._selected_manage_project()
        if not p:
            return
        local = _local_track_count(Path(p["path"])) if Path(p["path"]).is_dir() else None
        remote = _remote_track_count(self._remote_path_for(p["sync_code"]))
        local_str = str(local) if local is not None else "no .als found"
        remote_str = str(remote) if remote is not None else "no .als found"
        mismatch = local is not None and remote is not None and local != remote
        self.manage_status_label.config(
            text=f"Tracks — local: {local_str} | drive: {remote_str}",
            fg="#b8860b" if mismatch else "gray",
        )

    def _manage_share(self) -> None:
        p = self._selected_manage_project()
        if not p:
            return
        email = self.manage_partner_email_var.get().strip()
        if not email:
            self._banner("Enter the collaborator's Google account email.", "error")
            return
        self._share_folder(p["sync_code"], email, self.manage_status_label)

    # -- Settings tab ---------------------------------------------------

    def _build_settings_tab(self) -> None:
        frame = self.settings_tab

        tk.Label(
            frame,
            text="Connect your Google Drive account (opens a browser sign-in):",
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0))
        self.gdrive_setup_button = tk.Button(
            frame, text="GDrive Setup", width=16, command=self._settings_gdrive_setup
        )
        self.gdrive_setup_button.grid(row=1, column=0, columnspan=2, pady=4)
        self.remote_status_label = tk.Label(frame, text="", fg="gray")
        self.remote_status_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=12)

        tk.Label(frame, text="Your username (shown to collaborators):").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 0)
        )
        tk.Entry(frame, textvariable=self.username_var, width=40).grid(
            row=4, column=0, columnspan=2, padx=12, pady=4, sticky="we"
        )

        tk.Label(
            frame, text="Custom VST2 folders (only needed if you don't use standard install paths):"
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 0))
        self.vst2_listbox = tk.Listbox(frame, height=4, width=60)
        self.vst2_listbox.grid(row=6, column=0, columnspan=2, padx=12, pady=4, sticky="we")
        vst2_button_row = tk.Frame(frame)
        vst2_button_row.grid(row=7, column=0, columnspan=2)
        tk.Button(vst2_button_row, text="Add...", command=lambda: self._settings_add_path("vst2")).pack(
            side="left", padx=6
        )
        tk.Button(
            vst2_button_row, text="Remove Selected", command=lambda: self._settings_remove_path("vst2")
        ).pack(side="left", padx=6)

        tk.Label(frame, text="Custom VST3 folders:").grid(
            row=8, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 0)
        )
        self.vst3_listbox = tk.Listbox(frame, height=4, width=60)
        self.vst3_listbox.grid(row=9, column=0, columnspan=2, padx=12, pady=4, sticky="we")
        vst3_button_row = tk.Frame(frame)
        vst3_button_row.grid(row=10, column=0, columnspan=2)
        tk.Button(vst3_button_row, text="Add...", command=lambda: self._settings_add_path("vst3")).pack(
            side="left", padx=6
        )
        tk.Button(
            vst3_button_row, text="Remove Selected", command=lambda: self._settings_remove_path("vst3")
        ).pack(side="left", padx=6)

        self.settings_status_label = tk.Label(frame, text="", fg="gray")
        self.settings_status_label.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)
        tk.Button(frame, text="Save Settings", width=16, command=self._settings_save).grid(
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
            title=f"Select a custom {fmt.upper()} folder", initialdir=str(Path.home())
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
        self.remote_status_label.config(text="Opening browser for Google sign-in...", fg="gray")
        self.gdrive_setup_button.config(state="disabled")

        def work() -> subprocess.CompletedProcess:
            _kill_stale_oauth_listener()
            return subprocess.run(
                ["rclone", "config", "create", GDRIVE_REMOTE_NAME, "drive", "scope=drive"],
                capture_output=True,
                text=True,
            )

        self._run_async(work, self._settings_gdrive_setup_done)

    def _settings_gdrive_setup_done(self, result: subprocess.CompletedProcess) -> None:
        self.gdrive_setup_button.config(state="normal")
        if result.returncode == 0 and rclone_remote_exists(GDRIVE_REMOTE_NAME):
            self.remote_status_label.config(text="Google Drive connected.", fg="green")
            self._banner("Google Drive is set up.", "success")
        else:
            self.remote_status_label.config(text="Setup failed.", fg="red")
            self._banner(f"GDrive setup failed: {result.stderr or 'Unknown error'}", "error")
        self._update_new_tab_gate()

    def _settings_save(self) -> None:
        config = load_config()
        config["username"] = self.username_var.get().strip()
        config["custom_vst_paths"] = {"vst2": self.custom_vst2_paths, "vst3": self.custom_vst3_paths}
        save_config(config)
        self.settings_status_label.config(text="Settings saved.", fg="green")


def main() -> None:
    ensure_config_exists()
    root = tk.Tk()
    ProjectPickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
