import sys
import os
import re
import subprocess
import tarfile
import shutil
import tempfile
import io
import time
from pathlib import Path

BOT_DIR = os.path.dirname(os.path.abspath(__file__))

def print_banner():
    print(r"""
============================================================
      VAST.AI OTOMATIK UBUNTU BOT YUKLEYICI & BASLATICI     
============================================================
""")

def parse_ssh_command(cmd_text):
    cmd_text = cmd_text.strip().replace('"', '').replace("'", "")
    
    port = "22"
    user = "root"
    host = ""
    identity_file = None

    # -i key parametresi
    i_match = re.search(r'-i\s+([^\s]+)', cmd_text)
    if i_match:
        identity_file = i_match.group(1)

    # -p port parametresi
    p_match = re.search(r'-p\s+(\d+)', cmd_text)
    if p_match:
        port = p_match.group(1)

    # user@host
    u_h_match = re.search(r'([a-zA-Z0-9_\.-]+)@([a-zA-Z0-9_\.-]+)', cmd_text)
    if u_h_match:
        user = u_h_match.group(1)
        host = u_h_match.group(2)
    else:
        for token in cmd_text.split():
            if ":" in token and not token.startswith("-"):
                parts = token.split(":")
                host = parts[0]
                port = parts[1]
                break

    # Eger identity file belirtilmemisse kullanicinin .ssh klasorundeki anahtarlari otomatik bul
    if not identity_file:
        ssh_dir = Path.home() / ".ssh"
        for key_candidate in ["id_ed25519", "id_rsa"]:
            key_path = ssh_dir / key_candidate
            if key_path.exists():
                identity_file = str(key_path)
                break

    return user, host, port, identity_file

def get_base_ssh_args(user, host, port, identity_file):
    args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-p", str(port)
    ]
    if identity_file:
        args.extend(["-i", identity_file])
    args.append(f"{user}@{host}")
    return args

def wait_for_ssh_ready(user, host, port, identity_file, max_attempts=24, delay=5):
    """
    Vast.ai sunucusu yeni kiralandiginda Docker imajini indirirken SSH kapali olur.
    Sunucu hazir olana kadar baglantiyi otomatik olarak bekler.
    """
    print(f"[*] Sunucu baglantisi kontrol ediliyor ({host}:{port})...")
    test_args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=5",
        "-p", str(port)
    ]
    if identity_file:
        test_args.extend(["-i", identity_file])
    test_args.extend([f"{user}@{host}", "echo ready"])

    for attempt in range(1, max_attempts + 1):
        try:
            res = subprocess.run(
                test_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10
            )
            if res.returncode == 0 and b"ready" in res.stdout:
                print("[OK] Sunucu aktif ve SSH baglantisina hazir!\n")
                return True
        except Exception:
            pass

        print(f"[*] Sunucu henuz basliyor / Docker imaji yukleniyor... Hazir olmasi bekleniyor ({attempt}/{max_attempts}) - {delay} sn sonra tekrar...")
        time.sleep(delay)

    return False

