import os
import sqlite3
import urllib.parse
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, Text, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from fastapi import Request, Response

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# Baca dari variabel Railway, fallback ke lokal jika dijalankan di laptop
if not raw_url or raw_url.startswith("${{"):
    DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/db_bob"
else:
    DATABASE_URL = raw_url

# Perbaiki prefix postgres:// menjadi postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, poolclass=NullPool)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_session():
    return SessionLocal()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(100), nullable=False)
    nama_karyawan = Column(String(100), nullable=False)
    role = Column(String(50), nullable=False)  # 'maker', 'approver', 'super_user'
    status = Column(String(20), default="Aktif")


class COA(Base):
    __tablename__ = "coa"
    kode = Column(String(50), primary_key=True)
    nama = Column(String(100), nullable=False)
    kelompok = Column(String(100))  # BARU: Sesuai Excel
    kategori = Column(String(100))  # BARU: Sesuai Excel
    saldo_normal = Column(String(50))
    status = Column(String(20), default="Aktif")  # BARU: Aktif/Tidak


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    waktu = Column(DateTime, default=datetime.now)
    username = Column(String(50), nullable=False)
    nama_user = Column(String(100), default="-")
    role = Column(String(30), default="-")
    modul = Column(String(50), nullable=False)
    aksi = Column(String(50), nullable=False)
    referensi = Column(String(100), default="-")
    keterangan = Column(Text, default="")
    ip_address = Column(String(50), default="127.0.0.1")


class JurnalHeader(Base):
    __tablename__ = "jurnal_header"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kode_voucher = Column(String(50))
    kode_cabang = Column(String(50), default="HO")
    kode_departemen = Column(String(50), default="FIN")
    tanggal = Column(String(50))
    no_referensi = Column(String(100), unique=True)

    # --- Tambahan untuk menerima data dari web ---
    keterangan = Column(Text, default="-")
    total_debit = Column(Float, default=0.0)
    total_kredit = Column(Float, default=0.0)
    pembuat = Column(String(50))
    # -------------------------------------------

    status = Column(String(50))  # Draft, Pending, Posted

    baris_detail = relationship(
        "JurnalDetail", back_populates="header", cascade="all, delete-orphan"
    )


class JurnalDetail(Base):
    __tablename__ = "jurnal_detail"

    id = Column(Integer, primary_key=True, autoincrement=True)
    header_id = Column(Integer, ForeignKey("jurnal_header.id"))
    kode_akun = Column(String(50))
    nama_akun = Column(Text, default="-")
    keterangan = Column(Text, default="-")
    debit = Column(Float, default=0.0)
    kredit = Column(Float, default=0.0)

    # Relasi balik ke JurnalHeader
    header = relationship("JurnalHeader", back_populates="baris_detail")


# ==========================================
# MODEL MASTER VENDOR (ACCOUNT PAYABLE)
# ==========================================
class MasterVendor(Base):
    __tablename__ = "master_vendor"

    kode_vendor = Column(String(50), primary_key=True)  # Contoh: VND-001
    nama_vendor = Column(String(150), nullable=False)
    npwp = Column(String(50), default="-")
    alamat = Column(String(255), default="-")
    telepon = Column(String(50), default="-")
    email = Column(String(100), default="-")
    kontak_person = Column(String(100), default="-")

    # Termin Pembayaran (TOP) dalam hitungan hari (misal: 0 = COD, 30 = Net 30)
    termin_hari = Column(Integer, default=30)

    # Pemetaan COA: Default Akun Hutang (Prefix 21/Kewajiban) & Default Akun Beban/Persediaan
    kode_akun_hutang = Column(String(50), default="211000")
    nama_akun_hutang = Column(String(150), default="Hutang Usaha")

    # Rekening Bank Vendor (Untuk kemudahan bagian Finance saat transfer)
    nama_bank = Column(String(50), default="-")
    no_rekening = Column(String(50), default="-")
    atas_nama = Column(String(100), default="-")

    status = Column(String(20), default="Aktif")  # Aktif / Nonaktif


