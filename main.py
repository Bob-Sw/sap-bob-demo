import os
import re
import shutil
import time
import threading
import webbrowser
from datetime import datetime
from typing import Optional, List, Any, Union
import license_engine
from backup_engine import (
    buat_backup_terenkripsi,
    pulihkan_backup_terenkripsi,
    list_file_backup,
    pindahkan_file_backup,
    hapus_file_backup,
)
from security import require_roles, catat_audit
from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    Request,
    Response,
    UploadFile,
    File,
    Form,
    status,
)
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Text,
    ForeignKey,
    DateTime,
    func,
    text,
)
from sqlalchemy.orm import relationship
import uvicorn
import traceback
from database import (
    init_db,
    get_session,
    ActivityLog,
    JurnalHeader,
    JurnalDetail,
    User,
    COA,
    Cabang,
    Departemen,
    Voucher,
    FixedAsset,
    MasterBarang,
    InventoryHeader,
    InventoryDetail,
    TransaksiInventory,
    ProfilUsaha,
    RekeningUsaha,
    MasterVendor,
    VendorBillHeader,
    VendorBillDetail,
    VendorPayment,
    PaymentRequest,
    MasterCustomer,
    CustomerInvoiceHeader,
    CustomerInvoiceDetail,
    MasterCustomer,
    catat_log,
)

from security import (
    get_current_user,
    require_roles,
    buat_token_sesi,
    verify_password,
    catat_audit,
    COOKIE_NAME,
)
from scheduler_backup import mulai_scheduler_backup
import demo_sandbox
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


# Inisialisasi Database & App
init_db()
app = FastAPI(title="Sistem Akuntansi - Modul SAP BoB (Secure Enterprise Edition)")
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

# Folder Uploads Profil Usaha
os.makedirs("web/uploads", exist_ok=True)
os.makedirs("uploads", exist_ok=True)


# CORS Kebijakan Terkendali
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://bahanaoptima.my.id",
        "https://demo.bahanaoptima.my.id",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# SCHEMA (PYDANTIC MODELS)
# ==========================================
class PayloadLicense(BaseModel):
    license_key: str


class FormLogin(BaseModel):
    username: str
    password: str


class PayloadUser(BaseModel):
    id: Optional[int] = None
    username: str
    password: str
    nama_karyawan: str
    role: str


class PayloadOrg(BaseModel):
    kode: str
    nama: str
    keterangan: Optional[str] = "-"
    status: Optional[str] = "Aktif"


class PayloadCOA(BaseModel):
    kode: str
    nama: str
    kelompok: Optional[str] = "-"
    kategori: Optional[str] = "-"
    saldo_normal: Optional[str] = "Debit"
    status: Optional[str] = "Aktif"


class PayloadCOABulk(BaseModel):
    data: List[PayloadCOA]


class CoaItem(BaseModel):
    no_akun: str
    nama_akun: str
    tipe_akun: Optional[str] = "-"
    laporan: Optional[str] = "Neraca"
    posisi_normal: Optional[str] = "Debit"

    @field_validator("no_akun", "nama_akun", mode="before")
    @classmethod
    def pastikan_string(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("tipe_akun", "laporan", "posisi_normal", mode="before")
    @classmethod
    def sanitasi_default(cls, v: Any) -> str:
        if v is None or not str(v).strip():
            return "-"
        return str(v).strip()


class BarisJurnal(BaseModel):
    kode_akun: str
    nama_akun: str
    keterangan: str
    debit: float
    kredit: float


class PayloadJurnal(BaseModel):
    kode_voucher: str
    kode_cabang: Optional[str] = "HO"
    kode_departemen: Optional[str] = "FIN"
    tanggal: str
    no_referensi: str
    status: str
    baris_detail: List[BarisJurnal]


class PayloadFixedAsset(BaseModel):
    kode_aset: str
    nama_aset: str
    kode_cabang: str = "HO"
    kode_departemen: str = "FIN"
    golongan_fiskal: str
    tgl_perolehan: str
    harga_perolehan: float
    nilai_residu: float = 0.0
    masa_manfaat_bulan: int
    akun_aset: str
    akun_akumulasi: str
    akun_beban: str


class PayloadDisposeAsset(BaseModel):
    kode_aset: str
    tgl_penjualan: str
    harga_jual: float
    akun_kas: str
    akun_laba_rugi: str
    kode_cabang: str = "HO"
    kode_departemen: str = "FIN"
    keterangan: Optional[str] = "Penjualan / Pelepasan Aset Tetap"


class ItemAssetMigrasi(BaseModel):
    kode_aset: str
    nama_aset: str
    kode_cabang: str = "00"
    kode_departemen: str = "00"
    golongan_fiskal: str = "GOL1"
    tgl_perolehan: str
    harga_perolehan: float
    akumulasi_penyusutan: float = 0.0
    nilai_residu: float = 0.0
    masa_manfaat_bulan: int = 48
    akun_aset: str
    akun_akumulasi: str
    akun_beban: str


class PayloadVendor(BaseModel):
    kode_vendor: str
    nama_vendor: str
    npwp: Optional[str] = "-"
    alamat: Optional[str] = "-"
    telepon: Optional[str] = "-"
    email: Optional[str] = "-"
    kontak_person: Optional[str] = "-"
    termin_hari: Optional[int] = 30
    kode_akun_hutang: str
    nama_akun_hutang: str
    nama_bank: Optional[str] = "-"
    no_rekening: Optional[str] = "-"
    atas_nama: Optional[str] = "-"
    status: Optional[str] = "Aktif"


class ItemBillDetail(BaseModel):
    kode_akun: str
    nama_akun: str
    deskripsi: Optional[str] = ""
    nominal: float


class PayloadVendorBill(BaseModel):
    kode_voucher: Optional[str] = "AP"
    no_bill: str
    no_faktur_vendor: str
    tanggal: str
    jatuh_tempo: str
    kode_vendor: str
    kode_cabang: Optional[str] = "HO"
    kode_departemen: Optional[str] = "FIN"
    keterangan: str
    kode_akun_ppn: Optional[str] = ""
    ppn_nominal: Optional[float] = 0.0
    jenis_pph: Optional[str] = ""
    kode_akun_pph: Optional[str] = ""
    pph_nominal: Optional[float] = 0.0
    pembuat: Optional[str] = "FIN_AP"
    detail: List[ItemBillDetail]


class PayloadVendorPayment(BaseModel):
    kode_voucher: Optional[str] = "BKK"
    no_payment: str
    tanggal: str
    kode_vendor: str
    no_bill: str
    kode_cabang: Optional[str] = "HO"
    kode_departemen: Optional[str] = "FIN"
    keterangan: str
    kode_akun_kas: str
    nominal_bayar: float
    jenis_pph: Optional[str] = ""
    kode_akun_pph: Optional[str] = ""
    pph_nominal: Optional[float] = 0.0
    pembuat: Optional[str] = "FIN_AP"


class PayloadRFP(BaseModel):
    no_rfp: str
    tanggal: str
    no_bill: str
    nominal_diajukan: float
    keterangan: str
    pemohon: Optional[str] = "Staff_AP"


class PayloadFinanceExecute(BaseModel):
    rfp_ids: List[int]
    kode_voucher: Optional[str] = "BKK"
    no_payment: str
    tanggal: str
    kode_cabang: Optional[str] = "HO"
    kode_departemen: Optional[str] = "FIN"
    kode_akun_kas: str
    keterangan: str
    pembuat: Optional[str] = "Finance"


class PayloadFinancePayment(BaseModel):
    no_rfp: str
    no_payment: str
    tanggal: str
    kode_voucher: Optional[str] = "BKK"
    kode_cabang: Optional[str] = "HO"
    kode_departemen: Optional[str] = "FIN"
    kode_akun_kas: str
    jenis_pph: Optional[str] = ""
    kode_akun_pph: Optional[str] = ""
    pph_nominal: Optional[float] = 0.0
    keterangan: str
    pembuat: Optional[str] = "Finance"


class PayloadBatchPayment(BaseModel):
    rfp_ids: List[int]
    kode_voucher: Optional[str] = "BKK"
    no_payment: str
    tanggal: str
    kode_cabang: Optional[str] = "HO"
    kode_departemen: Optional[str] = "FIN"
    kode_akun_kas: str
    jenis_pph: Optional[str] = ""
    kode_akun_pph: Optional[str] = ""
    pph_nominal: Optional[float] = 0.0
    keterangan: str
    pembuat: Optional[str] = "Finance"


class PayloadBarang(BaseModel):
    kode_barang: str
    nama_barang: str
    kategori: Optional[str] = "-"
    satuan: Optional[str] = "PCS"
    harga_beli: float = 0.0
    harga_jual: float = 0.0
    kode_akun_persediaan: str
    status: Optional[str] = "Aktif"


class DetailInv(BaseModel):
    kode_barang: str
    qty: float
    harga_satuan: float
    total_nilai: float


class PayloadInventory(BaseModel):
    no_bukti: str
    tanggal: str
    jenis_transaksi: str
    kode_cabang: str = "HO"
    kode_departemen: str = "LOG"
    keterangan: str
    akun_lawan: str
    pembuat: str
    detail: List[DetailInv]


class PayloadTransaksiInventory(BaseModel):
    no_bukti: str
    tanggal: str
    kode_barang: str
    jenis_transaksi: str
    qty: float
    harga_satuan: float
    akun_lawan: str
    kode_cabang: str = "HO"
    kode_departemen: Optional[str] = "LOG"
    keterangan: str = "Transaksi Inventory"


class ItemSyncDetail(BaseModel):
    kode_barang: str
    qty: float
    harga_satuan: float
    total_nilai: float


class HeaderSyncItem(BaseModel):
    no_bukti: str
    tanggal: str
    jenis_transaksi: str
    kode_cabang: str
    kode_departemen: str
    keterangan: str
    akun_lawan: str
    details: List[ItemSyncDetail]


class PayloadBatchSync(BaseModel):
    id_pengirim: str
    items: List[HeaderSyncItem]


class RekeningItem(BaseModel):
    id: Optional[int] = None
    nama_bank: str
    nomor_rekening: str
    atas_nama: str
    catatan: Optional[str] = ""


class ProfilUsahaPayload(BaseModel):
    nama_usaha: str
    npwp: str
    alamat: str
    kota: str
    rekening_list: List[RekeningItem] = []


class ItemOpname(BaseModel):
    kode_barang: str
    stok_buku: float
    stok_fisik: float
    selisih: float
    harga_satuan: float


class PayloadStockOpname(BaseModel):
    no_bukti: str
    tanggal: str
    kode_cabang: str = "HO"
    akun_selisih: str
    keterangan: str
    items: List[ItemOpname]


class PayloadDepreciationRun(BaseModel):
    periode_bulan: str
    kode_cabang: str = "SEMUA"


class PayloadGantiPassword(BaseModel):
    username: str
    password_lama: str
    password_baru: str


class PayloadSyncHO(BaseModel):
    kode_cabang: str
    kode_departemen: str
    total_dokumen: int
    total_nilai_masuk: float
    total_nilai_keluar: float
    daftar_no_bukti: List[str]


class DetailMigrasi(BaseModel):
    kode_akun: str
    nama_akun: str
    keterangan: str
    debit: float
    kredit: float


class JurnalMigrasi(BaseModel):
    no_referensi: str
    tanggal: str
    kode_voucher: str
    keterangan: str
    detail: List[DetailMigrasi]


# VERSI DEMO
@app.get("/demo")
async def masuk_mode_demo():
    """Rute pertama yang dibuka calon pengguna: langsung buat sandbox baru dan set cookie."""
    sandbox_id = demo_sandbox.buat_sandbox_baru()

    # Alihkan ke halaman login utama
    res = RedirectResponse(url="/", status_code=302)

    # Pasang cookie Sandbox ID (berlaku 7 hari, secure=True untuk HTTPS Cloudflare)
    res.set_cookie(
        key="demo_sandbox_id",
        value=sandbox_id,
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=True,
    )
    return res


def get_current_db(request: Request):
    sandbox_id = request.cookies.get("demo_sandbox_id")
    if not sandbox_id:
        from database import get_session

        db = get_session()
        try:
            yield db
        finally:
            db.close()
        return

    db = demo_sandbox.get_db_session_for_sandbox(sandbox_id)
    if not db:
        raise HTTPException(
            status_code=404,
            detail="Masa trial demo Anda (7 hari) telah berakhir atau database telah dibersihkan.",
        )
    try:
        yield db
    finally:
        db.close()


from apscheduler.schedulers.background import BackgroundScheduler


def bersihkan_sandbox_kedaluwarsa():
    meta = demo_sandbox.load_metadata()
    now = datetime.now()
    sandbox_aktif = {}

    for s_id, info in meta.items():
        expired_at = datetime.fromisoformat(info["expires_at"])
        if now > expired_at:
            # Hapus file database yg ada
            file_dat = os.path.join(demo_sandbox.SANDBOX_DIR, f"{s_id}.dat")
            if os.path.exists(file_dat):
                try:
                    os.remove(file_dat)
                    print(f"[Cleanup] Database sandbox {s_id} berhasil dihapus.")
                except Exception as e:
                    print(f"[Cleanup Error] Gagal hapus {s_id}: {e}")
        else:
            sandbox_aktif[s_id] = info

    demo_sandbox.save_metadata(sandbox_aktif)


# Jalankan scheduler di startup FastAPI
scheduler = BackgroundScheduler()
scheduler.add_job(bersihkan_sandbox_kedaluwarsa, "cron", hour=0, minute=0)
scheduler.start()


# ==========================================
# 1. AUTENTIKASI & MANAJEMEN SESI HTTP-ONLY
# ==========================================
@app.post("/api/login")
async def cek_login(data: FormLogin, request: Request, response: Response):
    db = get_session()
    try:
        user = (
            db.query(User)
            .filter(User.username == data.username, User.password == data.password)
            .first()
        )
        if not user or not verify_password(data.password, user.password):
            raise HTTPException(status_code=401, detail="Username atau password salah!")

        # Generate token aman server
        token = buat_token_sesi(user.username, user.role)

        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            secure=True,
            max_age=28800,  # 8 Jam
        )

        catat_audit(
            db,
            request,
            user,
            modul="AUTH",
            aksi="LOGIN",
            referensi=user.username,
            keterangan=f"User {user.username} berhasil login dengan wewenang {user.role}",
        )

        return {
            "status": "success",
            "role": user.role,
            "username": user.username,
            "nama_karyawan": user.nama_karyawan,
            "pesan": f"Selamat datang, {user.nama_karyawan}!",
        }
    finally:
        db.close()


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"status": "success", "pesan": "Berhasil keluar dari sistem."}


@app.get("/api/auth/me")
async def get_my_profile(current_user: User = Depends(get_current_user)):
    return {
        "username": current_user.username,
        "nama_karyawan": current_user.nama_karyawan,
        "role": current_user.role,
    }