def upload_folder(user, host, port, identity_file, local_folder, remote_dest_folder):
    print("[1/3] Bot dosyalari paketleniyor...")
    
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w:gz") as tar:
        for root, dirs, files in os.walk(local_folder):
            if "__pycache__" in root or ".git" in root:
                continue
            for file in files:
                if file.endswith(".pyc") or file == "bot_files.tar.gz":
                    continue
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, local_folder).replace("\\", "/")
                
                # .sh dosyalarinin Linux uyumlu LF satir sonu olmasini garanti et
                if file.endswith(".sh"):
                    with open(full_path, "rb") as sf:
                        content = sf.read().replace(b"\r\n", b"\n")
                    ti = tarfile.TarInfo(name=rel_path)
                    ti.size = len(content)
                    ti.mode = 0o755
                    tar.addfile(ti, io.BytesIO(content))
                else:
                    tar.add(full_path, arcname=rel_path)

    tar_bytes = tar_stream.getvalue()
    print(f"[2/3] Dosyalar sunucuya aktariliyor ({host}:{port} -> {remote_dest_folder})...")

    # SSH Pipe ile direkt bellekten sunucuda ac (en hizli yontem)
    ssh_args = get_base_ssh_args(user, host, port, identity_file)
    remote_unpack_cmd = f"mkdir -p {remote_dest_folder} && tar -xzf - -C {remote_dest_folder} && chmod +x {remote_dest_folder}/*.sh"
    
    try:
        proc = subprocess.Popen(
            ssh_args + [remote_unpack_cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = proc.communicate(input=tar_bytes, timeout=60)
        if proc.returncode == 0:
            print("[+] Dosyalar basariyla aktarildi ve sunucuda acildi!")
            return True
        else:
            err_msg = stderr.decode(errors="ignore")
            print(f"[UYARI] Hizli aktarim: {err_msg.strip()}. SCP deneniyor...")
    except Exception as e:
        print(f"[UYARI] Hizli aktarim: {e}. SCP deneniyor...")

    # Yedek SCP Yontemi
    temp_dir = tempfile.mkdtemp()
    tar_path = os.path.join(temp_dir, "bot_files.tar.gz")
    try:
        with open(tar_path, "wb") as f:
            f.write(tar_bytes)

        subprocess.run(ssh_args + [f"mkdir -p {remote_dest_folder}"])

        scp_args = [
            "scp",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-P", str(port)
        ]
        if identity_file:
            scp_args.extend(["-i", identity_file])
        scp_args.extend([tar_path, f"{user}@{host}:{remote_dest_folder}/bot_files.tar.gz"])

        scp_res = subprocess.run(scp_args)
        if scp_res.returncode == 0:
            extract_cmd = f"tar -xzf {remote_dest_folder}/bot_files.tar.gz -C {remote_dest_folder} && rm -f {remote_dest_folder}/bot_files.tar.gz && chmod +x {remote_dest_folder}/*.sh"
            subprocess.run(ssh_args + [extract_cmd])
            print("[+] Dosyalar SCP ile basariyla sunucuya yuklendi!")
            return True
        else:
            print("[HATA] SCP ile aktarim basarisiz oldu.")
            return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    print_banner()
    print("Vast.ai panelinden kopyaladiginiz SSH komutunu buraya yapistirip Enter'a basin:")
    print("Ornek: ssh -p 12345 root@ssh4.vast.ai -L 8080:localhost:8080")
    print("-" * 65)
    
    raw_input = input("SSH Komutu: ").strip()
    if not raw_input:
        print("\n[HATA] SSH komutu girmediniz.")
        input("Cikmak icin Enter'a basin...")
        return

    user, host, port, identity_file = parse_ssh_command(raw_input)
    if not host:
        print(f"\n[HATA] Girdiginiz metinden sunucu adresi cikarilamadi: '{raw_input}'")
        input("Cikmak icin Enter'a basin...")
        return

    print("\n[OK] Ayristirilan Baglanti:")
    print(f" -> Sunucu IP/Host : {host}")
    print(f" -> Port           : {port}")
    print(f" -> Kullanici      : {user}")
    if identity_file:
        print(f" -> SSH Anahtari   : {identity_file}")
    print("-" * 65)

    remote_folder = "/root/bb"

    # Tarayici sayisi
    try:
        raw_b = input("Kac adet tarayici calissin? [Varsayilan: 5 - Direkt Enter'a basabilirsiniz]: ").strip()
        browser_count = int(raw_b) if raw_b else 5
        if browser_count < 1:
            browser_count = 1
    except ValueError:
        browser_count = 5

    print(f"\n[+] Sunucuda {browser_count} tarayici baslatilacak.")

    # 1. Sunucu hazir mi kontrol et ve gerekirse bekle
    if not wait_for_ssh_ready(user, host, port, identity_file):
        print("\n[!] Sunucuya baglanilamadi!")
        print("Muhtemel Sebepler:")
        print(" 1. Vast.ai panelinde sunucu henuz 'loading' (imaj yukleniyor) durumundadir.")
        print(" 2. Sunucu 'stopped' veya 'error' durumuna gecmis olabilir.")
        print(" 3. Vast.ai panelindeki 'Instances' kisminda sunucu durumunun yesil 'running' oldugundan emin olun.")
        input("\nCikmak icin Enter'a basin...")
        return

    # 2. Dosyalari yukle
    ok = upload_folder(user, host, port, identity_file, BOT_DIR, remote_folder)
    if not ok:
        input("\nCikmak icin Enter'a basin...")
        return

    # 3. Kurulum ve Baslatma Komutu
    print(f"\n[3/3] Sunucuda Playwright ve paketler kurulup bot baslatiliyor...")
    print("[*] Lutfen bekleyin, loglar birazdan ekrana akmaya baslayacak...\n")

    ssh_args = get_base_ssh_args(user, host, port, identity_file)
    run_cmd = f"bash {remote_folder}/start_linux.sh {browser_count}"

    try:
        subprocess.run(ssh_args + [run_cmd])
    except KeyboardInterrupt:
        print("\n" + "=" * 65)
        print("[BILGI] Baglanti penceresi kapatildi.")
        print("[OK] Bot sunucuda arka planda 7/24 calismaya devam ediyor!")
        print(f"Sunucuya tekrar baglanip loglari izlemek icin:")
        print(f"  ssh -p {port} {user}@{host}")
        print(f"  tail -f {remote_folder}/bot.log")
        print("=" * 65)
        input("\nCikmak icin Enter'a basin...")

if __name__ == "__main__":
    main()
