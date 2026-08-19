"""Muestra el texto del primer PDF en cache para depurar regex."""
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
with open("cache_pdfs.json", encoding="utf-8") as f:
    cache = json.load(f)
url, texto = next(iter(cache.items()))
print("URL:", url)
print("="*60)
print(texto[:4000])
