"""Muestra las secciones clave de varios PDFs del cache para diagnosticar fallos."""
import json, sys, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open("cache_pdfs.json", encoding="utf-8") as f:
    cache = json.load(f)

# Cargar proyectos para saber cuáles fallan
with open("proyectos.json", encoding="utf-8") as f:
    proyectos = json.load(f)

sin_mun = [p for p in proyectos if not p.get("municipio")]
print(f"Proyectos sin municipio: {len(sin_mun)}/{len(proyectos)}\n")

# Mostrar texto de antecedentes de los primeros 5 que fallan
mostrados = 0
for p in sin_mun[:6]:
    pdf_url = p["pdf_url"]
    texto = cache.get(pdf_url, "")
    if not texto:
        continue

    # Extraer sección de antecedentes
    m = re.search(r'(ANTECEDENTES.{0,3000}?)(?:FUNDAMENTOS|ANÁLISIS|CONSIDERACIONES|$)',
                  texto, re.DOTALL | re.IGNORECASE)
    fragmento = m.group(1)[:2000] if m else texto[:2000]

    print("="*70)
    print(f"PDF: {pdf_url.split('/')[-1]}")
    print(f"Nombre actual: {p.get('nombre', '—')[:80]}")
    print("-"*70)
    print(fragmento)
    print()
    mostrados += 1
    if mostrados >= 6:
        break
