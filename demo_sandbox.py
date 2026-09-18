import os
import uuid
import json
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Impor Base dan Model User dari database.py yang sudah ada
from database import Base, User

SANDBOX_DIR = os.path.abspath("demo_sandboxes")
os.makedirs(SANDBOX_DIR, exist_ok=True)
META_FILE = os.path.join(SANDBOX_DIR, "metadata.json")


def load_metadata():
    if not os.path.exists(META_FILE):
        return {}
    with open(META_FILE, "r") as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def save_metadata(data):
    with open(META_FILE, "w") as f:
        json.dump(data, f, indent=2)


def buat_sandbox_baru() -> str:
    """Membuat ID Sandbox, file SQLite baru, struktur tabel, dan hanya 1 user admin."""
    sandbox_id = f"demo_{uuid.uuid4().hex[:12]}"
    db_path = os.path.join(SANDBOX_DIR, f"{sandbox_id}.dat")

    # 1. Bangun engine SQLite untuk sandbox ini
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )

    # 2. Bangun seluruh tabel dalam keadaan kosong (COA, Jurnal, Cabang, dll)
    Base.metadata.create_all(bind=engine)

    # 3. Masukkan HANYA 1 user admin / 123 sebagai super_user
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = Session()
    try:
        admin_user = User(
            username="admin",
            password="123",  # Atau hash_password("123") jika menggunakan sistem security hash
            nama_karyawan="Administrator Demo",
            role="super_user",
        )
        db.add(admin_user)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[Error Sandbox Init] Gagal membuat user admin: {e}")
    finally:
        db.close()

    # 4. Catat waktu kedaluwarsa 7 hari
    meta = load_metadata()
    meta[sandbox_id] = {
        "created_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(days=7)).isoformat(),
    }
    save_metadata(meta)

    return sandbox_id


def get_db_session_for_sandbox(sandbox_id: str):
    """Membuka koneksi ke file database sandbox milik pengunjung tertentu."""
    db_path = os.path.join(SANDBOX_DIR, f"{sandbox_id}.dat")
    if not os.path.exists(db_path):
        return None
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session()
