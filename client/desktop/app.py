"""Native desktop workspace. All network and subprocess work runs off the UI thread."""
import base64
import json
import queue
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from .layout import FlowRow, ScrollPage, configure_dpi, initial_geometry
from .local import LocalRunner
from .remote import Remote, RemoteError, sync_records
from .store import LocalStore


class Desktop:
    def __init__(self, root, directory, server):
        import tkinter as tk
        from tkinter import messagebox, ttk
        self.tk, self.ttk, self.messagebox, self.root = tk, ttk, messagebox, root
        self.store = LocalStore(directory)
        self.profile, self.remote, self.online, self.busy = None, None, False, False
        self.runner, self.remote_job, self.output_value = None, None, ""
        self.messages, self.closed = queue.Queue(), False
        self.server_default = server
        self.preferences_path = None
        self.preferences = {}
        root.title("RISARMS Portal")
        width,height=initial_geometry(root.winfo_screenwidth(),root.winfo_screenheight())
        root.geometry(f"{width}x{height}")
        root.minsize(min(240,width),min(180,height))
        style = ttk.Style(root)
        if "clam" in style.theme_names(): style.theme_use("clam")
        style.configure("TButton", padding=7)
        style.layout("Portal.TNotebook.Tab", [])
        self.status = tk.StringVar(value="Local mode · unlock a device profile")
        ttk.Label(root, text="RISARMS Portal", font=("TkDefaultFont", 22, "bold")).pack(anchor="w", padx=20, pady=(16, 0))
        self.status_label=ttk.Label(root, textvariable=self.status,wraplength=max(40,width-40))
        self.status_label.pack(anchor="w", padx=12, pady=(2, 8))
        root.bind('<Configure>',lambda e:self.status_label.configure(wraplength=max(40,e.width-24)) if e.widget is root else None,add='+')
        self.login_page=ScrollPage(root,padding=12);self.login_page.pack(fill='both',expand=True)
        self.container=self.login_page.content
        self.login_screen()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(75, self.drain)

    def field(self, frame, label, value="", secret=False, width=45):
        self.ttk.Label(frame, text=label).pack(anchor="w", pady=(8, 3))
        entry = self.ttk.Entry(frame, width=1, show="*" if secret else "")
        entry.insert(0, value); entry.pack(fill="x")
        return entry

    def login_screen(self):
        for widget in self.container.winfo_children(): widget.destroy()
        self.ttk.Label(self.container, text="Your device profile", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        self.ttk.Label(self.container, text="A separate local passphrase unlocks offline records and history. Server login comes afterwards.", wraplength=750).pack(anchor="w", pady=8)
        self.local_name = self.field(self.container, "Local profile name")
        self.local_password = self.field(self.container, "Local passphrase (12+ characters for a new profile)", secret=True)
        row = FlowRow(self.container); row.pack(fill="x", pady=14)
        self.ttk.Button(row, text="Unlock", command=lambda:self.unlock(False)).pack(side="left", padx=(0, 8))
        self.ttk.Button(row, text="Create local profile", command=lambda:self.unlock(True)).pack(side="left")
        self.ttk.Label(self.container, text="Local files rely on this device's OS permissions and disk encryption. The passphrase is an app lock, not file encryption.", wraplength=750).pack(anchor="w", pady=10)

    def unlock(self, create):
        name, password = self.local_name.get(), self.local_password.get()
        def work():
            if create: self.store.create(name, password)
            return self.store.unlock(name, password)
        def opened(profile):
            self.profile = profile
            folder = self.store.directory/"profiles"/profile
            folder.mkdir(parents=True,exist_ok=True)
            self.preferences_path = folder/"preferences.json"
            self.preferences = json.loads(self.preferences_path.read_text()) if self.preferences_path.is_file() else {}
            self.local_password.delete(0, "end")
            self.status.set(f"Local mode · {name} · records stay on this device until Sync")
            self.workspace()
        self.work(work, opened)

    def workspace(self):
        for widget in self.container.winfo_children(): widget.destroy()
        self.nav=self.ttk.Combobox(self.container,state='readonly',width=1)
        self.nav.pack(fill='x',pady=(0,8))
        self.book = self.ttk.Notebook(self.container,style='Portal.TNotebook',height=400); self.book.pack(fill="both", expand=True)
        self.pages={}
        self.nav.bind('<<ComboboxSelected>>',lambda e:self.book.select(self.pages[self.nav.get()]))
        self.book.bind('<<NotebookTabChanged>>',self.selected_page)
        tabs = {}
        for name in ("Connection", "Tasks", "My storage", "Cloud files", "History", "Local setup", "Server admin"):
            page=ScrollPage(self.book,padding=10)
            self.pages[name]=page;tabs[name]=page.content;self.book.add(page,text=name)
        self.nav.configure(values=tuple(tabs));self.nav.set('Connection')
        self.tabs = tabs
        connection = tabs["Connection"]
        bound = self.store.binding(self.profile)
        self.server_url = self.field(connection, "Server HTTPS origin", bound["server"] or self.server_default)
        self.server_name = self.field(connection, "Server email or username")
        self.server_password = self.field(connection, "Server password", secret=True)
        self.owner_name = self.field(connection, "Display name for first-owner setup")
        self.setup_key = self.field(connection, "First-owner setup key from the server journal", secret=True)
        buttons = FlowRow(connection); buttons.pack(fill="x", pady=10)
        self.ttk.Button(buttons, text="Connect and sign in", command=lambda:self.connect(False)).pack(side="left", padx=(0, 8))
        self.ttk.Button(buttons, text="Disconnect / work locally", command=self.disconnect).pack(side="left", padx=(0,8))
        self.ttk.Button(buttons, text="First-owner setup", command=lambda:self.connect(True)).pack(side="left", padx=(0,8))
        self.ttk.Button(buttons, text="Lock local profile", command=self.lock_profile).pack(side="left")
        self.ttk.Button(connection, text="Register and request portal access", command=self.register).pack(anchor="w",pady=8)
        self.ttk.Label(connection, text="No server password or session cookie is saved to disk. A local profile binds to one server account; use another local profile for another account.", wraplength=800).pack(anchor="w", pady=10)
        task = tabs["Tasks"]
        row = FlowRow(task); row.pack(fill="x")
        self.source = self.ttk.Combobox(row, state="readonly", values=("Auto", "Server", "Local model", "Local service"), width=18); self.source.set("Auto"); self.source.pack(side="left")
        self.service = self.ttk.Combobox(row, state="readonly", values=("asis", "ascs", "tiviss"), width=12); self.service.set("asis"); self.service.pack(side="left", padx=8)
        self.mode = self.ttk.Combobox(row, state="readonly", values=("plan", "build", "auto"), width=10); self.mode.set("plan"); self.mode.pack(side="left")
        self.model_name = self.field(task, "Installed local Ollama model", self.preferences.get("model", ""))
        self.ttk.Label(task, text="Auto uses the connected server; when disconnected it uses the local model. Local service runs the actual installed ASIS/ASCS/TIVISS. Coding needs explicit confirmation.", wraplength=900).pack(anchor="w", pady=8)
        self.prompt = self.tk.Text(task, width=1, height=4, wrap="word"); self.prompt.pack(fill="x")
        actions = FlowRow(task); actions.pack(fill="x", pady=8)
        for label, command in (("Run", self.send), ("Stop", self.stop), ("Save output locally", self.save_output), ("Refresh service availability", self.services)):
            self.ttk.Button(actions, text=label, command=command).pack(side="left", padx=(0, 7))
        self.output = self.tk.Text(task, width=1, height=14, wrap="word", state="disabled"); self.output.pack(fill="both", expand=True)
        storage = tabs["My storage"]
        self.record_service = self.ttk.Combobox(storage, state="readonly", values=("workspace", "asis", "ascs", "rescs", "tiviss")); self.record_service.set("workspace"); self.record_service.pack(anchor="w")
        self.record_service.bind("<<ComboboxSelected>>", lambda event:self.list_records())
        self.record_list = self.tk.Listbox(storage,width=1, height=6); self.record_list.pack(fill="x", pady=6)
        self.record_list.bind("<<ListboxSelect>>", self.select_record)
        self.record_key = self.field(storage, "Record key")
        self.record_value = self.tk.Text(storage, width=1, height=7, wrap="word"); self.record_value.insert("1.0", '{"text": ""}'); self.record_value.pack(fill="both", expand=True, pady=6)
        row = FlowRow(storage); row.pack(fill="x")
        for label, command in (("Save locally / queue sync", self.save_record), ("Load local records", self.list_records), ("Refresh server cache", self.refresh), ("Sync pending records", self.sync)):
            self.ttk.Button(row, text=label, command=command).pack(side="left", padx=(0, 6))
        self.storage_note = self.tk.StringVar(value="Pending records remain local until you choose Sync.")
        self.ttk.Label(storage, textvariable=self.storage_note, wraplength=900).pack(anchor="w", pady=8)
        history = tabs["History"]
        self.history_text = self.tk.Text(history, width=1, wrap="word", state="disabled"); self.history_text.pack(fill="both", expand=True)
        self.ttk.Button(history, text="Refresh local task history", command=self.show_history).pack(anchor="w", pady=8)
        self.ttk.Button(history, text="Load my server chat history", command=self.server_history).pack(anchor="w", pady=4)
        setup = tabs["Local setup"]
        self.advanced_setup = self.ttk.Frame(setup); self.advanced_setup.pack(fill="x")
        self.local_paths = {name:self.field(self.advanced_setup, name.upper()+" source with an installed .venv", self.preferences.get(name, "")) for name in ("asis", "ascs", "tiviss")}
        self.chat_storage = self.ttk.Combobox(setup,state="readonly",values=("both","local","server")); self.chat_storage.set(self.preferences.get("chat_storage","both")); self.chat_storage.pack(anchor="w",pady=6)
        self.ttk.Label(setup,text="Chat storage: both / local / server. Offline work stays local until explicit Sync.").pack(anchor="w")
        model_buttons=FlowRow(setup);model_buttons.pack(fill="x",pady=6)
        self.ttk.Button(model_buttons,text="Check this device and choose model",command=self.check_model).pack(side="left",padx=(0,8))
        self.ttk.Button(model_buttons,text="Download recommended local model",command=self.download_model).pack(side="left")
        self.hardware_output=self.tk.Text(setup,width=1,height=5,wrap="word",state="disabled");self.hardware_output.pack(fill="both",expand=True)
        self.ttk.Button(setup, text="Save local service paths and model", command=self.save_preferences).pack(anchor="w", pady=14)
        self.ttk.Label(setup, text="Install Ollama and download the selected model beforehand. Local service repositories need their own dependencies. No service or model is bundled. ROVERT hardware control has no offline adapter in this release.", wraplength=850).pack(anchor="w", pady=8)
        admin = tabs["Server admin"]
        self.access_rows=[]
        self.ttk.Label(admin,text="Portal accounts and access requests").pack(anchor="w")
        self.access_list=self.tk.Listbox(admin,width=1,height=4);self.access_list.pack(fill="x",pady=5)
        access_buttons=FlowRow(admin);access_buttons.pack(fill="x",pady=4)
        for text,action in (("Refresh accounts",None),("Approve","approve"),("Reject","reject"),("Revoke access","revoke")):
            self.ttk.Button(access_buttons,text=text,command=lambda action=action:self.review_access(action)).pack(side="left",padx=(0,5))
        self.admin_action = self.ttk.Combobox(admin, state="readonly", values=("List profiles", "Create member", "Read settings", "Save settings", "Audit events", "Server status", "Start RESCS", "Pending access requests", "Review access request"), width=28); self.admin_action.set("List profiles"); self.admin_action.pack(anchor="w")
        self.ttk.Label(admin, text='Create member JSON: {"username":"...","password":"...","name":"..."}. Settings use the same JSON fields as the portal. Owner access is enforced by the server.', wraplength=900).pack(anchor="w", pady=8)
        self.admin_body = self.tk.Text(admin, width=1, height=6); self.admin_body.insert("1.0", "{}"); self.admin_body.pack(fill="x")
        self.ttk.Button(admin, text="Run owner action", command=self.admin).pack(anchor="w", pady=8)
        self.admin_output = self.tk.Text(admin, width=1, state="disabled", wrap="word"); self.admin_output.pack(fill="both", expand=True)
        self.make_files_tab(tabs["Cloud files"])
        self.apply_role((bound or {}).get("remote_role","local"))
        self.list_records(); self.show_history()

    def selected_page(self,event=None):
        selected=self.book.select()
        for name,page in self.pages.items():
            if str(page)==selected:
                self.nav.set(name);page.wrap(page.content,max(40,page.canvas.winfo_width()-32));break

    def work(self, fn, success=None):
        if self.busy:
            self.messagebox.showinfo("Task in progress", "Wait for the current operation, or stop the active task."); return
        self.busy = True
        def worker():
            try: self.messages.put(("result", (success, fn())))
            except Exception as exc: self.messages.put(("error", exc))
            finally: self.messages.put(("finished", None))
        threading.Thread(target=worker, daemon=True).start()

    def drain(self):
        if self.closed: return
        while True:
            try: kind, value = self.messages.get_nowait()
            except queue.Empty: break
            if kind == "finished": self.busy = False
            elif kind == "result":
                callback, result = value
                if callback:
                    try: callback(result)
                    except Exception as exc: self.messagebox.showerror("C.O.R.E.", str(exc))
            elif kind == "model-progress":
                self.set_text(self.hardware_output,value)
            elif kind == "append":
                self.output_value = (self.output_value + value)[-500000:]
                self.output.configure(state="normal"); self.output.insert("end", value)
                if self.output.count("1.0", "end", "chars")[0] > 500000: self.output.delete("1.0", "1.0+50000c")
                self.output.see("end"); self.output.configure(state="disabled")
            elif kind == "error":
                if isinstance(value, RemoteError):
                    self.online = False; self.status.set("Server unavailable or rejected request · local records remain available")
                self.messagebox.showerror("C.O.R.E.", str(value))
        self.root.after(75, self.drain)

    def connect(self, first=False):
        if self.busy: return
        url, username, password = self.server_url.get(), self.server_name.get(), self.server_password.get()
        setup_key, display_name = self.setup_key.get(), self.owner_name.get()
        self.setup_key.delete(0, "end")
        self.server_password.delete(0, "end")
        def work():
            candidate = Remote(url)
            try:
                if first: candidate.request("POST", "/api/setup", {"setup_key":setup_key,"username":username,"password":password,"name":display_name})
                profile = candidate.login(username, password)
                self.store.bind(self.profile, candidate.url, profile["id"], profile["role"])
            except Exception: candidate.close(); raise
            previous, self.remote = self.remote, candidate
            if previous: previous.close()
            return profile
        def done(profile):
            if profile.get("password_change_required"):
                self.status.set("Change the temporary owner password to unlock server access")
                self.root.after(150, lambda:self.change_initial_password(username, password))
                return
            self.online = True
            self.apply_role(profile["role"])
            self.status.set(f"Connected · {profile['name']} · {profile['role']} · {self.remote.url}")
        self.work(work, done)

    def change_initial_password(self, username, current):
        from tkinter import simpledialog
        new=simpledialog.askstring("Change temporary password", "Choose a NEW server password (at least 12 characters):", show="*", parent=self.root)
        if not new:
            self.disconnect(); return
        confirm=simpledialog.askstring("Confirm new password", "Enter the new password again:", show="*", parent=self.root)
        if new!=confirm or len(new)<12:
            self.messagebox.showerror("Password", "Passwords must match and contain at least 12 characters.")
            self.disconnect(); return
        def change():
            self.remote.request("POST","/api/password",{"current_password":current,"new_password":new})
            return self.remote.login(username,new)
        def done(profile):
            self.online=True;self.apply_role(profile["role"])
            self.status.set(f"Connected · {profile['name']} · {profile['role']} · temporary password replaced")
        self.work(change,done)

    def disconnect(self):
        if self.busy: self.messagebox.showinfo("Task in progress", "Stop or finish the task before disconnecting."); return
        remote, self.remote, self.online = self.remote, None, False
        if remote: threading.Thread(target=remote.close, daemon=True).start()
        self.status.set("Local mode · installed models and local records remain available")

    def lock_profile(self):
        if self.busy: return
        self.disconnect(); self.profile = None; self.login_screen()
        self.status.set("Local profile locked")

    def require_remote(self):
        if not self.online or self.remote is None: raise RemoteError("Connect to the server first. This action has no local substitute.")
        return self.remote

    def services(self):
        def work(): return self.require_remote().request("GET", "/api/services")
        self.work(work, lambda data:self.append(json.dumps(data, indent=2)+"\n"))

    def append(self, text): self.messages.put(("append", text))

    def send(self):
        if self.busy: return
        prompt, service, mode, model, source = self.prompt.get("1.0", "end").strip(), self.service.get(), self.mode.get(), self.model_name.get().strip(), self.source.get()
        if not prompt or len(prompt) > 32000: self.messagebox.showerror("Message", "Enter 1–32000 characters."); return
        if source == "Auto": source = "Server" if self.online else "Local model"
        if service == "ascs" and source in ("Server", "Local service"):
            if not self.messagebox.askyesno("Allow coding task", "ASCS can execute code with the selected machine account's permissions. Run this task?"): return
        root = self.preferences.get(service, "")
        chat_storage = self.chat_storage.get()
        self.output_value = ""
        self.output.configure(state="normal"); self.output.delete("1.0", "end"); self.output.configure(state="disabled")
        self.runner = LocalRunner()
        def work():
            chunks = []
            def emit(text):
                chunks.append(text)
                if len(chunks) > 5000: del chunks[:500]
                self.append(text)
            if source == "Server":
                remote = self.require_remote()
                job = remote.request("POST", "/api/jobs", {"service":service, "prompt":prompt, "mode":mode, "persist":chat_storage!="local"})
                self.remote_job, after = job["id"], 0
                try:
                    while not self.closed:
                        data = remote.request("GET", "/api/jobs/"+job["id"]+"?after="+str(after))
                        for event in data["events"]:
                            after = event["id"]; emit(event["text"])
                        if data["status"] not in ("queued", "starting", "running"): result = data["status"]; break
                        time.sleep(.5)
                    else: result = "client closed; server job may still be running"
                finally: self.remote_job = None
            elif source == "Local service":
                result = self.runner.service(service, root, self.store.directory/"profiles"/self.profile, prompt, model, emit, mode)
            else:
                history=[]
                previous=[r for r in self.store.history(self.profile) if r["source"]=="Local model:"+service][:3]
                for row in reversed(previous):history.extend([{"role":"user","content":row["prompt"][-6000:]},{"role":"assistant","content":row["output"][-6000:]}])
                result = self.runner.model(model, prompt, emit, history)
            if chat_storage in ("local","both") or source != "Server":
                self.store.log(self.profile, source+":"+service, prompt, "".join(chunks))
            if source != "Server" and chat_storage in ("server","both"):
                self.store.save(self.profile,"asis","chat-"+str(time.time_ns()),{"prompt":prompt,"output":"".join(chunks)})
            return result
        self.work(work, lambda result:self.append("\n["+result+"]\n"))

    def stop(self):
        def stop():
            try:
                if self.remote_job and self.remote: self.remote.request("POST", "/api/jobs/"+self.remote_job+"/stop")
                elif self.runner: self.runner.stop()
            except Exception as exc: self.messages.put(("error", exc))
        threading.Thread(target=stop, daemon=True).start()

    def list_records(self):
        rows = self.store.records(self.profile, self.record_service.get())
        self.visible_records = rows
        self.record_list.delete(0, "end")
        for row in rows: self.record_list.insert("end", row["key"] + ("  [pending sync]" if row["pending"] else "  [cached]"))
        self.storage_note.set(str(len(self.store.records(self.profile, pending=True)))+" pending local records · server deletions are not mirrored")

    def select_record(self, event=None):
        selection = self.record_list.curselection()
        if not selection: return
        row = self.visible_records[selection[0]]
        self.record_key.delete(0, "end"); self.record_key.insert(0, row["key"])
        self.record_value.delete("1.0", "end"); self.record_value.insert("1.0", json.dumps(row["value"], indent=2))

    def save_record(self):
        try:
            self.store.save(self.profile, self.record_service.get(), self.record_key.get(), json.loads(self.record_value.get("1.0", "end")))
            self.list_records()
        except Exception as exc: self.messagebox.showerror("Record", str(exc))

    def save_output(self):
        if not self.output_value: return
        key = "task-"+str(time.time_ns())
        self.store.save(self.profile, self.service.get(), key, {"output":self.output_value})
        self.list_records(); self.messagebox.showinfo("Saved locally", "Saved as "+key+". Use Sync to send it to RESCS.")

    def refresh(self):
        service = self.record_service.get()
        def work():
            remote, offset = self.require_remote(), 0
            while offset < 100000:
                page = remote.request("GET", "/api/records?"+urlencode({"service":service, "limit":100, "offset":offset}))
                if not isinstance(page.get("items"), list): raise RemoteError("Invalid record collection.")
                self.store.cache(self.profile, service, page["items"])
                offset += len(page["items"])
                if offset >= page["total"]: return offset
                if not page["items"]: raise RemoteError("Incomplete server record page.")
            raise RemoteError("Record cache scan limit reached.")
        self.work(work, lambda count:self.list_records())

    def sync(self):
        def work(): return sync_records(self.store, self.profile, self.require_remote())
        def done(result):
            self.list_records()
            self.messagebox.showinfo("Sync result", f"Saved: {result['saved']}\nConflicts kept locally: "+", ".join(result["conflicts"])+"\nFor conflicts, compare the server value and save a new key or use the browser portal to resolve explicitly.")
        self.work(work, done)

    def show_history(self):
        text = "\n\n".join(f"{row['source']}\n{row['prompt']}\n{row['output']}" for row in self.store.history(self.profile))
        self.set_text(self.history_text, text or "No completed local task history yet.")

    @staticmethod
    def set_text(widget, text):
        widget.configure(state="normal"); widget.delete("1.0", "end"); widget.insert("1.0", text); widget.configure(state="disabled")

    def save_preferences(self):
        self.preferences = {key:entry.get().strip() for key,entry in self.local_paths.items()}
        self.preferences["model"] = self.model_name.get().strip()
        self.preferences["chat_storage"] = self.chat_storage.get()
        self.preferences_path.write_text(json.dumps(self.preferences, indent=2))
        self.messagebox.showinfo("Saved", "Local service paths and model were saved on this device.")

    def admin_password(self):
        from tkinter import simpledialog
        return simpledialog.askstring('Unlock administration','Re-enter your owner password:',show='*',parent=self.root)

    def admin_request(self,password,method,path,body=None):
        remote=self.require_remote()
        remote.request('POST','/api/reauth',{'password':password})
        return remote.request(method,path,body)

    def admin(self):
        action = self.admin_action.get()
        mapping = {"List profiles":("GET", "/api/profiles"), "Create member":("POST", "/api/profiles"),
                   "Read settings":("GET", "/api/settings"), "Save settings":("POST", "/api/settings"), "Audit events":("GET", "/api/audit"), "Server status":("GET", "/api/status"), "Start RESCS":("POST", "/api/jobs"), "Pending access requests":("GET", "/api/access"), "Review access request":("POST", "/api/access")}
        try: body = json.loads(self.admin_body.get("1.0", "end"))
        except ValueError: self.messagebox.showerror("JSON", "Enter a valid JSON object."); return
        self.admin_body.delete("1.0", "end"); self.admin_body.insert("1.0", "{}")
        method, path = mapping[action]
        if action == "Start RESCS": body = {"service":"rescs"}
        password=self.admin_password()
        if password is None:return
        self.work(lambda:self.admin_request(password,method,path,body), lambda data:self.set_text(self.admin_output, json.dumps(data, indent=2)))

    def review_access(self, action):
        password=self.admin_password()
        if password is None:return
        if action is None:
            def done(data):
                self.access_rows=data["profiles"];self.access_list.delete(0,"end")
                for row in self.access_rows:self.access_list.insert("end",row["name"]+" · "+row["username"]+" · "+row["role"])
            self.work(lambda:self.admin_request(password,"GET","/api/profiles"),done)
            return
        selected=self.access_list.curselection()
        if not selected:return
        identity=self.access_rows[selected[0]]["id"]
        self.work(lambda:self.admin_request(password,"POST","/api/access",{"user_id":identity,"action":action}),
                  lambda data:self.messagebox.showinfo("Access updated",data["username"]+" is now "+data["role"]+". Refresh accounts to reload."))

    def apply_role(self, role):
        public = role not in ("owner","local")
        self.book.tab(self.pages["Server admin"],state="hidden" if public else "normal")
        self.nav.configure(values=tuple(name for name in self.tabs if not public or name!='Server admin'))
        if public and self.nav.get()=='Server admin':self.book.select(self.pages['Connection'])
        self.source.configure(values=("Auto","Server","Local model") if public else ("Auto","Server","Local model","Local service"))
        self.source.set("Auto")
        self.service.configure(values=("asis",) if public else ("asis","ascs","tiviss"));self.service.set("asis")
        self.mode.configure(state="disabled" if public else "readonly")
        self.record_service.configure(values=("workspace","asis") if public else ("workspace","asis","ascs","rescs","tiviss"))
        self.record_service.set("workspace")
        if public:self.advanced_setup.pack_forget()
        elif not self.advanced_setup.winfo_manager():self.advanced_setup.pack(fill="x")

    def register(self):
        if self.busy:return
        url,username,password,name=self.server_url.get(),self.server_name.get(),self.server_password.get(),self.owner_name.get()
        self.server_password.delete(0,"end")
        self.work(lambda:Remote(url).request("POST","/api/register",{"username":username,"password":password,"name":name}),
            lambda result:self.messagebox.showinfo("Access requested",result["message"]))

    def check_model(self):
        from ..model_support import system_report
        def done(report):
            self.hardware_report=report
            self.set_text(self.hardware_output,json.dumps(report,indent=2))
            if report["recommended_model"]:
                self.model_name.delete(0,"end");self.model_name.insert(0,report["recommended_model"])
        self.work(lambda:system_report(self.store.directory),done)

    def download_model(self):
        from ..model_support import download_recommended, system_report
        if self.busy:return
        if not self.messagebox.askyesno("Download model","This downloads the hardware-selected model using Ollama and can use several GB of internet data. Continue?"):return
        def work():return download_recommended(system_report(self.store.directory),lambda text:self.messages.put(("model-progress",text)))
        def done(model):
            self.model_name.delete(0,"end");self.model_name.insert(0,model);self.save_preferences()
        self.work(work,done)

    def server_history(self):
        self.work(lambda:self.require_remote().request("GET","/api/chats"),lambda data:self.set_text(self.history_text,json.dumps(data,indent=2)))

    def make_files_tab(self, frame):
        self.file_rows=[];self.cloud_file_rows=[]
        self.ttk.Label(frame,text="Local files and pending uploads · up to 8 MiB per file").pack(anchor="w")
        self.local_file_list=self.tk.Listbox(frame,width=1,height=5);self.local_file_list.pack(fill="x",pady=5)
        buttons=FlowRow(frame);buttons.pack(fill="x",pady=5)
        for label,command in (("Add local file",self.add_file),("Export cached file",self.export_file),("Sync pending uploads",self.sync_files)):
            self.ttk.Button(buttons,text=label,command=command).pack(side="left",padx=(0,6))
        self.ttk.Label(frame,text="My server files · owner filter is assigned by the server").pack(anchor="w",pady=(18,4))
        self.cloud_file_list=self.tk.Listbox(frame,width=1,height=6);self.cloud_file_list.pack(fill="both",expand=True)
        buttons=FlowRow(frame);buttons.pack(fill="x",pady=6)
        for label,command in (("Refresh cloud files",self.refresh_files),("Cache selected file",self.cache_file),("Delete selected cloud file",self.delete_file)):
            self.ttk.Button(buttons,text=label,command=command).pack(side="left",padx=(0,6))
        self.list_files()

    def list_files(self):
        self.file_rows=self.store.files(self.profile);self.local_file_list.delete(0,"end")
        for row in self.file_rows:self.local_file_list.insert("end",row["name"]+(" [pending]" if row["pending"] else " [cached]"))

    def add_file(self):
        from tkinter import filedialog
        path=filedialog.askopenfilename()
        if not path:return
        try:
            with open(path,"rb") as stream:data=stream.read(8*1024*1024+1)
            self.store.add_file(self.profile,Path(path).name,data);self.list_files()
        except Exception as exc:self.messagebox.showerror("File",str(exc))

    def export_file(self):
        from tkinter import filedialog
        selected=self.local_file_list.curselection()
        if not selected:return
        row=self.file_rows[selected[0]];target=filedialog.asksaveasfilename(initialfile=row["name"])
        if target:shutil.copyfile(row["path"],target)

    def sync_files(self):
        def work():
            remote=self.require_remote();count=0
            for row in self.store.files(self.profile):
                if row["pending"]:
                    data=Path(row["path"]).read_bytes()
                    meta=remote.request("POST","/api/files",{"name":row["name"],"upload_id":row["id"],"base64":base64.b64encode(data).decode()})
                    self.store.file_ack(self.profile,row["id"],meta["id"]);count+=1
            return count
        def done(count):self.list_files();self.messagebox.showinfo("Files synced",str(count)+" file(s) uploaded")
        self.work(work,done)

    def refresh_files(self):
        def work():
            rows=[];offset=0;remote=self.require_remote()
            while offset<10000:
                page=remote.request("GET","/api/files?offset="+str(offset));rows.extend(page["items"]);offset+=len(page["items"])
                if offset>=page["total"]:return rows
                if not page["items"]:raise RemoteError("Incomplete cloud file page.")
            raise RemoteError("Cloud file scan limit reached.")
        def done(rows):
            self.cloud_file_rows=rows;self.cloud_file_list.delete(0,"end")
            for row in rows:self.cloud_file_list.insert("end",row["filename"]+" · "+str(row["size"])+" bytes")
        self.work(work,done)

    def cache_file(self):
        import hashlib
        selected=self.cloud_file_list.curselection()
        if not selected:return
        row=self.cloud_file_rows[selected[0]]
        def work():
            result=self.require_remote().request("GET","/api/files/"+row["id"])
            data=base64.b64decode(result["base64"],validate=True)
            if hashlib.sha256(data).hexdigest()!=result["metadata"]["sha256"]:raise ValueError("File integrity check failed.")
            self.store.add_file(self.profile,row["filename"],data,row["id"])
        self.work(work,lambda _:self.list_files())

    def delete_file(self):
        selected=self.cloud_file_list.curselection()
        if not selected:return
        row=self.cloud_file_rows[selected[0]]
        if not self.messagebox.askyesno("Delete cloud file","Delete "+row["filename"]+" from cloud storage? Local cached copies remain."):return
        self.work(lambda:self.require_remote().request("POST","/api/files/"+row["id"]+"/delete"),lambda _:self.cloud_file_list.delete(selected[0]))

    def close(self):
        self.closed = True
        if self.runner: threading.Thread(target=self.runner.stop, daemon=False).start()
        if self.remote: threading.Thread(target=self.remote.close, daemon=True).start()
        # Daemon workers may still finish a database write; the OS closes the DB at process exit.
        self.root.destroy()


def launch(directory, server):
    try:
        import tkinter as tk
        configure_dpi()
        root = tk.Tk()
    except Exception as exc:
        raise SystemExit("Desktop requires Python Tk and a graphical session. On Ubuntu/Debian install python3-tk. Use core-client browser on headless devices.") from exc
    Desktop(root, directory, server)
    root.mainloop()
    return 0
