"""Device-local profiles, cached records and a durable explicit-sync queue."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class LocalStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "desktop.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY,name TEXT UNIQUE,salt BLOB,digest BLOB,server TEXT,remote_id TEXT);
        CREATE TABLE IF NOT EXISTS records(profile TEXT,service TEXT,key TEXT,value TEXT,base TEXT,pending INTEGER,
          PRIMARY KEY(profile,service,key));
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,profile TEXT,name TEXT,path TEXT,remote_id TEXT,pending INTEGER);
        CREATE TABLE IF NOT EXISTS history(id TEXT PRIMARY KEY,profile TEXT,at REAL,source TEXT,prompt TEXT,output TEXT);
        ''')
        if "remote_role" not in {r[1] for r in self.db.execute("PRAGMA table_info(profiles)")}:
            self.db.execute("ALTER TABLE profiles ADD COLUMN remote_role TEXT DEFAULT 'local'")
        self.db.commit()
        if os.name == "posix":
            self.directory.chmod(0o700)
            (self.directory / "desktop.sqlite3").chmod(0o600)

    @staticmethod
    def digest(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)

    def create(self, name, password):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", name):
            raise ValueError("Local name: 3–40 letters, numbers, dots, underscores or dashes.")
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise ValueError("Use a local passphrase of 12–256 characters.")
        salt, identity = secrets.token_bytes(16), uuid.uuid4().hex
        try:
            with self.lock, self.db:
                self.db.execute("INSERT INTO profiles(id,name,salt,digest,server,remote_id) VALUES(?,?,?,?,?,?)", (identity, name.lower(), salt, self.digest(password, salt), "", ""))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Local profile already exists.") from exc
        return identity

    def unlock(self, name, password):
        if not isinstance(name, str) or not isinstance(password, str) or len(password) > 256:
            raise PermissionError("Incorrect local name or passphrase.")
        with self.lock:
            row = self.db.execute("SELECT * FROM profiles WHERE name=?", (name.lower(),)).fetchone()
            actual = self.digest(password, row["salt"] if row else b"0" * 16)
            if row is None or not hmac.compare_digest(actual, row["digest"]):
                raise PermissionError("Incorrect local name or passphrase.")
            return row["id"]

    def bind(self, profile, server, remote_id, role="member"):
        with self.lock, self.db:
            row = self.db.execute("SELECT server,remote_id,remote_role FROM profiles WHERE id=?", (profile,)).fetchone()
            if row is None:
                raise PermissionError("Unlock a local profile first.")
            if row["remote_id"] and (row["server"] != server or row["remote_id"] != remote_id):
                raise PermissionError("This local profile belongs to another server account. Create a separate local profile.")
            self.db.execute("UPDATE profiles SET server=?,remote_id=?,remote_role=? WHERE id=?", (server, remote_id, role, profile))

    def binding(self, profile):
        with self.lock:
            row = self.db.execute("SELECT server,remote_id,remote_role FROM profiles WHERE id=?", (profile,)).fetchone()
            return dict(row) if row else None

    def save(self, profile, service, key, value):
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", service) or not isinstance(key, str) or not 1 <= len(key) <= 200 or not isinstance(value, dict):
            raise ValueError("Choose a service, a key of 1–200 characters and a JSON object.")
        raw = json.dumps(value)
        if len(raw.encode()) > 200000:
            raise ValueError("Record exceeds the local size limit.")
        with self.lock, self.db:
            self.db.execute('''INSERT INTO records VALUES(?,?,?,?,?,1)
              ON CONFLICT(profile,service,key) DO UPDATE SET value=excluded.value,pending=1''', (profile, service, key, raw, ""))

    def records(self, profile, service=None, pending=False):
        query, params = "SELECT * FROM records WHERE profile=?", [profile]
        if service:
            query += " AND service=?"; params.append(service)
        if pending:
            query += " AND pending=1"
        with self.lock:
            return [{**dict(row), "value": json.loads(row["value"])} for row in self.db.execute(query + " ORDER BY service,key", params)]

    def cache(self, profile, service, rows):
        with self.lock, self.db:
            for row in rows:
                self.db.execute('''INSERT INTO records VALUES(?,?,?,?,?,0)
                  ON CONFLICT(profile,service,key) DO UPDATE SET value=excluded.value,base=excluded.base
                  WHERE records.pending=0''', (profile, service, row["key"], json.dumps(row["value"]), revision(row["value"])))

    def acknowledged(self, profile, item, saved):
        # A local edit made during the network request must remain queued.
        with self.lock, self.db:
            current = self.db.execute("SELECT value FROM records WHERE profile=? AND service=? AND key=?", (profile, item["service"], item["key"])).fetchone()
            if current:
                clean = json.loads(current["value"]) == item["value"]
                self.db.execute("UPDATE records SET base=?,pending=? WHERE profile=? AND service=? AND key=?",
                    (revision(saved["value"]), 0 if clean else 1, profile, item["service"], item["key"]))

    def history(self, profile):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM history WHERE profile=? ORDER BY at DESC LIMIT 50", (profile,))]

    def log(self, profile, source, prompt, output):
        with self.lock, self.db:
            self.db.execute("INSERT INTO history VALUES(?,?,?,?,?,?)", (uuid.uuid4().hex, profile, time.time(), source, prompt, output[-200000:]))

    def close(self):
        with self.lock: self.db.close()

    def add_file(self, profile, name, data, remote_id=""):
        if len(data)>8*1024*1024: raise ValueError("Files must be at most 8 MiB.")
        identity=uuid.uuid4().hex
        home=self.directory/"profiles"/profile/"files";home.mkdir(parents=True,exist_ok=True,mode=0o700)
        if sum(Path(r["path"]).stat().st_size for r in self.files(profile) if Path(r["path"]).is_file())+len(data)>250*1024*1024:
            raise ValueError("Local file cache quota is 250 MiB for this profile.")
        path=home/identity;path.write_bytes(data)
        if os.name=="posix":path.chmod(0o600)
        with self.lock,self.db:
            self.db.execute("INSERT INTO files VALUES(?,?,?,?,?,?)",(identity,profile,Path(name).name,str(path),remote_id,0 if remote_id else 1))
        return identity

    def files(self, profile):
        with self.lock:return [dict(r) for r in self.db.execute("SELECT * FROM files WHERE profile=? ORDER BY name",(profile,))]

    def file_ack(self, profile, identity, remote_id):
        with self.lock,self.db:
            self.db.execute("UPDATE files SET pending=0,remote_id=? WHERE profile=? AND id=?",(remote_id,profile,identity))
