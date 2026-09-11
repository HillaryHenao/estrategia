"""
Re-extrae capacidad_kw/capacidad_texto y municipio/departamento de todos los
proyectos ya guardados, usando el texto de PDF ya cacheado (sin volver a
scrapear el sitio). Útil después de mejorar las regex de extracción en
extraer.py, para aplicar la corrección a todo lo ya guardado.

- Capacidad: corrige el caso donde, a falta de capacidad en el nombre del
  proyecto, la extracción caía a buscar cualquier "número + MW/KW" en todo el
  texto y enganchaba boilerplate legal (ej. "potencia superior a los 10 MW")
  en vez de la capacidad real, ahora priorizada vía la frase explícita
  "Capacidad de X kW".
- Departamento: corrige el caso donde el PDF dice "departamento del Cesar"
  (con "del") en vez de "departamento de X", que la regex original no
  contemplaba y dejaba el departamento vacío.

Uso:
    python reparse_capacidad.py
"""

import json
import sys

from extraer import extraer_capacidad, extraer_ubicacion

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROYECTOS_FILE = "proyectos.json"
CACHE_FILE = "cache_pdfs.json"


def main():
    with open(PROYECTOS_FILE, encoding="utf-8") as f:
        proyectos = json.load(f)
    with open(CACHE_FILE, encoding="utf-8") as f:
        cache = json.load(f)

    cambios_capacidad = []
    cambios_ubicacion = []
    sin_cache = 0

    for p in proyectos:
        pdf_url = p.get("pdf_url")
        if not pdf_url or pdf_url not in cache:
            sin_cache += 1
            continue

        texto = cache[pdf_url]

        # ── Capacidad ──
        antes_kw = p.get("capacidad_kw")
        antes_texto = p.get("capacidad_texto")

        m = extraer_capacidad(p.get("nombre"), texto)
        if m:
            val = float(m.group(1).replace(",", "."))
            unidad = m.group(2).upper()
            nuevo_kw = round(val * 1000 if "MW" in unidad else val, 2)
            nuevo_texto = " ".join(m.group(0).split())
        else:
            nuevo_kw = None
            nuevo_texto = None

        if nuevo_kw != antes_kw:
            cambios_capacidad.append({
                "solicitante": p.get("solicitante"),
                "pdf_url": pdf_url,
                "antes": antes_texto,
                "despues": nuevo_texto,
            })
            p["capacidad_kw"] = nuevo_kw
            p["capacidad_texto"] = nuevo_texto

        # ── Municipio / departamento ──
        antes_mun = p.get("municipio")
        antes_dep = p.get("departamento")

        nuevo_mun, nuevo_dep = extraer_ubicacion(texto)
        # No pisar un municipio ya capturado si esta vez no se encontró nada
        # (evita perder datos si el texto cacheado varía o está incompleto),
        # pero sí corregirlo si cambió (ej. limpieza de conectores pegados).
        if nuevo_mun and nuevo_mun != antes_mun:
            p["municipio"] = nuevo_mun
        if nuevo_dep and nuevo_dep != antes_dep:
            cambios_ubicacion.append({
                "solicitante": p.get("solicitante"),
                "municipio": p.get("municipio"),
                "pdf_url": pdf_url,
                "antes": antes_dep,
                "despues": nuevo_dep,
            })
            p["departamento"] = nuevo_dep

    with open(PROYECTOS_FILE, "w", encoding="utf-8") as f:
        json.dump(proyectos, f, ensure_ascii=False, indent=2)

    print(f"Proyectos revisados: {len(proyectos)} (sin PDF cacheado: {sin_cache})")
    print(f"Capacidad corregida en {len(cambios_capacidad)} proyectos")
    for c in cambios_capacidad[:20]:
        print(f"  - {c['solicitante']}: {c['antes']} -> {c['despues']}  ({c['pdf_url'].split('/')[-1]})")
    if len(cambios_capacidad) > 20:
        print(f"  ... y {len(cambios_capacidad) - 20} más")

    print(f"\nDepartamento corregido en {len(cambios_ubicacion)} proyectos")
    for c in cambios_ubicacion[:20]:
        print(f"  - {c['solicitante']} ({c['municipio']}): {c['antes']} -> {c['despues']}  ({c['pdf_url'].split('/')[-1]})")
    if len(cambios_ubicacion) > 20:
        print(f"  ... y {len(cambios_ubicacion) - 20} más")


if __name__ == "__main__":
    main()
