"""Verifica cuántos resultados hay en cada página de búsqueda."""
import requests, urllib3, sys
from bs4 import BeautifulSoup
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
urllib3.disable_warnings()

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
BASE = "https://www.mininterior.gov.co"

for termino in ["minigranja", "generacion+distribuida"]:
    print(f"\n=== {termino} ===")
    for p in range(1, 30):
        url = f"{BASE}/?filter=true&s={termino}&page={p}&is_search=true"
        r = requests.get(url, headers=HEADERS, verify=False, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        links = [a["href"] for a in soup.find_all("a", href=True) if "/normativas/" in a["href"]]
        links = list(dict.fromkeys(links))
        print(f"  Página {p:2d}: {len(links)} resultados", end="")
        if links:
            print(f" | Primer link: {links[0].split('/normativas/')[1][:50]}")
        else:
            print(" ← FIN")
            break
        import time; time.sleep(0.3)
