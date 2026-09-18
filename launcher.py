import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def kill_process_by_port(port: int):
    try:
        import psutil
        for conn in psutil.net_connections():
            if conn.laddr.port == port and conn.pid:
                try:
                    p = psutil.Process(conn.pid)
                    p.kill()
                except Exception:
                    pass
    except Exception:
        pass

def main():
    cf_exe = ROOT / "cloudflared.exe"
    if not cf_exe.exists():
        print(f"[ERROR] cloudflared.exe not found at {cf_exe}")
        sys.exit(1)

    print("[1/3] Starting Cloudflare Tunnel...")
    log_file = Path(os.environ.get("TEMP", ".")) / "cf_tunnel.log"
    if log_file.exists():
        try:
            log_file.unlink()
        except Exception:
            pass

    log_handle = open(log_file, "a+", encoding="utf-8", errors="ignore")
    cf_proc = subprocess.Popen(
        [str(cf_exe), "tunnel", "--url", "http://127.0.0.1:8765"],
        stdout=log_handle,
        stderr=log_handle,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    tunnel_url = None
    deadline = time.time() + 35
    # Must match a trycloudflare URL that is NOT api.trycloudflare.com
    url_pattern = re.compile(r"https://(?!(?:api|developers)\.)([a-zA-Z0-9\-]+)\.trycloudflare\.com")

    while time.time() < deadline:
        time.sleep(1.0)
        if log_file.exists():
            try:
                content = log_file.read_text(encoding="utf-8", errors="ignore")
                match = url_pattern.search(content)
                if match:
                    tunnel_url = match.group(0).strip()
                    break
            except Exception:
                pass

    if tunnel_url:
        print(f"   Tunnel URL: {tunnel_url}")
        print("[2/3] Updating .env with WEBAPP_URL...")
        env_file = ROOT / ".env"
        if env_file.exists():
            env_text = env_file.read_text(encoding="utf-8")
            if re.search(r"^WEBAPP_URL=.*$", env_text, flags=re.MULTILINE):
                env_text = re.sub(r"^WEBAPP_URL=.*$", f"WEBAPP_URL={tunnel_url}", env_text, flags=re.MULTILINE)
            else:
                env_text = env_text.rstrip() + f"\nWEBAPP_URL={tunnel_url}\n"
            env_file.write_text(env_text, encoding="utf-8")
            print(f"   .env updated: WEBAPP_URL={tunnel_url}")
    else:
        print("   [INFO] Quick tunnel URL not obtained immediately or offline. Using configured WEBAPP_URL.")

    print("[3/3] Starting GoodBingo server & bot via run.py...")
    import run
    if not run._acquire_single_instance():
        print("Another instance is running. Exiting.")
        return

    import threading
    server_thread = threading.Thread(target=run.run_server, daemon=True)
    server_thread.start()
    print("Game server running at http://0.0.0.0:8765")
    run.run_bot()

if __name__ == "__main__":
    main()
