#!/usr/bin/env bash
# Menjalankan bot di background sebagai service systemd (Linux / VPS).
#   ./bot.sh install    pasang service: bot jalan sekarang dan otomatis start setiap VPS boot
#   ./bot.sh uninstall  hentikan dan hapus service
#   ./bot.sh start | stop | restart | status
#   ./bot.sh log        lihat log langsung (Ctrl+C untuk keluar)
set -euo pipefail

NAME=cvbot
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT="/etc/systemd/system/$NAME.service"
LOG_FILE="$ROOT/data/bot.log"
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO=sudo

python_bin() {
    if [ -x "$ROOT/.venv/bin/python" ]; then
        echo "$ROOT/.venv/bin/python"
    elif command -v python3 >/dev/null; then
        command -v python3
    else
        echo "python3 tidak ditemukan." >&2
        exit 1
    fi
}

require_installed() {
    [ -f "$UNIT" ] || { echo "Service belum terpasang. Jalankan dulu: ./bot.sh install" >&2; exit 1; }
}

case "${1:-status}" in
    install)
        [ -f "$ROOT/.env" ] || { echo "File .env belum ada. Salin dari .env.example lalu isi dulu." >&2; exit 1; }
        PY="$(python_bin)"
        $SUDO tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=CV Bot Telegram
After=network-online.target
Wants=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=$ROOT
ExecStart=$PY bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
        $SUDO systemctl daemon-reload
        $SUDO systemctl enable "$NAME" >/dev/null
        $SUDO systemctl restart "$NAME"
        echo "Terpasang. Bot jalan di background dan otomatis start setiap VPS boot ($PY)."
        echo "Cek log: ./bot.sh log"
        ;;
    uninstall)
        if [ -f "$UNIT" ]; then
            $SUDO systemctl disable --now "$NAME" >/dev/null 2>&1 || true
            $SUDO rm -f "$UNIT"
            $SUDO systemctl daemon-reload
        fi
        echo "Bot dihentikan dan service dihapus."
        ;;
    start | stop | restart)
        require_installed
        $SUDO systemctl "$1" "$NAME"
        echo "OK: $1"
        ;;
    status)
        require_installed
        systemctl status "$NAME" --no-pager -n 0 || true
        if [ -f "$LOG_FILE" ]; then
            echo
            echo "Log terakhir:"
            tail -n 10 "$LOG_FILE"
        fi
        ;;
    log)
        require_installed
        # journalctl juga menangkap error sebelum logging bot siap (misalnya modul belum ter-install)
        $SUDO journalctl -u "$NAME" -n 50 -f
        ;;
    *)
        echo "Pemakaian: ./bot.sh {install|uninstall|start|stop|restart|status|log}" >&2
        exit 1
        ;;
esac
