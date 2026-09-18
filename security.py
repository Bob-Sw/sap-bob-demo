import os
import hashlib
from datetime import datetime, timedelta
from typing import List, Optional
import bcrypt

if not hasattr(bcrypt, "__about__"):

    class DummyAbout:
        __version__ = getattr(bcrypt, "__version__", "4.0.1")

    bcrypt.__about__ = DummyAbout()
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.context import CryptContext
from fastapi import Request, HTTPException, status, Depends
from sqlalchemy.orm import Session
from database import get_session, User, ActivityLog

# Kunci rahasia server (Generate otomatis atau ambil dari ENV)
SECRET_KEY = os.environ.get(
    "SAP_SECRET_KEY", "SAP_BOB_ENTERPRISE_SECRET_KEY_2026_VERY_SECURE"
)
COOKIE_NAME = "sap_session"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeTimedSerializer(SECRET_KEY)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifikasi kecocokan password plaintext, SHA-256, atau Bcrypt."""
    if not hashed_password:
        return False

    # 1. Jika sudah berformat Bcrypt
    if hashed_password.startswith(("$2b$", "$2a$")):
        return pwd_context.verify(plain_password, hashed_password)

    # 2. Jika akun lama masih berupa Plaintext biasa
    if plain_password == hashed_password:
        return True

    # 3. Fallback jika hash lama berupa SHA-256
    return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password


def hash_password(password: str) -> str:
    """Enkripsi password menggunakan bcrypt."""
    return pwd_context.hash(password)


def buat_token_sesi(username: str, role: str) -> str:
    """Membuat session token berumur 8 jam."""
    payload = {"username": username, "role": role.strip().lower()}
    return serializer.dumps(payload)


def validasi_token_sesi(token: str) -> dict:
    """Membongkar dan memverifikasi token sesi."""
    try:
        # Maksimal token berlaku 8 jam (28800 detik)
        data = serializer.loads(token, max_age=28800)
        return data
    except SignatureExpired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesi telah kedaluwarsa. Silakan login kembali.",
        )
    except BadSignature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesi tidak valid / tanda tangan palsu.",
        )


# Dependensi FastAPI untuk mengekstrak pengguna aktif dari Cookie
async def get_current_user(
    request: Request, db: Session = Depends(get_session)
) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Akses ditolak: Anda belum login.",
        )

    sesi = validasi_token_sesi(token)
    user = db.query(User).filter(User.username == sesi["username"]).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Pengguna tidak ditemukan."
        )
    if hasattr(user, "status") and getattr(user, "status", "Aktif") != "Aktif":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Akun dinonaktifkan."
        )

    return user


# Dependensi Pengecek Hak Akses (RBAC Backend)
def require_roles(allowed_roles: List[str]):
    def role_checker(current_user: User = Depends(get_current_user)):
        user_role = str(current_user.role).strip().lower().replace(" ", "_")

        # Peta sinonim/alias antar skema lama dan baru
        role_aliases = {
            "maker": ["maker", "gl_user"],
            "gl_user": ["maker", "gl_user"],
            "super_user": ["super_user", "superuser", "admin"],
        }

        # Perluas daftar role yang diizinkan
        expanded_allowed = set()
        for r in allowed_roles:
            r_clean = r.strip().lower().replace(" ", "_")
            expanded_allowed.add(r_clean)
            if r_clean in role_aliases:
                expanded_allowed.update(role_aliases[r_clean])

        # Cek apakah role user ada di dalam daftar yang diizinkan
        if user_role not in expanded_allowed and "super_user" not in user_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Wewenang ditolak. Tindakan ini hanya untuk role: {', '.join(allowed_roles)}",
            )
        return current_user

    return role_checker


# Helper Audit Log yang terikat identitas server
def catat_audit(
    db: Session,
    request: Request,
    user: User,
    modul: str,
    aksi: str,
    referensi: str = "-",
    keterangan: str = "",
):
    try:
        ip_client = (
            request.headers.get("cf-connecting-ip")
            or (
                request.headers.get("x-forwarded-for").split(",")[0].strip()
                if request.headers.get("x-forwarded-for")
                else None
            )
            or (request.client.host if request.client else "127.0.0.1")
        )

        log = ActivityLog(
            waktu=datetime.now(),
            username=user.username,
            nama_user=getattr(user, "nama_karyawan", user.username),
            role=user.role,
            modul=modul,
            aksi=aksi,
            referensi=referensi,
            keterangan=keterangan,
            ip_address=ip_client,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        print(f"Gagal mencatat audit log: {e}")
