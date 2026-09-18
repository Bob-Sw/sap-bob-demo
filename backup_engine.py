import os
import sqlite3
import base64
import hashlib
import shutil
from datetime import datetime
from cryptography.fernet import Fernet
from database import DATABASE_URL

# Lokasi database dan folder backup
DB_DIR = os.path.abspath("data_internal")

BACKUP_DIR = os.path.abspath("data_internal/secure_backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

# KUNCI MASTER ENKRIPSI BACKUP
# PERINGATAN: Simpan kunci ini dengan aman! Jangan diubah setelah backup dibuat
BACKUP_SECRET_KEY = os.environ.get(
    "SAP_BACKUP_KEY", "KUNCI_RAHASIAnya_Deandra&Dhimas_HARIJANTO_!@#$%^"
)


def _get_cipher_suite():
    """Menghasilkan kunci enkripsi AES-256 (Fernet) dari Master Key."""
    key = hashlib.sha256(BACKUP_SECRET_KEY.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key)
    return Fernet(fernet_key)


def buat_backup_terenkripsi() -> dict:
    """
    1. Membuat snapshot database secara konsisten (tanpa lock)
    2. Mengenkripsi snapshot dengan AES-256
    3. Menyimpan dalam format biner terproteksi (.enc)
    """
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError("File database utama tidak ditemukan!")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_sqlite = os.path.join(BACKUP_DIR, f"temp_{timestamp}.tmp")
    encrypted_file = os.path.join(BACKUP_DIR, f"BACKUP_SAPBOB_{timestamp}.enc")

    try:
        # 1. Gunakan SQLite Online Backup API (Aman dari data korup saat multi-user aktif)
        src_conn = sqlite3.connect(DB_FILE)
        dst_conn = sqlite3.connect(temp_sqlite)
        with dst_conn:
            src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()

        # 2. Baca file sementara dan lakukan Enkripsi Penuh
        with open(temp_sqlite, "rb") as f_in:
            raw_data = f_in.read()

        cipher = _get_cipher_suite()
        encrypted_data = cipher.encrypt(raw_data)

        # 3. Simpan file terenkripsi
        with open(encrypted_file, "wb") as f_out:
            f_out.write(encrypted_data)

        # Kunci hak akses file (Linux/macOS)
        try:
            os.chmod(encrypted_file, 0o600)
        except Exception:
            pass

        ukuran_kb = round(os.path.getsize(encrypted_file) / 1024, 2)

        return {
            "status": "success",
            "file_name": os.path.basename(encrypted_file),
            "ukuran": f"{ukuran_kb} KB",
            "waktu": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    finally:
        # Pastikan file sementara selalu dihapus agar tidak meninggalkan jejak terbuka
        if os.path.exists(temp_sqlite):
            os.remove(temp_sqlite)


def pulihkan_backup_terenkripsi(file_name: str) -> dict:
    """Mendekripsi file backup dan mengembalikannya ke database aktif."""
    target_backup = os.path.join(BACKUP_DIR, file_name)
    if not os.path.exists(target_backup):
        raise FileNotFoundError("File backup tidak ditemukan!")

    # 1. Dekripsi data
    cipher = _get_cipher_suite()
    with open(target_backup, "rb") as f_in:
        encrypted_data = f_in.read()

    try:
        decrypted_data = cipher.decrypt(encrypted_data)
    except Exception:
        raise ValueError("Gagal mendekripsi: Kunci keamanan salah atau file rusak!")

    # 2. Tulis kembali ke sys_bob.dat secara atomik
    temp_restore = DB_FILE + ".restoring"
    with open(temp_restore, "wb") as f_out:
        f_out.write(decrypted_data)

    # Ganti file database utama
    if os.path.exists(DB_FILE):
        os.replace(temp_restore, DB_FILE)
    else:
        os.rename(temp_restore, DB_FILE)

    return {
        "status": "success",
        "pesan": f"Database berhasil dipulihkan dari {file_name}",
    }


def list_file_backup():
    """Melihat daftar seluruh file backup terenkripsi yang ada."""
    files = []
    for f in os.listdir(BACKUP_DIR):
        if f.endswith(".enc"):
            p = os.path.join(BACKUP_DIR, f)
            files.append(
                {
                    "file_name": f,
                    "ukuran": f"{round(os.path.getsize(p) / 1024, 2)} KB",
                    "waktu_buat": datetime.fromtimestamp(os.path.getctime(p)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            )
    # Urutkan dari yang paling baru
    files.sort(key=lambda x: x["waktu_buat"], reverse=True)
    return files


def pindahkan_file_backup(file_name: str, target_directory: str) -> dict:
    """
    Memindahkan file cadangan (.enc) dari folder backup internal
    ke disk/direktori lain (misal: flashdisk, D:/Backups, dll).
    """
    # Cegah Directory Traversal pada nama file
    clean_name = os.path.basename(file_name)
    source_path = os.path.join(BACKUP_DIR, clean_name)

    if not os.path.exists(source_path):
        raise FileNotFoundError(
            f"File backup '{clean_name}' tidak ditemukan di sistem!"
        )

    target_dir_abs = os.path.abspath(target_directory)
    os.makedirs(target_dir_abs, exist_ok=True)

    dest_path = os.path.join(target_dir_abs, clean_name)

    # Pindahkan file (shutil.move aman bekerja lintas partisi/disk drive)
    shutil.move(source_path, dest_path)

    return {
        "status": "success",
        "file_name": clean_name,
        "target_path": dest_path,
        "pesan": f"File {clean_name} berhasil dipindahkan ke {dest_path}",
    }


def hapus_file_backup(file_name: str) -> dict:
    """Menghapus file cadangan (.enc) secara permanen."""
    clean_name = os.path.basename(file_name)
    target_path = os.path.join(BACKUP_DIR, clean_name)

    if not os.path.exists(target_path):
        raise FileNotFoundError(f"File backup '{clean_name}' tidak ditemukan!")

    os.remove(target_path)

    return {
        "status": "success",
        "pesan": f"File cadangan '{clean_name}' berhasil dihapus permanen dari server.",
    }