# ==========================================
# MODEL VENDOR BILL / HUTANG USAHA (AP)
# ==========================================
class VendorBillHeader(Base):
    __tablename__ = "vendor_bill_header"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kode_voucher = Column(String(20), default="AP")
    no_bill = Column(String(100), unique=True, nullable=False)
    no_faktur_vendor = Column(String(100), default="-")
    tanggal = Column(String(50), nullable=False)
    jatuh_tempo = Column(String(50), nullable=False)
    kode_vendor = Column(String(50), ForeignKey("master_vendor.kode_vendor"))
    nama_vendor = Column(String(150), nullable=False)

    kode_cabang = Column(String(50), default="HO")
    kode_departemen = Column(String(50), default="FIN")
    keterangan = Column(String(255), default="")

    # Akun Hutang Usaha (Prefix 21) dari Master Vendor
    kode_akun_hutang = Column(String(50), nullable=False)
    nama_akun_hutang = Column(String(150), nullable=False)

    subtotal = Column(Float, default=0.0)

    # Pajak Masukan (PPN)
    kode_akun_ppn = Column(String(50), default="")
    ppn_nominal = Column(Float, default=0.0)

    # Potongan Pajak Penghasilan (PPh)
    jenis_pph = Column(String(50), default="")  # PPH Ps. 4 (2), 21, 22, 23, 26
    kode_akun_pph = Column(String(50), default="")  # Akun Hutang PPh (COA)
    pph_nominal = Column(Float, default=0.0)

    total_tagihan = Column(Float, default=0.0)  # Nilai bersih hutang ke vendor
    saldo_terutang = Column(Float, default=0.0)

    tgl_bayar = Column(String(50), default="-")

    status = Column(String(50), default="Posted")
    pembuat = Column(String(50), default="SYS_AP")

    baris_detail = relationship(
        "VendorBillDetail", back_populates="header", cascade="all, delete-orphan"
    )


class VendorBillDetail(Base):
    __tablename__ = "vendor_bill_detail"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bill_id = Column(Integer, ForeignKey("vendor_bill_header.id"))
    kode_akun = Column(
        String(50), nullable=False
    )  # Akun Beban (Prefix 5/6) atau Persediaan (Prefix 11)
    nama_akun = Column(String(150), nullable=False)
    deskripsi = Column(String(255), default="")
    nominal = Column(Float, default=0.0)

    header = relationship("VendorBillHeader", back_populates="baris_detail")


class PaymentRequest(Base):
    __tablename__ = "payment_request"

    id = Column(Integer, primary_key=True, autoincrement=True)
    no_rfp = Column(String(100), unique=True, nullable=False)
    tanggal = Column(String(50), nullable=False)
    kode_vendor = Column(String(50), ForeignKey("master_vendor.kode_vendor"))
    nama_vendor = Column(String(150), nullable=False)
    no_bill = Column(String(100), ForeignKey("vendor_bill_header.no_bill"))
    no_faktur_vendor = Column(String(100), default="-")
    nominal_diajukan = Column(Float, default=0.0)
    jenis_pph = Column(String(50), default="")
    kode_akun_pph = Column(String(50), default="")
    pph_nominal = Column(Float, default=0.0)
    rekening_tujuan = Column(String(200), default="-")
    keterangan = Column(String(255), default="")
    pemohon = Column(String(50), default="Staff_AP")
    status = Column(String(50), default="Submitted")


# =========================
# MODEL PEMBAYARAN VENDOR
# =========================


