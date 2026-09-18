// web/role_akses.js - RBAC Presisi Berbasis URL Aktif
document.addEventListener("DOMContentLoaded", () => {
    const rawRole = localStorage.getItem('role') || '';
    let role = rawRole.trim().toLowerCase().replace(/\s+/g, '_');

    // Pemetaan kompatibilitas jika masih tersimpan 'maker'
    if (role === 'maker') role = 'gl_user';

    // 1. Cek Sesi Login
    if (!role) {
        window.location.replace('/');
        return;
    }

    // 2. Pembersihan Tampilan Iframe (Workspace Mode)
    if (window.self !== window.top) {
        const style = document.createElement('style');
        style.innerHTML = `
            aside, body > nav, [id*="sidebar"] { 
                display: none !important; 
                width: 0 !important; 
                min-width: 0 !important; 
            }
            main { 
                width: 100% !important; 
                flex: 1 1 100% !important; 
            }
        `;
        document.head.appendChild(style);
    }

    // 3. Matriks Hak Akses Modul Sesuai Skema
    const moduleRules = [
        // Pembelian & Logistik
        { path: '/pr', roles: ['pr_user', 'pr_approver', 'approver', 'super_user'] },
        { path: '/purchase_request', roles: ['pr_user', 'pr_approver', 'approver', 'super_user'] },
        { path: '/po', roles: ['po_user', 'purchasing', 'approver', 'super_user'] },
        { path: '/purchase_order', roles: ['po_user', 'purchasing', 'approver', 'super_user'] },
        { path: '/gr', roles: ['gr_user', 'purchasing', 'approver', 'super_user'] },
        { path: '/goods_receipt', roles: ['gr_user', 'purchasing', 'approver', 'super_user'] },
        { path: '/master_vendor', roles: ['purchasing', 'approver', 'super_user'] },
        { path: '/vendor', roles: ['purchasing', 'approver', 'super_user'] },

        // AP & Pembayaran
        { path: '/vendor_bill', roles: ['ap_user', 'approver', 'super_user'] },
        { path: '/payment_request', roles: ['ap_user', 'approver', 'super_user'] },

        // Fixed Asset
        { path: '/fa_input', roles: ['fa_user', 'approver', 'super_user'] },
        { path: '/fixed_asset', roles: ['fa_user', 'approver', 'super_user'] },
        { path: '/daftar_fixed_asset', roles: ['fa_user', 'approver', 'super_user'] },
        { path: '/penyusutan', roles: ['fa_user', 'approver', 'super_user'] },
        { path: '/kategori_aset', roles: ['approver', 'super_user'] },

        // Input Jurnal (GL)
        { path: '/gl', roles: ['gl_user', 'maker', 'approver', 'super_user'] },
        { path: '/buku_besar', roles: ['gl_user', 'maker', 'approver', 'super_user'] },
        { path: '/posting', roles: ['approver', 'super_user'] },

        // Laporan Keuangan
        { path: '/trial_balance', roles: ['approver', 'super_user'] },
        { path: '/balance_sheet', roles: ['approver', 'super_user'] },
        { path: '/profit_loss', roles: ['approver', 'super_user'] },
        { path: '/cash_flow', roles: ['approver', 'super_user'] },
        { path: '/laporan_kelompok', roles: ['approver', 'super_user'] },

        // Master & Setup
        { path: '/setup_coa', roles: ['approver', 'super_user'] },
        { path: '/setup_user', roles: ['approver', 'super_user'] },
        { path: '/setup_org', roles: ['approver', 'super_user'] },
        { path: '/setup_voucher', roles: ['approver', 'super_user'] },
        { path: '/migrasi', roles: ['super_user'] },
        { path: '/backup_restore', roles: ['super_user'] }
    ];

    const currentPath = window.location.pathname.toLowerCase();

    // 4. Validasi Keamanan Rute (HANYA Evaluasi Rute yang Sedang Dibuka)
    if (currentPath !== '/' && currentPath !== '/workspace' && currentPath !== '/dashboard') {
        const matched = moduleRules.find(item => 
            currentPath === item.path || currentPath.startsWith(item.path + '/')
        );

        // Jika halaman terdaftar dalam proteksi dan role aktif tidak memiliki hak akses:
        if (matched && !matched.roles.includes(role)) {
            alert(`⛔ AKSES DITOLAK: Role '${rawRole}' tidak memiliki wewenang untuk membuka halaman ini.`);
            window.location.replace('/gl');
            return;
        }
    }

    // 5. Penyaringan Visibilitas Menu Sidebar
    const menuMap = {
        'menu-pr': ['pr_user', 'pr_approver', 'approver', 'super_user'],
        'menu-po': ['po_user', 'purchasing', 'approver', 'super_user'],
        'menu-gr': ['gr_user', 'purchasing', 'approver', 'super_user'],
        'menu-vendor': ['purchasing', 'approver', 'super_user'],
        'menu-ap-invoice': ['ap_user', 'approver', 'super_user'],
        'menu-ap-payment': ['ap_user', 'approver', 'super_user'],
        'menu-fa-input': ['fa_user', 'approver', 'super_user'],
        'menu-fa-list': ['fa_user', 'approver', 'super_user'],
        'menu-fa-depresiasi': ['fa_user', 'approver', 'super_user'],
        'menu-fa-kategori': ['approver', 'super_user'],
        'menu-gl': ['gl_user', 'maker', 'approver', 'super_user'],
        'menu-buku-besar': ['gl_user', 'maker', 'approver', 'super_user'],
        'menu-posting': ['approver', 'super_user'],
        'menu-trial-balance': ['approver', 'super_user'],
        'menu-laporan-keuangan': ['approver', 'super_user'],
        'menu-dropdown-laporan': ['approver', 'super_user'],
        'section-setup-system': ['approver', 'super_user'],
        'menu-setup-coa': ['approver', 'super_user'],
        'menu-setup-user': ['approver', 'super_user'],
        'menu-setup-org': ['approver', 'super_user'],
        'menu-migrasi': ['super_user'],
        'menu-backup-restore': ['super_user']
    };

    Object.entries(menuMap).forEach(([id, allowedRoles]) => {
        if (!allowedRoles.includes(role)) {
            const el = document.getElementById(id);
            if (el) el.remove();
        }
    });
});