"""
Prueba puntual de páginas aleatorias del buscador (para ver si el sitio
sigue bloqueando tras el 403 en la página 8 de 'parque solar').
No descarga PDFs, solo pide la página de resultados.

Uso: python probar_paginas.py [--termino parque+solar] [--paginas 9 31 19 21 ...]
"""
import argparse
import random
import sys

from extraer import get_links_pagina, TERMINOS

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

parser = argparse.ArgumentParser()
parser.add_argument("--termino", default="parque+solar")
parser.add_argument("--paginas", type=int, nargs="+", default=None)
parser.add_argument("--rango", type=int, nargs=2, default=[8, 33], help="rango [min max] para elegir al azar")
parser.add_argument("--n", type=int, default=5)
args = parser.parse_args()

if args.paginas:
    paginas = args.paginas
else:
    lo, hi = args.rango
    paginas = random.sample(range(lo, hi + 1), min(args.n, hi - lo + 1))

print(f"Término: {args.termino.replace('+', ' ')}")
print(f"Páginas a probar (orden aleatorio): {paginas}\n")

bloqueadas = 0
for num_pagina in paginas:
    print(f"Página {num_pagina}...", end=" ", flush=True)
    links = get_links_pagina(args.termino, num_pagina)
    if links:
        print(f"OK — {len(links)} resultados | primero: {links[0][:70]}")
    else:
        print("SIN RESULTADOS o bloqueada (ver mensaje de error arriba si lo hubo)")
        bloqueadas += 1

print(f"\n{'='*50}")
print(f"{len(paginas) - bloqueadas}/{len(paginas)} páginas respondieron con resultados")
