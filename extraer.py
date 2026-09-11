"""
Extrae proyectos energéticos de las resoluciones PDF del Ministerio del Interior.
Salida: proyectos.json

Uso:
    python extraer.py                  # scraping completo (~20 páginas)
    python extraer.py --paginas 5      # solo primeras 5 páginas (más rápido)
    python extraer.py --termino minigranja --paginas 3
    python extraer.py --termino parque+solar --paginas 35 --desde-anio 2024
"""

import requests
import urllib3
from bs4 import BeautifulSoup
import pdfplumber
import re
import json
import time
import io
import sys
import os
import argparse
import unicodedata

# Fix encoding for Windows terminals
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# SSL en algunos entornos Windows falla; desactivamos verificación solo para este scraping
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
VERIFY_SSL = False

BASE_URL = "https://www.mininterior.gov.co"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    "Referer": "https://www.mininterior.gov.co/",
    "Connection": "keep-alive",
}
TERMINOS = ["minigranja", "generacion+distribuida", "parque+solar"]
EMPRESAS_EXCLUIDAS = []
CACHE_FILE = "cache_pdfs.json"
TIMEOUT = 60
REINTENTOS = 3
PAUSA_ENTRE_PETICIONES = 2.5

# No interesan los proyectos de gran escala (parques de 99-100 MW y similares);
# el foco son proyectos pequeños/medianos. Si no se detectó capacidad en el PDF,
# se conserva el proyecto (mejor revisarlo a mano que descartarlo a ciegas).
CAPACIDAD_MAXIMA_KW = 20000


def excluido_por_capacidad(proyecto):
    kw = proyecto.get("capacidad_kw")
    return kw is not None and kw > CAPACIDAD_MAXIMA_KW


def anio_subida_pdf(pdf_url):
    """Extrae el año de la carpeta /uploads/AAAA/MM/ en la URL del PDF, sin descargarlo."""
    m = re.search(r'/uploads/(\d{4})/\d{2}/', pdf_url)
    return int(m.group(1)) if m else None


