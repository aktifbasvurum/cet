# -*- coding: utf-8 -*-
"""
fetch_update_proxies.py

Ücretsiz proxy kaynaklarından HTTP proxy listesini çeker, formatı temizler
(`ip:port`) ve `Webshare 1000 proxies.txt` dosyasına yazar.
"""

import datetime
import pathlib
import ssl
import sys
import urllib.request

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

PROXY_URLS = [
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/https/data.txt",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
    "https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/main/proxy_files/http_proxies.txt",
    "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/http.txt",
    "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/https.txt",
    "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/all.txt",
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/https.txt",
    "https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/http.txt",
    "https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/https.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt",
]

LOCAL_PROXY_FILE = pathlib.Path(__file__).with_name("ham_proxyler.txt")
LOG_FILE = pathlib.Path(__file__).with_name("fetch_and_test.log")


def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(msg, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{ts} | {msg}\n")


def normalize_proxy(raw: str) -> str | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    low = line.lower()
    if low.startswith(("socks4://", "socks5://", "socks://")):
        return None
    for prefix in ("http://", "https://"):
        if low.startswith(prefix):
            line = line[len(prefix):]
            break
    line = line.rstrip("/")
    parts = line.split(":")
    if len(parts) < 2 or not parts[0] or not parts[1].isdigit():
        return None
    return line


def fetch_proxies() -> list[str]:
    proxies: set[str] = set()
    for url in PROXY_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
                if resp.status != 200:
                    log(f"[UYARI] {url} -> HTTP {resp.status}")
                    continue
                text = resp.read().decode("utf-8", errors="ignore")
                before = len(proxies)
                for line in text.splitlines():
                    cleaned = normalize_proxy(line)
                    if cleaned:
                        proxies.add(cleaned)
                log(f"[OK] {len(proxies) - before} yeni proxy cekildi (Toplam: {len(proxies)})")
        except Exception as e:
            log(f"[HATA] {url} -> {type(e).__name__}: {e}")
    return sorted(proxies)


def main() -> None:
    log("=== Proxy listesi guncelleniyor ===")
    all_proxies = fetch_proxies()
    if not all_proxies:
        log("[HATA] Hic proxy cekilemedi, mevcut dosya korunuyor.")
        sys.exit(1)

    LOCAL_PROXY_FILE.write_text("\n".join(all_proxies) + "\n", encoding="utf-8")
    log(f"[OK] {len(all_proxies)} adet proxy '{LOCAL_PROXY_FILE.name}' dosyasina yazildi.")


if __name__ == "__main__":
    main()