class VendorPayment(Base):
    __tablename__ = "vendor_payment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kode_voucher = Column(String(20), default="BKK")  # BKK / BK
    no_payment = Column(
        String(100), unique=True, nullable=False
    )  # Contoh: BKK-202609-001
    tanggal = Column(String(50), nullable=False)

    kode_vendor = Column(String(50), ForeignKey("master_vendor.kode_vendor"))
    nama_vendor = Column(String(150), nullable=False)
    no_bill = Column(String(100), ForeignKey("vendor_bill_header.no_bill"))
    no_faktur_vendor = Column(String(100), default="-")
    no_rfp = Column(String(100), default="")

    kode_cabang = Column(String(50), default="HO")
    kode_departemen = Column(String(50), default="FIN")
    keterangan = Column(String(255), default="")

    # Akun Asal Kas / Bank (Prefix 11)
    kode_akun_kas = Column(String(50), nullable=False)
    nama_akun_kas = Column(String(150), nullable=False)

    # Akun Hutang Usaha yang dilunasi (Prefix 21)
    kode_akun_hutang = Column(String(50), nullable=False)
    nama_akun_hutang = Column(String(150), nullable=False)

    # Nominal
    nominal_bayar = Column(
        Float, default=0.0
    )  # Nilai pengurang saldo hutang AP (Debit)

    # Potongan Pajak PPh saat pembayaran kas keluar
    jenis_pph = Column(String(50), default="")  # PPH Ps. 4 (2), 21, 23, dst
    kode_akun_pph = Column(String(50), default="")  # Akun Hutang PPh (Kredit)
    pph_nominal = Column(Float, default=0.0)

    jumlah_kas_keluar = Column(Float, default=0.0)  # Kas riil yang keluar (Kredit)

    status = Column(String(50), default="Pending")  # Pending, Posted, Draft
    pembuat = Column(String(50), default="FIN_AP")


# ==========================================
# MODEL INVENTORY / GUDANG (BARU)
# ==========================================


class MasterBarang(Base):
    __tablename__ = "master_barang"

    kode_barang = Column(String(50), primary_key=True)
    nama_barang = Column(String(150), nullable=False)
    kategori = Column(String(50), default="-")  # <--- Pastikan baris ini ada
    satuan = Column(String(20), default="PCS")
    stok_sekarang = Column(Float, default=0.0)
    harga_rata_rata = Column(Float, default=0.0)
    harga_beli = Column(Float, default=0.0)
    harga_jual = Column(Float, default=0.0)
    akun_persediaan = Column(String(50), default="1130-00")
    status = Column(String(20), default="Aktif")


class InventoryHeader(Base):
    __tablename__ = "inventory_header"

    id = Column(Integer, primary_key=True, autoincrement=True)
    no_bukti = Column(String(100), unique=True)  # Contoh: IN-202310-001, OUT-202310-005
    tanggal = Column(String(50))
    jenis_transaksi = Column(String(50))  # 'Masuk' atau 'Keluar'
    kode_cabang = Column(String(50), default="HO")
    keterangan = Column(String(255))
    pembuat = Column(String(50))
    status = Column(String(50), default="Posted")  # Status dokumen
    is_synced = Column(Integer, default=0)  # 0 = Belum Kirim, 1 = Sudah dikirim ke HO

    baris_detail = relationship(
        "InventoryDetail", back_populates="header", cascade="all, delete-orphan"
    )


class InventoryDetail(Base):
    __tablename__ = "inventory_detail"

    id = Column(Integer, primary_key=True, autoincrement=True)
    header_id = Column(Integer, ForeignKey("inventory_header.id"))
    kode_barang = Column(String(50))  # Kode barang yang ditransaksikan
    qty = Column(Float, default=0.0)
    harga_satuan = Column(Float, default=0.0)
    total_nilai = Column(Float, default=0.0)  # Hasil dari qty * harga_satuan

    # Relasi balik ke InventoryHeader
    header = relationship("InventoryHeader", back_populates="baris_detail")


class TransaksiInventory(Base):
    __tablename__ = "transaksi_inventory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tanggal = Column(String(50))
    kode_barang = Column(String(50), ForeignKey("master_barang.kode_barang"))
    jenis_transaksi = Column(String(20))  # MASUK, KELUAR
    qty = Column(Float, default=0.0)
    harga_satuan = Column(Float, default=0.0)
    total_nilai = Column(Float, default=0.0)
    kode_cabang = Column(String(50), default="HO")
    kode_departemen = Column(String(50), default="LOG")
    keterangan = Column(String(255))


class Cabang(Base):
    __tablename__ = "cabang"
    kode = Column(String(50), primary_key=True)
    nama = Column(String(100), nullable=False)
    keterangan = Column(String(255))
    status = Column(String(20), default="Aktif")