MESES = {
    "ENE": "enero", "FEB": "febrero", "MAR": "marzo", "ABR": "abril",
    "MAY": "mayo", "JUN": "junio", "JUL": "julio", "AGO": "agosto",
    "SEP": "septiembre", "SET": "septiembre", "OCT": "octubre",
    "NOV": "noviembre", "DIC": "diciembre",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def get_con_reintentos(url, **kwargs):
    """El sitio de Mininterior bloquea/corta conexiones agresivas; reintenta con sesión persistente y backoff."""
    ultimo_error = None
    for intento in range(1, REINTENTOS + 1):
        try:
            r = SESSION.get(url, timeout=TIMEOUT, verify=VERIFY_SSL, **kwargs)
            time.sleep(PAUSA_ENTRE_PETICIONES)
            return r
        except Exception as e:
            ultimo_error = e
            if intento < REINTENTOS:
                time.sleep(5 * intento)
    raise ultimo_error


# ── Utilidades ────────────────────────────────────────────────────────────────

def norm(texto):
    """Quita tildes para simplificar regex (solo para búsqueda, no para mostrar)."""
    return unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII").upper()


def cargar_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


# ── Scraping ──────────────────────────────────────────────────────────────────

def get_links_pagina(termino, num_pagina):
    """Retorna los hrefs de resultados de una página de búsqueda."""
    url = f"{BASE_URL}/?filter=true&s={termino}&page={num_pagina}&is_search=true"
    try:
        r = get_con_reintentos(url, headers=HEADERS)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # El sitio usa Divi Builder; los links están como <a href="/normativas/...">
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/normativas/" in href and href not in links:
                links.append(href)
        return links
    except Exception as e:
        print(f"    Error en página {num_pagina}: {e}")
        return []


def get_pdf_url(pagina_url):
    """Extrae el link al PDF desde la página de detalle."""
    try:
        r = get_con_reintentos(pagina_url, headers=HEADERS)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().endswith(".pdf"):
                return href
    except Exception as e:
        print(f"    Error obteniendo PDF desde {pagina_url}: {e}")
    return None


def descargar_texto_pdf(pdf_url, cache):
    """Descarga el PDF y extrae texto. Usa caché para no re-descargar."""
    if pdf_url in cache:
        return cache[pdf_url]
    try:
        r = get_con_reintentos(pdf_url, headers=HEADERS)
        r.raise_for_status()
        texto = ""
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            for pagina in pdf.pages[:8]:
                t = pagina.extract_text()
                if t:
                    texto += t + "\n"
        cache[pdf_url] = texto
        guardar_cache(cache)
        return texto
    except Exception as e:
        print(f"    Error descargando PDF {pdf_url}: {e}")
    return ""


# ── Parseo del PDF ─────────────────────────────────────────────────────────────

def extraer_coordenadas(texto):
    """
    Soporta tres formatos de coordenadas encontrados en los PDFs:
      1. WGS84 punto decimal:  "1 11.2278  -73.3229"
      2. WGS84 coma decimal:   "1 8,950829 -75,846303"
      3. MAGNA-SIRGAS CTM12:   "1 4800884,57  2738710,99"
    """
    puntos = []

    # ── WGS84 con PUNTO decimal ──
    for lat_s, lon_s in re.findall(
        r'(?<!\d)\d{1,2}\s+([-]?\d{1,2}\.\d{3,9})[°º]?\s+([-]\d{2,3}\.\d{3,9})[°º]?',
        texto
    ):
        lat, lon = float(lat_s), float(lon_s)
        if -2 <= lat <= 14 and -82 <= lon <= -66:
            puntos.append({"lat": round(lat, 6), "lon": round(lon, 6)})

    # ── WGS84 con COMA decimal (ej: "1 8,950829 -75,846303") ──
    if not puntos:
        for lat_s, lon_s in re.findall(
            r'(?<!\d)\d{1,2}\s+([-]?\d{1,2},\d{3,9})\s+([-]\d{2,3},\d{3,9})',
            texto
        ):
            lat = float(lat_s.replace(',', '.'))
            lon = float(lon_s.replace(',', '.'))
            if -2 <= lat <= 14 and -82 <= lon <= -66:
                puntos.append({"lat": round(lat, 6), "lon": round(lon, 6)})

    # ── MAGNA-SIRGAS CTM12 (EPSG:9377, X~4.8M Y~2.7M) ──
    if not puntos:
        ctm12 = re.findall(r'(?<!\d)\d{1,2}\s+(\d{7}[,.]\d+)\s+(\d{7}[,.]\d+)', texto)
        if ctm12:
            try:
                from pyproj import Transformer
                tr = Transformer.from_crs("EPSG:9377", "EPSG:4326", always_xy=True)
                for x_s, y_s in ctm12:
                    lon, lat = tr.transform(float(x_s.replace(',', '.')),
                                            float(y_s.replace(',', '.')))
                    if -2 <= lat <= 14 and -82 <= lon <= -66:
                        puntos.append({"lat": round(lat, 6), "lon": round(lon, 6)})
            except Exception as e:
                print(f"    Error convirtiendo MAGNA-SIRGAS: {e}")

    # Dedup y centroide
    vistos = set()
    unicos = []
    for p in puntos:
        k = (p["lat"], p["lon"])
        if k not in vistos:
            vistos.add(k)
            unicos.append(p)

    if unicos:
        lat_c = round(sum(p["lat"] for p in unicos) / len(unicos), 6)
        lon_c = round(sum(p["lon"] for p in unicos) / len(unicos), 6)
        return unicos, lat_c, lon_c
    return [], None, None


def geocodificar_municipio(municipio, departamento):
    """Fallback: geolocaliza el municipio con Nominatim si no hay coordenadas en el PDF."""
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": f"{municipio}, {departamento}, Colombia",
            "format": "json",
            "limit": 1,
            "countrycodes": "co"
        }
        headers = {"User-Agent": "unergy-energy-map/1.0 hillary@unergy.io"}
        r = requests.get(url, params=params, headers=headers, timeout=10, verify=VERIFY_SSL)
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None, None


def extraer_resolucion_fecha(texto):
    """
    Extrae número y fecha de la resolución. El formato real es
    "RESOLUCIÓN NÚMERO ST-0291 DE 18 MAR 2024" (mes abreviado en mayúsculas,
    sin "DE" entre día y mes). También se acepta el formato con mes completo
    y "DE" ("18 DE MARZO DE 2024").
    """
    m = re.search(
        r'RESOLUCI[OÓ]N\s+N[UÚ]MERO\s+([\w\s\-–]+?)\s+DE\s+'
        r'(\d{1,2})\s+(?:DE\s+)?([A-ZÁÉÍÓÚ]+)\.?\s+(?:DE\s+)?(\d{4})',
        texto, re.IGNORECASE
    )
    if not m:
        return None, None
    resolucion = re.sub(r'\s+', ' ', m.group(1)).strip()
    dia, mes_tok, anio = m.group(2), m.group(3).upper(), m.group(4)
    mes = MESES.get(mes_tok[:3], mes_tok.lower())
    return resolucion, f"{int(dia)} de {mes} de {anio}"


