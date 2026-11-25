#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

echo "============================================================"
echo "         HALK BOT - VAST.AI UBUNTU KURULUM & CALISTIRMA     "
echo "============================================================"

export DEBIAN_FRONTEND=noninteractive

echo "[1/4] Gerekli sistem paketleri hazirlaniyor..."
apt-get update -qq >/dev/null 2>&1 || true
apt-get install -y -qq python3 python3-pip libgl1 libglib2.0-0 libgomp1 curl wget procps screen tmux >/dev/null 2>&1 || true

echo "[2/4] Python paketleri ve bagimliliklar yukleniyor..."
python3 -m pip install --upgrade pip -q --break-system-packages 2>/dev/null || python3 -m pip install --upgrade pip -q
python3 -m pip install -r requirements.txt -q --break-system-packages 2>/dev/null || python3 -m pip install -r requirements.txt -q

echo "[3/4] Playwright Chromium tarayicisi kuruluyor..."
python3 -m playwright install --with-deps chromium

BROWSER_COUNT=${1:-5}

echo "[4/4] Bot baslatiliyor ($BROWSER_COUNT adet tarayici ile)..."
# Varsa eski bot calismalarini sonlandir
pkill -f "python3.*halk.py" || true
sleep 1

# Botu arka planda nohup ile baslat
nohup python3 halk.py "$BROWSER_COUNT" > bot.log 2>&1 &
BOT_PID=$!

echo "============================================================"
echo "[OK] Bot basariyla calistirildi! (PID: $BOT_PID)"
echo "Loglar ekrana aktariliyor..."
echo "(Bu pencereyi kapatsaniz bile bot sunucuda arka planda calismaya devam eder!)"
echo "============================================================"
sleep 2
tail -f bot.log
