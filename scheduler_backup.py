import os
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from backup_engine import buat_backup_terenkripsi
from database import get_session, ActivityLog

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AUTO_BACKUP")

scheduler = BackgroundScheduler(timezone="Asia/Jakarta")

JOB_ID_REGULER = "backup_reguler_rabu_jumat"
JOB_ID_RETRY = "backup_retry_per_2_jam"

def catat_log_sistem(db, aksi: str, referensi: str, keterangan: str):
    """Mencatat aktivitas scheduler otomatis langsung ke tabel ActivityLog."""
    try:
        log = ActivityLog(
            waktu=datetime.now(),
            username="SYSTEM_SCHEDULER",
            nama_user="Otomatisasi Sistem",
            role="SYSTEM",
            modul="SYSTEM_BACKUP",
            aksi=aksi,
            referensi=referensi,
            keterangan=keterangan,
            ip_address="127.0.0.1"
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"Gagal mencatat audit log sistem: {e}")

def eksekusi_backup_otomatis(is_retry=False):
    tipe_eksekusi = "RETRY (Per 2 Jam)" if is_retry else "REGULER (Rabu/Jumat)"
    logger.info(f"[{datetime.now()}] Menjalankan Backup Otomatis - {tipe_eksekusi}...")
    
    db = get_session()
    try:
        hasil = buat_backup_terenkripsi()
        catat_log_sistem(
            db=db,
            aksi="AUTO_BACKUP_SUCCESS",
            referensi=hasil.get("file_name", "-"),
            keterangan=f"Backup otomatis ({tipe_eksekusi}) BERHASIL. Ukuran: {hasil.get('ukuran', '-')}"
        )
        logger.info(f"✅ Backup Berhasil: {hasil.get('file_name')}")

        if scheduler.get_job(JOB_ID_RETRY):
            scheduler.remove_job(JOB_ID_RETRY)
            logger.info("ℹ️ Siklus Retry dihentikan karena backup telah berhasil.")

    except Exception as err:
        logger.error(f"❌ Backup Gagal: {str(err)}")
        catat_log_sistem(
            db=db,
            aksi="AUTO_BACKUP_FAILED",
            referensi="FAILED",
            keterangan=f"Backup otomatis ({tipe_eksekusi}) GAGAL: {str(err)}. Sistem menjadwalkan retry 2 jam lagi."
        )

        waktu_retry = datetime.now() + timedelta(hours=2)
        scheduler.add_job(
            func=eksekusi_backup_otomatis,
            trigger=DateTrigger(run_date=waktu_retry),
            id=JOB_ID_RETRY,
            name="Retry Backup Gagal",
            replace_existing=True,
            kwargs={"is_retry": True}
        )
        logger.warning(f"⚠️ Jadwal retry diset pada: {waktu_retry.strftime('%Y-%m-%d %H:%M:%S')}")
        
    finally:
        db.close()

def mulai_scheduler_backup():
    if not scheduler.running:
        trigger_reguler = CronTrigger(
            day_of_week="wed,fri",
            hour=23,
            minute=50
        )
        
        scheduler.add_job(
            func=eksekusi_backup_otomatis,
            trigger=trigger_reguler,
            id=JOB_ID_REGULER,
            name="Backup Rutin Rabu-Jumat 23:50",
            replace_existing=True,
            kwargs={"is_retry": False}
        )
        scheduler.start()
        logger.info("🚀 Scheduler Backup Otomatis Aktif: Rabu & Jumat pukul 23:50 WIB.")