_PATRON_MW_KW = r'(\d+(?:[.,]\d+)?)\s*(MW[pPnN]?|KW[pPnN]?)'

# Frases de umbral regulatorio (FNCER / generación distribuida) que preceden a
# una cifra de MW/KW sin que esa cifra sea la capacidad real del proyecto, ej.
# "...proyectos con potencia superior a los 10 MW..." o "...planta con
# capacidad menor a 1MW...". Se usan para descartar esos números del escaneo
# genérico de último recurso (ver extraer_capacidad).
_CONTEXTO_UMBRAL_RE = re.compile(
    r'(?:menor|inferior|superior|mayor)(?:\s+o\s+igual)?\s+a\s+(?:los\s+|las\s+)?$',
    re.IGNORECASE,
)


def extraer_capacidad(nombre, texto):
    """
    Busca la capacidad del proyecto en orden de confiabilidad:
      1. En el nombre del proyecto (ej. "...9.9 MW..."), si lo trae.
      2. La frase explícita "capacidad (instalada) de X kW/MW" en el cuerpo del
         PDF (sección de descripción del proyecto), ej. "Capacidad de 990 kW".
      3. Como último recurso, la primera mención de "número + MW/KW" en el
         texto completo que NO esté precedida por una frase de umbral
         regulatorio (ver _CONTEXTO_UMBRAL_RE) — evita enganchar boilerplate
         legal como "potencia superior a los 10 MW" en vez de la capacidad
         real del proyecto.
    """
    I = re.IGNORECASE
    if nombre:
        m = re.search(_PATRON_MW_KW, nombre, I)
        if m:
            return m

    m = re.search(r'capacidad\s+(?:instalada\s+)?de\s+' + _PATRON_MW_KW, texto, I)
    if m:
        return re.search(_PATRON_MW_KW, m.group(0), I)

    for m in re.finditer(_PATRON_MW_KW, texto, I):
        contexto_previo = texto[max(0, m.start() - 45):m.start()]
        if _CONTEXTO_UMBRAL_RE.search(contexto_previo):
            continue
        return m

    return None


def limpio(s):
    return re.sub(r'\s+', ' ', s or '').strip()


# Conectores que a veces quedan pegados al final del municipio capturado
# porque el corte antes de "departamento" (ver extraer_ubicacion) no exige
# coma previa, ej. "Guamo en el" (de "...municipio de Guamo en el
# departamento de Tolima") o "El Copey jurisdicción del" (de "...jurisdicción
# del Departamento del Cesar").
_RUIDO_MUNICIPIO_RE = re.compile(
    r'\s+(?:en\s+el|en\s+la|jurisdicci[oó]n\s+del?|y\s+al\s+interior\s+del?)\s*$',
    re.IGNORECASE,
)


def extraer_ubicacion(texto):
    """
    Estructura típica: "municipio de X[, vereda/correg Y,] [en el] departamento
    de Z". Se busca municipio y luego departamento dentro de un rango cercano.
    Admite tanto "de" como "del" (departamentos masculinos: Cesar, Meta,
    Tolima... vs. de Bolívar, de La Guajira...).
    """
    # Nota: se excluyen , ; . del texto capturado pero NO el salto de línea,
    # porque el PDF a veces parte el nombre justo ahí (ej. "La\nGuajira",
    # "departamento del\nCesar"); limpio() colapsa esos saltos a un espacio.
    # La captura del municipio corta además apenas aparece la palabra
    # "departamento" (sin exigir coma antes), porque muchos PDFs escriben
    # "municipio de X en el departamento de Y" o "...X jurisdicción del
    # Departamento de Y" sin coma de separación, y sin este corte el grupo se
    # comía también el departamento, dejándolo vacío.
    # El "de"/"del" es opcional: algunos PDFs escriben "municipio Aguachica,"
    # directo, sin preposición.
    I = re.IGNORECASE
    m_mun = re.search(
        r'municipios?\s+(?:del?\s+)?(.{3,45}?)(?=\s*[,;.]|\bdepartamento\b|$)',
        texto, I | re.DOTALL,
    )
    if not m_mun:
        return None, None
    crudo = limpio(m_mun.group(1))
    crudo = _RUIDO_MUNICIPIO_RE.sub('', crudo).strip()
    municipio = crudo.title()
    fragmento = texto[m_mun.end(): m_mun.end() + 220]
    # Corta antes de conectores/puntuación que suelen seguir al departamento
    # en la misma oración ("...del Tolima, localizado en...", "...del Cesar y
    # en el municipio de...", comillas de cierre, paréntesis, dos puntos).
    m_dep = re.search(
        r'departamento\s+del?\s+(.{3,40}?)'
        r'(?=\s*[,;.:)”“"\']|\s+(?:y\s+en|que\s+se|localiza\w*|ubicad[oa]|identificad[oa])\b|$)',
        fragmento, I | re.DOTALL,
    )
    departamento = limpio(m_dep.group(1)).title() if m_dep else None
    return municipio, departamento