class Departemen(Base):
    __tablename__ = "departemen"
    kode = Column(String(50), primary_key=True)
    nama = Column(String(100), nullable=False)
    keterangan = Column(String(255))
    status = Column(String(20), default="Aktif")


class Voucher(Base):
    __tablename__ = "voucher"
    kode = Column(String(50), primary_key=True)
    nama = Column(String(100), nullable=False)
    keterangan = Column(String(255))
    status = Column(String(20), default="Aktif")


class FixedAsset(Base):
    __tablename__ = "fixed_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kode_aset = Column(String(50), unique=True, index=True)  # Contoh: FA-2026-001
    nama_aset = Column(String(150), nullable=False)
    kode_cabang = Column(String(50), default="HO")
    kode_departemen = Column(String(50), default="FIN")
    golongan_fiskal = Column(
        String(50), default="GOL1"
    )  # GOL1, GOL2, GOL3, GOL4, BG_PERMANEN, BG_NON
    tgl_perolehan = Column(String(50), nullable=False)  # YYYY-MM-DD
    harga_perolehan = Column(Float, default=0.0)
    nilai_residu = Column(Float, default=0.0)  # PSAK Nilai Sisa (Pajak biasanya 0)
    masa_manfaat_bulan = Column(Integer, default=48)  # 4 thn x 12 bln
    akumulasi_penyusutan = Column(Float, default=0.0)
    nilai_buku = Column(Float, default=0.0)
    akun_aset = Column(String(50), nullable=False)  # 1230-00 (Debit Aset)
    akun_akumulasi = Column(
        String(50), nullable=False
    )  # 1235-00 (Kredit Akum. Penyusutan)
    akun_beban = Column(String(50), nullable=False)  # 6180-00 (Debit Beban Penyusutan)

    status = Column(String(50), default="Aktif")  # Aktif, Disposed (Dijual/Dihapus)


class ProfilUsaha(Base):
    __tablename__ = "profil_usaha"

    id = Column(Integer, primary_key=True, default=1)
    nama_usaha = Column(String(150), nullable=False, default="PT Usaha Maju Bersama")
    npwp = Column(String(50), default="")
    alamat = Column(Text, default="")
    kota = Column(String(100), default="")
    logo_path = Column(String(255), default="/static/img/default_logo.png")

    # Relasi one-to-many ke rekening bank
    rekening_list = relationship(
        "RekeningUsaha", back_populates="usaha", cascade="all, delete-orphan"
    )


class RekeningUsaha(Base):
    __tablename__ = "rekening_usaha"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profil_id = Column(Integer, ForeignKey("profil_usaha.id"), default=1)
    nama_bank = Column(String(50), nullable=False)  # Contoh: BCA, Mandiri, BRI
    nomor_rekening = Column(String(50), nullable=False)
    atas_nama = Column(String(100), nullable=False)
    catatan = Column(String(100), default="")  # Misal: "Utama / IDR", "Opsional"

    usaha = relationship("ProfilUsaha", back_populates="rekening_list")


# ==========================================
# MODEL MASTER CUSTOMER (ACCOUNT RECEIVABLE)
# ==========================================
class MasterCustomer(Base):
    __tablename__ = "master_customer"

    kode_customer = Column(String(50), primary_key=True)  # Contoh: CUST-001
    nama_customer = Column(String(150), nullable=False)
    npwp = Column(String(50), default="-")
    alamat = Column(String(255), default="-")
    kota = Column(String(100), default="-")
    telepon = Column(String(50), default="-")
    email = Column(String(100), default="-")
    kontak_person = Column(String(100), default="-")
    termin_hari = Column(Integer, default=30)
    kode_akun_piutang = Column(String(50), default="1130-00")  # Default Piutang Usaha
    nama_akun_piutang = Column(String(150), default="PIUTANG USAHA")
    kode_akun_pendapatan = Column(String(50), default="4100-00")
    nama_akun_pendapatan = Column(String(150), default="PENDAPATAN USAHA")
    status = Column(String(20), default="Aktif")  # Aktif / Nonaktif


