"""Muestra los caracteres exactos alrededor del nombre del proyecto."""
import json, sys, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open("cache_pdfs.json", encoding="utf-8") as f:
    cache = json.load(f)
with open("proyectos.json", encoding="utf-8") as f:
    proyectos = json.load(f)

vistos = 0
for p in proyectos[:20]:
    texto = cache.get(p["pdf_url"], "")
    m = re.search(r'para el desarrollo del proyecto.{0,15}', texto, re.IGNORECASE)
    if not m:
        continue
    inicio = m.end()
    fragmento = texto[inicio:inicio+120]
    print(f"\nPDF: {p['pdf_url'].split('/')[-1][:50]}")
    print(f"Texto: {fragmento[:80]}")
    print(f"Chars: {[hex(ord(c)) for c in fragmento[:8]]}")
    vistos += 1
    if vistos >= 8:
        break