@app.post("/api/user/ganti-password")
async def ganti_password_user(
    data: PayloadGantiPassword,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    if (
        current_user.username != data.username
        and current_user.role.lower() != "super_user"
    ):
        raise HTTPException(
            status_code=403, detail="Tidak diizinkan mengubah password pengguna lain!"
        )

    db = get_session()
    try:
        target_user = db.query(User).filter(User.username == data.username).first()
        if not target_user:
            raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan!")

        if current_user.role.lower() != "super_user" and not verify_password(
            data.password_lama, target_user.password
        ):
            raise HTTPException(status_code=400, detail="Password lama salah!")

        if len(data.password_baru.strip()) < 4:
            raise HTTPException(
                status_code=400, detail="Password baru minimal 4 karakter!"
            )

        from security import hash_password

        target_user.password = hash_password(data.password_baru.strip())

        catat_audit(
            db,
            request,
            current_user,
            modul="USER",
            aksi="UPDATE",
            referensi=data.username,
            keterangan="Memperbarui password akun",
        )
        db.commit()
        return {"status": "success", "pesan": "Password berhasil diperbarui!"}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ==========================================
# 2. RUTE LISENSI & ROOT UTAMA
# ==========================================
@app.get("/aktivasi")
async def halaman_aktivasi():
    return FileResponse("web/aktivasi.html")


@app.get("/api/license/status")
async def get_license_status():
    db = get_session()
    try:
        total_jurnal = db.query(JurnalHeader).count()
        trial_info = license_engine.get_trial_info(total_jurnal)
        return {
            "valid": license_engine.is_license_valid(),
            "machine_id": license_engine.get_machine_id(),
            "trial": trial_info,
        }
    finally:
        db.close()


@app.post("/api/license/activate")
async def api_license_activate(data: PayloadLicense):
    mid = license_engine.get_machine_id()
    expected_key = license_engine.generate_license_key(mid)

    if data.license_key.strip().upper() != expected_key:
        raise HTTPException(
            status_code=400, detail="License Key tidak cocok untuk perangkat ini!"
        )

    with open(license_engine.LICENSE_FILE, "w") as f:
        f.write(data.license_key.strip().upper())

    return {
        "status": "success",
        "pesan": "Perangkat berhasil diaktivasi secara permanen!",
    }


@app.get("/")
async def halaman_utama():
    db = get_session()
    try:
        total_jurnal = db.query(JurnalHeader).count()
        trial_info = license_engine.get_trial_info(total_jurnal)

        # Jika trial habis dan belum berlisensi penuh, kunci ke /aktivasi
        if not trial_info["is_full_version"] and trial_info["is_locked"]:
            return RedirectResponse(url="/aktivasi")

        return FileResponse("web/login.html")
    finally:
        db.close()


# ==========================================
# 3. RUTE HALAMAN WEB (FRONTEND VIEW)
# ==========================================
@app.get("/dashboard")
async def halaman_dashboard(current_user: User = Depends(get_current_user)):
    return FileResponse("web/dashboard.html")


@app.get("/gl")
async def halaman_gl(current_user: User = Depends(get_current_user)):
    return FileResponse("web/gl.html")


@app.get("/posting")
async def halaman_posting(
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    return FileResponse("web/posting.html")


@app.get("/buku_besar")
async def halaman_buku_besar():
    return FileResponse("web/buku_besar.html")


@app.get("/trial_balance")
async def halaman_trial_balance(
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    return FileResponse("web/trial_balance.html")


@app.get("/balance_sheet")
async def halaman_balance_sheet(
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    return FileResponse("web/balance_sheet.html")


@app.get("/profit_loss")
async def halaman_profit_loss(
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    return FileResponse("web/profit_loss.html")


@app.get("/cash_flow")
async def halaman_cash_flow(
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    return FileResponse("web/cash_flow.html")


@app.get("/laporan_kelompok")
async def halaman_laporan_kelompok(
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    return FileResponse("web/laporan_kelompok.html")


@app.get("/laporan_eksekutif")
async def halaman_laporan_eksekutif(
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    return FileResponse("web/laporan_eksekutif.html")


@app.get("/setup_user")
async def halaman_setup_user(
    current_user: User = Depends(require_roles(["super_user"])),
):
    return FileResponse("web/setup_user.html")


@app.get("/setup_org")
async def halaman_setup_org(
    current_user: User = Depends(require_roles(["super_user"])),
):
    return FileResponse("web/setup_org.html")


@app.get("/setup_voucher")
async def halaman_setup_voucher(
    current_user: User = Depends(require_roles(["super_user"])),
):
    return FileResponse("web/setup_voucher.html")


@app.get("/setup_coa")
async def halaman_setup_coa(
    current_user: User = Depends(require_roles(["super_user"])),
):
    return FileResponse("web/setup_coa.html")


@app.get("/setup_perusahaan")
async def halaman_setup_perusahaan(
    current_user: User = Depends(require_roles(["super_user"])),
):
    return FileResponse("web/setup_perusahaan.html")


@app.get("/log_activity")
async def halaman_log_activity(
    current_user: User = Depends(require_roles(["super_user"])),
):
    return FileResponse("web/log_activity.html")


@app.get("/migrasi")
async def halaman_migrasi(current_user: User = Depends(require_roles(["super_user"]))):
    return FileResponse("web/migrasi.html")


@app.get("/cetak_jurnal")
async def halaman_cetak_jurnal(current_user: User = Depends(get_current_user)):
    return FileResponse("web/cetak_jurnal.html")


@app.get("/fixed_asset")
async def halaman_fixed_asset(current_user: User = Depends(get_current_user)):
    return FileResponse("web/fixed_asset.html")


@app.get("/daftar_fixed_asset")
async def halaman_daftar_fixed_asset(current_user: User = Depends(get_current_user)):
    return FileResponse("web/daftar_fixed_asset.html")


@app.get("/workspace")
async def halaman_workspace(current_user: User = Depends(get_current_user)):
    return FileResponse("web/workspace.html")


@app.get("/master_vendor")
async def halaman_master_vendor(current_user: User = Depends(get_current_user)):
    return FileResponse("web/master_vendor.html")


@app.get("/vendor_bill")
async def halaman_vendor_bill(current_user: User = Depends(get_current_user)):
    return FileResponse("web/vendor_bill.html")


@app.get("/daftar_ap")
async def halaman_daftar_ap(current_user: User = Depends(get_current_user)):
    return FileResponse("web/daftar_ap.html")


@app.get("/vendor_payment")
async def halaman_vendor_payment(current_user: User = Depends(get_current_user)):
    return FileResponse("web/vendor_payment.html")


@app.get("/payment_request")
async def halaman_payment_request(current_user: User = Depends(get_current_user)):
    return FileResponse("web/payment_request.html")


@app.get("/master_barang")
async def halaman_master_barang(current_user: User = Depends(get_current_user)):
    return FileResponse("web/master_barang.html")


@app.get("/inventory")
async def halaman_inventory(current_user: User = Depends(get_current_user)):
    return FileResponse("web/inventory.html")


@app.get("/kartu_stok")
async def halaman_kartu_stok(current_user: User = Depends(get_current_user)):
    return FileResponse("web/kartu_stok.html")


@app.get("/stock_opname")
async def halaman_stock_opname(current_user: User = Depends(get_current_user)):
    return FileResponse("web/stock_opname.html")


@app.get("/role_akses.js")
async def get_role_akses():
    return FileResponse("web/role_akses.js")


# ==========================================
# 4. DASHBOARD & SETUP MASTER DATA
# ==========================================
@app.get("/api/dashboard/summary")
async def get_dashboard_summary(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        total_draft = (
            db.query(JurnalHeader).filter(JurnalHeader.status == "Draft").count()
        )
        total_pending = (
            db.query(JurnalHeader).filter(JurnalHeader.status == "Pending").count()
        )
        total_posted = (
            db.query(JurnalHeader).filter(JurnalHeader.status == "Posted").count()
        )

        jurnal_terakhir = (
            db.query(JurnalHeader).order_by(JurnalHeader.id.desc()).limit(5).all()
        )

        recent_data = [
            {
                "tanggal": j.tanggal,
                "no_referensi": j.no_referensi,
                "kode_voucher": j.kode_voucher,
                "status": j.status,
                "cabang": j.kode_cabang or "HO",
            }
            for j in jurnal_terakhir
        ]

        return {
            "kpi": {
                "draft": total_draft,
                "pending": total_pending,
                "posted": total_posted,
            },
            "recent": recent_data,
        }
    except Exception as e:
        return {"kpi": {"draft": 0, "pending": 0, "posted": 0}, "recent": []}
    finally:
        db.close()


# CRUD Users (Super User Only)
@app.get("/api/users")
async def get_all_users(current_user: User = Depends(require_roles(["super_user"]))):
    db = get_session()
    try:
        users = db.query(User).all()
        return [
            {
                "id": u.id,
                "username": u.username,
                "nama_karyawan": u.nama_karyawan,
                "role": u.role,
            }
            for u in users
        ]
    finally:
        db.close()


@app.post("/api/users")
async def simpan_atau_update_user(
    data: PayloadUser,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        from security import hash_password

        if data.id:
            user = db.query(User).filter(User.id == data.id).first()
            if not user:
                raise HTTPException(status_code=404, detail="User tidak ditemukan.")
            user.username = data.username
            if data.password and not data.password.startswith("$2b$"):
                user.password = hash_password(data.password)
            user.nama_karyawan = data.nama_karyawan
            user.role = data.role
            pesan = f"User {data.username} berhasil diperbarui!"
            aksi = "UPDATE"
        else:
            cek = db.query(User).filter(User.username == data.username).first()
            if cek:
                raise HTTPException(
                    status_code=400,
                    detail=f"Username '{data.username}' sudah digunakan!",
                )
            user_baru = User(
                username=data.username,
                password=hash_password(data.password),
                nama_karyawan=data.nama_karyawan,
                role=data.role,
            )
            db.add(user_baru)
            pesan = f"User baru {data.username} berhasil ditambahkan!"
            aksi = "CREATE"

        catat_audit(
            db,
            request,
            current_user,
            modul="USER",
            aksi=aksi,
            referensi=data.username,
            keterangan=pesan,
        )
        db.commit()
        return {"status": "success", "pesan": pesan}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.delete("/api/users/{user_id}")
async def hapus_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User tidak ditemukan.")
        if user.username == "admin":
            raise HTTPException(
                status_code=400, detail="User 'admin' utama tidak boleh dihapus!"
            )

        catat_audit(
            db,
            request,
            current_user,
            modul="USER",
            aksi="DELETE",
            referensi=user.username,
            keterangan="Hapus user",
        )
        db.delete(user)
        db.commit()
        return {"status": "success", "pesan": "User berhasil dihapus."}
    finally:
        db.close()


@app.post("/api/users")
async def tambah_user_baru(data: PayloadUser):
    # 1. Cek Validitas Lisensi Perangkat
    lic = license_engine.get_license_data()
    if not lic["valid"]:
        raise HTTPException(
            status_code=403,
            detail="Aplikasi belum diaktivasi atau lisensi tidak valid untuk perangkat ini.",
        )

    db = get_session()
    try:
        # 2. Hitung jumlah user aktif saat ini di database
        total_user_saat_ini = db.query(User).count()
        max_users = lic["max_users"]

        # 3. KUNCI KUOTA USER
        if total_user_saat_ini >= max_users:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"⛔ BATAS KUOTA USER TERCAPAI!\n\n"
                    f"Lisensi Anda hanya mencakup maksimal {max_users} user terdaftar "
                    f"(saat ini sudah ada {total_user_saat_ini} user).\n"
                    f"Silakan hubungi pengembang aplikasi untuk upgrade kuota user lisensi."
                ),
            )

        # Simpan user baru jika kuota masih tersedia
        user_baru = User(
            username=data.username,
            password=data.password,
            nama_karyawan=data.nama_karyawan,
            role=data.role,
        )
        db.add(user_baru)
        db.commit()
        return {
            "status": "success",
            "pesan": f"User {data.username} berhasil didaftarkan!",
        }
    finally:
        db.close()


# Cabang
@app.get("/api/cabang")
async def get_cabang(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        return [
            {
                "kode": c.kode,
                "nama": c.nama,
                "keterangan": c.keterangan,
                "status": c.status,
            }
            for c in db.query(Cabang).all()
        ]
    finally:
        db.close()


@app.post("/api/cabang")
async def simpan_cabang(
    data: PayloadOrg,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        cek = db.query(Cabang).filter(Cabang.kode == data.kode).first()
        if cek:
            cek.nama = data.nama
            cek.keterangan = data.keterangan
            cek.status = data.status
            pesan = f"Cabang {data.kode} diperbarui."
        else:
            db.add(
                Cabang(
                    kode=data.kode,
                    nama=data.nama,
                    keterangan=data.keterangan,
                    status=data.status,
                )
            )
            pesan = f"Cabang {data.kode} ditambahkan."
        catat_audit(
            db,
            request,
            current_user,
            modul="CABANG",
            aksi="SAVE",
            referensi=data.kode,
            keterangan=pesan,
        )
        db.commit()
        return {"status": "success", "pesan": pesan}
    finally:
        db.close()


@app.delete("/api/cabang/{kode}")
async def hapus_cabang(
    kode: str,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        db.query(Cabang).filter(Cabang.kode == kode).delete()
        catat_audit(
            db,
            request,
            current_user,
            modul="CABANG",
            aksi="DELETE",
            referensi=kode,
            keterangan="Hapus cabang",
        )
        db.commit()
        return {"status": "success"}
    finally:
        db.close()


# Departemen
@app.get("/api/departemen")
async def get_departemen(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        return [
            {
                "kode": d.kode,
                "nama": d.nama,
                "keterangan": d.keterangan,
                "status": d.status,
            }
            for d in db.query(Departemen).all()
        ]
    finally:
        db.close()


@app.post("/api/departemen")
async def simpan_departemen(
    data: PayloadOrg,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        cek = db.query(Departemen).filter(Departemen.kode == data.kode).first()
        if cek:
            cek.nama = data.nama
            cek.keterangan = data.keterangan
            cek.status = data.status
            pesan = f"Departemen {data.kode} diperbarui."
        else:
            db.add(
                Departemen(
                    kode=data.kode,
                    nama=data.nama,
                    keterangan=data.keterangan,
                    status=data.status,
                )
            )
            pesan = f"Departemen {data.kode} ditambahkan."
        catat_audit(
            db,
            request,
            current_user,
            modul="DEPT",
            aksi="SAVE",
            referensi=data.kode,
            keterangan=pesan,
        )
        db.commit()
        return {"status": "success", "pesan": pesan}
    finally:
        db.close()


@app.delete("/api/departemen/{kode}")
async def hapus_departemen(
    kode: str,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        db.query(Departemen).filter(Departemen.kode == kode).delete()
        catat_audit(
            db,
            request,
            current_user,
            modul="DEPT",
            aksi="DELETE",
            referensi=kode,
            keterangan="Hapus departemen",
        )
        db.commit()
        return {"status": "success"}
    finally:
        db.close()


# Voucher
@app.get("/api/voucher")
async def get_voucher(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        return [
            {
                "kode": v.kode,
                "nama": v.nama,
                "keterangan": v.keterangan,
                "status": v.status,
            }
            for v in db.query(Voucher).filter(Voucher.status == "Aktif").all()
        ]
    finally:
        db.close()


@app.post("/api/voucher")
async def simpan_voucher(
    data: PayloadOrg,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        cek = db.query(Voucher).filter(Voucher.kode == data.kode).first()
        if cek:
            cek.nama = data.nama
            cek.keterangan = data.keterangan
            cek.status = data.status
            pesan = f"Voucher {data.kode} diperbarui."
        else:
            db.add(
                Voucher(
                    kode=data.kode,
                    nama=data.nama,
                    keterangan=data.keterangan,
                    status=data.status,
                )
            )
            pesan = f"Voucher {data.kode} ditambahkan."
        catat_audit(
            db,
            request,
            current_user,
            modul="VOUCHER",
            aksi="SAVE",
            referensi=data.kode,
            keterangan=pesan,
        )
        db.commit()
        return {"status": "success", "pesan": pesan}
    finally:
        db.close()


@app.delete("/api/voucher/{kode}")
async def hapus_voucher(
    kode: str,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        db.query(Voucher).filter(Voucher.kode == kode).delete()
        catat_audit(
            db,
            request,
            current_user,
            modul="VOUCHER",
            aksi="DELETE",
            referensi=kode,
            keterangan="Hapus voucher",
        )
        db.commit()
        return {"status": "success"}
    finally:
        db.close()


# Chart of Accounts (COA)
@app.get("/api/coa")
async def get_all_coa(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        akun_all = db.query(COA).order_by(COA.kode.asc()).all()
        return [
            {
                "kode": a.kode,
                "nama": a.nama,
                "kode_akun": a.kode,
                "nama_akun": a.nama,
                "no_akun": a.kode,
                "laporan": getattr(
                    a, "kategori", getattr(a, "posisi_keuangan", "Neraca")
                )
                or "Neraca",
                "tipe_akun": getattr(a, "kelompok", getattr(a, "tipe_akun", "-"))
                or "-",
                "saldo_normal": getattr(a, "saldo_normal", "Debit") or "Debit",
                "status": getattr(a, "status", "Aktif") or "Aktif",
            }
            for a in akun_all
        ]
    finally:
        db.close()


@app.post("/api/coa")
async def simpan_coa(
    data: PayloadCOA,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        cek = db.query(COA).filter(COA.kode == data.kode).first()
        if cek:
            cek.nama = data.nama
            cek.kelompok = data.kelompok
            cek.kategori = data.kategori
            cek.saldo_normal = data.saldo_normal
            cek.status = data.status
            pesan = f"Akun {data.kode} berhasil diperbarui."
        else:
            akun_baru = COA(
                kode=data.kode,
                nama=data.nama,
                kelompok=data.kelompok,
                kategori=data.kategori,
                saldo_normal=data.saldo_normal,
                status=data.status,
            )
            db.add(akun_baru)
            pesan = f"Akun {data.kode} berhasil ditambahkan."

        catat_audit(
            db,
            request,
            current_user,
            modul="COA",
            aksi="SAVE",
            referensi=data.kode,
            keterangan=pesan,
        )
        db.commit()
        return {"status": "success", "pesan": pesan}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/api/coa/bulk-import")
async def bulk_import_coa(req: Request):
    db = get_session()
    try:
        body = await req.json()

        # Deteksi apakah payload berupa List [...] atau Object {"data": [...]}
        if isinstance(body, dict) and "data" in body:
            raw_items = body["data"]
        elif isinstance(body, list):
            raw_items = body
        else:
            raise HTTPException(status_code=400, detail="Format JSON tidak valid.")

        if not raw_items:
            raise HTTPException(status_code=400, detail="Data akun kosong.")

        berhasil = 0
        for row in raw_items:
            # Ambil nilai dengan fleksibel
            no_akun = str(row.get("no_akun") or row.get("kode") or "").strip()
            nama_akun = str(row.get("nama_akun") or row.get("nama") or "").strip()
            tipe_akun = (
                str(row.get("tipe_akun") or row.get("kelompok") or "-").strip() or "-"
            )
            laporan = (
                str(row.get("laporan") or row.get("kategori") or "Neraca").strip()
                or "Neraca"
            )
            posisi = (
                str(
                    row.get("posisi_normal") or row.get("saldo_normal") or "Debit"
                ).strip()
                or "Debit"
            )

            # Lewati jika no akun atau nama kosong
            if not no_akun or not nama_akun:
                continue

            cek = db.query(COA).filter(COA.kode == no_akun).first()
            if cek:
                cek.nama = nama_akun
                if hasattr(cek, "kelompok"):
                    cek.kelompok = tipe_akun
                if hasattr(cek, "kategori"):
                    cek.kategori = laporan
                if hasattr(cek, "saldo_normal"):
                    cek.saldo_normal = posisi
            else:
                akun_baru = COA(
                    kode=no_akun,
                    nama=nama_akun,
                    kelompok=tipe_akun,
                    kategori=laporan,
                    saldo_normal=posisi,
                    status="Aktif",
                )
                db.add(akun_baru)
            berhasil += 1

        db.commit()
        return {
            "status": "success",
            "pesan": f"Berhasil mengimpor/memperbarui {berhasil} akun COA!",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"Error Bulk Import COA: {e}")
        raise HTTPException(
            status_code=500, detail=f"Gagal simpan ke database: {str(e)}"
        )
    finally:
        db.close()


@app.delete("/api/coa/{kode_akun}")
async def hapus_coa(
    kode_akun: str,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        akun = db.query(COA).filter(COA.kode == kode_akun).first()
        if not akun:
            raise HTTPException(status_code=404, detail="Akun tidak ditemukan.")
        catat_audit(
            db,
            request,
            current_user,
            modul="COA",
            aksi="DELETE",
            referensi=kode_akun,
            keterangan="Hapus akun",
        )
        db.delete(akun)
        db.commit()
        return {"status": "success", "pesan": "Akun berhasil dihapus."}
    finally:
        db.close()


# API Hapus Semua COA (Reset Trial)
@app.post("/api/coa/hapus_semua")
async def hapus_semua_coa():
    db = get_session()
    try:
        # Menggunakan ORM SQLAlchemy untuk menghapus seluruh isi tabel COA
        db.query(COA).delete()
        db.commit()
        return {
            "status": "success",
            "pesan": "Seluruh data Master COA trial berhasil dikosongkan!",
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal menghapus COA: {str(e)}")
    finally:
        db.close()


# ==========================================
# 5. JURNAL UMUM & TRANSAKSI GL
# ==========================================
@app.get("/api/jurnal/next_ref")
async def get_next_ref(
    voucher: str, periode: str, current_user: User = Depends(get_current_user)
):
    db = get_session()
    try:
        prefix = f"{voucher} {periode}-"
        matching_jurnals = (
            db.query(JurnalHeader)
            .filter(JurnalHeader.no_referensi.like(f"{prefix}%"))
            .all()
        )
        seqs = []
        for j in matching_jurnals:
            try:
                seqs.append(int(j.no_referensi.split("-")[-1]))
            except ValueError:
                pass
        next_seq = max(seqs) + 1 if seqs else 1
        return {"next_ref": f"{prefix}{next_seq:03d}"}
    finally:
        db.close()


@app.post("/api/jurnal")
async def simpan_jurnal_api(payload: PayloadJurnal, db=Depends(get_current_db)):
    db = get_session()
    try:
        no_ref = payload.no_referensi.strip()
        cek = db.query(JurnalHeader).filter(JurnalHeader.no_referensi == no_ref).first()
        tot_deb = sum(d.debit for d in payload.baris_detail)
        tot_kre = sum(d.kredit for d in payload.baris_detail)
        user_pembuat = getattr(payload, "pembuat", "Staff")

        header_obj = None  # Inisialisasi awal mencegah UnboundLocalError

        if cek:
            # PROSES UPDATE JURNAL YANG SUDAH ADA
            if cek.status in ["Draft", "Pending"]:
                db.query(JurnalDetail).filter(JurnalDetail.header_id == cek.id).delete()
                cek.tanggal = payload.tanggal
                cek.kode_voucher = payload.kode_voucher
                cek.kode_cabang = payload.kode_cabang
                cek.kode_departemen = payload.kode_departemen
                cek.total_debit = tot_deb
                cek.total_kredit = tot_kre
                cek.status = payload.status

                header_obj = cek  # Pastikan terisi

                catat_log(
                    db=db,
                    username=user_pembuat,
                    modul="JURNAL",
                    aksi="UPDATE",
                    referensi=no_ref,
                    keterangan=f"Mengedit jurnal status {payload.status} (Total: Rp {tot_deb:,.0f})",
                    role="Maker",
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Nomor Referensi sudah digunakan atau jurnal sudah berstatus Posted dan tidak dapat diubah!",
                )
        else:
            # PENGECEKAN BATAS TRANSAKSI TRIAL & LICENSE
            if not license_engine.is_license_valid():
                total_transaksi = db.query(JurnalHeader).count()
                trial_info = license_engine.get_trial_info(total_transaksi)

                if trial_info.get("is_locked", False):
                    mid = license_engine.get_machine_id()
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            f"⛔ APLIKASI DIKUNCI!\n\n"
                            f"{trial_info.get('alasan', 'Masa trial telah berakhir.')}\n\n"
                            f"Silakan hubungi pengembang untuk mendapatkan License Key.\n"
                            f"Machine ID: {mid}"
                        ),
                    )

            # PROSES INPUT JURNAL BARU
            header_obj = JurnalHeader(
                no_referensi=no_ref,
                tanggal=payload.tanggal,
                kode_voucher=payload.kode_voucher,
                kode_cabang=payload.kode_cabang or "HO",
                kode_departemen=payload.kode_departemen or "FIN",
                total_debit=tot_deb,
                total_kredit=tot_kre,
                pembuat=user_pembuat,
                status=payload.status,
            )
            db.add(header_obj)
            db.flush()  # Mengambil header_obj.id dari PostgreSQL/Supabase sebelum commit

            catat_log(
                db=db,
                username=user_pembuat,
                modul="JURNAL",
                aksi="CREATE",
                referensi=no_ref,
                keterangan=f"Membuat jurnal baru status {payload.status} (Total: Rp {tot_deb:,.0f})",
                role="Maker",
            )

        # Validasi keamanan: pastikan header_obj terisi sebelum menyimpan detail
        if not header_obj:
            raise HTTPException(
                status_code=500, detail="Gagal memproses header jurnal."
            )

        # Simpan baris detail jurnal
        for d in payload.baris_detail:
            db.add(
                JurnalDetail(
                    header_id=header_obj.id,
                    kode_akun=d.kode_akun,
                    nama_akun=d.nama_akun,
                    keterangan=d.keterangan,
                    debit=d.debit,
                    kredit=d.kredit,
                )
            )

        db.commit()
        return {"status": "success", "pesan": f"Jurnal {no_ref} berhasil disimpan!"}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/jurnal/drafts")
async def get_jurnal_drafts(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        drafts = (
            db.query(JurnalHeader)
            .filter(
                JurnalHeader.status == "Draft",
                ~JurnalHeader.kode_voucher.in_(["DSP", "DEP"]),
            )
            .order_by(JurnalHeader.id.desc())
            .all()
        )
        return [
            {
                "no_referensi": getattr(d, "no_referensi", "") or "-",
                "tanggal": str(getattr(d, "tanggal", "")) or "-",
                "kode_voucher": getattr(d, "kode_voucher", "") or "-",
                "keterangan": getattr(d, "keterangan", "") or "Draft Jurnal",
                "total_debit": float(getattr(d, "total_debit", 0.0) or 0.0),
            }
            for d in drafts
        ]
    finally:
        db.close()


@app.get("/api/jurnal/pending")
async def get_jurnal_pending(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        jurnal_pending = (
            db.query(JurnalHeader).filter(JurnalHeader.status == "Pending").all()
        )
        return [
            {
                "no_referensi": j.no_referensi,
                "tanggal": j.tanggal,
                "kode_voucher": j.kode_voucher,
                "status": j.status,
            }
            for j in jurnal_pending
        ]
    finally:
        db.close()


@app.get("/api/jurnal/detail/{no_ref:path}")
async def get_jurnal_detail(
    no_ref: str, current_user: User = Depends(get_current_user)
):
    db = get_session()
    try:
        jurnal = (
            db.query(JurnalHeader).filter(JurnalHeader.no_referensi == no_ref).first()
        )
        if not jurnal:
            raise HTTPException(status_code=404, detail="Jurnal tidak ditemukan.")

        details = (
            db.query(JurnalDetail).filter(JurnalDetail.header_id == jurnal.id).all()
        )
        detail_data = [
            {
                "kode_akun": b.kode_akun,
                "nama_akun": b.nama_akun or "-",
                "keterangan": b.keterangan or "-",
                "debit": float(b.debit or 0.0),
                "kredit": float(b.kredit or 0.0),
            }
            for b in details
        ]

        return {
            "no_referensi": jurnal.no_referensi,
            "tanggal": str(jurnal.tanggal),
            "kode_voucher": jurnal.kode_voucher,
            "kode_cabang": getattr(jurnal, "kode_cabang", "HO") or "HO",
            "kode_departemen": getattr(jurnal, "kode_departemen", "FIN") or "FIN",
            "status": jurnal.status,
            "detail": detail_data,
        }
    finally:
        db.close()


@app.post("/api/jurnal/approve/{no_ref:path}")
async def approve_jurnal_api(
    no_ref: str,
    request: Request,
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    db = get_session()
    try:
        jurnal = (
            db.query(JurnalHeader).filter(JurnalHeader.no_referensi == no_ref).first()
        )
        if not jurnal:
            raise HTTPException(status_code=404, detail="Jurnal tidak ditemukan.")

        jurnal.status = "Posted"

        # Sinkronisasi AP
        bill = (
            db.query(VendorBillHeader)
            .filter(VendorBillHeader.no_bill == no_ref)
            .first()
        )
        if bill:
            bill.status = "Posted"

        # Sinkronisasi Pembayaran Kas Keluar
        pays = db.query(VendorPayment).filter(VendorPayment.no_payment == no_ref).all()
        for pay in pays:
            pay.status = "Posted"
            b_target = (
                db.query(VendorBillHeader)
                .filter(VendorBillHeader.no_bill == pay.no_bill)
                .first()
            )
            if b_target:
                b_target.saldo_terutang = max(
                    0.0,
                    float(b_target.saldo_terutang or 0.0)
                    - float(pay.nominal_bayar or 0.0),
                )
                b_target.tgl_bayar = pay.tanggal
            if getattr(pay, "no_rfp", None):
                rfp = (
                    db.query(PaymentRequest)
                    .filter(PaymentRequest.no_rfp == pay.no_rfp)
                    .first()
                )
                if rfp:
                    rfp.status = "Paid"

        # Sinkronisasi Pelepasan Aset
        if getattr(jurnal, "kode_voucher", "") == "DSP":
            for b in jurnal.baris_detail:
                aset = (
                    db.query(FixedAsset)
                    .filter(FixedAsset.akun_aset == b.kode_akun)
                    .first()
                )
                if aset and getattr(aset, "status", "") == "Pending Disposal":
                    aset.status = "Disposed"
                    aset.nilai_buku = 0.0

        if jurnal.kode_voucher in ["AR", "INV"]:
            inv = (
                db.query(CustomerInvoiceHeader)
                .filter(CustomerInvoiceHeader.no_invoice == no_ref)
                .first()
            )
            if inv:
                inv.status = "Posted"
                catat_log(
                    db=db,
                    username="Approver",
                    modul="AR_INVOICE",
                    aksi="POSTING",
                    referensi=no_ref,
                    keterangan=f"Menyetujui faktur penjualan dan memposting piutang ke Buku Besar (Total: Rp {jurnal.total_debit:,.0f})",
                    role="Approver",
                )

        catat_audit(
            db,
            request,
            current_user,
            modul="JURNAL",
            aksi="POSTING",
            referensi=no_ref,
            keterangan=f"Menyetujui posting jurnal ke Buku Besar (Rp {jurnal.total_debit:,.0f})",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Jurnal {no_ref} berhasil diposting ke Buku Besar!",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/api/jurnal/reject/{no_ref:path}")
async def reject_jurnal_api(
    no_ref: str,
    request: Request,
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    db = get_session()
    try:
        jurnal = (
            db.query(JurnalHeader).filter(JurnalHeader.no_referensi == no_ref).first()
        )
        if not jurnal:
            raise HTTPException(status_code=404, detail="Jurnal tidak ditemukan.")

        if jurnal.kode_voucher in ["AR", "INV"]:
            inv = (
                db.query(CustomerInvoiceHeader)
                .filter(CustomerInvoiceHeader.no_invoice == no_ref)
                .first()
            )
            if inv:
                inv.status = "Draft"
                catat_log(
                    db=db,
                    username="Approver",
                    modul="AR_INVOICE",
                    aksi="REJECT",
                    referensi=no_ref,
                    keterangan="Menolak faktur AR dan mengembalikan status ke Draft",
                    role="Approver",
                )

        if jurnal.kode_voucher == "AP":
            bill = (
                db.query(VendorBillHeader)
                .filter(VendorBillHeader.no_bill == no_ref)
                .first()
            )
            if bill:
                bill.status = "Draft"
            db.delete(jurnal)
            pesan = (
                f"Tagihan {no_ref} ditolak dan dikembalikan ke modul AP sebagai Draft!"
            )
        elif jurnal.kode_voucher == "DSP":
            for baris in jurnal.baris_detail:
                aset = (
                    db.query(FixedAsset)
                    .filter(FixedAsset.akun_aset == baris.kode_akun)
                    .first()
                )
                if aset and getattr(aset, "status", "") == "Pending Disposal":
                    aset.status = "Aktif"
            db.delete(jurnal)
            pesan = f"Pelepasan aset {no_ref} dibatalkan dan status aset dipulihkan menjadi Aktif."
        else:
            jurnal.status = "Draft"
            pesan = f"Jurnal {no_ref} dikembalikan ke Draft Jurnal Umum."

        catat_audit(
            db,
            request,
            current_user,
            modul="JURNAL",
            aksi="REJECT",
            referensi=no_ref,
            keterangan=pesan,
        )
        db.commit()
        return {"status": "success", "pesan": pesan}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.delete("/api/jurnal/{no_ref:path}")
async def hapus_jurnal_api(
    no_ref: str,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        jurnal = (
            db.query(JurnalHeader).filter(JurnalHeader.no_referensi == no_ref).first()
        )
        if not jurnal:
            raise HTTPException(status_code=404, detail="Jurnal tidak ditemukan.")

        if jurnal.kode_voucher == "DSP":
            for baris in jurnal.baris_detail:
                aset = (
                    db.query(FixedAsset)
                    .filter(FixedAsset.akun_aset == baris.kode_akun)
                    .first()
                )
                if aset and getattr(aset, "status", "") == "Pending Disposal":
                    aset.status = "Aktif"

        db.query(JurnalDetail).filter(JurnalDetail.header_id == jurnal.id).delete()
        catat_audit(
            db,
            request,
            current_user,
            modul="JURNAL",
            aksi="DELETE",
            referensi=no_ref,
            keterangan="Hapus permanen jurnal",
        )
        db.delete(jurnal)
        db.commit()
        return {
            "status": "success",
            "pesan": f"Jurnal {no_ref} berhasil dihapus permanen!",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/jurnal/cetak/{no_referensi:path}")
async def get_jurnal_cetak(no_referensi: str):
    db = get_session()
    try:
        # 1. Ambil Header
        header = (
            db.query(JurnalHeader)
            .filter(JurnalHeader.no_referensi == no_referensi)
            .first()
        )
        if not header:
            raise HTTPException(status_code=404, detail="Jurnal tidak ditemukan.")

        # 2. Ambil baris detail via relasi / query
        detail_data = []
        for d in header.baris_detail:
            akun = db.query(COA).filter(COA.kode == d.kode_akun).first()
            detail_data.append(
                {
                    "kode_akun": d.kode_akun,
                    "nama_akun": akun.nama if akun else getattr(d, "nama_akun", "-"),
                    "keterangan": d.keterangan or "-",
                    "debit": float(d.debit or 0),
                    "kredit": float(d.kredit or 0),
                }
            )

        return {
            "header": {
                "no_referensi": header.no_referensi,
                "tanggal": header.tanggal,
                "kode_voucher": getattr(header, "kode_voucher", "JV") or "JV",
                "kode_cabang": header.kode_cabang or "HO",
                "kode_departemen": header.kode_departemen or "FIN",
                "keterangan": getattr(header, "keterangan", "-") or "-",
                "total_debit": float(header.total_debit or 0),
                "total_kredit": float(header.total_kredit or 0),
                "pembuat": header.pembuat or "Staff",
                "status": header.status or "Pending",
            },
            "detail": detail_data,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error cetak jurnal: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ==========================================
# 6. MODUL HUTANG USAHA (ACCOUNTS PAYABLE)
# ==========================================
@app.get("/api/coa/hutang")
async def get_coa_hutang():
    db = get_session()
    try:
        # Ambil seluruh akun COA
        semua_coa = db.query(COA).all()
        hasil = []

        for c in semua_coa:
            kode = str(
                getattr(c, "kode", None)
                or getattr(c, "kode_akun", None)
                or getattr(c, "no_akun", "")
                or ""
            ).strip()
            nama = str(
                getattr(c, "nama", None) or getattr(c, "nama_akun", "") or ""
            ).strip()
            kelompok = str(getattr(c, "kelompok", "") or "").lower()
            kategori = str(getattr(c, "kategori", "") or "").lower()

            # Tangkap akun berkepala 21, 2, atau memuat kata hutang/kewajiban/liabilitas
            if (
                kode.startswith("211")
                or kode.startswith("2112")
                or "hutang" in nama.lower()
                or "kewajiban" in kelompok
                or "kewajiban" in kategori
                or "liabilitas" in kelompok
            ):
                hasil.append({"kode": kode, "nama": nama})

        # Fallback jika master COA belum memiliki akun kepala 2
        if not hasil:
            hasil = [
                {"kode": "2110-00", "nama": "Hutang Usaha (Vendor)"},
                {"kode": "211000", "nama": "Hutang Dagang"},
            ]

        return hasil
    except Exception as e:
        print(f"Error load coa hutang: {e}")
        return [
            {"kode": "2110-00", "nama": "Hutang Usaha (Vendor)"},
        ]
    finally:
        db.close()


@app.get("/api/vendor")
async def get_semua_vendor(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        vendors = db.query(MasterVendor).order_by(MasterVendor.kode_vendor.asc()).all()
        return [
            {
                "kode_vendor": v.kode_vendor,
                "nama_vendor": v.nama_vendor,
                "npwp": v.npwp or "-",
                "alamat": v.alamat or "-",
                "telepon": v.telepon or "-",
                "email": v.email or "-",
                "kontak_person": v.kontak_person or "-",
                "termin_hari": v.termin_hari or 30,
                "nama_bank": v.nama_bank or "-",
                "no_rekening": v.no_rekening or "-",
                "atas_nama": v.atas_nama or "-",
                "status": v.status or "Aktif",
            }
            for v in vendors
        ]
    except Exception as e:
        print(f"Error load vendor: {e}")
        return []
    finally:
        db.close()


@app.post("/api/vendor")
async def simpan_vendor(
    p: PayloadVendor,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "approver", "super_user"])),
):
    db = get_session()
    try:
        v = (
            db.query(MasterVendor)
            .filter(MasterVendor.kode_vendor == p.kode_vendor)
            .first()
        )
        if v:
            v.nama_vendor = p.nama_vendor
            v.npwp = p.npwp
            v.alamat = p.alamat
            v.telepon = p.telepon
            v.email = p.email
            v.kontak_person = p.kontak_person
            v.termin_hari = p.termin_hari
            v.nama_bank = p.nama_bank
            v.no_rekening = p.no_rekening
            v.atas_nama = p.atas_nama
            v.status = p.status
            pesan = (
                f"Data vendor {p.nama_vendor} ({p.kode_vendor}) berhasil diperbarui!"
            )
            aksi_log = "UPDATE"
        else:
            db.add(MasterVendor(**p.dict()))
            pesan = (
                f"Vendor baru {p.nama_vendor} ({p.kode_vendor}) berhasil didaftarkan!"
            )
            aksi_log = "CREATE"

        catat_audit(
            db,
            request,
            current_user,
            modul="AP_VENDOR",
            aksi=aksi_log,
            referensi=p.kode_vendor,
            keterangan=pesan,
        )
        db.commit()
        return {"status": "success", "pesan": pesan}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.delete("/api/vendor/{kode_vendor}")
async def hapus_vendor(
    kode_vendor: str,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        v = (
            db.query(MasterVendor)
            .filter(MasterVendor.kode_vendor == kode_vendor)
            .first()
        )
        if not v:
            raise HTTPException(status_code=404, detail="Vendor tidak ditemukan")
        catat_audit(
            db,
            request,
            current_user,
            modul="AP_VENDOR",
            aksi="DELETE",
            referensi=kode_vendor,
            keterangan="Hapus vendor",
        )
        db.delete(v)
        db.commit()
        return {"status": "success", "pesan": f"Vendor {kode_vendor} berhasil dihapus."}
    finally:
        db.close()


@app.get("/api/vendor_bill")
async def get_all_vendor_bill(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        bills = db.query(VendorBillHeader).order_by(VendorBillHeader.id.desc()).all()
        return [
            {
                "id": b.id,
                "no_bill": b.no_bill,
                "no_faktur_vendor": b.no_faktur_vendor,
                "tanggal": b.tanggal,
                "jatuh_tempo": b.jatuh_tempo,
                "kode_vendor": b.kode_vendor,
                "nama_vendor": b.nama_vendor,
                "kode_cabang": b.kode_cabang,
                "total_tagihan": b.total_tagihan,
                "saldo_terutang": b.saldo_terutang,
                "status": b.status,
                "keterangan": b.keterangan,
            }
            for b in bills
        ]
    finally:
        db.close()


@app.get("/api/vendor_bill/next-no")
async def get_next_bill_no(
    voucher: str = "AP",
    tanggal: str = "",
    current_user: User = Depends(get_current_user),
):
    if not tanggal:
        return {"next_no": ""}
    db = get_session()
    try:
        prefix_bulan = tanggal[:7].replace("-", "")
        prefix_doc = f"{voucher} {prefix_bulan}-"
        matching_bills = (
            db.query(VendorBillHeader)
            .filter(VendorBillHeader.no_bill.like(f"{prefix_doc}%"))
            .all()
        )
        seqs = []
        for b in matching_bills:
            try:
                seqs.append(int(b.no_bill.split("-")[-1]))
            except (ValueError, IndexError):
                pass
        next_seq = max(seqs) + 1 if seqs else 1
        return {"next_no": f"{prefix_doc}{next_seq:03d}"}
    finally:
        db.close()


@app.post("/api/vendor_bill")
async def simpan_vendor_bill(
    p: PayloadVendorBill,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "approver", "super_user"])),
):
    db = get_session()
    try:
        bill_lama = (
            db.query(VendorBillHeader)
            .filter(VendorBillHeader.no_bill == p.no_bill)
            .first()
        )
        if bill_lama and bill_lama.status != "Draft":
            raise HTTPException(
                status_code=400,
                detail=f"Nomor Dokumen {p.no_bill} sudah terdaftar dan berstatus {bill_lama.status}!",
            )

        vendor = (
            db.query(MasterVendor)
            .filter(MasterVendor.kode_vendor == p.kode_vendor)
            .first()
        )
        if not vendor:
            raise HTTPException(
                status_code=404, detail="Data Vendor rekanan tidak ditemukan!"
            )

        if not p.detail:
            raise HTTPException(
                status_code=400, detail="Rincian baris biaya/pembelian minimal 1 baris!"
            )

        subtotal = sum(d.nominal for d in p.detail if d.nominal > 0)
        ppn = float(p.ppn_nominal or 0.0)
        total_hutang_vendor = subtotal + ppn

        if bill_lama and bill_lama.status == "Draft":
            bill_lama.kode_voucher = p.kode_voucher or "AP"
            bill_lama.no_faktur_vendor = p.no_faktur_vendor
            bill_lama.tanggal = p.tanggal
            bill_lama.jatuh_tempo = p.jatuh_tempo
            bill_lama.kode_vendor = vendor.kode_vendor
            bill_lama.nama_vendor = vendor.nama_vendor
            bill_lama.kode_cabang = p.kode_cabang
            bill_lama.kode_departemen = p.kode_departemen
            bill_lama.keterangan = p.keterangan
            bill_lama.subtotal = subtotal
            bill_lama.kode_akun_ppn = p.kode_akun_ppn or ""
            bill_lama.ppn_nominal = ppn
            bill_lama.total_tagihan = total_hutang_vendor
            bill_lama.saldo_terutang = total_hutang_vendor
            bill_lama.status = "Pending"
            bill_hdr = bill_lama
            db.query(VendorBillDetail).filter(
                VendorBillDetail.bill_id == bill_lama.id
            ).delete()
        else:
            bill_hdr = VendorBillHeader(
                kode_voucher=p.kode_voucher or "AP",
                no_bill=p.no_bill,
                no_faktur_vendor=p.no_faktur_vendor,
                tanggal=p.tanggal,
                jatuh_tempo=p.jatuh_tempo,
                kode_vendor=vendor.kode_vendor,
                nama_vendor=vendor.nama_vendor,
                kode_cabang=p.kode_cabang,
                kode_departemen=p.kode_departemen,
                keterangan=p.keterangan,
                kode_akun_hutang=vendor.kode_akun_hutang,
                nama_akun_hutang=vendor.nama_akun_hutang,
                subtotal=subtotal,
                kode_akun_ppn=p.kode_akun_ppn or "",
                ppn_nominal=ppn,
                total_tagihan=total_hutang_vendor,
                saldo_terutang=total_hutang_vendor,
                status="Pending",
                pembuat=current_user.username,
            )
            db.add(bill_hdr)
            db.flush()

        for d in p.detail:
            if d.nominal <= 0:
                continue
            db.add(
                VendorBillDetail(
                    bill_id=bill_hdr.id,
                    kode_akun=d.kode_akun,
                    nama_akun=d.nama_akun,
                    deskripsi=d.deskripsi,
                    nominal=d.nominal,
                )
            )

        # Re-generate Auto-Jurnal AP Pending
        jurnal_ap = (
            db.query(JurnalHeader)
            .filter(JurnalHeader.no_referensi == p.no_bill)
            .first()
        )
        if jurnal_ap:
            db.query(JurnalDetail).filter(
                JurnalDetail.header_id == jurnal_ap.id
            ).delete()
            jurnal_ap.status = "Pending"
            jurnal_ap.total_debit = total_hutang_vendor
            jurnal_ap.total_kredit = total_hutang_vendor
            jurnal_ap.tanggal = p.tanggal
        else:
            jurnal_ap = JurnalHeader(
                kode_voucher=p.kode_voucher or "AP",
                tanggal=p.tanggal,
                no_referensi=p.no_bill,
                kode_cabang=p.kode_cabang,
                kode_departemen=p.kode_departemen,
                keterangan=f"Tagihan {vendor.nama_vendor} (Inv: {p.no_faktur_vendor}) - {p.keterangan}",
                total_debit=total_hutang_vendor,
                total_kredit=total_hutang_vendor,
                pembuat=current_user.username,
                status="Pending",
            )
            db.add(jurnal_ap)
            db.flush()

        for d in p.detail:
            if d.nominal <= 0:
                continue
            db.add(
                JurnalDetail(
                    header_id=jurnal_ap.id,
                    kode_akun=d.kode_akun,
                    nama_akun=d.nama_akun,
                    keterangan=f"{p.no_faktur_vendor} - {d.deskripsi or p.keterangan}",
                    debit=d.nominal,
                    kredit=0.0,
                )
            )

        if ppn > 0 and p.kode_akun_ppn:
            obj_ppn = db.query(COA).filter(COA.kode == p.kode_akun_ppn).first()
            db.add(
                JurnalDetail(
                    header_id=jurnal_ap.id,
                    kode_akun=p.kode_akun_ppn,
                    nama_akun=obj_ppn.nama if obj_ppn else "PPN Masukan",
                    keterangan=f"PPN Masukan Inv {p.no_faktur_vendor}",
                    debit=ppn,
                    kredit=0.0,
                )
            )

        db.add(
            JurnalDetail(
                header_id=jurnal_ap.id,
                kode_akun=vendor.kode_akun_hutang,
                nama_akun=vendor.nama_akun_hutang,
                keterangan=f"Hutang Usaha Inv {p.no_faktur_vendor} - {vendor.nama_vendor}",
                debit=0.0,
                kredit=total_hutang_vendor,
            )
        )

        catat_audit(
            db,
            request,
            current_user,
            modul="AP_BILL",
            aksi="SUBMIT",
            referensi=p.no_bill,
            keterangan=f"Mengajukan tagihan vendor {vendor.nama_vendor} Rp {total_hutang_vendor:,.0f}",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Tagihan {p.no_bill} berhasil diajukan dengan total hutang Rp {total_hutang_vendor:,.0f}.",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/vendor_bill/detail/{no_bill:path}")
async def get_vendor_bill_detail(
    no_bill: str, current_user: User = Depends(get_current_user)
):
    db = get_session()
    try:
        bill = (
            db.query(VendorBillHeader)
            .filter(VendorBillHeader.no_bill == no_bill)
            .first()
        )
        if not bill:
            raise HTTPException(
                status_code=404, detail="Dokumen tagihan vendor tidak ditemukan."
            )

        details = (
            db.query(VendorBillDetail).filter(VendorBillDetail.bill_id == bill.id).all()
        )
        return {
            "header": {
                "no_bill": bill.no_bill,
                "kode_voucher": bill.kode_voucher,
                "no_faktur_vendor": bill.no_faktur_vendor,
                "tanggal": bill.tanggal,
                "jatuh_tempo": bill.jatuh_tempo,
                "kode_vendor": bill.kode_vendor,
                "nama_vendor": bill.nama_vendor,
                "kode_cabang": bill.kode_cabang,
                "kode_departemen": bill.kode_departemen,
                "keterangan": bill.keterangan,
                "kode_akun_hutang": bill.kode_akun_hutang,
                "nama_akun_hutang": bill.nama_akun_hutang,
                "subtotal": bill.subtotal,
                "kode_akun_ppn": bill.kode_akun_ppn,
                "ppn_nominal": bill.ppn_nominal,
                "total_tagihan": bill.total_tagihan,
                "saldo_terutang": bill.saldo_terutang,
                "status": bill.status,
            },
            "detail": [
                {
                    "kode_akun": d.kode_akun,
                    "nama_akun": d.nama_akun,
                    "deskripsi": d.deskripsi,
                    "nominal": d.nominal,
                }
                for d in details
            ],
        }
    finally:
        db.close()


@app.delete("/api/vendor_bill/{no_bill:path}")
async def hapus_draft_vendor_bill(
    no_bill: str,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "super_user"])),
):
    db = get_session()
    try:
        bill = (
            db.query(VendorBillHeader)
            .filter(VendorBillHeader.no_bill == no_bill)
            .first()
        )
        if not bill:
            raise HTTPException(
                status_code=404, detail="Dokumen tagihan tidak ditemukan."
            )

        if bill.status == "Posted":
            raise HTTPException(
                status_code=400,
                detail="Tagihan yang sudah berstatus 'Posted' tidak boleh dihapus sembarangan!",
            )

        jurnal = (
            db.query(JurnalHeader).filter(JurnalHeader.no_referensi == no_bill).first()
        )
        if jurnal:
            db.query(JurnalDetail).filter(JurnalDetail.header_id == jurnal.id).delete()
            db.delete(jurnal)

        db.delete(bill)
        catat_audit(
            db,
            request,
            current_user,
            modul="AP_BILL",
            aksi="DELETE",
            referensi=no_bill,
            keterangan="Hapus draft tagihan",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Draft tagihan {no_bill} berhasil dihapus dari sistem.",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# main.py di dalam endpoint /api/ap/monitoring
@app.get("/api/ap/monitoring")
async def get_ap_monitoring(
    q: Optional[str] = "",
    status_bayar: Optional[str] = "SEMUA",
    kode_vendor: Optional[str] = "SEMUA",
    kode_cabang: Optional[str] = "SEMUA",  # <-- Parameter kode cabang
):
    db = get_session()
    try:
        query = db.query(VendorBillHeader)

        # 1. Filter Pencarian Teks Bebas
        if q:
            keyword = f"%{q.strip()}%"
            query = query.filter(
                (VendorBillHeader.no_bill.like(keyword))
                | (VendorBillHeader.no_faktur_vendor.like(keyword))
                | (VendorBillHeader.nama_vendor.like(keyword))
                | (VendorBillHeader.kode_vendor.like(keyword))
                | (VendorBillHeader.kode_cabang.like(keyword))
            )

        # 2. Filter Spesifik Vendor
        if kode_vendor and kode_vendor != "SEMUA":
            query = query.filter(VendorBillHeader.kode_vendor == kode_vendor)

        # 3. Filter Spesifik Cabang
        if kode_cabang and kode_cabang != "SEMUA":
            query = query.filter(VendorBillHeader.kode_cabang == kode_cabang)

        # 4. Filter Status Bayar
        if status_bayar == "UNPAID":
            query = query.filter(VendorBillHeader.saldo_terutang > 0)
        elif status_bayar == "PAID":
            query = query.filter(VendorBillHeader.saldo_terutang <= 0)

        bills = query.order_by(VendorBillHeader.id.desc()).all()

        # Query Rekonsiliasi AP Aktif (Perhitungkan juga filter cabang jika dipilih)
        unpaid_query = db.query(VendorBillHeader).filter(
            VendorBillHeader.status == "Posted", VendorBillHeader.saldo_terutang > 0
        )
        if kode_cabang and kode_cabang != "SEMUA":
            unpaid_query = unpaid_query.filter(
                VendorBillHeader.kode_cabang == kode_cabang
            )
        if kode_vendor and kode_vendor != "SEMUA":
            unpaid_query = unpaid_query.filter(
                VendorBillHeader.kode_vendor == kode_vendor
            )

        unpaid_bills = unpaid_query.all()

        # Summary Per Vendor
        vendor_dict = {}
        for b in unpaid_bills:
            kv = b.kode_vendor or "-"
            if kv not in vendor_dict:
                vendor_dict[kv] = {
                    "kode_vendor": kv,
                    "nama_vendor": b.nama_vendor or "-",
                    "jumlah_faktur": 0,
                    "total_tagihan": 0.0,
                    "saldo_hutang": 0.0,
                }
            vendor_dict[kv]["jumlah_faktur"] += 1
            vendor_dict[kv]["total_tagihan"] += float(b.total_tagihan or 0.0)
            vendor_dict[kv]["saldo_hutang"] += float(b.saldo_terutang or 0.0)

        summary_vendor = sorted(
            list(vendor_dict.values()), key=lambda x: x["nama_vendor"]
        )

        # Kontrol Saldo AP vs GL
        tot_ap = sum(float(b.saldo_terutang or 0.0) for b in unpaid_bills)
        tot_gl = 0.0
        try:
            akun_hutang_terpakai = {
                str(getattr(b, "kode_akun_hutang", "") or "").strip()
                for b in unpaid_bills
                if getattr(b, "kode_akun_hutang", "")
            }
            # Bersihkan nilai kosong
            akun_hutang_terpakai.discard("")

            gl_query = (
                db.query(JurnalDetail)
                .join(JurnalHeader, JurnalDetail.header_id == JurnalHeader.id)
                .filter(JurnalHeader.status == "Posted")
            )

            # Filter akun: mencakup 211%, 21-1%, atau kode akun hutang spesifik dari master vendor
            if akun_hutang_terpakai:
                gl_query = gl_query.filter(
                    (JurnalDetail.kode_akun.like("211%"))
                    | (JurnalDetail.kode_akun.like("21-1%"))
                    | (JurnalDetail.kode_akun.in_(list(akun_hutang_terpakai)))
                )
            else:
                gl_query = gl_query.filter(
                    (JurnalDetail.kode_akun.like("211%"))
                    | (JurnalDetail.kode_akun.like("21-1%"))
                )

            # Jika memilih cabang tertentu selain SEMUA
            if kode_cabang and kode_cabang != "SEMUA":
                gl_query = gl_query.filter(JurnalHeader.kode_cabang == kode_cabang)

            gl_lines = gl_query.all()

            # Saldo normal hutang adalah Kredit (Kredit - Debit)
            tot_gl = sum(
                float(l.kredit or 0.0) - float(l.debit or 0.0) for l in gl_lines
            )

        except Exception as err_gl:
            print(f"DEBUG ERROR REKONSILIASI GL: {err_gl}")

        selisih = abs(tot_ap - tot_gl)

        return {
            "summary_vendor": summary_vendor,
            "kontrol_gl": {
                "total_ap": tot_ap,
                "total_gl": tot_gl,
                "selisih": selisih,
                "status_balance": selisih < 1.0,
            },
            "daftar_bill": [
                {
                    "no_bill": b.no_bill or "-",
                    "no_faktur_vendor": b.no_faktur_vendor or "-",
                    "kode_vendor": b.kode_vendor or "-",
                    "nama_vendor": b.nama_vendor or "-",
                    "kode_cabang": getattr(b, "kode_cabang", "HO") or "HO",
                    "tanggal": b.tanggal or "-",
                    "jatuh_tempo": b.jatuh_tempo or "-",
                    "total_tagihan": float(b.total_tagihan or 0.0),
                    "saldo_terutang": float(b.saldo_terutang or 0.0),
                    "status_tagihan": "Paid"
                    if float(b.saldo_terutang or 0.0) <= 0
                    else "Unpaid",
                    "status_approval": b.status or "Draft",
                }
                for b in bills
            ],
        }
    finally:
        db.close()


# ==========================================
# 7. TREASURY & PEMBAYARAN AP (BKK / RFP)
# ==========================================
@app.get("/api/vendor_payment/next-no")
async def get_next_payment_no(
    voucher: str = "BKK",
    tanggal: str = "",
    current_user: User = Depends(get_current_user),
):
    if not tanggal:
        return {"next_no": ""}
    db = get_session()
    try:
        prefix_bulan = tanggal[:7].replace("-", "")
        prefix_doc = f"{voucher}-{prefix_bulan}-"
        matching = (
            db.query(VendorPayment)
            .filter(VendorPayment.no_payment.like(f"{prefix_doc}%"))
            .all()
        )
        seqs = []
        for m in matching:
            try:
                seqs.append(int(m.no_payment.split("-")[-1]))
            except (ValueError, IndexError):
                pass
        next_seq = max(seqs) + 1 if seqs else 1
        return {"next_no": f"{prefix_doc}{next_seq:03d}"}
    finally:
        db.close()


@app.get("/api/vendor_payment/unpaid-bills")
async def get_unpaid_bills(
    kode_vendor: Optional[str] = "", current_user: User = Depends(get_current_user)
):
    db = get_session()
    try:
        rfp_pending_bills = [
            r.no_bill
            for r in db.query(PaymentRequest.no_bill)
            .filter(PaymentRequest.status.in_(["Submitted", "Approved_Finance"]))
            .all()
        ]
        query = db.query(VendorBillHeader).filter(
            VendorBillHeader.status == "Posted",
            VendorBillHeader.saldo_terutang > 0,
            ~VendorBillHeader.no_bill.in_(rfp_pending_bills),
        )
        if kode_vendor:
            query = query.filter(VendorBillHeader.kode_vendor == kode_vendor)

        bills = query.order_by(VendorBillHeader.id.desc()).all()
        return [
            {
                "no_bill": b.no_bill,
                "no_faktur_vendor": b.no_faktur_vendor or "-",
                "tanggal": b.tanggal,
                "jatuh_tempo": b.jatuh_tempo,
                "kode_vendor": b.kode_vendor,
                "nama_vendor": b.nama_vendor,
                "kode_akun_hutang": b.kode_akun_hutang,
                "nama_akun_hutang": b.nama_akun_hutang,
                "total_tagihan": float(b.total_tagihan or 0.0),
                "saldo_terutang": float(b.saldo_terutang or 0.0),
                "jenis_pph": str(getattr(b, "jenis_pph", "") or ""),
                "kode_akun_pph": str(getattr(b, "kode_akun_pph", "") or ""),
                "pph_nominal": float(getattr(b, "pph_nominal", 0.0) or 0.0),
            }
            for b in bills
        ]
    finally:
        db.close()


@app.post("/api/vendor_payment")
async def simpan_vendor_payment(
    p: PayloadVendorPayment,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "approver", "super_user"])),
):
    db = get_session()
    try:
        cek = (
            db.query(VendorPayment)
            .filter(VendorPayment.no_payment == p.no_payment)
            .first()
        )
        if cek and cek.status != "Draft":
            raise HTTPException(
                status_code=400, detail=f"Nomor bukti {p.no_payment} sudah terdaftar!"
            )

        bill = (
            db.query(VendorBillHeader)
            .filter(VendorBillHeader.no_bill == p.no_bill)
            .first()
        )
        if not bill or bill.status != "Posted":
            raise HTTPException(
                status_code=400,
                detail="Faktur tagihan vendor tidak valid atau belum disetujui di GL!",
            )

        if p.nominal_bayar <= 0 or p.nominal_bayar > bill.saldo_terutang:
            raise HTTPException(
                status_code=400, detail="Nominal pembayaran tidak valid!"
            )

        akun_kas = db.query(COA).filter(COA.kode == p.kode_akun_kas).first()
        if not akun_kas:
            raise HTTPException(status_code=404, detail="Akun kas/bank tidak valid!")

        pph_nom = float(p.pph_nominal or 0.0)
        kas_keluar = p.nominal_bayar - pph_nom
        if kas_keluar < 0:
            raise HTTPException(
                status_code=400,
                detail="Potongan PPh tidak boleh melebihi nominal bayar!",
            )

        if cek and cek.status == "Draft":
            cek.tanggal = p.tanggal
            cek.kode_cabang = p.kode_cabang
            cek.kode_departemen = p.kode_departemen
            cek.keterangan = p.keterangan
            cek.kode_akun_kas = akun_kas.kode
            cek.nama_akun_kas = akun_kas.nama
            cek.nominal_bayar = p.nominal_bayar
            cek.jenis_pph = p.jenis_pph or ""
            cek.kode_akun_pph = p.kode_akun_pph or ""
            cek.pph_nominal = pph_nom
            cek.jumlah_kas_keluar = kas_keluar
            cek.status = "Pending"
        else:
            db.add(
                VendorPayment(
                    kode_voucher=p.kode_voucher or "BKK",
                    no_payment=p.no_payment,
                    tanggal=p.tanggal,
                    kode_vendor=bill.kode_vendor,
                    nama_vendor=bill.nama_vendor,
                    no_bill=bill.no_bill,
                    no_faktur_vendor=bill.no_faktur_vendor,
                    kode_cabang=p.kode_cabang,
                    kode_departemen=p.kode_departemen,
                    keterangan=p.keterangan,
                    kode_akun_kas=akun_kas.kode,
                    nama_akun_kas=akun_kas.nama,
                    kode_akun_hutang=bill.kode_akun_hutang,
                    nama_akun_hutang=bill.nama_akun_hutang,
                    nominal_bayar=p.nominal_bayar,
                    jenis_pph=p.jenis_pph or "",
                    kode_akun_pph=p.kode_akun_pph or "",
                    pph_nominal=pph_nom,
                    jumlah_kas_keluar=kas_keluar,
                    status="Pending",
                    pembuat=current_user.username,
                )
            )

        # Jurnal Pengeluaran Kas
        jurnal_lama = (
            db.query(JurnalHeader)
            .filter(JurnalHeader.no_referensi == p.no_payment)
            .first()
        )
        if jurnal_lama:
            db.query(JurnalDetail).filter(
                JurnalDetail.header_id == jurnal_lama.id
            ).delete()
            jurnal_bkk = jurnal_lama
            jurnal_bkk.tanggal = p.tanggal
            jurnal_bkk.status = "Pending"
            jurnal_bkk.total_debit = p.nominal_bayar
            jurnal_bkk.total_kredit = p.nominal_bayar
        else:
            jurnal_bkk = JurnalHeader(
                kode_voucher=p.kode_voucher or "BKK",
                tanggal=p.tanggal,
                no_referensi=p.no_payment,
                kode_cabang=p.kode_cabang,
                kode_departemen=p.kode_departemen,
                keterangan=f"Pembayaran AP {bill.nama_vendor} ({bill.no_bill}) - {p.keterangan}",
                total_debit=p.nominal_bayar,
                total_kredit=p.nominal_bayar,
                pembuat=current_user.username,
                status="Pending",
            )
            db.add(jurnal_bkk)
            db.flush()

        db.add(
            JurnalDetail(
                header_id=jurnal_bkk.id,
                kode_akun=bill.kode_akun_hutang,
                nama_akun=bill.nama_akun_hutang,
                keterangan=f"Pelunasan Inv {bill.no_faktur_vendor} - {bill.nama_vendor}",
                debit=p.nominal_bayar,
                kredit=0.0,
            )
        )

        if pph_nom > 0 and p.kode_akun_pph:
            obj_pph = db.query(COA).filter(COA.kode == p.kode_akun_pph).first()
            db.add(
                JurnalDetail(
                    header_id=jurnal_bkk.id,
                    kode_akun=p.kode_akun_pph,
                    nama_akun=obj_pph.nama if obj_pph else f"Hutang {p.jenis_pph}",
                    keterangan=f"Potongan {p.jenis_pph} atas pembayaran {bill.no_faktur_vendor}",
                    debit=0.0,
                    kredit=pph_nom,
                )
            )

        db.add(
            JurnalDetail(
                header_id=jurnal_bkk.id,
                kode_akun=akun_kas.kode,
                nama_akun=akun_kas.nama,
                keterangan=f"Pembayaran ke {bill.nama_vendor} ({akun_kas.nama})",
                debit=0.0,
                kredit=kas_keluar,
            )
        )

        catat_audit(
            db,
            request,
            current_user,
            modul="AP_PAYMENT",
            aksi="CREATE",
            referensi=p.no_payment,
            keterangan=f"Mengajukan pembayaran {bill.no_bill} senilai Rp {p.nominal_bayar:,.0f}",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Pengajuan pembayaran {p.no_payment} berhasil dikirim ke antrean Posting Pending!",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# RFP (Request For Payment)
@app.get("/api/rfp/next-no")
async def get_next_rfp_no(
    tanggal: str = "", current_user: User = Depends(get_current_user)
):
    prefix_bulan = (
        tanggal[:7].replace("-", "")
        if tanggal and len(tanggal) >= 7
        else datetime.now().strftime("%Y%m")
    )
    prefix_doc = f"RFP-{prefix_bulan}-"
    db = get_session()
    try:
        matching = (
            db.query(PaymentRequest)
            .filter(PaymentRequest.no_rfp.like(f"{prefix_doc}%"))
            .all()
        )
        seqs = []
        for m in matching:
            try:
                seqs.append(int(m.no_rfp.split("-")[-1]))
            except (ValueError, IndexError):
                pass
        next_seq = max(seqs) + 1 if seqs else 1
        return {"next_no": f"{prefix_doc}{next_seq:03d}"}
    finally:
        db.close()


@app.post("/api/rfp")
async def ajukan_rfp(
    p: PayloadRFP,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "approver", "super_user"])),
):
    db = get_session()
    try:
        bill = (
            db.query(VendorBillHeader)
            .filter(VendorBillHeader.no_bill == p.no_bill)
            .first()
        )
        if not bill or bill.status != "Posted":
            raise HTTPException(
                status_code=400,
                detail="Tagihan tidak valid atau belum disetujui Approver di GL!",
            )

        sisa_hutang = float(bill.saldo_terutang or 0.0)
        pph_nom = float(getattr(bill, "pph_nominal", 0.0) or 0.0)
        maksimal_kas = max(0.0, sisa_hutang - pph_nom)

        if p.nominal_diajukan <= 0 or p.nominal_diajukan > maksimal_kas:
            raise HTTPException(
                status_code=400,
                detail="Nominal pengajuan tidak valid atau melebihi batas sisa hutang!",
            )

        vendor = (
            db.query(MasterVendor)
            .filter(MasterVendor.kode_vendor == bill.kode_vendor)
            .first()
        )
        rek_info = (
            f"{vendor.nama_bank} - {vendor.no_rekening} a/n {vendor.atas_nama}"
            if vendor
            else "-"
        )

        rfp = PaymentRequest(
            no_rfp=p.no_rfp,
            tanggal=p.tanggal,
            kode_vendor=bill.kode_vendor,
            nama_vendor=bill.nama_vendor,
            no_bill=bill.no_bill,
            no_faktur_vendor=bill.no_faktur_vendor,
            nominal_diajukan=p.nominal_diajukan,
            jenis_pph=getattr(bill, "jenis_pph", "") or "",
            kode_akun_pph=getattr(bill, "kode_akun_pph", "") or "",
            pph_nominal=pph_nom,
            rekening_tujuan=rek_info,
            keterangan=p.keterangan,
            pemohon=current_user.username,
            status="Submitted",
        )
        db.add(rfp)
        catat_audit(
            db,
            request,
            current_user,
            modul="RFP",
            aksi="SUBMIT",
            referensi=p.no_rfp,
            keterangan=f"Pengajuan RFP tagihan {p.no_bill}",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Dokumen {p.no_rfp} berhasil diajukan ke Keuangan!",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/rfp/inbox-finance")
async def get_rfp_inbox(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        rfps = (
            db.query(PaymentRequest)
            .filter(PaymentRequest.status == "Submitted")
            .order_by(PaymentRequest.id.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "no_rfp": r.no_rfp,
                "no_bill": r.no_bill or "-",
                "tanggal": r.tanggal,
                "kode_vendor": r.kode_vendor,
                "nama_vendor": r.nama_vendor,
                "no_faktur_vendor": r.no_faktur_vendor,
                "nominal_diajukan": float(r.nominal_diajukan or 0.0),
                "jenis_pph": r.jenis_pph or "-",
                "kode_akun_pph": r.kode_akun_pph or "",
                "pph_nominal": float(r.pph_nominal or 0.0),
                "kas_bersih": max(
                    0.0, float(r.nominal_diajukan or 0.0) - float(r.pph_nominal or 0.0)
                ),
                "rekening_tujuan": r.rekening_tujuan,
                "keterangan": r.keterangan,
                "pemohon": r.pemohon,
            }
            for r in rfps
        ]
    finally:
        db.close()


@app.post("/api/vendor_payment/batch-rfp")
async def eksekusi_pembayaran_rfp(
    p: PayloadFinanceExecute,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "approver", "super_user"])),
):
    db = get_session()
    try:
        if not p.rfp_ids:
            raise HTTPException(status_code=400, detail="Pilih minimal 1 RFP!")

        rfp_list = (
            db.query(PaymentRequest)
            .filter(
                PaymentRequest.id.in_(p.rfp_ids), PaymentRequest.status == "Submitted"
            )
            .all()
        )
        if not rfp_list:
            raise HTTPException(
                status_code=400, detail="RFP tidak ditemukan atau sudah diproses!"
            )

        akun_bank = db.query(COA).filter(COA.kode == p.kode_akun_kas).first()
        if not akun_bank:
            raise HTTPException(
                status_code=404, detail="Akun Kas/Bank pembayar tidak valid!"
            )

        total_hutang_ap = sum(float(r.nominal_diajukan or 0.0) for r in rfp_list)
        total_potongan_pph = sum(float(r.pph_nominal or 0.0) for r in rfp_list)
        total_kas_keluar = total_hutang_ap - total_potongan_pph

        for r in rfp_list:
            r.status = "Approved_Finance"
            bill = (
                db.query(VendorBillHeader)
                .filter(VendorBillHeader.no_bill == r.no_bill)
                .first()
            )
            db.add(
                VendorPayment(
                    kode_voucher=p.kode_voucher or "BKK",
                    no_payment=p.no_payment,
                    tanggal=p.tanggal,
                    kode_vendor=r.kode_vendor,
                    nama_vendor=r.nama_vendor,
                    no_bill=r.no_bill,
                    no_faktur_vendor=r.no_faktur_vendor,
                    kode_cabang=p.kode_cabang,
                    kode_departemen=p.kode_departemen,
                    keterangan=f"[RFP: {r.no_rfp}] {p.keterangan}",
                    kode_akun_kas=akun_bank.kode,
                    nama_akun_kas=akun_bank.nama,
                    kode_akun_hutang=bill.kode_akun_hutang if bill else "211000",
                    nama_akun_hutang=bill.nama_akun_hutang if bill else "Hutang Usaha",
                    nominal_bayar=r.nominal_diajukan,
                    jenis_pph=r.jenis_pph,
                    kode_akun_pph=r.kode_akun_pph,
                    pph_nominal=r.pph_nominal,
                    jumlah_kas_keluar=r.nominal_diajukan - r.pph_nominal,
                    status="Pending",
                    pembuat=current_user.username,
                )
            )

        ringkasan_rfp = ", ".join([r.no_rfp for r in rfp_list])
        jurnal_bkk = JurnalHeader(
            kode_voucher=p.kode_voucher or "BKK",
            tanggal=p.tanggal,
            no_referensi=p.no_payment,
            kode_cabang=p.kode_cabang,
            kode_departemen=p.kode_departemen,
            keterangan=f"Pencairan Kas via {akun_bank.nama} ({ringkasan_rfp}) - {p.keterangan}",
            total_debit=total_hutang_ap,
            total_kredit=total_hutang_ap,
            pembuat=current_user.username,
            status="Pending",
        )
        db.add(jurnal_bkk)
        db.flush()

        for r in rfp_list:
            bill = (
                db.query(VendorBillHeader)
                .filter(VendorBillHeader.no_bill == r.no_bill)
                .first()
            )
            db.add(
                JurnalDetail(
                    header_id=jurnal_bkk.id,
                    kode_akun=bill.kode_akun_hutang if bill else "211000",
                    nama_akun=bill.nama_akun_hutang if bill else "Hutang Usaha",
                    keterangan=f"Pelunasan {r.no_rfp} (Inv: {r.no_faktur_vendor}) - {r.nama_vendor}",
                    debit=r.nominal_diajukan,
                    kredit=0.0,
                )
            )
            if r.pph_nominal > 0 and r.kode_akun_pph:
                obj_pph = db.query(COA).filter(COA.kode == r.kode_akun_pph).first()
                db.add(
                    JurnalDetail(
                        header_id=jurnal_bkk.id,
                        kode_akun=r.kode_akun_pph,
                        nama_akun=obj_pph.nama if obj_pph else f"Hutang {r.jenis_pph}",
                        keterangan=f"Potongan {r.jenis_pph} ({r.no_rfp})",
                        debit=0.0,
                        kredit=r.pph_nominal,
                    )
                )

        db.add(
            JurnalDetail(
                header_id=jurnal_bkk.id,
                kode_akun=akun_bank.kode,
                nama_akun=akun_bank.nama,
                keterangan=f"Pengeluaran dana transfer ({ringkasan_rfp})",
                debit=0.0,
                kredit=total_kas_keluar,
            )
        )

        catat_audit(
            db,
            request,
            current_user,
            modul="FIN_PAYMENT",
            aksi="BATCH_PAY",
            referensi=p.no_payment,
            keterangan=f"Pencairan {len(rfp_list)} RFP via {akun_bank.nama} Kas Keluar Rp {total_kas_keluar:,.0f}",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Bukti Kas Keluar {p.no_payment} berhasil diajukan ke antrean Posting Pending!",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ==========================================
# 8. MODUL LOGISTIK & INVENTORY GUDANG
# ==========================================
@app.get("/api/barang")
async def get_semua_barang(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        barang = db.query(MasterBarang).all()
        return [
            {
                "kode_barang": b.kode_barang,
                "nama_barang": b.nama_barang,
                "kategori": getattr(b, "kategori", "-") or "-",
                "satuan": getattr(b, "satuan", "PCS") or "PCS",
                "stok_sekarang": getattr(b, "stok_sekarang", 0.0) or 0.0,
                "harga_rata_rata": getattr(b, "harga_rata_rata", 0.0)
                or getattr(b, "harga_beli", 0.0)
                or 0.0,
                "harga_beli": getattr(b, "harga_beli", 0.0) or 0.0,
                "harga_jual": getattr(b, "harga_jual", 0.0) or 0.0,
                "kode_akun_persediaan": getattr(
                    b, "akun_persediaan", getattr(b, "kode_akun_persediaan", "1150-00")
                )
                or "1150-00",
                "status": getattr(b, "status", "Aktif") or "Aktif",
            }
            for b in barang
        ]
    finally:
        db.close()


@app.post("/api/barang")
async def simpan_barang(
    data: PayloadBarang,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "approver", "super_user"])),
):
    db = get_session()
    try:
        cek = (
            db.query(MasterBarang)
            .filter(MasterBarang.kode_barang == data.kode_barang)
            .first()
        )
        if cek:
            cek.nama_barang = data.nama_barang
            cek.kategori = data.kategori
            cek.satuan = data.satuan
            cek.harga_beli = data.harga_beli
            cek.harga_jual = data.harga_jual
            cek.kode_akun_persediaan = data.kode_akun_persediaan
            cek.status = data.status
            pesan = f"Barang {data.kode_barang} berhasil diperbarui."
            aksi = "UPDATE"
        else:
            db.add(
                MasterBarang(
                    kode_barang=data.kode_barang,
                    nama_barang=data.nama_barang,
                    kategori=data.kategori,
                    satuan=data.satuan,
                    harga_beli=data.harga_beli,
                    harga_jual=data.harga_jual,
                    kode_akun_persediaan=data.kode_akun_persediaan,
                    status=data.status,
                )
            )
            pesan = f"Barang {data.kode_barang} berhasil ditambahkan."
            aksi = "CREATE"

        catat_audit(
            db,
            request,
            current_user,
            modul="INVENTORY",
            aksi=aksi,
            referensi=data.kode_barang,
            keterangan=pesan,
        )
        db.commit()
        return {"status": "success", "pesan": pesan}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.delete("/api/barang/{kode_barang}")
async def hapus_barang(
    kode_barang: str,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        barang = (
            db.query(MasterBarang)
            .filter(MasterBarang.kode_barang == kode_barang)
            .first()
        )
        if not barang:
            raise HTTPException(status_code=404, detail="Barang tidak ditemukan.")
        catat_audit(
            db,
            request,
            current_user,
            modul="INVENTORY",
            aksi="DELETE",
            referensi=kode_barang,
            keterangan="Hapus master barang",
        )
        db.delete(barang)
        db.commit()
        return {"status": "success", "pesan": f"Barang {kode_barang} berhasil dihapus."}
    finally:
        db.close()


@app.post("/api/inventory/transaksi")
async def simpan_transaksi_inventory(
    data: PayloadTransaksiInventory,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "approver", "super_user"])),
):
    db = get_session()
    try:
        barang = (
            db.query(MasterBarang)
            .filter(MasterBarang.kode_barang == data.kode_barang)
            .first()
        )
        if not barang:
            raise HTTPException(status_code=404, detail="Barang tidak ditemukan.")
        if data.qty <= 0:
            raise HTTPException(status_code=400, detail="Qty harus lebih besar dari 0.")

        cabang_val = data.kode_cabang or "HO"
        dept_val = data.kode_departemen or "LOG"

        if data.jenis_transaksi.upper() == "MASUK":
            total_nilai_lama = (barang.stok_sekarang or 0.0) * (
                barang.harga_rata_rata or 0.0
            )
            total_nilai_baru = data.qty * data.harga_satuan
            stok_baru = (barang.stok_sekarang or 0.0) + data.qty
            barang.harga_rata_rata = (
                (total_nilai_lama + total_nilai_baru) / stok_baru
                if stok_baru > 0
                else data.harga_satuan
            )
            barang.stok_sekarang = stok_baru
            harga_transaksi = data.harga_satuan
            total_transaksi = total_nilai_baru
        elif data.jenis_transaksi.upper() == "KELUAR":
            stok_saat_ini = barang.stok_sekarang or 0.0
            if stok_saat_ini < data.qty:
                raise HTTPException(
                    status_code=400,
                    detail=f"Stok tidak mencukupi! Sisa: {stok_saat_ini}",
                )
            harga_transaksi = barang.harga_rata_rata or barang.harga_beli or 0.0
            total_transaksi = data.qty * harga_transaksi
            barang.stok_sekarang = stok_saat_ini - data.qty
        else:
            raise HTTPException(status_code=400, detail="Jenis transaksi tidak valid.")

        db.add(
            TransaksiInventory(
                tanggal=data.tanggal,
                kode_barang=data.kode_barang,
                jenis_transaksi=data.jenis_transaksi.upper(),
                qty=data.qty,
                harga_satuan=harga_transaksi,
                total_nilai=total_transaksi,
                kode_cabang=cabang_val,
                kode_departemen=dept_val,
                keterangan=data.keterangan,
            )
        )

        inv_hdr = InventoryHeader(
            no_bukti=data.no_bukti,
            tanggal=data.tanggal,
            jenis_transaksi=data.jenis_transaksi,
            kode_cabang=cabang_val,
            kode_departemen=dept_val,
            keterangan=data.keterangan or "Transaksi Inventory",
            pembuat=current_user.username,
            status="Posted",
        )
        db.add(inv_hdr)
        db.flush()

        db.add(
            InventoryDetail(
                header_id=inv_hdr.id,
                kode_barang=data.kode_barang,
                qty=data.qty,
                harga_satuan=harga_transaksi,
                total_nilai=total_transaksi,
            )
        )

        # Auto-Jurnal Inventory
        jurnal_hdr = JurnalHeader(
            no_referensi=data.no_bukti,
            tanggal=data.tanggal,
            kode_voucher="INV",
            kode_cabang=cabang_val,
            kode_departemen=dept_val,
            keterangan=f"{data.keterangan} ({data.jenis_transaksi})",
            total_debit=total_transaksi,
            total_kredit=total_transaksi,
            pembuat=current_user.username,
            status="Posted",
        )
        db.add(jurnal_hdr)
        db.flush()

        akun_persediaan = (
            getattr(
                barang,
                "akun_persediaan",
                getattr(barang, "kode_akun_persediaan", "1130-00"),
            )
            or "1130-00"
        )
        obj_akun_pers = db.query(COA).filter(COA.kode == akun_persediaan).first()
        nama_pers = (
            obj_akun_pers.nama if obj_akun_pers else f"Persediaan {barang.nama_barang}"
        )
        obj_akun_lawan = db.query(COA).filter(COA.kode == data.akun_lawan).first()
        nama_lawan = obj_akun_lawan.nama if obj_akun_lawan else "Akun Lawan"

        if data.jenis_transaksi.upper() == "MASUK":
            db.add(
                JurnalDetail(
                    header_id=jurnal_hdr.id,
                    kode_akun=akun_persediaan,
                    nama_akun=nama_pers,
                    keterangan=data.keterangan,
                    debit=total_transaksi,
                    kredit=0.0,
                )
            )
            db.add(
                JurnalDetail(
                    header_id=jurnal_hdr.id,
                    kode_akun=data.akun_lawan,
                    nama_akun=nama_lawan,
                    keterangan=data.keterangan,
                    debit=0.0,
                    kredit=total_transaksi,
                )
            )
        else:
            db.add(
                JurnalDetail(
                    header_id=jurnal_hdr.id,
                    kode_akun=data.akun_lawan,
                    nama_akun=nama_lawan,
                    keterangan=data.keterangan,
                    debit=total_transaksi,
                    kredit=0.0,
                )
            )
            db.add(
                JurnalDetail(
                    header_id=jurnal_hdr.id,
                    kode_akun=akun_persediaan,
                    nama_akun=nama_pers,
                    keterangan=data.keterangan,
                    debit=0.0,
                    kredit=total_transaksi,
                )
            )

        catat_audit(
            db,
            request,
            current_user,
            modul="INVENTORY",
            aksi=data.jenis_transaksi.upper(),
            referensi=data.no_bukti,
            keterangan=f"Mutasi {data.kode_barang} senilai Rp {total_transaksi:,.0f}",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Transaksi {data.no_bukti} berhasil disimpan dan jurnal terposting!",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/inventory/next-ref")
async def get_next_inventory_ref(
    jenis: str = "Masuk",
    tanggal: str = "",
    current_user: User = Depends(get_current_user),
):
    if not tanggal:
        return {"no_bukti": ""}
    db = get_session()
    try:
        prefix_jenis = "IN" if jenis.lower() == "masuk" else "OUT"
        yyyymm = tanggal[:7].replace("-", "")
        prefix_doc = f"{prefix_jenis}-{yyyymm}-"
        matching_trx = (
            db.query(InventoryHeader)
            .filter(InventoryHeader.no_bukti.like(f"{prefix_doc}%"))
            .all()
        )
        seqs = []
        for t in matching_trx:
            try:
                seqs.append(int(t.no_bukti.split("-")[-1]))
            except (ValueError, IndexError):
                pass
        next_seq = max(seqs) + 1 if seqs else 1
        return {"no_bukti": f"{prefix_doc}{next_seq:03d}"}
    finally:
        db.close()


@app.get("/api/inventory/kartu-stok")
async def get_kartu_stok(
    kode_barang: str,
    tgl_mulai: str,
    tgl_selesai: str,
    current_user: User = Depends(get_current_user),
):
    db = get_session()
    try:
        barang = (
            db.query(MasterBarang)
            .filter(MasterBarang.kode_barang == kode_barang)
            .first()
        )
        if not barang:
            raise HTTPException(status_code=404, detail="Barang tidak ditemukan.")

        trx_past = (
            db.query(TransaksiInventory)
            .filter(
                TransaksiInventory.kode_barang == kode_barang,
                TransaksiInventory.tanggal < tgl_mulai,
            )
            .all()
        )

        saldo_awal_qty = 0.0
        saldo_awal_nilai = 0.0
        for t in trx_past:
            if t.jenis_transaksi.upper() == "MASUK":
                saldo_awal_qty += t.qty
                saldo_awal_nilai += t.total_nilai
            else:
                saldo_awal_qty -= t.qty
                saldo_awal_nilai -= t.total_nilai

        mutasi_trx = (
            db.query(TransaksiInventory)
            .filter(
                TransaksiInventory.kode_barang == kode_barang,
                TransaksiInventory.tanggal >= tgl_mulai,
                TransaksiInventory.tanggal <= tgl_selesai,
            )
            .order_by(TransaksiInventory.tanggal.asc(), TransaksiInventory.id.asc())
            .all()
        )

        running_qty = saldo_awal_qty
        running_nilai = saldo_awal_nilai
        mutasi_data = []
        tot_masuk_qty = tot_masuk_nilai = tot_keluar_qty = tot_keluar_nilai = 0.0

        for m in mutasi_trx:
            masuk_qty = m.qty if m.jenis_transaksi.upper() == "MASUK" else 0.0
            masuk_nilai = m.total_nilai if m.jenis_transaksi.upper() == "MASUK" else 0.0
            keluar_qty = m.qty if m.jenis_transaksi.upper() == "KELUAR" else 0.0
            keluar_nilai = (
                m.total_nilai if m.jenis_transaksi.upper() == "KELUAR" else 0.0
            )

            running_qty += masuk_qty - keluar_qty
            running_nilai += masuk_nilai - keluar_nilai
            tot_masuk_qty += masuk_qty
            tot_masuk_nilai += masuk_nilai
            tot_keluar_qty += keluar_qty
            tot_keluar_nilai += keluar_nilai

            mutasi_data.append(
                {
                    "tanggal": m.tanggal,
                    "jenis": m.jenis_transaksi,
                    "keterangan": m.keterangan or "-",
                    "harga_satuan": m.harga_satuan,
                    "masuk_qty": masuk_qty,
                    "masuk_nilai": masuk_nilai,
                    "keluar_qty": keluar_qty,
                    "keluar_nilai": keluar_nilai,
                    "saldo_qty": running_qty,
                    "saldo_nilai": running_nilai,
                }
            )

        return {
            "barang": {
                "kode_barang": barang.kode_barang,
                "nama_barang": barang.nama_barang,
                "satuan": getattr(barang, "satuan", "PCS"),
                "harga_rata_rata": barang.harga_rata_rata,
            },
            "saldo_awal": {"qty": saldo_awal_qty, "nilai": saldo_awal_nilai},
            "total_mutasi": {
                "masuk_qty": tot_masuk_qty,
                "masuk_nilai": tot_masuk_nilai,
                "keluar_qty": tot_keluar_qty,
                "keluar_nilai": tot_keluar_nilai,
            },
            "saldo_akhir": {"qty": running_qty, "nilai": running_nilai},
            "mutasi": mutasi_data,
        }
    finally:
        db.close()


@app.post("/api/inventory/stock-opname")
async def simpan_stock_opname(
    payload: PayloadStockOpname,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "approver", "super_user"])),
):
    db = get_session()
    try:
        cek_inv = (
            db.query(InventoryHeader)
            .filter(InventoryHeader.no_bukti == payload.no_bukti)
            .first()
        )
        if cek_inv:
            raise HTTPException(
                status_code=400, detail="Nomor Bukti Opname sudah digunakan!"
            )

        inv_header = InventoryHeader(
            no_bukti=payload.no_bukti,
            tanggal=payload.tanggal,
            jenis_transaksi="Opname",
            kode_cabang=payload.kode_cabang,
            keterangan=payload.keterangan,
            pembuat=current_user.username,
            status="Posted",
        )
        db.add(inv_header)
        db.flush()

        total_nilai_debit = total_nilai_kredit = 0.0
        baris_jurnal = []
        obj_akun_selisih = (
            db.query(COA).filter(COA.kode == payload.akun_selisih).first()
        )
        nama_akun_selisih = (
            obj_akun_selisih.nama if obj_akun_selisih else "Selisih Persediaan"
        )

        for item in payload.items:
            if item.selisih == 0:
                continue

            barang = (
                db.query(MasterBarang)
                .filter(MasterBarang.kode_barang == item.kode_barang)
                .first()
            )
            if not barang:
                raise HTTPException(
                    status_code=404,
                    detail=f"Barang {item.kode_barang} tidak ditemukan.",
                )

            akun_persediaan = getattr(barang, "akun_persediaan", "113000") or "113000"
            obj_akun_pers = db.query(COA).filter(COA.kode == akun_persediaan).first()
            nama_akun_pers = (
                obj_akun_pers.nama
                if obj_akun_pers
                else f"Persediaan {barang.nama_barang}"
            )

            qty_penyesuaian = abs(item.selisih)
            nilai_penyesuaian = qty_penyesuaian * item.harga_satuan

            db.add(
                InventoryDetail(
                    header_id=inv_header.id,
                    kode_barang=item.kode_barang,
                    qty=item.selisih,
                    harga_satuan=item.harga_satuan,
                    total_nilai=nilai_penyesuaian,
                )
            )

            db.add(
                TransaksiInventory(
                    tanggal=payload.tanggal,
                    kode_barang=item.kode_barang,
                    jenis_transaksi="MASUK" if item.selisih > 0 else "KELUAR",
                    qty=qty_penyesuaian,
                    harga_satuan=item.harga_satuan,
                    total_nilai=nilai_penyesuaian,
                    keterangan=f"Penyesuaian Opname ({payload.no_bukti})",
                )
            )

            barang.stok_sekarang = item.stok_fisik

            if item.selisih > 0:
                baris_jurnal.append(
                    JurnalDetail(
                        kode_akun=akun_persediaan,
                        nama_akun=nama_akun_pers,
                        keterangan=f"Surplus - {barang.nama_barang}",
                        debit=nilai_penyesuaian,
                        kredit=0.0,
                    )
                )
                baris_jurnal.append(
                    JurnalDetail(
                        kode_akun=payload.akun_selisih,
                        nama_akun=nama_akun_selisih,
                        keterangan=f"Surplus - {barang.nama_barang}",
                        debit=0.0,
                        kredit=nilai_penyesuaian,
                    )
                )
            else:
                baris_jurnal.append(
                    JurnalDetail(
                        kode_akun=payload.akun_selisih,
                        nama_akun=nama_akun_selisih,
                        keterangan=f"Defisit - {barang.nama_barang}",
                        debit=nilai_penyesuaian,
                        kredit=0.0,
                    )
                )
                baris_jurnal.append(
                    JurnalDetail(
                        kode_akun=akun_persediaan,
                        nama_akun=nama_akun_pers,
                        keterangan=f"Defisit - {barang.nama_barang}",
                        debit=0.0,
                        kredit=nilai_penyesuaian,
                    )
                )

            total_nilai_debit += nilai_penyesuaian
            total_nilai_kredit += nilai_penyesuaian

        if baris_jurnal and total_nilai_debit > 0:
            jurnal_hdr = JurnalHeader(
                no_referensi=payload.no_bukti,
                tanggal=payload.tanggal,
                kode_voucher="OPN",
                kode_cabang=payload.kode_cabang,
                kode_departemen="LOG",
                keterangan=f"Penyesuaian Stock Opname: {payload.keterangan}",
                total_debit=total_nilai_debit,
                total_kredit=total_nilai_kredit,
                pembuat=current_user.username,
                status="Posted",
            )
            for bj in baris_jurnal:
                jurnal_hdr.baris_detail.append(bj)
            db.add(jurnal_hdr)

        catat_audit(
            db,
            request,
            current_user,
            modul="OPNAME",
            aksi="PROCESS",
            referensi=payload.no_bukti,
            keterangan=f"Eksekusi opname dengan net penyesuaian Rp {total_nilai_debit:,.0f}",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Stock Opname {payload.no_bukti} berhasil diproses & jurnal terposting!",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ==========================================
# 9. MODUL ASET TETAP (FIXED ASSETS)
# ==========================================
@app.get("/api/fixed_assets")
async def get_all_fixed_assets(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        assets = db.query(FixedAsset).all()
        return [
            {
                "kode_aset": getattr(a, "kode_aset", "") or "-",
                "nama_aset": getattr(a, "nama_aset", "") or "-",
                "kode_cabang": getattr(a, "kode_cabang", "HO") or "HO",
                "kode_departemen": getattr(a, "kode_departemen", "FIN") or "FIN",
                "golongan_fiskal": getattr(a, "golongan_fiskal", "GOL1") or "GOL1",
                "tgl_perolehan": str(getattr(a, "tgl_perolehan", "")) or "-",
                "harga_perolehan": float(getattr(a, "harga_perolehan", 0.0) or 0.0),
                "akumulasi_penyusutan": float(
                    getattr(a, "akumulasi_penyusutan", 0.0) or 0.0
                ),
                "nilai_residu": float(getattr(a, "nilai_residu", 0.0) or 0.0),
                "masa_manfaat_bulan": int(getattr(a, "masa_manfaat_bulan", 48) or 48),
                "nilai_buku": float(getattr(a, "nilai_buku", 0.0) or 0.0),
                "status": getattr(a, "status", "Aktif") or "Aktif",
            }
            for a in assets
        ]
    finally:
        db.close()


@app.post("/api/fixed_assets")
async def simpan_fixed_asset(
    data: PayloadFixedAsset,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "approver", "super_user"])),
):
    db = get_session()
    try:
        cek = (
            db.query(FixedAsset).filter(FixedAsset.kode_aset == data.kode_aset).first()
        )
        if cek:
            raise HTTPException(status_code=400, detail="Kode Aset sudah terdaftar!")

        aset_baru = FixedAsset(
            kode_aset=data.kode_aset,
            nama_aset=data.nama_aset,
            kode_cabang=data.kode_cabang,
            kode_departemen=data.kode_departemen,
            golongan_fiskal=data.golongan_fiskal,
            tgl_perolehan=data.tgl_perolehan,
            harga_perolehan=data.harga_perolehan,
            nilai_residu=data.nilai_residu,
            masa_manfaat_bulan=data.masa_manfaat_bulan,
            akumulasi_penyusutan=0.0,
            nilai_buku=data.harga_perolehan,
            akun_aset=data.akun_aset,
            akun_akumulasi=data.akun_akumulasi,
            akun_beban=data.akun_beban,
            status="Aktif",
        )
        db.add(aset_baru)
        catat_audit(
            db,
            request,
            current_user,
            modul="ASSET",
            aksi="CREATE",
            referensi=data.kode_aset,
            keterangan=f"Mendaftarkan aset tetap {data.nama_aset}",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Aset {data.nama_aset} berhasil didaftarkan!",
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/api/fixed_assets/bulk-import")
async def bulk_import_fixed_assets(
    items: List[ItemAssetMigrasi],
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    if not items:
        raise HTTPException(status_code=400, detail="Data aset kosong.")

    db = get_session()
    try:
        total_berhasil = 0
        for it in items:
            cek = (
                db.query(FixedAsset)
                .filter(FixedAsset.kode_aset == it.kode_aset)
                .first()
            )
            nilai_buku = it.harga_perolehan - it.akumulasi_penyusutan
            if cek:
                cek.nama_aset = it.nama_aset
                cek.kode_cabang = it.kode_cabang
                cek.kode_departemen = it.kode_departemen
                cek.golongan_fiskal = it.golongan_fiskal
                cek.tgl_perolehan = it.tgl_perolehan
                cek.harga_perolehan = it.harga_perolehan
                cek.akumulasi_penyusutan = it.akumulasi_penyusutan
                cek.nilai_residu = it.nilai_residu
                cek.masa_manfaat_bulan = it.masa_manfaat_bulan
                cek.nilai_buku = nilai_buku
                cek.akun_aset = it.akun_aset
                cek.akun_akumulasi = it.akun_akumulasi
                cek.akun_beban = it.akun_beban
            else:
                db.add(
                    FixedAsset(
                        kode_aset=it.kode_aset,
                        nama_aset=it.nama_aset,
                        kode_cabang=it.kode_cabang,
                        kode_departemen=it.kode_departemen,
                        golongan_fiskal=it.golongan_fiskal,
                        tgl_perolehan=it.tgl_perolehan,
                        harga_perolehan=it.harga_perolehan,
                        akumulasi_penyusutan=it.akumulasi_penyusutan,
                        nilai_residu=it.nilai_residu,
                        masa_manfaat_bulan=it.masa_manfaat_bulan,
                        nilai_buku=nilai_buku,
                        akun_aset=it.akun_aset,
                        akun_akumulasi=it.akun_akumulasi,
                        akun_beban=it.akun_beban,
                        status="Aktif",
                    )
                )
            total_berhasil += 1

        catat_audit(
            db,
            request,
            current_user,
            modul="ASSET",
            aksi="IMPORT",
            keterangan=f"Import {total_berhasil} data aset tetap",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Berhasil mengimpor/memperbarui {total_berhasil} data aset tetap!",
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Gagal migrasi aset: {str(e)}")
    finally:
        db.close()


@app.post("/api/fixed_assets/depreciation-run")
async def run_monthly_depreciation(
    data: PayloadDepreciationRun,
    request: Request,
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    db = get_session()
    try:
        query = db.query(FixedAsset).filter(FixedAsset.status == "Aktif")
        if data.kode_cabang != "SEMUA":
            query = query.filter(FixedAsset.kode_cabang == data.kode_cabang)

        assets = query.all()
        if not assets:
            raise HTTPException(
                status_code=400, detail="Tidak ada aset aktif untuk disusutkan."
            )

        total_jurnal_dibuat = 0
        prefix_bulan = data.periode_bulan
        yyyymm = prefix_bulan.replace("-", "")

        for a in assets:
            if a.tgl_perolehan[:7] > prefix_bulan:
                continue

            depreciable_base = a.harga_perolehan - a.nilai_residu
            beban_per_bulan = depreciable_base / a.masa_manfaat_bulan
            max_susut = (a.harga_perolehan - a.akumulasi_penyusutan) - a.nilai_residu
            if max_susut <= 0:
                continue

            nominal_susut = round(min(beban_per_bulan, max_susut), 2)
            if nominal_susut <= 0:
                continue

            ket_jurnal = (
                f"Penyusutan {a.nama_aset} ({a.kode_aset}) Periode {prefix_bulan}"
            )
            cek_sudah_ada = (
                db.query(JurnalDetail)
                .join(JurnalHeader, JurnalDetail.header_id == JurnalHeader.id)
                .filter(
                    JurnalHeader.tanggal.like(f"{prefix_bulan}%"),
                    JurnalDetail.keterangan == ket_jurnal,
                )
                .first()
            )
            if cek_sudah_ada:
                continue

            jumlah_adj = (
                db.query(JurnalHeader)
                .filter(
                    JurnalHeader.kode_voucher == "ADJ",
                    JurnalHeader.tanggal.like(f"{prefix_bulan}%"),
                )
                .count()
            )

            no_ref_adj = f"ADJ-{yyyymm}-{jumlah_adj + 1:03d}"
            akun_beban_obj = db.query(COA).filter(COA.kode == a.akun_beban).first()
            akun_akum_obj = db.query(COA).filter(COA.kode == a.akun_akumulasi).first()

            jurnal_hdr = JurnalHeader(
                no_referensi=no_ref_adj,
                tanggal=f"{prefix_bulan}-28",
                kode_voucher="ADJ",
                kode_cabang=a.kode_cabang or "HO",
                kode_departemen=a.kode_departemen or "FIN",
                total_debit=nominal_susut,
                total_kredit=nominal_susut,
                pembuat=current_user.username,
                status="Posted",
            )
            db.add(jurnal_hdr)
            db.flush()

            db.add(
                JurnalDetail(
                    header_id=jurnal_hdr.id,
                    kode_akun=a.akun_beban,
                    nama_akun=akun_beban_obj.nama
                    if akun_beban_obj
                    else "Beban Penyusutan",
                    keterangan=ket_jurnal,
                    debit=nominal_susut,
                    kredit=0.0,
                )
            )
            db.add(
                JurnalDetail(
                    header_id=jurnal_hdr.id,
                    kode_akun=a.akun_akumulasi,
                    nama_akun=akun_akum_obj.nama
                    if akun_akum_obj
                    else "Akumulasi Penyusutan",
                    keterangan=ket_jurnal,
                    debit=0.0,
                    kredit=nominal_susut,
                )
            )

            a.akumulasi_penyusutan += nominal_susut
            a.nilai_buku = a.harga_perolehan - a.akumulasi_penyusutan
            total_jurnal_dibuat += 1

        catat_audit(
            db,
            request,
            current_user,
            modul="ASSET_DEP",
            aksi="RUN",
            referensi=prefix_bulan,
            keterangan=f"Eksekusi penyusutan aset bulanan periode {prefix_bulan} ({total_jurnal_dibuat} transaksi)",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Berhasil memposting {total_jurnal_dibuat} transaksi penyusutan (ADJ) ke Buku Besar!",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/fixed_assets/riwayat-penyusutan")
async def get_riwayat_penyusutan(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        jurnals = (
            db.query(JurnalHeader)
            .filter(
                (JurnalHeader.kode_voucher == "ADJ")
                | (JurnalHeader.no_referensi.like("ADJ%"))
                | (JurnalHeader.no_referensi.like("DEP%"))
            )
            .order_by(JurnalHeader.id.desc())
            .all()
        )
        return [
            {
                "no_referensi": j.no_referensi,
                "tanggal": j.tanggal,
                "kode_cabang": j.kode_cabang or "HO",
                "kode_departemen": j.kode_departemen or "FIN",
                "total_debit": j.total_debit,
                "status": j.status,
            }
            for j in jurnals
        ]
    finally:
        db.close()


@app.post("/api/fixed_assets/dispose")
async def dispose_fixed_asset(
    data: PayloadDisposeAsset,
    request: Request,
    current_user: User = Depends(require_roles(["maker", "approver", "super_user"])),
):
    db = get_session()
    try:
        aset = (
            db.query(FixedAsset).filter(FixedAsset.kode_aset == data.kode_aset).first()
        )
        if not aset:
            raise HTTPException(status_code=404, detail="Data aset tidak ditemukan.")
        if aset.status in ["Disposed", "Pending Disposal"]:
            raise HTTPException(
                status_code=400,
                detail="Aset ini sedang menunggu approval pelepasan atau sudah dilepas!",
            )

        harga_perolehan = float(aset.harga_perolehan or 0.0)
        akum_susut = float(aset.akumulasi_penyusutan or 0.0)
        nilai_buku = harga_perolehan - akum_susut
        harga_jual = float(data.harga_jual or 0.0)
        selisih_laba_rugi = harga_jual - nilai_buku

        prefix_bln = data.tgl_penjualan[:7].replace("-", "")
        jumlah_dsp = (
            db.query(JurnalHeader)
            .filter(JurnalHeader.no_referensi.like(f"DSP-{prefix_bln}%"))
            .count()
        )
        no_ref_dsp = f"DSP-{prefix_bln}-{jumlah_dsp + 1:03d}"

        jurnal_hdr = JurnalHeader(
            no_referensi=no_ref_dsp,
            tanggal=data.tgl_penjualan,
            kode_voucher="DSP",
            kode_cabang=data.kode_cabang or aset.kode_cabang or "HO",
            kode_departemen=data.kode_departemen or aset.kode_departemen or "FIN",
            keterangan=f"{data.keterangan}: {aset.nama_aset} ({aset.kode_aset})",
            total_debit=0.0,
            total_kredit=0.0,
            pembuat=current_user.username,
            status="Pending",
        )
        db.add(jurnal_hdr)
        db.flush()

        tot_debit = tot_kredit = 0.0
        if harga_jual > 0:
            db.add(
                JurnalDetail(
                    header_id=jurnal_hdr.id,
                    kode_akun=data.akun_kas,
                    nama_akun="Kas/Bank Penjualan Aset",
                    keterangan=f"Penerimaan {aset.nama_aset}",
                    debit=harga_jual,
                    kredit=0.0,
                )
            )
            tot_debit += harga_jual

        if akum_susut > 0:
            db.add(
                JurnalDetail(
                    header_id=jurnal_hdr.id,
                    kode_akun=aset.akun_akumulasi,
                    nama_akun="Pembalikan Akumulasi",
                    keterangan=f"Eliminasi akum. {aset.nama_aset}",
                    debit=akum_susut,
                    kredit=0.0,
                )
            )
            tot_debit += akum_susut

        db.add(
            JurnalDetail(
                header_id=jurnal_hdr.id,
                kode_akun=aset.akun_aset,
                nama_akun="Pembalikan Nilai Aset",
                keterangan=f"Pelepasan {aset.nama_aset}",
                debit=0.0,
                kredit=harga_perolehan,
            )
        )
        tot_kredit += harga_perolehan

        if selisih_laba_rugi > 0:
            db.add(
                JurnalDetail(
                    header_id=jurnal_hdr.id,
                    kode_akun=data.akun_laba_rugi,
                    nama_akun="Laba Penjualan Aset",
                    keterangan=f"Keuntungan {aset.nama_aset}",
                    debit=0.0,
                    kredit=selisih_laba_rugi,
                )
            )
            tot_kredit += selisih_laba_rugi
        elif selisih_laba_rugi < 0:
            rugi_nominal = abs(selisih_laba_rugi)
            db.add(
                JurnalDetail(
                    header_id=jurnal_hdr.id,
                    kode_akun=data.akun_laba_rugi,
                    nama_akun="Rugi Penjualan Aset",
                    keterangan=f"Kerugian {aset.nama_aset}",
                    debit=rugi_nominal,
                    kredit=0.0,
                )
            )
            tot_debit += rugi_nominal

        jurnal_hdr.total_debit = tot_debit
        jurnal_hdr.total_kredit = tot_kredit
        aset.status = "Pending Disposal"

        catat_audit(
            db,
            request,
            current_user,
            modul="ASSET_DISP",
            aksi="REQUEST",
            referensi=no_ref_dsp,
            keterangan=f"Pengajuan pelepasan aset {aset.nama_aset}",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Pengajuan pelepasan {aset.nama_aset} berhasil diajukan dengan voucher {no_ref_dsp} (Status Pending).",
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.api_route("/api/fixed_assets/reset-pending", methods=["GET", "POST"])
async def reset_pending_assets(
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        pending_assets = (
            db.query(FixedAsset).filter(FixedAsset.status == "Pending Disposal").all()
        )
        dipulihkan = 0
        for aset in pending_assets:
            jurnal_aktif = (
                db.query(JurnalHeader)
                .join(JurnalDetail, JurnalDetail.header_id == JurnalHeader.id)
                .filter(
                    JurnalHeader.kode_voucher == "DSP",
                    JurnalHeader.status == "Pending",
                    JurnalDetail.kode_akun == aset.akun_aset,
                )
                .first()
            )
            if not jurnal_aktif:
                aset.status = "Aktif"
                dipulihkan += 1
        db.commit()
        return {
            "status": "success",
            "pesan": f"Sinkronisasi selesai. {dipulihkan} aset berhasil dipulihkan ke status Aktif.",
        }
    finally:
        db.close()


# ==========================================
# 10. LAPORAN KEUANGAN (GL, TB, NERACA, LR, CF)
# ==========================================
@app.get("/api/buku_besar")
async def get_buku_besar(
    kode_akun: str,
    tgl_mulai: str,
    tgl_selesai: str,
    kode_cabang: str = "SEMUA",
    kode_departemen: str = "SEMUA",
):
    db = get_session()
    try:
        base_query = (
            db.query(JurnalDetail, JurnalHeader)
            .join(JurnalHeader, JurnalDetail.header_id == JurnalHeader.id)
            .filter(
                JurnalHeader.status == "Posted", JurnalDetail.kode_akun == kode_akun
            )
        )
        if kode_cabang != "SEMUA":
            base_query = base_query.filter(JurnalHeader.kode_cabang == kode_cabang)

        if kode_departemen != "SEMUA":
            base_query = base_query.filter(
                JurnalHeader.kode_departemen == kode_departemen
            )

        kode_str = str(kode_akun).strip()
        digit1 = kode_str[0] if kode_str else "0"
        is_nominal = digit1 in ["4", "5", "6", "7", "8", "9"]
        tahun_aktif = tgl_mulai[:4] if tgl_mulai else "2026"
        tgl_awal_tahun = f"{tahun_aktif}-01-01"

        if is_nominal:
            data_past = base_query.filter(
                JurnalHeader.tanggal >= tgl_awal_tahun, JurnalHeader.tanggal < tgl_mulai
            ).all()
        else:
            data_past = base_query.filter(JurnalHeader.tanggal < tgl_mulai).all()

        saldo_awal = sum(detail.debit - detail.kredit for detail, header in data_past)
        data_mutasi = (
            base_query.filter(
                JurnalHeader.tanggal >= tgl_mulai, JurnalHeader.tanggal <= tgl_selesai
            )
            .order_by(JurnalHeader.tanggal.asc(), JurnalHeader.id.asc())
            .all()
        )

        mutasi_list = []
        tot_debit = tot_kredit = 0.0
        for detail, header in data_mutasi:
            mutasi_list.append(
                {
                    "tanggal": header.tanggal,
                    "no_referensi": header.no_referensi,
                    "keterangan": detail.keterangan,
                    "debit": float(detail.debit or 0.0),
                    "kredit": float(detail.kredit or 0.0),
                }
            )
            tot_debit += float(detail.debit or 0.0)
            tot_kredit += float(detail.kredit or 0.0)

        saldo_akhir = saldo_awal + tot_debit - tot_kredit
        return {
            "kode_akun": kode_akun,
            "saldo_awal": saldo_awal,
            "total_debit": tot_debit,
            "total_kredit": tot_kredit,
            "saldo_akhir": saldo_akhir,
            "mutasi": mutasi_list,
        }
    finally:
        db.close()


@app.get("/api/trial_balance")
async def get_trial_balance(
    tgl_mulai: str,
    tgl_selesai: str,
    kode_cabang: str = "SEMUA",
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    db = get_session()
    try:
        coas = db.query(COA).all()
        base_query = (
            db.query(JurnalDetail, JurnalHeader)
            .join(JurnalHeader, JurnalDetail.header_id == JurnalHeader.id)
            .filter(JurnalHeader.status == "Posted")
        )
        if kode_cabang != "SEMUA":
            base_query = base_query.filter(JurnalHeader.kode_cabang == kode_cabang)

        all_trans = base_query.all()
        tahun_aktif = tgl_mulai[:4]
        tgl_awal_tahun = f"{tahun_aktif}-01-01"

        tb_data = []
        tot_deb_awal = tot_kre_awal = tot_deb_mutasi = tot_kre_mutasi = (
            tot_deb_akhir
        ) = tot_kre_akhir = 0

        trans_pl_lalu = [
            (d, h)
            for d, h in all_trans
            if h.tanggal < tgl_awal_tahun
            and str(d.kode_akun).strip()[:1] in ["4", "5", "6", "7", "8", "9"]
        ]
        pendapatan_lalu = sum(
            d.kredit - d.debit
            for d, h in trans_pl_lalu
            if str(d.kode_akun).strip()[:1] in ["4", "7"]
        )
        beban_lalu = sum(
            d.debit - d.kredit
            for d, h in trans_pl_lalu
            if str(d.kode_akun).strip()[:1] in ["5", "6", "8", "9"]
        )
        laba_ditahan_otomatis = pendapatan_lalu - beban_lalu

        for c in coas:
            kode = str(c.kode).strip()
            digit1 = kode[0] if kode else "0"
            is_nominal = digit1 in ["4", "5", "6", "7", "8", "9"]

            trans_akun = [
                (d, h) for d, h in all_trans if str(d.kode_akun).strip() == kode
            ]
            if is_nominal:
                past_trans = [
                    (d, h)
                    for d, h in trans_akun
                    if tgl_awal_tahun <= h.tanggal < tgl_mulai
                ]
            else:
                past_trans = [(d, h) for d, h in trans_akun if h.tanggal < tgl_mulai]

            saldo_awal = sum(d.debit - d.kredit for d, h in past_trans)
            if kode == "310002":
                saldo_awal -= laba_ditahan_otomatis

            period_trans = [
                (d, h) for d, h in trans_akun if tgl_mulai <= h.tanggal <= tgl_selesai
            ]
            mutasi_debit = sum(d.debit for d, h in period_trans)
            mutasi_kredit = sum(d.kredit for d, h in period_trans)
            saldo_akhir = saldo_awal + mutasi_debit - mutasi_kredit

            deb_awal = saldo_awal if saldo_awal > 0 else 0
            kre_awal = abs(saldo_awal) if saldo_awal < 0 else 0
            deb_akhir = saldo_akhir if saldo_akhir > 0 else 0
            kre_akhir = abs(saldo_akhir) if saldo_akhir < 0 else 0

            if (
                saldo_awal != 0
                or mutasi_debit != 0
                or mutasi_kredit != 0
                or saldo_akhir != 0
            ):
                tb_data.append(
                    {
                        "kode_akun": kode,
                        "nama_akun": c.nama.strip() if c.nama else "",
                        "debit_awal": deb_awal,
                        "kredit_awal": kre_awal,
                        "debit_mutasi": mutasi_debit,
                        "kredit_mutasi": mutasi_kredit,
                        "debit_akhir": deb_akhir,
                        "kredit_akhir": kre_akhir,
                    }
                )
                tot_deb_awal += deb_awal
                tot_kre_awal += kre_awal
                tot_deb_mutasi += mutasi_debit
                tot_kre_mutasi += mutasi_kredit
                tot_deb_akhir += deb_akhir
                tot_kre_akhir += kre_akhir

        return {
            "periode": f"{tgl_mulai} s/d {tgl_selesai}",
            "data": tb_data,
            "total": {
                "debit_awal": tot_deb_awal,
                "kredit_awal": tot_kre_awal,
                "debit_mutasi": tot_deb_mutasi,
                "kredit_mutasi": tot_kre_mutasi,
                "debit_akhir": tot_deb_akhir,
                "kredit_akhir": tot_kre_akhir,
            },
        }
    finally:
        db.close()


@app.get("/api/balance_sheet")
async def get_balance_sheet(
    per_tanggal: str,
    kode_cabang: str = "SEMUA",
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    db = get_session()
    try:
        coas = {str(c.kode).strip(): c for c in db.query(COA).all()}
        tahun_aktif = int(per_tanggal[:4]) if per_tanggal else 2026
        tahun_lalu = tahun_aktif - 1
        tgl_awal_tahun = f"{tahun_aktif}-01-01"

        base_query = (
            db.query(JurnalDetail, JurnalHeader)
            .join(JurnalHeader, JurnalDetail.header_id == JurnalHeader.id)
            .filter(
                JurnalHeader.status == "Posted", JurnalHeader.tanggal <= per_tanggal
            )
        )
        cbg_target = str(kode_cabang or "SEMUA").strip()
        if " - " in cbg_target:
            cbg_target = cbg_target.split(" - ")[0].strip()

        if cbg_target.upper() != "SEMUA":
            if cbg_target in ["00", "HO"]:
                base_query = base_query.filter(
                    JurnalHeader.kode_cabang.in_(["00", "HO", None, ""])
                )
            else:
                base_query = base_query.filter(JurnalHeader.kode_cabang == cbg_target)

        all_rows = base_query.all()
        balances = {}
        for d, h in all_rows:
            kode = str(d.kode_akun or "").strip()
            if not kode:
                continue
            if kode not in balances:
                balances[kode] = {"debit": 0.0, "kredit": 0.0}
            balances[kode]["debit"] += float(d.debit or 0.0)
            balances[kode]["kredit"] += float(d.kredit or 0.0)

        trans_lalu = [
            (d, h)
            for d, h in all_rows
            if str(h.tanggal or "") < tgl_awal_tahun
            and str(d.kode_akun or "").strip()[:1] in ["4", "5", "6", "7", "8", "9"]
        ]
        pend_lalu = sum(
            float(d.kredit or 0.0) - float(d.debit or 0.0)
            for d, h in trans_lalu
            if str(d.kode_akun or "").strip()[:1] in ["4", "7"]
        )
        beban_lalu = sum(
            float(d.debit or 0.0) - float(d.kredit or 0.0)
            for d, h in trans_lalu
            if str(d.kode_akun or "").strip()[:1] in ["5", "6", "8", "9"]
        )
        laba_ditahan_otomatis = pend_lalu - beban_lalu

        trans_kini = [
            (d, h)
            for d, h in all_rows
            if str(h.tanggal or "") >= tgl_awal_tahun
            and str(d.kode_akun or "").strip()[:1] in ["4", "5", "6", "7", "8", "9"]
        ]
        pend_kini = sum(
            float(d.kredit or 0.0) - float(d.debit or 0.0)
            for d, h in trans_kini
            if str(d.kode_akun or "").strip()[:1] in ["4", "7"]
        )
        beban_kini = sum(
            float(d.debit or 0.0) - float(d.kredit or 0.0)
            for d, h in trans_kini
            if str(d.kode_akun or "").strip()[:1] in ["5", "6", "8", "9"]
        )
        laba_tahun_berjalan = pend_kini - beban_kini

        aktiva_lancar_dict = {}
        aktiva_tetap_dict = {}
        aktiva_tak_berwujud_dict = {}
        passiva_lancar_dict = {
            "Hutang Usaha": 0.0,
            "Akrual": 0.0,
            "Hutang Lainnya": 0.0,
        }
        pinjaman_dict = {}
        modal_dict = {}

        for kode, val in balances.items():
            digit1 = kode[0] if kode else "0"
            prefix2 = kode[:2] if len(kode) >= 2 else ""
            if digit1 not in ["1", "2", "3"]:
                continue

            deb, kre = val["debit"], val["kredit"]
            c = coas.get(kode)
            kelompok = str(
                getattr(c, "kelompok", "")
                or getattr(c, "tipe_akun", "")
                or (c.nama if c else "")
                or "Lain-lain"
            ).strip()
            kl_lower = kelompok.lower()
            nama_akun = str(c.nama if c else "").strip().lower()

            if digit1 == "1":
                saldo = deb - kre
                if (
                    "amortisasi" in kl_lower
                    or "tak berwujud" in kl_lower
                    or kode.startswith("13")
                ):
                    aktiva_tak_berwujud_dict[kelompok] = (
                        aktiva_tak_berwujud_dict.get(kelompok, 0.0) + saldo
                    )
                elif (
                    "penyusutan" in kl_lower
                    or "tetap" in kl_lower
                    or "tetap" in nama_akun
                    or kode.startswith("12")
                ):
                    aktiva_tetap_dict[kelompok] = (
                        aktiva_tetap_dict.get(kelompok, 0.0) + saldo
                    )
                else:
                    aktiva_lancar_dict[kelompok] = (
                        aktiva_lancar_dict.get(kelompok, 0.0) + saldo
                    )
            elif digit1 == "2":
                saldo = kre - deb
                if prefix2 == "211":
                    passiva_lancar_dict["Hutang Usaha"] += saldo
                elif prefix2 == "212":
                    passiva_lancar_dict["Akrual"] += saldo
                elif prefix2 in ["213", "214"]:
                    passiva_lancar_dict["Hutang Lainnya"] += saldo
                elif prefix2 == "22":
                    label_p = (
                        kelompok
                        if ("pinjaman" in kl_lower or "hutang" in kl_lower)
                        else "Pinjaman"
                    )
                    pinjaman_dict[label_p] = pinjaman_dict.get(label_p, 0.0) + saldo
                else:
                    passiva_lancar_dict["Hutang Lainnya"] += saldo
            elif digit1 == "3":
                saldo = kre - deb
                if kode == "310002":
                    saldo += laba_ditahan_otomatis
                    modal_dict[f"Laba Ditahan Tahun {tahun_lalu}"] = (
                        modal_dict.get(f"Laba Ditahan Tahun {tahun_lalu}", 0.0) + saldo
                    )
                else:
                    modal_dict[kelompok] = modal_dict.get(kelompok, 0.0) + saldo

        modal_dict[f"Laba Tahun Berjalan {tahun_aktif}"] = (
            modal_dict.get(f"Laba Tahun Berjalan {tahun_aktif}", 0.0)
            + laba_tahun_berjalan
        )

        aktiva_lancar_items = [
            {"nama": k, "saldo": v} for k, v in aktiva_lancar_dict.items() if v != 0
        ]
        aktiva_tetap_items = [
            {"nama": k, "saldo": v} for k, v in aktiva_tetap_dict.items() if v != 0
        ]
        aktiva_tak_berwujud_items = [
            {"nama": k, "saldo": v}
            for k, v in aktiva_tak_berwujud_dict.items()
            if v != 0
        ]
        passiva_lancar_items = [
            {"nama": k, "saldo": passiva_lancar_dict[k]}
            for k in ["Hutang Usaha", "Akrual", "Hutang Lainnya"]
            if passiva_lancar_dict[k] != 0
        ]
        pinjaman_items = [
            {"nama": k, "saldo": v} for k, v in pinjaman_dict.items() if v != 0
        ]
        modal_items = [{"nama": k, "saldo": v} for k, v in modal_dict.items() if v != 0]

        tot_aktiva = (
            sum(x["saldo"] for x in aktiva_lancar_items)
            + sum(x["saldo"] for x in aktiva_tetap_items)
            + sum(x["saldo"] for x in aktiva_tak_berwujud_items)
        )
        tot_pasiva = (
            sum(x["saldo"] for x in passiva_lancar_items)
            + sum(x["saldo"] for x in pinjaman_items)
            + sum(x["saldo"] for x in modal_items)
        )

        return {
            "per_tanggal": per_tanggal,
            "kode_cabang": kode_cabang,
            "aktiva": {
                "lancar": aktiva_lancar_items,
                "tetap": aktiva_tetap_items,
                "tak_berwujud": aktiva_tak_berwujud_items,
                "total": tot_aktiva,
            },
            "pasiva": {
                "lancar": passiva_lancar_items,
                "pinjaman": pinjaman_items,
                "modal": modal_items,
                "total": tot_pasiva,
            },
            "is_balanced": abs(tot_aktiva - tot_pasiva) < 1.0,
        }
    finally:
        db.close()


@app.get("/api/profit_loss")
async def get_profit_loss(
    tgl_mulai: str,
    tgl_selesai: str,
    kode_cabang: str = "SEMUA",
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    db = get_session()
    try:
        base_query = (
            db.query(JurnalDetail, JurnalHeader)
            .join(JurnalHeader)
            .filter(
                JurnalHeader.status == "Posted",
                JurnalHeader.tanggal >= tgl_mulai,
                JurnalHeader.tanggal <= tgl_selesai,
            )
        )
        cbg_target = str(kode_cabang or "SEMUA").strip()
        if " - " in cbg_target:
            cbg_target = cbg_target.split(" - ")[0].strip()

        if cbg_target.upper() != "SEMUA":
            if cbg_target in ["00", "HO"]:
                base_query = base_query.filter(
                    JurnalHeader.kode_cabang.in_(["00", "HO", None, ""])
                )
            else:
                base_query = base_query.filter(JurnalHeader.kode_cabang == cbg_target)

        balances = {}
        for d, h in base_query.all():
            kode = str(d.kode_akun).strip()
            if kode not in balances:
                balances[kode] = {"debit": 0, "kredit": 0, "nama": d.nama_akun}
            balances[kode]["debit"] += d.debit
            balances[kode]["kredit"] += d.kredit

        pendapatan, hpp, beban_adm, pend_lain, beban_lain = [], [], [], [], []
        tot_pendapatan = tot_hpp = tot_beban_adm = tot_pend_lain = tot_beban_lain = 0

        for kode, val in balances.items():
            deb, kre = val["debit"], val["kredit"]
            nama = val["nama"] or ""
            if deb == 0 and kre == 0:
                continue

            digit1 = kode[0] if len(kode) >= 1 else "0"
            item = {"no_akun": kode, "nama_akun": nama, "saldo": 0}

            if digit1 == "4":
                item["saldo"] = kre - deb
                pendapatan.append(item)
                tot_pendapatan += item["saldo"]
            elif digit1 == "5":
                item["saldo"] = deb - kre
                hpp.append(item)
                tot_hpp += item["saldo"]
            elif digit1 == "6":
                item["saldo"] = deb - kre
                beban_adm.append(item)
                tot_beban_adm += item["saldo"]
            elif digit1 == "7":
                item["saldo"] = kre - deb
                pend_lain.append(item)
                tot_pend_lain += item["saldo"]
            elif digit1 in ["8", "9"]:
                item["saldo"] = deb - kre
                beban_lain.append(item)
                tot_beban_lain += item["saldo"]

        laba_kotor = tot_pendapatan - tot_hpp
        laba_operasional = laba_kotor - tot_beban_adm
        total_lain_lain = tot_pend_lain - tot_beban_lain
        laba_bersih = laba_operasional + total_lain_lain

        return {
            "periode": f"{tgl_mulai} s/d {tgl_selesai}",
            "cabang": kode_cabang,
            "data": {
                "pendapatan": pendapatan,
                "tot_pendapatan": tot_pendapatan,
                "hpp": hpp,
                "tot_hpp": tot_hpp,
                "laba_kotor": laba_kotor,
                "beban_adm": beban_adm,
                "tot_beban_adm": tot_beban_adm,
                "laba_operasional": laba_operasional,
                "pendapatan_lain": pend_lain,
                "tot_pendapatan_lain": tot_pend_lain,
                "beban_lain": beban_lain,
                "tot_beban_lain": tot_beban_lain,
                "total_lain_lain": total_lain_lain,
                "laba_bersih": laba_bersih,
            },
        }
    finally:
        db.close()


@app.get("/api/profit_loss_grouped_summary")
async def get_profit_loss_grouped_summary(
    tgl_mulai: str,
    tgl_selesai: str,
    kode_cabang: str = "SEMUA",
    current_user: User = Depends(require_roles(["approver", "super_user"])),
):
    db = get_session()
    try:
        coas = {str(c.kode).strip(): c for c in db.query(COA).all()}
        base_query = (
            db.query(JurnalDetail, JurnalHeader)
            .join(JurnalHeader)
            .filter(
                JurnalHeader.status == "Posted",
                JurnalHeader.tanggal >= tgl_mulai,
                JurnalHeader.tanggal <= tgl_selesai,
            )
        )
        if kode_cabang != "SEMUA":
            base_query = base_query.filter(JurnalHeader.kode_cabang == kode_cabang)

        balances = {}
        for d, h in base_query.all():
            kode = str(d.kode_akun).strip()
            if kode not in balances:
                balances[kode] = {"debit": 0, "kredit": 0}
            balances[kode]["debit"] += d.debit
            balances[kode]["kredit"] += d.kredit

        summary_pendapatan = {}
        summary_beban = {}

        for kode, val in balances.items():
            deb, kre = val["debit"], val["kredit"]
            if deb == 0 and kre == 0:
                continue

            c = coas.get(kode)
            digit1 = kode[0] if kode else "0"
            if digit1 not in ["4", "5", "6", "7", "8", "9"]:
                continue

            kelompok = (
                str(c.kelompok or "").strip().title() if c else "Kelompok Lainnya"
            )
            normal = "kredit" if digit1 in ["4", "8"] else "debit"
            saldo = (kre - deb) if normal == "kredit" else (deb - kre)

            if normal == "kredit":
                summary_pendapatan[kelompok] = (
                    summary_pendapatan.get(kelompok, 0) + saldo
                )
            else:
                summary_beban[kelompok] = summary_beban.get(kelompok, 0) + saldo

        return {
            "periode": f"{tgl_mulai} s/d {tgl_selesai}",
            "pendapatan": [
                {"kelompok": k, "saldo": v} for k, v in summary_pendapatan.items()
            ],
            "total_pendapatan": sum(summary_pendapatan.values()),
            "beban": [{"kelompok": k, "saldo": v} for k, v in summary_beban.items()],
            "total_beban": sum(summary_beban.values()),
            "laba_bersih": sum(summary_pendapatan.values())
            - sum(summary_beban.values()),
        }
    finally:
        db.close()


# ==========================================
# API DIAGNOSTIK CASH FLOW (CEK KONEKSI & AKUN KAS)
# ==========================================
@app.get("/api/debug/cek_kas")
async def debug_cek_kas():
    db = get_session()
    try:
        coas = db.query(COA).all()
        kas_detected = []
        for c in coas:
            kd = str(c.kode or "").strip()
            nm = str(c.nama or "").lower()
            kl = str(getattr(c, "kelompok", "") or "").lower()
            if (
                "kas" in nm
                or "bank" in nm
                or "kas" in kl
                or "bank" in kl
                or kd.startswith("11")
            ):
                kas_detected.append(
                    {
                        "kode": kd,
                        "nama": c.nama,
                        "kelompok": getattr(c, "kelompok", "-"),
                    }
                )

        # Cek apakah ada jurnal yang memakai akun-akun ini
        kodes = [x["kode"] for x in kas_detected]
        total_baris_kas = (
            db.query(JurnalDetail)
            .join(JurnalHeader)
            .filter(JurnalHeader.status == "Posted", JurnalDetail.kode_akun.in_(kodes))
            .count()
        )

        return {
            "jumlah_akun_kas_terdeteksi": len(kas_detected),
            "daftar_akun_kas": kas_detected,
            "total_baris_jurnal_kas_posted": total_baris_kas,
        }
    finally:
        db.close()


def ekstrak_angka_murni(val: str) -> str:
    """Membersihkan strip, titik, spasi, dll sehingga '1-1110-01' -> '1111001'"""
    if not val:
        return ""
    return re.sub(r"\D", "", str(val))


@app.get("/api/cash_flow")
async def get_cash_flow(tgl_mulai: str, tgl_selesai: str, kode_cabang: str = "SEMUA"):
    db = get_session()
    try:
        coas = db.query(COA).all()

        kas_codes = set()
        for c in coas:
            raw_k = str(c.kode or "").strip()
            num_k = "".join(ch for ch in raw_k if ch.isdigit())
            nm = str(c.nama or "").lower()
            kl = str(getattr(c, "kelompok", "") or "").lower()

            if (
                "kas" in nm
                or "bank" in nm
                or "petty" in nm
                or "kas" in kl
                or "bank" in kl
                or num_k.startswith("111")
                or num_k.startswith("110")
            ):
                if not (
                    num_k.startswith("2")
                    or num_k.startswith("7")
                    or "hutang" in nm
                    or "beban" in nm
                ):
                    kas_codes.add(raw_k)
                    kas_codes.add(num_k)

        kas_codes.update(
            {
                "111001",
                "1-1100",
                "1-1110",
                "1-1111",
                "1-1120",
                "1-1121",
                "1100",
                "1110",
                "1111",
                "1120",
                "1121",
            }
        )

        def is_kas(kd_val):
            s = str(kd_val or "").strip()
            num = "".join(ch for ch in s if ch.isdigit())
            return (s in kas_codes) or (num in kas_codes)

        cbg_target = str(kode_cabang or "SEMUA").strip()
        if " - " in cbg_target:
            cbg_target = cbg_target.split(" - ")[0].strip()

        # 2. Saldo Awal (Sebelum tgl_mulai)
        q_awal = (
            db.query(JurnalDetail, JurnalHeader)
            .join(JurnalHeader, JurnalDetail.header_id == JurnalHeader.id)
            .filter(JurnalHeader.status == "Posted", JurnalHeader.tanggal < tgl_mulai)
        )
        if cbg_target.upper() != "SEMUA":
            if cbg_target in ["00", "HO"]:
                q_awal = q_awal.filter(
                    JurnalHeader.kode_cabang.in_(["00", "HO", None, ""])
                )
            else:
                q_awal = q_awal.filter(JurnalHeader.kode_cabang == cbg_target)

        saldo_awal = sum(
            float(d.debit or 0.0) - float(d.kredit or 0.0)
            for d, h in q_awal.all()
            if is_kas(d.kode_akun)
        )

        # 3. Mutasi Periode Berjalan
        q_mutasi = (
            db.query(JurnalDetail, JurnalHeader)
            .join(JurnalHeader, JurnalDetail.header_id == JurnalHeader.id)
            .filter(
                JurnalHeader.status == "Posted",
                JurnalHeader.tanggal >= tgl_mulai,
                JurnalHeader.tanggal <= tgl_selesai,
            )
        )
        if cbg_target.upper() != "SEMUA":
            if cbg_target in ["00", "HO"]:
                q_mutasi = q_mutasi.filter(
                    JurnalHeader.kode_cabang.in_(["00", "HO", None, ""])
                )
            else:
                q_mutasi = q_mutasi.filter(JurnalHeader.kode_cabang == cbg_target)

        # Kelompokkan per voucher
        vouchers = {}
        for d, h in q_mutasi.all():
            if h.id not in vouchers:
                vouchers[h.id] = {"header": h, "kas": [], "non_kas": []}
            if is_kas(d.kode_akun):
                vouchers[h.id]["kas"].append(d)
            else:
                vouchers[h.id]["non_kas"].append(d)

        op_in, op_out = 0.0, 0.0
        inv_in, inv_out = 0.0, 0.0
        pen_in, pen_out = 0.0, 0.0

        for vid, item in vouchers.items():
            kas_list = item["kas"]
            if not kas_list:
                continue

            kas_net = sum(
                float(d.debit or 0.0) - float(d.kredit or 0.0) for d in kas_list
            )
            if round(kas_net, 2) == 0:
                continue

            non_kas_list = item["non_kas"]
            kd_lawan = ""
            if non_kas_list:
                major = max(
                    non_kas_list,
                    key=lambda x: float(x.kredit if kas_net > 0 else x.debit),
                )
                kd_lawan = str(major.kode_akun or "").strip()

            clean_lawan = "".join(ch for ch in kd_lawan if ch.isdigit())
            nom = abs(kas_net)

            # Klasifikasi
            if clean_lawan.startswith("12") or clean_lawan.startswith("13"):
                if kas_net > 0:
                    inv_in += nom
                else:
                    inv_out += nom
            elif clean_lawan.startswith("22") or (
                clean_lawan and clean_lawan[0] == "3"
            ):
                if kas_net > 0:
                    pen_in += nom
                else:
                    pen_out += nom
            else:
                if kas_net > 0:
                    op_in += nom
                else:
                    op_out += nom

        net_op = op_in - op_out
        net_inv = inv_in - inv_out
        net_pen = pen_in - pen_out
        kenaikan = net_op + net_inv + net_pen

        return {
            "periode": f"{tgl_mulai} s/d {tgl_selesai}",
            "cabang": cbg_target,
            "saldo_awal": saldo_awal,
            "operasional": {
                "in": op_in,
                "out": op_out,
                "net": net_op,
                "masuk": [{"jumlah": op_in}],
                "keluar": [{"jumlah": op_out}],
            },
            "investasi": {
                "in": inv_in,
                "out": inv_out,
                "net": net_inv,
                "masuk": [{"jumlah": inv_in}],
                "keluar": [{"jumlah": inv_out}],
            },
            "pendanaan": {
                "in": pen_in,
                "out": pen_out,
                "net": net_pen,
                "masuk": [{"jumlah": pen_in}],
                "keluar": [{"jumlah": pen_out}],
            },
            "kenaikan_bersih": kenaikan,
            "saldo_akhir": saldo_awal + kenaikan,
        }
    except Exception as e:
        print(f"Error Cash Flow: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ==========================================
# 11. PROFIL USAHA & REKENING PERUSAHAAN
# ==========================================
@app.get("/api/profil_usaha")
async def get_profil_usaha(current_user: User = Depends(get_current_user)):
    db = get_session()
    try:
        profil = db.query(ProfilUsaha).filter(ProfilUsaha.id == 1).first()
        if not profil:
            profil = ProfilUsaha(
                id=1,
                nama_usaha="Perusahaan Anda",
                npwp="-",
                alamat="-",
                kota="-",
                logo_path="",
            )
            db.add(profil)
            db.commit()
            db.refresh(profil)

        rekening = db.query(RekeningUsaha).filter(RekeningUsaha.profil_id == 1).all()
        return {
            "id": profil.id,
            "nama_usaha": profil.nama_usaha,
            "npwp": profil.npwp,
            "alamat": profil.alamat,
            "kota": profil.kota,
            "logo_path": profil.logo_path or "",
            "rekening_list": [
                {
                    "id": r.id,
                    "nama_bank": r.nama_bank,
                    "nomor_rekening": r.nomor_rekening,
                    "atas_nama": r.atas_nama,
                    "catatan": r.catatan,
                }
                for r in rekening
            ],
        }
    finally:
        db.close()


@app.post("/api/profil_usaha")
async def simpan_profil_usaha(
    payload: ProfilUsahaPayload,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        profil = db.query(ProfilUsaha).filter(ProfilUsaha.id == 1).first()
        if not profil:
            profil = ProfilUsaha(id=1)
            db.add(profil)

        profil.nama_usaha = payload.nama_usaha
        profil.npwp = payload.npwp
        profil.alamat = payload.alamat
        profil.kota = payload.kota

        db.query(RekeningUsaha).filter(RekeningUsaha.profil_id == 1).delete()
        for rek in payload.rekening_list:
            if rek.nama_bank.strip() and rek.nomor_rekening.strip():
                db.add(
                    RekeningUsaha(
                        profil_id=1,
                        nama_bank=rek.nama_bank.strip().upper(),
                        nomor_rekening=rek.nomor_rekening.strip(),
                        atas_nama=rek.atas_nama.strip(),
                        catatan=rek.catatan or "",
                    )
                )

        catat_audit(
            db,
            request,
            current_user,
            modul="PROFIL",
            aksi="UPDATE",
            keterangan="Update profil & rekening perusahaan",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": "Pengaturan perusahaan berhasil diperbarui!",
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/api/profil_usaha/upload_logo")
async def upload_logo_usaha(
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        upload_dir = os.path.join("web", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        ext = file.filename.split(".")[-1]
        file_name = f"logo_perusahaan.{ext}"
        file_path = os.path.join(upload_dir, file_name)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        web_url = f"/web/uploads/{file_name}"
        profil = db.query(ProfilUsaha).filter(ProfilUsaha.id == 1).first()
        if not profil:
            profil = ProfilUsaha(id=1)
            db.add(profil)

        profil.logo_path = web_url
        db.commit()
        return {
            "status": "success",
            "pesan": "Logo berhasil diunggah!",
            "logo_url": web_url,
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ==========================================
# 12. SINKRONISASI CABANG (AUTO SYNC HO)
# ==========================================
@app.post("/api/sync/receive-summary-batch")
async def receive_summary_from_branch(
    data: PayloadSyncHO,
    request: Request,
    current_user: User = Depends(require_roles(["super_user", "approver", "maker"])),
):
    db = get_session()
    try:
        tgl_hari_ini = datetime.now().strftime("%Y-%m-%d")
        timestamp_str = datetime.now().strftime("%Y%m%d-%H%M%S")
        ref_sync = f"SYNC-{data.kode_cabang}-{timestamp_str}"

        if data.total_nilai_masuk > 0:
            jurnal_in = JurnalHeader(
                no_referensi=f"{ref_sync}-IN",
                tanggal=tgl_hari_ini,
                kode_voucher="JV",
                kode_cabang=data.kode_cabang,
                kode_departemen=data.kode_departemen,
                keterangan=f"Rekap Masuk {data.total_dokumen} Dokumen Gudang Cabang {data.kode_cabang}",
                total_debit=data.total_nilai_masuk,
                total_kredit=data.total_nilai_masuk,
                pembuat="AUTO_SYNC",
                status="Posted",
            )
            jurnal_in.baris_detail.append(
                JurnalDetail(
                    kode_akun="1130-00",
                    keterangan=f"Persediaan Masuk Cabang {data.kode_cabang}",
                    debit=data.total_nilai_masuk,
                    kredit=0.0,
                )
            )
            jurnal_in.baris_detail.append(
                JurnalDetail(
                    kode_akun="2110-00",
                    keterangan=f"Hutang Pembelian Cabang {data.kode_cabang}",
                    debit=0.0,
                    kredit=data.total_nilai_masuk,
                )
            )
            db.add(jurnal_in)

        if data.total_nilai_keluar > 0:
            jurnal_out = JurnalHeader(
                no_referensi=f"{ref_sync}-OUT",
                tanggal=tgl_hari_ini,
                kode_voucher="JV",
                kode_cabang=data.kode_cabang,
                kode_departemen=data.kode_departemen,
                keterangan=f"Rekap Keluar Gudang Cabang {data.kode_cabang}",
                total_debit=data.total_nilai_keluar,
                total_kredit=data.total_nilai_keluar,
                pembuat="AUTO_SYNC",
                status="Posted",
            )
            jurnal_out.baris_detail.append(
                JurnalDetail(
                    kode_akun="5110-00",
                    keterangan=f"HPP/Pemakaian Cabang {data.kode_cabang}",
                    debit=data.total_nilai_keluar,
                    kredit=0.0,
                )
            )
            jurnal_out.baris_detail.append(
                JurnalDetail(
                    kode_akun="1130-00",
                    keterangan=f"Pengurangan Persediaan Cabang {data.kode_cabang}",
                    debit=0.0,
                    kredit=data.total_nilai_keluar,
                )
            )
            db.add(jurnal_out)

        catat_audit(
            db,
            request,
            current_user,
            modul="SYNC",
            aksi="RECEIVE",
            referensi=ref_sync,
            keterangan=f"Terima batch cabang {data.kode_cabang}",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": f"Batch Cabang {data.kode_cabang} ({data.total_dokumen} dokumen) berhasil dibukukan di HO.",
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ==========================================
# 13. AUDIT LOG SERVER & ALAT MIGRASI
# ==========================================
@app.get("/api/logs")
async def get_activity_logs(
    limit: int = 100,
    modul: Optional[str] = "SEMUA",
    aksi: Optional[str] = "SEMUA",
    _t: Optional[str] = None,
    current_user: User = Depends(require_roles(["super_user"])),
):
    db = get_session()
    try:
        query = db.query(ActivityLog).order_by(ActivityLog.id.desc())
        if modul and modul != "SEMUA":
            query = query.filter(ActivityLog.modul == modul)
        if aksi and aksi != "SEMUA":
            query = query.filter(ActivityLog.aksi == aksi)

        logs = query.limit(limit).all()
        return [
            {
                "id": l.id,
                "waktu": l.waktu.strftime("%Y-%m-%d %H:%M:%S") if l.waktu else "-",
                "username": l.username,
                "role": l.role,
                "modul": l.modul,
                "aksi": l.aksi,
                "referensi": l.referensi,
                "keterangan": l.keterangan,
            }
            for l in logs
        ]
    finally:
        db.close()


@app.post("/api/migrasi/reset")
async def reset_semua_jurnal_api(
    request: Request, current_user: User = Depends(require_roles(["super_user"]))
):
    db = get_session()
    try:
        db.execute(text("DELETE FROM jurnal_detail"))
        db.execute(text("DELETE FROM jurnal_header"))
        catat_audit(
            db,
            request,
            current_user,
            modul="MIGRASI",
            aksi="RESET",
            keterangan="Mereset seluruh data jurnal transaksi",
        )
        db.commit()
        return {
            "status": "success",
            "pesan": "Seluruh data jurnal berhasil dikosongkan!",
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ==============================================================
# API IMPORT MIGRASI JURNAL - ANTI-CRASH & COMPATIBLE POSTGRESQL
# ==============================================================
@app.post("/api/migrasi/import")
async def migrasi_jurnal_excel(data: List[JurnalMigrasi]):
    if not data:
        raise HTTPException(status_code=400, detail="Data migrasi kosong!")

    db = get_session()
    try:
        total_dokumen = 0
        total_baris = 0

        for j in data:
            if not j.detail:
                continue

            # Hitung total debit & kredit dengan aman
            tot_debit = sum(float(d.debit or 0.0) for d in j.detail)
            tot_kredit = sum(float(d.kredit or 0.0) for d in j.detail)

            # 1. Simpan Header Jurnal
            header = JurnalHeader(
                no_referensi=str(j.no_referensi).strip(),
                tanggal=str(j.tanggal).strip(),
                kode_voucher=str(j.kode_voucher or "JV").strip(),
                kode_cabang="00",
                kode_departemen="00",
                keterangan=str(j.keterangan or "-"),
                total_debit=tot_debit,
                total_kredit=tot_kredit,
                pembuat="MIGRASI_SYS",
                status="Posted",
            )
            db.add(header)
            db.flush()  # Ambil header.id dari PostgreSQL sebelum simpan detail

            # 2. Simpan Baris Detail secara eksplisit
            for d in j.detail:
                detail = JurnalDetail(
                    header_id=header.id,
                    kode_akun=str(d.kode_akun).strip()[:50],
                    nama_akun=str(d.nama_akun or "-"),
                    keterangan=str(d.keterangan or "-"),
                    debit=float(d.debit or 0.0),
                    kredit=float(d.kredit or 0.0),
                )
                db.add(detail)
                total_baris += 1

            total_dokumen += 1

        db.commit()
        return {
            "status": "success",
            "pesan": f"Migrasi berhasil! {total_dokumen} dokumen jurnal ({total_baris} baris) tersimpan ke database lokal.",
        }

    except Exception as e:
        db.rollback()
        print(f"🔥 ERROR DETAIL MIGRASI: {type(e).__name__} - {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        db.close()


# Skema Validasi Pydantic Master Customer
class CustomerSchema(BaseModel):
    kode_customer: str
    nama_customer: str
    npwp: Optional[str] = "-"
    alamat: Optional[str] = "-"
    kota: Optional[str] = "-"
    telepon: Optional[str] = "-"
    email: Optional[str] = "-"
    kontak_person: Optional[str] = "-"
    termin_hari: Optional[int] = 30
    kode_akun_piutang: str
    nama_akun_piutang: str
    kode_akun_pendapatan: str
    nama_akun_pendapatan: str
    status: Optional[str] = "Aktif"
    mode: Optional[str] = "add"


@app.get("/master_customer")
async def halaman_master_customer():
    return FileResponse("web/master_customer.html")


#  API GET Semua Customer
@app.get("/api/customer")
async def get_all_customer():
    db = get_session()
    try:
        customers = (
            db.query(MasterCustomer).order_by(MasterCustomer.kode_customer.asc()).all()
        )
        return [
            {
                "kode_customer": c.kode_customer,
                "nama_customer": c.nama_customer,
                "npwp": c.npwp,
                "alamat": c.alamat,
                "kota": c.kota,
                "telepon": c.telepon,
                "email": c.email,
                "kontak_person": c.kontak_person,
                "termin_hari": c.termin_hari,
                "kode_akun_piutang": c.kode_akun_piutang,
                "nama_akun_piutang": c.nama_akun_piutang,
                "kode_akun_pendapatan": c.kode_akun_pendapatan,
                "nama_akun_pendapatan": c.nama_akun_pendapatan,
                "status": c.status,
            }
            for c in customers
        ]
    finally:
        db.close()


#  API GET Semua Customer Invoice
@app.get("/api/customer_invoice")
async def get_all_customer_invoices(
    q: Optional[str] = "",
    status_bayar: Optional[str] = "SEMUA",
    kode_customer: Optional[str] = "SEMUA",
    kode_cabang: Optional[str] = "SEMUA",
):
    db = get_session()
    try:
        query = db.query(CustomerInvoiceHeader)

        # 1. Filter Teks Pencarian (No. Invoice, Pelanggan, No. PO)
        if q:
            keyword = f"%{q.strip()}%"
            query = query.filter(
                (CustomerInvoiceHeader.no_invoice.ilike(keyword))
                | (CustomerInvoiceHeader.nama_customer.ilike(keyword))
                | (CustomerInvoiceHeader.no_po_customer.ilike(keyword))
            )

        # 2. Filter Customer
        if kode_customer and kode_customer != "SEMUA":
            query = query.filter(CustomerInvoiceHeader.kode_customer == kode_customer)

        # 3. Filter Cabang
        if kode_cabang and kode_cabang != "SEMUA":
            query = query.filter(CustomerInvoiceHeader.kode_cabang == kode_cabang)

        # 4. Filter Status Pembayaran
        if status_bayar == "OPEN":
            query = query.filter(
                CustomerInvoiceHeader.saldo_terutang > 0,
                CustomerInvoiceHeader.status != "Pending",
            )
        elif status_bayar == "PAID":
            query = query.filter(CustomerInvoiceHeader.saldo_terutang <= 0)
        elif status_bayar == "PENDING":
            query = query.filter(CustomerInvoiceHeader.status == "Pending")

        invoices = query.order_by(CustomerInvoiceHeader.id.desc()).all()
        return [
            {
                "id": inv.id,
                "kode_voucher": inv.kode_voucher,
                "no_invoice": inv.no_invoice,
                "no_po_customer": getattr(inv, "no_po_customer", "-") or "-",
                "tanggal": inv.tanggal,
                "jatuh_tempo": inv.jatuh_tempo,
                "kode_customer": inv.kode_customer,
                "nama_customer": inv.nama_customer,
                "kode_cabang": getattr(inv, "kode_cabang", "HO") or "HO",
                "total_invoice": float(inv.total_invoice or 0.0),
                "saldo_terutang": float(inv.saldo_terutang or 0.0),
                "status": inv.status,
            }
            for inv in invoices
        ]
    finally:
        db.close()


# API POST Simpan / Update Customer
@app.post("/api/customer")
async def simpan_customer(data: CustomerSchema):
    db = get_session()
    try:
        kode_bersih = data.kode_customer.strip().upper()
        cust = (
            db.query(MasterCustomer)
            .filter(MasterCustomer.kode_customer == kode_bersih)
            .first()
        )

        # VALIDASI PENCEGAHAN DUPLIKASI (MODE ADD)
        if data.mode == "add":
            if cust:
                raise HTTPException(
                    status_code=400,
                    detail=f"Kode Customer '{kode_bersih}' sudah digunakan oleh '{cust.nama_customer}'. Gunakan kode customer yang lain!",
                )

            cust_baru = MasterCustomer(
                kode_customer=kode_bersih,
                nama_customer=data.nama_customer.strip(),
                npwp=data.npwp,
                alamat=data.alamat,
                kota=data.kota,
                telepon=data.telepon,
                email=data.email,
                kontak_person=data.kontak_person,
                termin_hari=data.termin_hari,
                kode_akun_piutang=data.kode_akun_piutang,
                nama_akun_piutang=data.nama_akun_piutang,
                kode_akun_pendapatan=data.kode_akun_pendapatan,
                nama_akun_pendapatan=data.nama_akun_pendapatan,
                status=data.status,
            )
            db.add(cust_baru)
            pesan = f"Customer baru {kode_bersih} berhasil didaftarkan!"

        # MODE EDIT (UPDATE)
        elif data.mode == "edit":
            if not cust:
                raise HTTPException(
                    status_code=404,
                    detail=f"Customer dengan kode '{kode_bersih}' tidak ditemukan untuk diperbarui.",
                )
            cust.nama_customer = data.nama_customer.strip()
            cust.npwp = data.npwp
            cust.alamat = data.alamat
            cust.kota = data.kota
            cust.telepon = data.telepon
            cust.email = data.email
            cust.kontak_person = data.kontak_person
            cust.termin_hari = data.termin_hari
            cust.kode_akun_piutang = data.kode_akun_piutang
            cust.nama_akun_piutang = data.nama_akun_piutang
            cust.kode_akun_pendapatan = data.kode_akun_pendapatan
            cust.nama_akun_pendapatan = data.nama_akun_pendapatan
            cust.status = data.status
            pesan = f"Data customer {kode_bersih} berhasil diperbarui!"
        else:
            raise HTTPException(status_code=400, detail="Mode operasi tidak valid.")

        db.commit()
        return {"status": "success", "detail": pesan}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# 4. API DELETE Customer
@app.delete("/api/customer/{kode}")
async def hapus_customer(kode: str):
    db = get_session()
    try:
        cust = (
            db.query(MasterCustomer)
            .filter(MasterCustomer.kode_customer == kode)
            .first()
        )
        if not cust:
            raise HTTPException(status_code=404, detail="Customer tidak ditemukan.")
        db.delete(cust)
        db.commit()
        return {"status": "success", "detail": f"Customer {kode} telah dihapus."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


class ItemInvoiceDetail(BaseModel):
    kode_akun: str
    nama_akun: str
    deskripsi: Optional[str] = ""
    nominal: float


class PayloadCustomerInvoice(BaseModel):
    kode_voucher: Optional[str] = "AR"
    no_invoice: str
    no_faktur_pajak: Optional[str] = "-"
    no_po_customer: Optional[str] = "-"
    tanggal: str
    jatuh_tempo: str
    kode_customer: str
    kode_cabang: Optional[str] = "HO"
    kode_departemen: Optional[str] = "MKT"
    keterangan: Optional[str] = "-"
    kode_akun_piutang: Optional[str] = "1130-00"
    kode_akun_debit: Optional[str] = None
    kode_akun_ppn: Optional[str] = ""
    ppn_nominal: Optional[float] = 0.0
    jenis_pph: Optional[str] = ""
    kode_akun_pph: Optional[str] = ""
    pph_nominal: Optional[float] = 0.0
    pembuat: Optional[str] = "Staff_AR"
    detail: List[ItemInvoiceDetail]


# 1. Tampilan Halaman Faktur Penjualan
@app.get("/customer_invoice")
async def halaman_customer_invoice():
    return FileResponse("web/customer_invoice.html")


# 2. API Next Number Faktur Penjualan (Sequential)
@app.get("/api/customer_invoice/next-no")
async def get_next_invoice_no(voucher: str = "AR", tanggal: str = ""):
    db = get_session()
    try:
        if not tanggal:
            tanggal = datetime.now().strftime("%Y-%m-%d")
        ym = tanggal.replace("-", "")[:6]
        prefix_pattern = f"{voucher} {ym}-%"

        last_inv = (
            db.query(CustomerInvoiceHeader)
            .filter(CustomerInvoiceHeader.no_invoice.like(prefix_pattern))
            .order_by(CustomerInvoiceHeader.no_invoice.desc())
            .first()
        )

        if last_inv:
            last_no_str = last_inv.no_invoice.split("-")[-1]
            nomor_urut = f"{int(last_no_str) + 1:03d}"
        else:
            nomor_urut = "001"

        return {"next_no": f"{voucher} {ym}-{nomor_urut}"}
    finally:
        db.close()


# 3. GET DAFTAR FAKTUR PENJUALAN
# ==========================================
@app.get("/api/customer_invoice")
async def get_all_customer_invoices():
    db = get_session()
    try:
        invoices = (
            db.query(CustomerInvoiceHeader)
            .order_by(CustomerInvoiceHeader.id.desc())
            .all()
        )
        return [
            {
                "id": inv.id,
                "kode_voucher": inv.kode_voucher,
                "no_invoice": inv.no_invoice,
                "no_po_customer": inv.no_po_customer,
                "tanggal": inv.tanggal,
                "jatuh_tempo": inv.jatuh_tempo,
                "kode_customer": inv.kode_customer,
                "nama_customer": inv.nama_customer,
                "kode_cabang": getattr(inv, "kode_cabang", "HO") or "HO",
                "total_invoice": inv.total_invoice,
                "saldo_terutang": inv.saldo_terutang,
                "status": inv.status,
            }
            for inv in invoices
        ]
    finally:
        db.close()


# 3. API Simpan Faktur Penjualan (Auto-Jurnal Pending)
@app.post("/api/customer_invoice")
async def simpan_customer_invoice(
    p: PayloadCustomerInvoice,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    db = get_session()
    try:
        # 1. Validasi Duplikasi No. Invoice
        cek_inv = (
            db.query(CustomerInvoiceHeader)
            .filter(CustomerInvoiceHeader.no_invoice == p.no_invoice)
            .first()
        )
        if cek_inv:
            raise HTTPException(
                status_code=400,
                detail=f"No. Invoice '{p.no_invoice}' sudah pernah digunakan!",
            )

        # 2. Ambil Data Customer (Aman jika tidak ditemukan)
        cust = (
            db.query(MasterCustomer)
            .filter(MasterCustomer.kode_customer == p.kode_customer)
            .first()
        )
        nama_customer = cust.nama_customer if cust else p.kode_customer

        # 3. Deteksi Akun Debit (Mendukung p.kode_akun_piutang maupun p.kode_akun_debit)
        akun_debit_kode = (
            getattr(p, "kode_akun_piutang", None)
            or getattr(p, "kode_akun_debit", None)
            or "1130-00"
        )
        obj_debit = db.query(COA).filter(COA.kode == akun_debit_kode).first()
        nama_debit = obj_debit.nama if obj_debit else "Piutang Usaha"

        # 4. Hitung Subtotal dan Pajak secara Aman
        subtotal = sum(float(d.nominal or 0.0) for d in p.detail)
        ppn_nom = float(getattr(p, "ppn_nominal", 0.0) or 0.0)
        pph_nom = float(getattr(p, "pph_nominal", 0.0) or 0.0)
        total_tagihan = (subtotal + ppn_nom) - pph_nom

        if total_tagihan <= 0:
            raise HTTPException(
                status_code=400,
                detail="Total tagihan faktur harus lebih besar dari Rp 0!",
            )

        # 5. Simpan Header Invoice
        inv_header = CustomerInvoiceHeader(
            kode_voucher=getattr(p, "kode_voucher", "AR") or "AR",
            no_invoice=p.no_invoice,
            tanggal=p.tanggal,
            jatuh_tempo=p.jatuh_tempo,
            kode_customer=p.kode_customer,
            nama_customer=nama_customer,
            kode_cabang=getattr(p, "kode_cabang", "HO") or "HO",
            kode_departemen=getattr(p, "kode_departemen", "MKT") or "MKT",
            keterangan=getattr(p, "keterangan", "-") or "-",
            subtotal=subtotal,
            total_invoice=total_tagihan,
            saldo_terutang=total_tagihan,
            status="Pending",
            pembuat=current_user.username,
        )

        # Kolom Dinamis Tambahan
        inv_header.kode_akun_piutang = akun_debit_kode
        inv_header.nama_akun_piutang = nama_debit

        if hasattr(inv_header, "kode_akun_debit"):
            inv_header.kode_akun_debit = akun_debit_kode
        if hasattr(inv_header, "nama_akun_debit"):
            inv_header.nama_akun_debit = nama_debit

        # Atribut Pelengkap Lainnya
        if hasattr(inv_header, "no_po_customer"):
            inv_header.no_po_customer = (
                getattr(p, "no_po_customer", "-")
                or getattr(p, "no_faktur_pajak", "-")
                or "-"
            )
        if hasattr(inv_header, "no_faktur_pajak"):
            inv_header.no_faktur_pajak = getattr(p, "no_faktur_pajak", "-") or "-"
        if hasattr(inv_header, "kode_akun_ppn"):
            inv_header.kode_akun_ppn = getattr(p, "kode_akun_ppn", "") or ""
            inv_header.ppn_nominal = ppn_nom
        if hasattr(inv_header, "jenis_pph"):
            inv_header.jenis_pph = getattr(p, "jenis_pph", "") or ""
            inv_header.kode_akun_pph = getattr(p, "kode_akun_pph", "") or ""
            inv_header.pph_nominal = pph_nom

        db.add(inv_header)
        db.flush()

        # 6. Simpan Detail Faktur
        for d in p.detail:
            # Cari nama akun jika belum terisi dari frontend
            nama_akun_det = d.nama_akun
            if not nama_akun_det or nama_akun_det == "-":
                obj_coa = db.query(COA).filter(COA.kode == d.kode_akun).first()
                nama_akun_det = obj_coa.nama if obj_coa else "Pendapatan Usaha"

            det = CustomerInvoiceDetail(
                header_id=inv_header.id,
                kode_akun=d.kode_akun,
                nama_akun=nama_akun_det,
                deskripsi=d.deskripsi or "-",
                nominal=float(d.nominal or 0.0),
            )
            db.add(det)

        # 7. Auto-Jurnal Masuk ke Posting Pending
        # Total Debit & Kredit seimbang = Subtotal + PPN
        total_balance = subtotal + ppn_nom

        jurnal = JurnalHeader(
            kode_voucher=p.kode_voucher or "AR",
            tanggal=p.tanggal,
            no_referensi=p.no_invoice,
            kode_cabang=p.kode_cabang or "HO",
            kode_departemen=p.kode_departemen or "MKT",
            keterangan=f"Faktur Penjualan {p.no_invoice} - {nama_customer} ({p.keterangan or '-'})",
            total_debit=total_balance,
            total_kredit=total_balance,
            pembuat=current_user.username,
            status="Pending",
        )
        db.add(jurnal)
        db.flush()

        # [DEBIT] Piutang Usaha / Kas/Bank
        db.add(
            JurnalDetail(
                header_id=jurnal.id,
                kode_akun=akun_debit_kode,
                nama_akun=nama_debit,
                keterangan=f"Tagihan {p.no_invoice} an {nama_customer}",
                debit=total_tagihan,
                kredit=0.0,
            )
        )

        # [DEBIT] Potongan PPh (Bila ada)
        kode_pph = getattr(p, "kode_akun_pph", "")
        if pph_nom > 0 and kode_pph:
            obj_pph = db.query(COA).filter(COA.kode == kode_pph).first()
            nama_pph = (
                obj_pph.nama
                if obj_pph
                else f"Uang Muka {getattr(p, 'jenis_pph', 'PPh')}"
            )
            db.add(
                JurnalDetail(
                    header_id=jurnal.id,
                    kode_akun=kode_pph,
                    nama_akun=nama_pph,
                    keterangan=f"Potongan {getattr(p, 'jenis_pph', 'PPh')} oleh {nama_customer}",
                    debit=pph_nom,
                    kredit=0.0,
                )
            )

        # [KREDIT] Pendapatan Penjualan Detail
        for d in p.detail:
            obj_coa = db.query(COA).filter(COA.kode == d.kode_akun).first()
            nama_akun_det = (
                obj_coa.nama if obj_coa else (d.nama_akun or "Pendapatan Usaha")
            )
            db.add(
                JurnalDetail(
                    header_id=jurnal.id,
                    kode_akun=d.kode_akun,
                    nama_akun=nama_akun_det,
                    keterangan=d.deskripsi or f"Penjualan {p.no_invoice}",
                    debit=0.0,
                    kredit=float(d.nominal or 0.0),
                )
            )

        # [KREDIT] PPN Keluaran
        if ppn_nom > 0 and p.kode_akun_ppn:
            obj_ppn = db.query(COA).filter(COA.kode == p.kode_akun_ppn).first()
            nama_ppn = obj_ppn.nama if obj_ppn else "PPN Keluaran"
            db.add(
                JurnalDetail(
                    header_id=jurnal.id,
                    kode_akun=p.kode_akun_ppn,
                    nama_akun=nama_ppn,
                    keterangan=f"PPN Keluaran {p.no_invoice}",
                    debit=0.0,
                    kredit=ppn_nom,
                )
            )

        # 8. Log Aktivitas
        catat_audit(
            db=db,
            request=request,
            user=current_user,
            modul="AR_INVOICE",
            aksi="CREATE",
            referensi=p.no_invoice,
            keterangan=f"Menerbitkan faktur {p.no_invoice} senilai Rp {total_tagihan:,.0f} ke antrean Posting Pending",
        )

        db.commit()
        return {
            "status": "success",
            "pesan": f"Faktur Penjualan {p.no_invoice} berhasil disimpan dan dikirim ke antrean Posting Pending!",
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Gagal simpan invoice: {str(e)}")
    finally:
        db.close()


# ==============================================================
# RUTE CETAK FAKTUR PENJUALAN (CUSTOMER INVOICE)
# ==============================================================
@app.get("/cetak_invoice", response_class=HTMLResponse)
async def cetak_customer_invoice(no_invoice: str):
    db = get_session()
    try:
        # 1. Cari Header Faktur
        inv = (
            db.query(CustomerInvoiceHeader)
            .filter(CustomerInvoiceHeader.no_invoice == no_invoice)
            .first()
        )
        if not inv:
            raise HTTPException(
                status_code=404,
                detail=f"Faktur Penjualan '{no_invoice}' tidak ditemukan.",
            )

        # 2. Cari Detail Faktur
        details = (
            db.query(CustomerInvoiceDetail)
            .filter(CustomerInvoiceDetail.header_id == inv.id)
            .all()
        )

        # 3. Cari Data Customer untuk Alamat & Kontak
        cust = (
            db.query(MasterCustomer)
            .filter(MasterCustomer.kode_customer == inv.kode_customer)
            .first()
        )
        alamat_cust = cust.alamat if cust else "-"
        kota_cust = cust.kota if cust else "-"
        npwp_cust = cust.npwp if cust else "-"
        telepon_cust = cust.telepon if cust else "-"

        # 4. Susun Baris Rincian Item Penjualan
        baris_html = ""
        for idx, d in enumerate(details, start=1):
            baris_html += f"""
            <tr>
                <td style="border: 1px solid #cbd5e1; padding: 7px; text-align: center;">{idx}</td>
                <td style="border: 1px solid #cbd5e1; padding: 7px; font-family: monospace; font-size: 10px;">{d.kode_akun}</td>
                <td style="border: 1px solid #cbd5e1; padding: 7px;">
                    <strong>{d.deskripsi}</strong>
                </td>
                <td style="border: 1px solid #cbd5e1; padding: 7px; text-align: right; font-family: monospace;">Rp {d.nominal:,.0f}</td>
            </tr>
            """

        # Rincian Pajak
        baris_pajak = ""
        ppn_nom = float(getattr(inv, "ppn_nominal", 0.0) or 0.0)
        pph_nom = float(getattr(inv, "pph_nominal", 0.0) or 0.0)
        jenis_pph = getattr(inv, "jenis_pph", "") or "PPh"

        if ppn_nom > 0:
            baris_pajak += f"""
            <tr>
                <td colspan="3" style="border: 1px solid #cbd5e1; padding: 6px; text-align: right; font-weight: 600;">(+) PPN Keluaran:</td>
                <td style="border: 1px solid #cbd5e1; padding: 6px; text-align: right; font-family: monospace;">Rp {ppn_nom:,.0f}</td>
            </tr>
            """
        if pph_nom > 0:
            baris_pajak += f"""
            <tr>
                <td colspan="3" style="border: 1px solid #cbd5e1; padding: 6px; text-align: right; font-weight: 600; color: #b91c1c;">(-) Potongan {jenis_pph}:</td>
                <td style="border: 1px solid #cbd5e1; padding: 6px; text-align: right; font-family: monospace; color: #b91c1c;">Rp {pph_nom:,.0f}</td>
            </tr>
            """

        no_po_ref = (
            getattr(inv, "no_po_customer", "-")
            or getattr(inv, "no_faktur_pajak", "-")
            or "-"
        )

        html_content = f"""
        <!DOCTYPE html>
        <html lang="id">
        <head>
            <meta charset="UTF-8">
            <title>Faktur Penjualan - {inv.no_invoice}</title>
            <style>
                @page {{
                    size: A4 portrait;
                    margin: 12mm 15mm;
                }}
                body {{
                    font-family: Arial, sans-serif;
                    font-size: 11px;
                    color: #1e293b;
                    margin: 0;
                    padding: 15px;
                    box-sizing: border-box;
                    background: #fff;
                }}
                table {{ width: 100%; border-collapse: collapse; }}
                .kop-table {{ border-bottom: 2px solid #0f172a; padding-bottom: 8px; margin-bottom: 12px; }}
                .title-inv {{ font-size: 18px; font-weight: 900; text-align: right; color: #0f172a; text-transform: uppercase; letter-spacing: 1px; }}
                .badge-status {{ font-size: 9px; padding: 2px 8px; font-weight: bold; border-radius: 3px; display: inline-block; }}
                .status-pending {{ background: #fef3c7; color: #b45309; }}
                .status-posted {{ background: #e0e7ff; color: #3730a3; }}
                .status-lunas {{ background: #d1fae5; color: #065f46; }}
                .box-info {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 4px; padding: 8px; margin-bottom: 12px; }}
                .no-print {{ margin-bottom: 15px; }}
                @media print {{
                    body {{ padding: 0; }}
                    .no-print {{ display: none !important; }}
                    @page {{ margin: 10mm 12mm; }}
                }}
            </style>
        </head>
        <body>
            <div class="no-print" style="text-align: right;">
                <button onclick="window.print()" style="padding: 7px 18px; background: #2563eb; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 12px;">🖨️ Cetak Faktur / Simpan PDF</button>
            </div>

            <!-- KOP PERUSAHAAN (DINAMIS DARI PROFIL USAHA) -->
            <table class="kop-table" style="border: none;">
                <tr>
                    <td style="width: 60%; vertical-align: top;">
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <img id="kop_logo" src="/web/Logo_SAP.png" style="height: 48px; width: auto; object-contain: contain; margin-bottom: 4px;" onerror="this.style.display='none'">
                            <div>
                                <h2 id="kop_nama" style="margin: 0; font-size: 12px; font-weight: 800; text-transform: uppercase;">PT. SAP BoB Nusantara</h2>
                                <p id="kop_alamat" style="margin: 2px 0; color: #475569; font-size: 10px;">Jl. Raya Bisnis Terpadu No. 88</p>
                                <p id="kop_npwp" style="margin: 2px 0; color: #475569; font-size: 10px; font-family: monospace;">NPWP: 01.234.567.8-001.000</p>
                            </div>
                        </div>
                    </td>
                    <td style="width: 40%; vertical-align: top; text-align: right;">
                        <div class="title-inv">FAKTUR PENJUALAN</div>
                        <div style="font-size: 12px; font-weight: bold; font-family: monospace; color: #2563eb; margin-top: 3px;">No. Invoice: {inv.no_invoice}</div>
                    </td>
                </tr>
            </table>

            <!-- INFO PELANGGAN & DETAIL FAKTUR -->
            <table style="margin-bottom: 12px; font-size: 10.5px;">
                <tr>
                    <td style="width: 55%; vertical-align: top; padding-right: 15px;">
                        <div class="box-info">
                            <span style="font-size: 9px; font-weight: bold; color: #64748b; text-transform: uppercase; display: block; margin-bottom: 3px;">Kepada:</span>
                            <strong style="font-size: 12px; color: #0f172a;">{inv.nama_customer}</strong> ({inv.kode_customer})<br>
                            Alamat: {alamat_cust}, {kota_cust}<br>
                            NPWP: <span style="font-family: monospace;">{npwp_cust}</span> | Telp: {telepon_cust}
                        </div>
                    </td>
                    <td style="width: 45%; vertical-align: top;">
                        <div class="box-info">
                            <table style="font-size: 10px;">
                                <tr>
                                    <td style="width: 45%; padding: 2px 0; color: #64748b;">Tanggal Faktur</td>
                                    <td style="padding: 2px 0;">: <strong>{inv.tanggal}</strong></td>
                                </tr>
                                <tr>
                                    <td style="padding: 2px 0; color: #b91c1c; font-weight: 600;">Jatuh Tempo</td>
                                    <td style="padding: 2px 0; color: #b91c1c; font-weight: bold;">: {inv.jatuh_tempo}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 2px 0; color: #64748b;">No. PO / Ref Cust</td>
                                    <td style="padding: 2px 0; font-family: monospace;">: {no_po_ref}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 2px 0; color: #64748b;">Cabang</td>
                                    <td style="padding: 2px 0;">: {getattr(inv, "kode_cabang", "HO") or "HO"}</td>
                                </tr>
                            </table>
                        </div>
                    </td>
                </tr>
            </table>

            <!-- TABEL ITEM BARANG / JASA -->
            <table style="margin-bottom: 12px; font-size: 10.5px;">
                <thead>
                    <tr style="background: #f1f5f9; color: #334155; font-size: 10px; text-transform: uppercase;">
                        <th style="border: 1px solid #cbd5e1; padding: 7px; width: 5%;">No</th>
                        <th style="border: 1px solid #cbd5e1; padding: 7px; width: 15%;">Kode Akun</th>
                        <th style="border: 1px solid #cbd5e1; padding: 7px; width: 55%;">Deskripsi Barang / Jasa</th>
                        <th style="border: 1px solid #cbd5e1; padding: 7px; width: 25%; text-align: right;">Jumlah (Rp)</th>
                    </tr>
                </thead>
                <tbody>
                    {baris_html}
                </tbody>
                <tfoot>
                    <tr>
                        <td colspan="3" style="border: 1px solid #cbd5e1; padding: 6px; text-align: right; font-weight: 600;">Subtotal Penjualan:</td>
                        <td style="border: 1px solid #cbd5e1; padding: 6px; text-align: right; font-family: monospace; font-weight: 600;">Rp {inv.subtotal:,.0f}</td>
                    </tr>
                    {baris_pajak}
                    <tr style="background: #f8fafc; font-size: 11.5px;">
                        <td colspan="3" style="border: 1px solid #cbd5e1; padding: 8px; text-align: right; font-weight: bold; color: #1e3a8a;">TOTAL TAGIHAN:</td>
                        <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: right; font-family: monospace; font-weight: bold; color: #1e3a8a; font-size: 13px;">Rp {inv.total_invoice:,.0f}</td>
                    </tr>
                    <tr>
                        <td colspan="3" style="border: 1px solid #cbd5e1; padding: 5px; text-align: right; color: #64748b;">Sisa Saldo Terutang:</td>
                        <td style="border: 1px solid #cbd5e1; padding: 5px; text-align: right; font-family: monospace; font-weight: bold; color: #b91c1c;">Rp {inv.saldo_terutang:,.0f}</td>
                    </tr>
                </tfoot>
            </table>

            <!-- INSTRUKSI REKENING TRANSFER & CATATAN -->
            <div style="background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 4px; padding: 8px 12px; margin-bottom: 15px; font-size: 10px;">
                <strong style="color: #0f172a; display: block; margin-bottom: 3px;">💳 Instruksi Pembayaran Transfer Bank:</strong>
                <div id="inv_daftar_bank" style="margin-left: 2px;">
                    <span>Memuat rekening perusahaan...</span>
                </div>
                <div style="margin-top: 4px; color: #64748b; font-style: italic;">
                    * Mohon mencantumkan nomor invoice <strong>{inv.no_invoice}</strong> pada berita transfer.
                </div>
            </div>

            <!-- TANDA TANGAN -->
            <table style="margin-top: 20px; text-align: center; font-size: 10px; page-break-inside: avoid;">
                <tr>
                    <td style="width: 33%;">
                        Dibuat Oleh,<br><br><br><br>
                        <strong>( {inv.pembuat or "Staff AR"} )</strong><br>
                        <span style="color: #64748b;">Billing / Keuangan</span>
                    </td>
                    <td style="width: 33%;">
                        Mengetahui,<br><br><br><br>
                        <strong>( ................................... )</strong><br>
                        <span style="color: #64748b;">Finance / Accounting SPV</span>
                    </td>
                    <td style="width: 34%;">
                        Diterima Oleh (Pelanggan),<br><br><br><br>
                        <strong>( ................................... )</strong><br>
                        <span style="color: #64748b;">Nama & Stempel Perusahaan</span>
                    </td>
                </tr>
            </table>

            <!-- SCRIPT LOAD DATA KOP & REKENING DINAMIS -->
            <script>
                fetch('/api/profil_usaha')
                    .then(res => res.json())
                    .then(d => {{
                        if (d.nama_usaha) document.getElementById('kop_nama').innerText = d.nama_usaha;
                        if (d.alamat) document.getElementById('kop_alamat').innerText = d.alamat + (d.kota ? ', ' + d.kota : '');
                        if (d.npwp) document.getElementById('kop_npwp').innerText = "NPWP: " + d.npwp;
                        if (d.logo_path) {{
                            const logoEl = document.getElementById('kop_logo');
                            logoEl.src = d.logo_path.replace(/^\\/web\\//, '/');
                            logoEl.style.display = 'block';
                        }}

                        const bankBox = document.getElementById('inv_daftar_bank');
                        if (d.rekening_list && d.rekening_list.length > 0) {{
                            bankBox.innerHTML = d.rekening_list.map(r => `
                                <div style="margin: 2px 0;">
                                    <strong>${{r.nama_bank}}</strong> : <span style="font-family: monospace; font-weight: bold; font-size: 11px;">${{r.nomor_rekening}}</span> a/n <strong>${{r.atas_nama}}</strong>
                                </div>
                            `).join('');
                        }} else {{
                            bankBox.innerHTML = "<span>Hubungi bagian keuangan untuk instruksi rekening bank.</span>";
                        }}
                    }})
                    .catch(e => console.warn(e));
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)
    finally:
        db.close()


@app.get("/daftar_ar")
async def halaman_daftar_ar():
    return FileResponse("web/daftar_ar.html")


# ==========================================================


# Schema request restore
class PayloadRestore(BaseModel):
    file_name: str


# 1. Endpoint untuk Membuat Backup Seketika
@app.post("/api/system/backup")
async def api_trigger_backup(
    request: Request, current_user: User = Depends(require_roles(["super_user"]))
):
    try:
        hasil = buat_backup_terenkripsi()
        db = get_session()
        try:
            catat_audit(
                db=db,
                request=request,
                user=current_user,
                modul="SYSTEM",
                aksi="BACKUP",
                referensi=hasil["file_name"],
                keterangan=f"Backup DB terenkripsi dibuat manual ({hasil['ukuran']})",
            )
        finally:
            db.close()

        return {
            "status": "success",
            "data": hasil,
            "pesan": "Database berhasil dibackup dengan enkripsi AES-256!",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal melakukan backup: {str(e)}")


# 2. Endpoint Melihat Riwayat Backup (Khusus Super User)
@app.get("/api/system/backups")
async def api_get_backups(current_user: User = Depends(require_roles(["super_user"]))):
    try:
        daftar = list_file_backup()
        return {"status": "success", "backups": daftar}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# 3. Endpoint Restore Database (Khusus Super User)
@app.post("/api/system/restore")
async def api_trigger_restore(
    payload: PayloadRestore,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    try:
        hasil = pulihkan_backup_terenkripsi(payload.file_name)
        db = get_session()
        try:
            catat_audit(
                db=db,
                request=request,
                user=current_user,
                modul="SYSTEM",
                aksi="RESTORE",
                referensi=payload.file_name,
                keterangan="Memulihkan database dari snapshot cadangan terenkripsi",
            )
        finally:
            db.close()

        return hasil
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.on_event("startup")
def startup_event():
    # Menjalankan mesin backup otomatis di latar belakang
    mulai_scheduler_backup()


@app.get("/backup_restore")
async def halaman_backup_restore():
    return FileResponse("web/backup_restore.html")


# Skema request payload untuk pindah file
class PayloadMoveBackup(BaseModel):
    file_name: str
    target_directory: str


# 1. API PINDAHKAN FILE CADANGAN KE DISK LAIN
@app.post("/api/system/backup/move")
async def api_move_backup(
    payload: PayloadMoveBackup,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    try:
        # Bersihkan path jika user menginput tanda petik atau spasi di ujung
        clean_target = payload.target_directory.strip().strip('"').strip("'")

        hasil = pindahkan_file_backup(payload.file_name, clean_target)

        db = get_session()
        try:
            catat_audit(
                db=db,
                request=request,
                user=current_user,
                modul="SYSTEM",
                aksi="MOVE_BACKUP",
                referensi=payload.file_name,
                keterangan=f"Memindahkan cadangan ke disk/folder: {hasil['target_path']}",
            )
        finally:
            db.close()

        return hasil
    except Exception as e:
        print(f"Error Move Backup: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# 2. API HAPUS MANUAL FILE CADANGAN
@app.delete("/api/system/backup/{file_name}")
async def api_delete_backup(
    file_name: str,
    request: Request,
    current_user: User = Depends(require_roles(["super_user"])),
):
    try:
        hasil = hapus_file_backup(file_name)
        db = get_session()
        try:
            catat_audit(
                db=db,
                request=request,
                user=current_user,
                modul="SYSTEM",
                aksi="DELETE_BACKUP",
                referensi=file_name,
                keterangan="Menghapus manual file backup dari server",
            )
        finally:
            db.close()
        return hasil
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==============================================================
# API CEK ISI JURNAL RIIL (DIAGNOSTIK ARUS KAS)
# ==============================================================
@app.get("/api/debug/cek_jurnal_riil")
async def debug_cek_jurnal_riil():
    db = get_session()
    try:
        # Ambil 20 baris jurnal terakhir yang Posted
        rows = (
            db.query(
                JurnalHeader.no_referensi,
                JurnalHeader.tanggal,
                JurnalHeader.kode_cabang,
                JurnalDetail.kode_akun,
                JurnalDetail.nama_akun,
                JurnalDetail.debit,
                JurnalDetail.kredit,
            )
            .join(JurnalHeader, JurnalDetail.header_id == JurnalHeader.id)
            .filter(JurnalHeader.status == "Posted")
            .limit(30)
            .all()
        )

        daftar = [
            {
                "no_ref": r[0],
                "tanggal": r[1],
                "cabang": r[2],
                "kode_akun": str(r[3]),
                "nama_akun": r[4],
                "debit": float(r[5] or 0),
                "kredit": float(r[6] or 0),
            }
            for r in rows
        ]

        return {"total_baris_ditemukan": len(daftar), "sampel_transaksi": daftar}
    finally:
        db.close()


class DetailMigrasi(BaseModel):
    kode_akun: str
    nama_akun: str
    keterangan: str
    debit: float
    kredit: float


class JurnalMigrasi(BaseModel):
    no_referensi: str
    tanggal: str
    kode_voucher: str
    keterangan: str
    detail: List[DetailMigrasi]


def ambil_ip_client(request: Request) -> str:
    """Mendeteksi IP asli pengguna baik melalui LAN lokal maupun Cloudflare Tunnel."""
    if not request:
        return "127.0.0.1"

    # 1. Cek header Cloudflare Tunnel jika diakses online
    ip_cf = request.headers.get("cf-connecting-ip")
    if ip_cf:
        return ip_cf.strip()

    # 2. Cek header reverse-proxy / load-balancer standar
    ip_forwarded = request.headers.get("x-forwarded-for")
    if ip_forwarded:
        return ip_forwarded.split(",")[0].strip()

    # 3. Fallback ke IP LAN lokal (misal: 192.168.1.50)
    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"

@app.middleware("http")
async def monitor_akses_server(request: Request, call_next):
    waktu_mulai = time.time()
    
    # Ambil IP asli pengakses (baik dari proxy Railway/Cloudflare maupun lokal)
    ip_client = (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for").split(",")[0].strip() if request.headers.get("x-forwarded-for") else None)
        or (request.client.host if request.client else "Unknown")
    )
    
    response = await call_next(request)
    durasi = (time.time() - waktu_mulai) * 1000
    
    # Menampilkan ke Deploy Logs Railway atau Terminal Server
    print(f"📡 [AKSES MASUK] IP: {ip_client} | Endpoint: {request.method} {request.url.path} | Status: {response.status_code} ({durasi:.1f} ms)")
    return response

# ==========================================
# 14. BROWSER OTOMATIS & SERVER LAUNCHER
# ==========================================
def buka_browser_otomatis():
    time.sleep(3)
    webbrowser.open("http://127.0.0.1:8000")


app.mount("/uploads", StaticFiles(directory="web/uploads"), name="uploads")
app.mount("/web", StaticFiles(directory="web"), name="web")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