# ==========================================
# MODEL FAKTUR PENJUALAN (AR CUSTOMER INVOICE)
# ==========================================
class CustomerInvoiceHeader(Base):
    __tablename__ = "customer_invoice_header"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kode_voucher = Column(String(20), default="AR")
    no_invoice = Column(String(50), unique=True, index=True)
    no_po_customer = Column(String(100), default="-")
    no_faktur_pajak = Column(String(100), default="-")
    tanggal = Column(String(20), nullable=False)
    jatuh_tempo = Column(String(20), nullable=False)
    kode_customer = Column(String(50), nullable=False)
    nama_customer = Column(String(150), nullable=False)
    kode_cabang = Column(String(20), default="HO")
    kode_departemen = Column(String(20), default="MKT")
    keterangan = Column(String(255), default="-")

    # Kompatibilitas Dua Nama Kolom Akun Debit/Piutang
    kode_akun_piutang = Column(String(50), default="1130-00", nullable=True)
    nama_akun_piutang = Column(String(150), default="Piutang Usaha", nullable=True)
    kode_akun_debit = Column(String(50), default="1130-00", nullable=True)
    nama_akun_debit = Column(String(150), default="Piutang Usaha", nullable=True)

    # Nilai Finansial & Pajak
    kode_akun_ppn = Column(String(50), default="")
    ppn_nominal = Column(Float, default=0.0)
    jenis_pph = Column(String(50), default="")
    kode_akun_pph = Column(String(50), default="")
    pph_nominal = Column(Float, default=0.0)
    subtotal = Column(Float, default=0.0)
    total_invoice = Column(Float, default=0.0)
    saldo_terutang = Column(Float, default=0.0)
    status = Column(String(20), default="Pending")
    pembuat = Column(String(50), default="Staff_AR")


class CustomerInvoiceDetail(Base):
    __tablename__ = "customer_invoice_detail"

    id = Column(Integer, primary_key=True, autoincrement=True)
    header_id = Column(
        Integer,
        ForeignKey("customer_invoice_header.id", ondelete="CASCADE"),
        nullable=False,
    )
    kode_akun = Column(String(50), nullable=False)  # Akun Pendapatan (Prefix 41/71)
    nama_akun = Column(String(150), nullable=False)
    deskripsi = Column(String(255), default="-")
    nominal = Column(Float, default=0.0)


def catat_log(
    db,
    username: str,
    modul: str,
    aksi: str,
    referensi: str = "-",
    keterangan: str = "",
    role: str = "Staff",
    nama_user: str = "-",
    request: Request = None,
    ip: str = None,
):
    try:
        # Tentukan IP: prioritas dari request, lalu argumen manual, fallback 127.0.0.1
        ip_final = ambil_ip_client(request) if request else (ip or "127.0.0.1")

        # Username dan role harus disuplai manual satu per satu
        log = ActivityLog(
            waktu=datetime.now(),
            username=username,
            role=role,
            modul=modul,
            aksi=aksi,
            referensi=referensi,
            keterangan=keterangan,
            ip_address="127.0.0.1",  # Sering kali statis
        )
        db.add(log)
        db.commit()
    except Exception as e:
        print(f"[Log Error] Gagal mencatat activity log: {e}")


class JurnalEliminasiHeader(Base):
    __tablename__ = "jurnal_eliminasi_header"

    id = Column(Integer, primary_key=True, autoincrement=True)
    no_eliminasi = Column(String(100), unique=True)  # ELIM-2026-09-001
    periode_bulan = Column(String(20))  # 2026-09
    tanggal = Column(String(50))
    keterangan = Column(String(255))
    total_debit = Column(Float, default=0.0)
    total_kredit = Column(Float, default=0.0)
    pembuat = Column(String(50), default="Finance_HO")
    status = Column(String(50), default="Posted")


