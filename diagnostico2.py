"""Muestra el texto completo de un PDF fallando."""
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open("cache_pdfs.json", encoding="utf-8") as f:
    cache = json.load(f)

# Mirar el PDF de KEMPI (falla municipio y coords)
url = "https://www.mininterior.gov.co/wp-content/uploads/2025/04/resolucion-procedencia-de-consulta-previa-st-0921-de-2025.pdf"
texto = cache.get(url, "")
if not texto:
    # tomar cualquiera que falle
    with open("proyectos.json", encoding="utf-8") as f:
        proyectos = json.load(f)
    for p in proyectos:
        if not p.get("municipio") and p["pdf_url"] in cache:
            url = p["pdf_url"]
            texto = cache[url]
            break

print(f"PDF: {url.split('/')[-1]}")
print("="*70)
# Imprimir el texto en bloques para ver toda la estructura
for i in range(0, min(len(texto), 6000), 1000):
    print(f"\n--- BLOQUE {i}-{i+1000} ---")
    print(texto[i:i+1000])
