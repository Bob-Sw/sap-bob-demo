import os
import json
import base64
import subprocess
from datetime import datetime
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

LICENSE_FILE = "license.key"
TRIAL_FILE = "data_internal/sys_trial.dat"
MAX_TRIAL_DAYS = 120
MAX_TRIAL_JURNAL = 1000

# PUBLIC KEY (Boleh dilihat siapa saja, karena hanya bisa memverifikasi, BUKAN membuat kunci)
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
Deandra&Dhimas#Harijanto@8898*^($)... (GANTI DENGAN ISI public_key.pem ANDA) ...
-----END PUBLIC KEY-----"""


def get_machine_id() -> str:
    """Mengambil UUID unik hardware klien."""
    try:
        output = subprocess.check_output("wmic csproduct get uuid", shell=True).decode()
        lines = [line.strip() for line in output.split("\n") if line.strip()]
        if len(lines) >= 2:
            return lines[1]
    except Exception:
        pass
    import platform, uuid

    return f"{platform.node()}-{uuid.getnode()}"


def is_license_valid() -> bool:
    """Verifikasi tanda tangan RSA lisensi secara offline."""
    if not os.path.exists(LICENSE_FILE):
        return False
    try:
        with open(LICENSE_FILE, "r") as f:
            license_b64 = f.read().strip()

        signature = base64.b64decode(license_b64)
        current_mid = get_machine_id().strip().encode("utf-8")

        # Load public key
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM)

        # Verifikasi digital signature
        public_key.verify(
            signature,
            current_mid,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256(),
        )
        return True  # Tanda tangan sah & cocok dengan perangkat ini!
    except (InvalidSignature, Exception):
        return False


def init_trial_tracker():
    os.makedirs(os.path.dirname(TRIAL_FILE), exist_ok=True)
    if not os.path.exists(TRIAL_FILE):
        first_date = datetime.now().strftime("%Y-%m-%d")
        data = {"start_date": first_date}
        with open(TRIAL_FILE, "w") as f:
            json.dump(data, f)


def get_trial_info(total_jurnal_saat_ini: int) -> dict:
    if is_license_valid():
        return {
            "is_full_version": True,
            "is_locked": False,
            "pesan": "Versi Penuh Berlisensi Resmi",
        }

    init_trial_tracker()
    try:
        with open(TRIAL_FILE, "r") as f:
            data = json.load(f)
        start_date = datetime.strptime(data.get("start_date"), "%Y-%m-%d")
        selisih_hari = (datetime.now() - start_date).days
        sisa_hari = MAX_TRIAL_DAYS - selisih_hari
        sisa_jurnal = MAX_TRIAL_JURNAL - total_jurnal_saat_ini

        is_locked = (sisa_hari <= 0) or (sisa_jurnal <= 0)
        alasan = ""
        if sisa_hari <= 0:
            alasan = f"Masa trial {MAX_TRIAL_DAYS} hari telah habis."
        elif sisa_jurnal <= 0:
            alasan = f"Batas maksimal input {MAX_TRIAL_JURNAL} nomor jurnal trial telah tercapai."

        return {
            "is_full_version": False,
            "is_locked": is_locked,
            "sisa_hari": max(0, sisa_hari),
            "sisa_jurnal": max(0, sisa_jurnal),
            "alasan": alasan,
        }
    except Exception:
        return {
            "is_full_version": False,
            "is_locked": True,
            "alasan": "Validasi trial gagal.",
        }
