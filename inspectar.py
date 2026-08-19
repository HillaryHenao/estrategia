import requests, urllib3, sys
from bs4 import BeautifulSoup
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
urllib3.disable_warnings()

r = requests.get(
    "https://www.mininterior.gov.co/?filter=true&s=minigranja&page=20&is_search=true",
    headers={"User-Agent": "Mozilla/5.0"},
    verify=False, timeout=30
)
soup = BeautifulSoup(r.text, "html.parser")

# Buscar todos los links a /normativas/
links = []
for a in soup.find_all("a", href=True):
    href = a["href"]
    if "/normativas/" in href and href not in links:
        links.append(href)
        # Mostrar jerarquía del elemento padre
        parents = []
        p = a.parent
        for _ in range(4):
            if p and p.name:
                parents.append(f"{p.name}.{' '.join(p.get('class', []))}")
                p = p.parent
        print(f"LINK: {href[:80]}")
        print(f"  Padres: {' > '.join(parents[:3])}")
        print()

print(f"Total: {len(links)} links a /normativas/")