def parsear_proyecto(texto, url_fuente, pdf_url):
    """Extrae los campos del proyecto desde el texto del PDF."""
    I = re.IGNORECASE

    proyecto = {
        "fuente_url": url_fuente,
        "pdf_url": pdf_url,
        "nombre": None,
        "tipo": None,
        "capacidad_texto": None,
        "capacidad_kw": None,
        "municipio": None,
        "departamento": None,
        "vereda_corregimiento": None,
        "solicitante": None,
        "nit": None,
        "representante": None,
        "radicado": None,
        "fecha_solicitud": None,
        "resolucion": None,
        "fecha_resolucion": None,
        "coordenadas": [],
        "lat": None,
        "lon": None,
    }

    # ── Resolución y fecha ──
    proyecto["resolucion"], proyecto["fecha_resolucion"] = extraer_resolucion_fecha(texto)

    # ── Nombre del proyecto ──
    # Siempre aparece entre comillas (ASCII " o curvas “” o « »)
    # justo después de "para el desarrollo del proyecto:"
    ABRE = '[«“„\x22]'   # «  "  „  "
    CIERRA = '[»”\x22]'        # »  "  "
    m = re.search(
        rf'para el desarrollo del proyecto\s*:?\s*{ABRE}(.*?){CIERRA}\s*[,.]',
        texto, I | re.DOTALL
    )
    if m and len(m.group(1).strip()) > 5:
        proyecto["nombre"] = limpio(m.group(1))
    else:
        # Fallback: primera línea en mayúsculas que mencione minigranja/proyecto
        m = re.search(r'(?:MINIGRANJA|PROYECTO DE GENERACI[OÓ]N)[^\n]{5,150}', texto)
        if m:
            proyecto["nombre"] = limpio(m.group(0))

    # ── Tipo ──
    nb = (proyecto["nombre"] or "").upper()
    if "MINIGRANJA" in nb:
        proyecto["tipo"] = "Minigranja Solar"
    elif "GENERACI" in nb and "DISTRIBUIDA" in nb:
        proyecto["tipo"] = "Generación Distribuida"
    elif "FOTOVOLTAIC" in nb:
        proyecto["tipo"] = "Solar Fotovoltaico"
    else:
        proyecto["tipo"] = "Energía Renovable"

    # ── Capacidad ──
    m = extraer_capacidad(proyecto["nombre"], texto)
    if m:
        proyecto["capacidad_texto"] = limpio(m.group(0))
        val = float(m.group(1).replace(',', '.'))
        unidad = m.group(2).upper()
        proyecto["capacidad_kw"] = round(val * 1000 if 'MW' in unidad else val, 2)

    # ── Municipio y departamento ──
    proyecto["municipio"], proyecto["departamento"] = extraer_ubicacion(texto)

    # ── Vereda / corregimiento ──
    m = re.search(r'(?:vereda|corregimiento|inspecci[oó]n)\s+(?:de\s+)?([^,;.\n]{3,40}?)(?:[,;.\n])', texto, I)
    if m:
        proyecto["vereda_corregimiento"] = limpio(m.group(1)).title()

    # ── Solicitante ──
    m = re.search(
        r'(?:empresa|compa[ñn][íi]a|sociedad)\s+'
        r'([A-ZÁÉÍÓÚÑ\w][A-ZÁÉÍÓÚÑa-záéíóúñ\s.\w]+?'
        r'(?:S\.A\.S|SAS|ESP|S\.A\.|LTDA|S\.A\.S\.|SOLAR)[^,;\n]{0,30})',
        texto, I
    )
    if m:
        proyecto["solicitante"] = limpio(m.group(1))

    # ── NIT ──
    m = re.search(r'NIT\.?\s+(?:N[°oO]\.?\s*)?([\d.]+(?:-\d)?)', texto, I)
    if m:
        proyecto["nit"] = m.group(1)

    # ── Representante legal ──
    m = re.search(r'se[ñn]or\w*\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s]+?),?\s+identificad', texto, I)
    if m:
        proyecto["representante"] = limpio(m.group(1)).title()

    # ── Radicado ──
    m = re.search(r'radicado\s+(?:controldoc\s+n[°o]?\s*)?([\d-]+(?:\s+Id\s*[\w:]+)?)', texto, I)
    if m:
        proyecto["radicado"] = limpio(m.group(1))

    # ── Fecha de solicitud ──
    m = re.search(r'\bdel\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})\b', texto, I)
    if m:
        proyecto["fecha_solicitud"] = limpio(m.group(1)).title()

    # ── Coordenadas ──
    puntos, lat_c, lon_c = extraer_coordenadas(texto)
    proyecto["coordenadas"] = puntos
    proyecto["lat"] = lat_c
    proyecto["lon"] = lon_c

    # Fallback geocodificación si no hay coordenadas en el PDF
    if not lat_c and proyecto["municipio"]:
        print(f"    Sin coords en PDF, geocodificando {proyecto['municipio']}...")
        lat_c, lon_c = geocodificar_municipio(proyecto["municipio"], proyecto["departamento"] or "")
        proyecto["lat"] = lat_c
        proyecto["lon"] = lon_c
        time.sleep(1)

    return proyecto


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extrae proyectos energéticos de Mininterior")
    parser.add_argument("--paginas", type=int, default=25, help="Número máximo de páginas por término (default: 25)")
    parser.add_argument("--pagina-inicio", type=int, default=1, help="Página desde la cual empezar (default: 1), para continuar una búsqueda cortada por tandas")
    parser.add_argument("--termino", type=str, default=None, help="Término específico de búsqueda")
    parser.add_argument("--desde-anio", type=int, default=None,
                         help="Ignora PDFs subidos antes de este año (según la carpeta /uploads/AAAA/ de su URL), sin descargarlos")
    args = parser.parse_args()

    terminos = [args.termino] if args.termino else TERMINOS
    cache = cargar_cache()
    proyectos_por_pdf = {}  # dedup por URL de PDF

    # Proyectos ya conocidos de corridas previas: si un link de resultado ya está
    # capturado, no volvemos a pedir su página de detalle ni su PDF. Esto reduce
    # drásticamente el volumen de peticiones en re-corridas (el sitio bloquea tras
    # demasiadas peticiones seguidas), permitiendo avanzar más páginas por corrida.
    conocidos_por_fuente = {}
    if os.path.exists("proyectos.json"):
        with open("proyectos.json", encoding="utf-8") as f:
            for p in json.load(f):
                if p.get("fuente_url"):
                    conocidos_por_fuente[p["fuente_url"]] = p

    for termino in terminos:
        print(f"\n{'='*60}")
        print(f"Buscando: {termino.replace('+', ' ')}")
        print(f"{'='*60}")

        urls_vistas = set()
        for num_pagina in range(args.pagina_inicio, args.paginas + 1):
            print(f"\n  Página {num_pagina}...", end=" ", flush=True)
            links = get_links_pagina(termino, num_pagina)
            if not links:
                print("sin resultados, fin de búsqueda")
                break
            # Detectar loop: si todos los links ya fueron vistos, el sitio está repitiendo
            nuevos = [l for l in links if l not in urls_vistas]
            if not nuevos:
                print(f"sin resultados nuevos (loop detectado), fin de búsqueda")
                break
            urls_vistas.update(links)
            print(f"{len(links)} resultados ({len(nuevos)} nuevos)")

            for url in nuevos:
                if any(p["fuente_url"] == url for p in proyectos_por_pdf.values()):
                    print(f"    [ya procesado] {url[:70]}")
                    continue

                if url in conocidos_por_fuente:
                    p_conocido = conocidos_por_fuente[url]
                    pdf_conocido = p_conocido.get("pdf_url") or url
                    proyectos_por_pdf[pdf_conocido] = p_conocido
                    print(f"    [ya conocido, sin re-descargar] {url[:70]}")
                    continue

                print(f"    → {url[:70]}")
                pdf_url = get_pdf_url(url)
                if not pdf_url:
                    print("      Sin PDF")
                    continue
                print(f"      PDF: {pdf_url.split('/')[-1]}")

                if args.desde_anio:
                    anio = anio_subida_pdf(pdf_url)
                    if anio and anio < args.desde_anio:
                        print(f"      [Descartado sin descargar: subido en {anio}, antes de {args.desde_anio}]")
                        continue

                texto = descargar_texto_pdf(pdf_url, cache)
                if not texto.strip():
                    print("      PDF sin texto extraíble (¿escaneado?)")
                    continue

                # Filtrar solicitudes de Unergy (empresa del equipo)
                texto_upper = texto.upper()
                empresa_excluida = next((e for e in EMPRESAS_EXCLUIDAS if e in texto_upper), None)
                if empresa_excluida:
                    print(f"      [{empresa_excluida} - omitido]")
                    continue

                proyecto = parsear_proyecto(texto, url, pdf_url)

                if excluido_por_capacidad(proyecto):
                    print(f"      [Excluido: {proyecto['capacidad_texto']} - por encima de {CAPACIDAD_MAXIMA_KW/1000:.0f} MW]")
                    continue

                proyectos_por_pdf[pdf_url] = proyecto

                nombre_corto = (proyecto["nombre"] or "")[:55]
                cap = proyecto["capacidad_texto"] or "cap. desconocida"
                mun = proyecto["municipio"] or "mun. desconocida"
                coords = "✓ coords" if proyecto["lat"] else "✗ sin coords"
                print(f"      ✓ {nombre_corto} | {cap} | {mun} | {coords}")

                time.sleep(0.4)

            time.sleep(0.3)

    encontrados = list(proyectos_por_pdf.values())

    # El buscador del sitio es inconsistente entre corridas: una corrida puede no
    # mostrar en sus páginas de resultados proyectos que otra corrida sí encontró,
    # aunque el PDF siga existiendo. Por eso NUNCA se sobrescribe proyectos.json:
    # se fusiona con lo ya conocido (por pdf_url) y el registro nuevo gana si hay
    # conflicto, para quedarse con el parseo más reciente sin perder historial.
    # Proyectos grandes que ya estaban guardados de corridas anteriores (antes de
    # bajar el límite a CAPACIDAD_MAXIMA_KW) se conservan tal cual: el límite de
    # capacidad solo se aplica a lo que se encuentra en esta corrida en adelante.
    anteriores = []
    if os.path.exists("proyectos.json"):
        with open("proyectos.json", encoding="utf-8") as f:
            anteriores = json.load(f)

    fusion = {(p.get("pdf_url") or p.get("fuente_url")): p for p in anteriores}
    for pdf_url, p in proyectos_por_pdf.items():
        fusion[pdf_url] = p

    proyectos = list(fusion.values())
    proyectos.sort(key=lambda p: p["fecha_solicitud"] or "", reverse=True)

    if anteriores and len(encontrados) < len(anteriores) * 0.5:
        print(f"\n⚠ Esta corrida solo encontró {len(encontrados)} proyectos en las páginas de resultados")
        print(f"  (había {len(anteriores)} conocidos). Puede que el sitio haya bloqueado/cortado el scraping")
        print("  a mitad de camino. Se fusionó con lo ya conocido en vez de sobrescribir; revisa el log.")

    with open("proyectos.json", "w", encoding="utf-8") as f:
        json.dump(proyectos, f, ensure_ascii=False, indent=2)

    con_coords = sum(1 for p in proyectos if p["lat"])
    cap_total = sum(p["capacidad_kw"] or 0 for p in proyectos) / 1000

    print(f"\n{'='*60}")
    print(f"✓ {len(proyectos)} proyectos guardados en proyectos.json")
    print(f"  Con coordenadas: {con_coords}/{len(proyectos)}")
    print(f"  Capacidad total: {cap_total:.1f} MW")
    print(f"{'='*60}")
    print("\nAhora ejecuta:  python generar_mapa.py")


if __name__ == "__main__":
    main()