class JurnalEliminasiDetail(Base):
    __tablename__ = "jurnal_eliminasi_detail"

    id = Column(Integer, primary_key=True, autoincrement=True)
    header_id = Column(Integer, ForeignKey("jurnal_eliminasi_header.id"))
    kode_akun = Column(String(50))
    nama_akun = Column(String(150))
    entitas_terkait = Column(String(100))  # Misal: "PT A ke PT B"
    debit = Column(Float, default=0.0)
    kredit = Column(Float, default=0.0)
    keterangan = Column(String(255))


# ==========================================


def init_db():
    """Inisialisasi tabel dan auto-migrasi kolom baru yang belum ada di SQLite."""
    # 1. Buat tabel-tabel yang belum ada
    Base.metadata.create_all(bind=engine)

    db = get_session()
    if "DB_FILE" in globals():
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()

            # (Di dalam fungsi init_db() pada file database.py)
            cursor.execute("PRAGMA table_info(coa)")
            cols_coa = [r[1] for r in cursor.fetchall()]

            if cols_coa and "kelompok" not in cols_coa:
                cursor.execute("ALTER TABLE coa ADD COLUMN kelompok TEXT DEFAULT '-'")
            if cols_coa and "status" not in cols_coa:
                cursor.execute("ALTER TABLE coa ADD COLUMN status TEXT DEFAULT 'Aktif'")

            # MIGRASI UNTUK TABEL COA
            cursor.execute("PRAGMA table_info(coa)")
            cols_coa = [r[1] for r in cursor.fetchall()]
            if cols_coa and "level" not in cols_coa:
                cursor.execute("ALTER TABLE coa ADD COLUMN level INTEGER DEFAULT 4")
                print("Auto-migration: Kolom level (COA) berhasil ditambahkan.")
            if cols_coa and "tipe_akun" not in cols_coa:
                cursor.execute(
                    "ALTER TABLE coa ADD COLUMN tipe_akun TEXT DEFAULT 'Detail'"
                )
                print("Auto-migration: Kolom tipe_akun (COA) berhasil ditambahkan.")

            # Cek kolom pada tabel fixed_assets
            cursor.execute("PRAGMA table_info(fixed_assets)")
            columns = [row[1] for row in cursor.fetchall()]

            if columns and "akumulasi_penyusutan_awal" not in columns:
                cursor.execute(
                    "ALTER TABLE fixed_assets ADD COLUMN akumulasi_penyusutan_awal REAL DEFAULT 0.0"
                )
                print(
                    "Auto-migration: Kolom akumulasi_penyusutan_awal berhasil ditambahkan."
                )

            # Cek kolom pada tabel kategori_aset
            cursor.execute("PRAGMA table_info(kategori_aset)")
            cols_kat = [r[1] for r in cursor.fetchall()]
            if cols_kat and "kelompok_harta" not in cols_kat:
                cursor.execute(
                    "ALTER TABLE kategori_aset ADD COLUMN kelompok_harta TEXT DEFAULT 'Kelompok 1'"
                )
                print("Auto-migration: Kolom kelompok_harta berhasil ditambahkan.")

            # MIGRASI BARU UNTUK JURNAL HEADER
            cursor.execute("PRAGMA table_info(jurnal_header)")
            cols_jh = [r[1] for r in cursor.fetchall()]

            if cols_jh:
                if "kode_cabang" not in cols_jh:
                    cursor.execute(
                        "ALTER TABLE jurnal_header ADD COLUMN kode_cabang TEXT DEFAULT 'HO'"
                    )
                if "kode_departemen" not in cols_jh:
                    cursor.execute(
                        "ALTER TABLE jurnal_header ADD COLUMN kode_departemen TEXT DEFAULT 'FIN'"
                    )
                if "kode_voucher" not in cols_jh:
                    cursor.execute(
                        "ALTER TABLE jurnal_header ADD COLUMN kode_voucher TEXT"
                    )
                if "keterangan" not in cols_jh:
                    cursor.execute(
                        "ALTER TABLE jurnal_header ADD COLUMN keterangan TEXT"
                    )
                if "total_debit" not in cols_jh:
                    cursor.execute(
                        "ALTER TABLE jurnal_header ADD COLUMN total_debit REAL DEFAULT 0.0"
                    )
                if "total_kredit" not in cols_jh:
                    cursor.execute(
                        "ALTER TABLE jurnal_header ADD COLUMN total_kredit REAL DEFAULT 0.0"
                    )
                if "pembuat" not in cols_jh:
                    cursor.execute("ALTER TABLE jurnal_header ADD COLUMN pembuat TEXT")

                print(
                    "Auto-migration: Kolom baru pada jurnal_header berhasil disinkronkan!"
                )

            cursor.execute("PRAGMA table_info(master_barang)")
            kolom_ada = [row[1] for row in cursor.fetchall()]

            if kolom_ada:
                if "kategori" not in kolom_ada:
                    cursor.execute(
                        "ALTER TABLE master_barang ADD COLUMN kategori TEXT DEFAULT '-'"
                    )
                if "stok_sekarang" not in kolom_ada:
                    cursor.execute(
                        "ALTER TABLE master_barang ADD COLUMN stok_sekarang REAL DEFAULT 0.0"
                    )
                if "harga_rata_rata" not in kolom_ada:
                    cursor.execute(
                        "ALTER TABLE master_barang ADD COLUMN harga_rata_rata REAL DEFAULT 0.0"
                    )
                if "harga_beli" not in kolom_ada:
                    cursor.execute(
                        "ALTER TABLE master_barang ADD COLUMN harga_beli REAL DEFAULT 0.0"
                    )
                if "harga_jual" not in kolom_ada:
                    cursor.execute(
                        "ALTER TABLE master_barang ADD COLUMN harga_jual REAL DEFAULT 0.0"
                    )
                if "satuan" not in kolom_ada:
                    cursor.execute(
                        "ALTER TABLE master_barang ADD COLUMN satuan TEXT DEFAULT 'PCS'"
                    )
                if "akun_persediaan" not in kolom_ada:
                    cursor.execute(
                        "ALTER TABLE master_barang ADD COLUMN akun_persediaan TEXT DEFAULT '1130-00'"
                    )
                if "status" not in kolom_ada:
                    cursor.execute(
                        "ALTER TABLE master_barang ADD COLUMN status TEXT DEFAULT 'Aktif'"
                    )

            # Migrasi inventory_header
            cursor.execute("PRAGMA table_info(inventory_header)")
            cols_ih = [r[1] for r in cursor.fetchall()]
            if cols_ih:
                if "kode_cabang" not in cols_ih:
                    cursor.execute(
                        "ALTER TABLE inventory_header ADD COLUMN kode_cabang TEXT DEFAULT 'HO'"
                    )
                if "kode_departemen" not in cols_ih:
                    cursor.execute(
                        "ALTER TABLE inventory_header ADD COLUMN kode_departemen TEXT DEFAULT 'LOG'"
                    )

            if db.query(MasterBarang).count() == 0:
                db.add_all(
                    [
                        MasterBarang(
                            kode_barang="BRG-001",
                            nama_barang="Kertas HVS A4 75gr",
                            kategori="ATK",
                            satuan="RIM",
                            stok_sekarang=50.0,
                            harga_rata_rata=45000.0,
                            harga_beli=45000.0,
                            harga_jual=55000.0,
                            akun_persediaan="1130-00",
                            status="Aktif",
                        ),
                        MasterBarang(
                            kode_barang="BRG-002",
                            nama_barang="Tinta Printer Hitam",
                            kategori="Elektronik",
                            satuan="BOTOL",
                            stok_sekarang=20.0,
                            harga_rata_rata=85000.0,
                            harga_beli=85000.0,
                            harga_jual=110000.0,
                            akun_persediaan="1130-00",
                            status="Aktif",
                        ),
                    ]
                )

                # Migrasi transaksi_inventory
                cursor.execute("PRAGMA table_info(transaksi_inventory)")
                cols_ti = [r[1] for r in cursor.fetchall()]
                if cols_ti:
                    if "kode_cabang" not in cols_ti:
                        cursor.execute(
                            "ALTER TABLE transaksi_inventory ADD COLUMN kode_cabang TEXT DEFAULT 'HO'"
                        )
                    if "kode_departemen" not in cols_ti:
                        cursor.execute(
                            "ALTER TABLE transaksi_inventory ADD COLUMN kode_departemen TEXT DEFAULT 'LOG'"
                        )

            # Auto-Migrasi Tabel Detail Faktur AR
            cursor.execute("PRAGMA table_info(customer_invoice_detail)")
            cols_inv_det = [r[1] for r in cursor.fetchall()]
            if cols_inv_det and "header_id" not in cols_inv_det:
                cursor.execute(
                    "ALTER TABLE customer_invoice_detail ADD COLUMN header_id INTEGER REFERENCES customer_invoice_header(id)"
                )
                print(
                    "Auto-migration: Kolom header_id berhasil ditambahkan ke customer_invoice_detail!"
                )

            db.commit()
            print("Auto-seed: Data Master Barang awal berhasil dibuat.")

            # Auto-Migrasi Kolom Baru Customer Invoice Header
            cursor.execute("PRAGMA table_info(customer_invoice_header)")
            cols_ar = [r[1] for r in cursor.fetchall()]

            if cols_ar:
                kolom_cek = {
                    "no_po_customer": "VARCHAR(100) DEFAULT '-'",
                    "kode_akun_ppn": "VARCHAR(50) DEFAULT ''",
                    "ppn_nominal": "FLOAT DEFAULT 0.0",
                    "jenis_pph": "VARCHAR(50) DEFAULT ''",
                    "kode_akun_pph": "VARCHAR(50) DEFAULT ''",
                    "pph_nominal": "FLOAT DEFAULT 0.0",
                }
                for col, definisi in kolom_cek.items():
                    if col not in cols_ar:
                        cursor.execute(
                            f"ALTER TABLE customer_invoice_header ADD COLUMN {col} {definisi}"
                        )

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Peringatan auto-migration : {e}")

    # ==========================================
    # 3. AUTO-SEED USER DEFAULT (BARU)
    # ==========================================
    db = get_session()
    try:
        if db.query(User).count() == 0:
            default_users = [
                User(
                    username="admin",
                    password="123",
                    nama_karyawan="Administrator",
                    role="super_user",
                ),
                User(
                    username="gl_user1",
                    password="123",
                    nama_karyawan="GL User 1",
                    role="gl_user",
                ),
                User(
                    username="spv",
                    password="123",
                    nama_karyawan="Supervisor",
                    role="approver",
                ),
                User(
                    username="gl_user2",
                    password="123",
                    nama_karyawan="GL User 2",
                    role="gl_user",
                ),
            ]
            db.add_all(default_users)
            db.commit()
            print("Auto-seed: 5 User default berhasil dibuat di Supabase.")
    except Exception as e:
        db.rollback()
        print(f"Peringatan init Supabase: {e}")
    finally:
        db.close()

    # AUTO-SEED KODE VOUCHER DEFAULT
    try:
        if db.query(Voucher).count() == 0:
            default_vouchers = [
                Voucher(
                    kode="JV",
                    nama="Jurnal Umum",
                    keterangan="Jurnal Transaksi Umum",
                    status="Aktif",
                ),
                Voucher(
                    kode="BKK",
                    nama="Bukti Kas Keluar",
                    keterangan="Pengeluaran Kas/Bank",
                    status="Aktif",
                ),
                Voucher(
                    kode="BKM",
                    nama="Bukti Kas Masuk",
                    keterangan="Penerimaan Kas/Bank",
                    status="Aktif",
                ),
                Voucher(
                    kode="ADJ",
                    nama="Jurnal Penyesuaian",
                    keterangan="Jurnal Adjustment Akhir Periode",
                    status="Aktif",
                ),
            ]
            db.add_all(default_vouchers)
            db.commit()
            print("Auto-seed: Kode Voucher default berhasil dibuat.")
    except Exception as e:
        db.rollback()
        print(f"Error init vouchers: {e}")


# Jalankan migrasi otomatis saat modul database dimuat
init_db()
