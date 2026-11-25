import os
import sys
import time
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET_URL = 'https://sube.halkbank.com.tr/InternetBankingHost/HostLogin?CustomerType=Retail'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
}

# SSL dogrulamayi gevset (sunucu uyumlulugu)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

def find_proxy_file():
    candidates = [
        ROOT / 'Webshare 1000 proxies.txt',
        ROOT / 'Webshare 100 replaced proxies.txt',
        ROOT / 'proxies.txt',
        ROOT / 'proxy.txt'
    ]
    for c in candidates:
        if c.exists():
            return c
    return None

def parse_proxy_url(raw_line):
    line = raw_line.strip()
    for prefix in ('http://', 'https://'):
        if line.lower().startswith(prefix):
            line = line[len(prefix):]
            break
    line = line.rstrip('/')
    if '@' in line:
        return f'http://{line}'
    parts = line.split(':')
    if len(parts) == 2 and parts[0] and parts[1].isdigit():
        return f'http://{parts[0]}:{parts[1]}'
    if len(parts) >= 4 and parts[0] and parts[1].isdigit():
        ip, port, user, pwd = parts[0], parts[1], parts[2], parts[3]
        return f'http://{user}:{pwd}@{ip}:{port}'
    return None

def test_single_proxy(line, timeout=4):
    raw_line = line.strip()
    if not raw_line or raw_line.startswith('#'):
        return None, False, "Bos satir"

    proxy_url = parse_proxy_url(raw_line)
    if not proxy_url:
        return raw_line, False, "Format hatali"
    
    try:
        handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
        opener = urllib.request.build_opener(handler, urllib.request.HTTPSHandler(context=SSL_CTX))
        req = urllib.request.Request(TARGET_URL, headers=HEADERS)
        
        with opener.open(req, timeout=timeout) as resp:
            code = resp.getcode()
            if 200 <= code < 400:
                return raw_line, True, f"HTTP {code}"
            return raw_line, False, f"HTTP {code}"
    except Exception as e:
        err_str = type(e).__name__
        return raw_line, False, err_str

def main():
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--auto', action='store_true')
    parser.add_argument('--input', dest='input_file', default=None)
    args, _ = parser.parse_known_args()

    print(r"""
============================================================
           HALKBANK HIZLI PROXY TEST EDICI & AYIKLAYICI    
============================================================
""")
    if args.input_file:
        proxy_file = ROOT / args.input_file
        if not proxy_file.exists():
            proxy_file = find_proxy_file()
    else:
        proxy_file = find_proxy_file()

    target_output = ROOT / 'Webshare 1000 proxies.txt'

    if not proxy_file:
        print("[HATA] Proxy dosyasi bulunamadi! (Webshare 1000 proxies.txt veya proxies.txt)")
        if not args.auto:
            input("\nCikmak icin Enter'a basin...")
        return

    print(f"[*] Proxy Dosyasi Bulundu: {proxy_file.name}")
    all_lines = [l.strip() for l in proxy_file.read_text(encoding='utf-8', errors='ignore').splitlines() if l.strip()]
    total_proxies = len(all_lines)
    print(f"[*] Toplam {total_proxies} adet proxy test edilecek...")
    print(f"[*] Hedef: Halkbank Resmi Giris Sayfasi ({TARGET_URL})")
    print("[*] 200 es zamanli is parcacigi ile cok hizli test ediliyor...\n" + "-" * 60)

    working_proxies = []
    failed_count = 0
    checked_count = 0

    start_time = time.time()

    with ThreadPoolExecutor(max_workers=200) as executor:
        futures = {executor.submit(test_single_proxy, line): line for line in all_lines}
        
        for future in as_completed(futures):
            checked_count += 1
            line, is_working, reason = future.result()
            
            if is_working:
                working_proxies.append(line)
                ip = line.replace('http://', '').replace('https://', '').split(':')[0]
                print(f" [+] [CALISIYOR] {ip} -> {reason} ({checked_count}/{total_proxies})")
            else:
                failed_count += 1
                if checked_count % 250 == 0 or checked_count == total_proxies:
                    print(f" [-] Ilerleme: {checked_count}/{total_proxies} | Calisan: {len(working_proxies)} | Calismayan: {failed_count}")

    duration = round(time.time() - start_time, 1)
    print("\n" + "=" * 60)
    print(f" TEST TAMAMLANDI ({duration} saniye)")
    print(f" Toplam Test Edilen : {total_proxies}")
    print(f" Calisan Proxy Sayisi: {len(working_proxies)}")
    print(f" Calismayan / Engelli: {failed_count}")
    print("=" * 60)

    if working_proxies:
        # Orijinal dosyayi yedekle (yalnizca dogrudan hedef dosya test ediliyorsa)
        if not args.input_file:
            backup_file = proxy_file.with_name(f"{proxy_file.stem}_yedek{proxy_file.suffix}")
            if not backup_file.exists():
                backup_file.write_text('\n'.join(all_lines) + '\n', encoding='utf-8')
                print(f"[OK] Orijinal proxy listesi '{backup_file.name}' olarak yedeklendi.")

        # Hedef dosyayi calisanlarla guncelle
        out_file = target_output if args.input_file else proxy_file
        out_file.write_text('\n'.join(working_proxies) + '\n', encoding='utf-8')
        print(f"[BASARILI] '{out_file.name}' dosyasi guncellendi! Sadece calisan {len(working_proxies)} proxy yazildi.")
        
        # Ayrica calisan_proxyler.txt olarak da kaydet
        calisan_file = ROOT / 'calisan_proxyler.txt'
        calisan_file.write_text('\n'.join(working_proxies) + '\n', encoding='utf-8')
        print(f"[OK] Calisanlar ayrica '{calisan_file.name}' dosyasina da kaydedildi.")
    else:
        print("\n[UYARI] Maalesef test edilen proxy'lerin hicbiri Halkbank sayfasina ULASAMADI!")
        print("NEDENI:")
        print(" -> Webshare proxy'leri yabanci / datacenter IP'leridir.")
        print(" -> Halkbank guvenlik duvari yabanci datacenter IP'lerini direkt engeller/zaman asimina ugratir.")
        print(" -> Mevcut dosyanizdaki proxyler korunuyor.")
        print("\nTAVSIYE:")
        print(" -> Halkbank icin Turkiye Lokasyonlu Konut (Residential) veya Turkiye Mobil proxy kullanmaniz gerekir.")

    print("\n")
    if not args.auto:
        input("Kapatmak icin Enter'a basin...")

if __name__ == "__main__":
    main()
