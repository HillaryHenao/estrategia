"""
Genera mapa.html a partir de proyectos.json.
Uso: python generar_mapa.py
"""

import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENTRADA = "proyectos.json"
SALIDA = "mapa.html"

_SUFIJO_RUIDO_RE = re.compile(
    r"\s*(con\s+nit.*|identificada\s+con.*|identificada.*|solicit[oó].*)$",
    re.IGNORECASE,
)


def limpiar_nombre_empresa(nombre):
    """Quita coletillas tipo 'con NIT 900...' / 'identificada con...' del nombre."""
    n = _SUFIJO_RUIDO_RE.sub("", nombre.strip())
    return n.strip(" .")


def normalizar_nit(nit):
    """Deja solo dígitos/letras del NIT para poder comparar '901.748.280-5' == '901748280-5'."""
    if not nit:
        return None
    n = re.sub(r"[^0-9A-Za-z]", "", nit)
    return n or None


def _quitar_tildes(texto):
    """'ENERGÍA' -> 'ENERGIA', para que la agrupación no dependa de si el PDF trae tildes."""
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalizar_para_agrupar(nombre_limpio):
    """Uppercase sin tildes ni puntos ni espacios repetidos, para comparar 'SUNTACC SAS' ==
    'SUNTACC S.A.S.' == 'SUNTACC ENERGÍA SAS' con 'SUNTACC ENERGIA SAS'."""
    n = _quitar_tildes(nombre_limpio.upper()).replace(".", "")
    return re.sub(r"\s+", " ", n).strip()


# Términos genéricos del sector / relleno de nombres societarios en español.
# No cuentan como palabra "de marca" al decidir si dos nombres son la misma
# familia empresarial (ver _es_marca_compartida): comparten estas palabras
# muchísimas empresas sin relación entre sí (p.ej. "PARQUE SOLAR FOTOVOLTAICO
# EL COPEY" vs "...FUNDACIÓN", o "GENERADORA SAN JOSÉ" vs "...SAN JOAQUÍN").
PALABRAS_GENERICAS = {
    "PARQUE", "SOLAR", "FOTOVOLTAICO", "FOTOVOLTAICA", "EOLICO", "EÓLICO", "EOLICA", "EÓLICA",
    "GRANJA", "CENTRAL", "PLANTA", "HIDROELECTRICA", "HIDROELÉCTRICA", "GENERADORA",
    "PROYECTO", "PROYECTOS", "ENERGIA", "ENERGÍA", "RENOVABLE", "RENOVABLES",
    "COLOMBIA", "DE", "LA", "EL", "LOS", "LAS", "Y", "SAN", "SANTA", "SANTO",
    "SAS", "ESP", "SA", "LTDA", "COMPAÑIA", "COMPAÑÍA", "GRUPO",
}


def _palabra_normalizada(palabra):
    return re.sub(r"[^0-9A-Z]", "", _quitar_tildes(palabra.upper()))


def _prefijo_compartido(nombre_a, nombre_b):
    palabras_a = [_palabra_normalizada(w) for w in nombre_a.split()]
    palabras_b = [_palabra_normalizada(w) for w in nombre_b.split()]
    compartidas = []
    for a, b in zip(palabras_a, palabras_b):
        if a and a == b:
            compartidas.append(a)
        else:
            break
    return compartidas


def _es_marca_compartida(nombre_a, nombre_b):
    """
    Dos nombres se consideran de la misma marca/grupo empresarial si comparten
    al menos 2 palabras iniciales y, de esas, al menos 1 no es un término
    genérico del sector. Así "ABO WIND RENOVABLES PROYECTO CINCO" y
    "...PROYECTO NUEVE" se agrupan (aunque tengan NIT distinto, son la misma
    marca), pero "PARQUE SOLAR FOTOVOLTAICO EL COPEY" y "...FUNDACIÓN" no
    (todo lo que comparten es relleno genérico del sector).
    """
    compartidas = _prefijo_compartido(nombre_a, nombre_b)
    sustantivas = [w for w in compartidas if w not in PALABRAS_GENERICAS]
    return len(compartidas) >= 2 and len(sustantivas) >= 1


def agrupar_por_marca(nombres_canonicos, peso_por_nombre):
    """
    Segunda pasada de agrupación: une nombres de empresa ya depurados
    (agrupar_empresas) que comparten una marca/prefijo distintivo aunque sean
    entidades legales distintas (ej. "DSE NEIVA" / "DSE NEIVA SUR", cada
    "PROYECTO N" de ABO WIND RENOVABLES). El nombre del grupo resultante es el
    prefijo compartido, tomado con la ortografía de su miembro más frecuente.
    """
    padre = {n: n for n in nombres_canonicos}

    def encontrar(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def unir(a, b):
        ra, rb = encontrar(a), encontrar(b)
        if ra != rb:
            padre[ra] = rb

    for i, a in enumerate(nombres_canonicos):
        for b in nombres_canonicos[i + 1:]:
            if _es_marca_compartida(a, b):
                unir(a, b)

    miembros_por_raiz = defaultdict(list)
    for n in nombres_canonicos:
        miembros_por_raiz[encontrar(n)].append(n)

    renombrado = {}
    for miembros in miembros_por_raiz.values():
        if len(miembros) == 1:
            renombrado[miembros[0]] = miembros[0]
            continue
        palabras_comunes = None
        for n in miembros:
            palabras = n.split()
            if palabras_comunes is None:
                palabras_comunes = palabras
            else:
                nuevas = []
                for wa, wb in zip(palabras_comunes, palabras):
                    if _palabra_normalizada(wa) == _palabra_normalizada(wb):
                        nuevas.append(wa)
                    else:
                        break
                palabras_comunes = nuevas
        representante = max(miembros, key=lambda n: (peso_por_nombre[n], -len(n)))
        k = len(palabras_comunes)
        etiqueta = " ".join(representante.split()[:k]).strip(" .")
        for n in miembros:
            renombrado[n] = etiqueta
    return renombrado


def agrupar_empresas(proyectos):
    """
    Varias resoluciones citan la misma empresa con el nombre escrito de formas
    distintas (con/sin puntos, con coletilla de NIT, con/sin sigla societaria,
    etc.). Se unen en un solo grupo las variantes que comparten NIT y las que
    comparten nombre normalizado (mismo texto ignorando puntuación), y se
    elige como nombre canónico del grupo el más frecuente entre sus proyectos,
    para que el filtro no muestre entradas duplicadas de la misma empresa.

    Devuelve (empresa_canonica, empresas_ordenadas):
      - empresa_canonica: dict {nombre tal cual aparece en el dato -> nombre canónico}
      - empresas_ordenadas: lista de nombres canónicos, sin duplicados, ordenada
    """
    frecuencia = Counter()
    info = {}
    for p in proyectos:
        sol = p.get("solicitante")
        if not sol:
            continue
        frecuencia[sol] += 1
        if sol not in info:
            nombre_limpio = limpiar_nombre_empresa(sol)
            info[sol] = {
                "nombre_limpio": nombre_limpio,
                "nit_norm": normalizar_nit(p.get("nit")),
                "nombre_norm": normalizar_para_agrupar(nombre_limpio),
            }

    raws = list(info.keys())
    padre = {r: r for r in raws}

    def encontrar(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def unir(a, b):
        ra, rb = encontrar(a), encontrar(b)
        if ra != rb:
            padre[ra] = rb

    por_nit = defaultdict(list)
    por_nombre_norm = defaultdict(list)
    for r in raws:
        if info[r]["nit_norm"]:
            por_nit[info[r]["nit_norm"]].append(r)
        por_nombre_norm[info[r]["nombre_norm"]].append(r)

    for grupo in list(por_nit.values()) + list(por_nombre_norm.values()):
        for r in grupo[1:]:
            unir(grupo[0], r)

    miembros_por_raiz = defaultdict(list)
    for r in raws:
        miembros_por_raiz[encontrar(r)].append(r)

    empresa_canonica = {}
    peso_por_canonico = Counter()
    for miembros in miembros_por_raiz.values():
        conteo = Counter()
        for r in miembros:
            conteo[info[r]["nombre_limpio"]] += frecuencia[r]
        maximo = max(conteo.values())
        candidatos = [n for n, c in conteo.items() if c == maximo]
        canonico = min(candidatos, key=lambda n: (len(n), n))
        for r in miembros:
            empresa_canonica[r] = canonico
            peso_por_canonico[canonico] += frecuencia[r]

    nombres_canonicos = sorted(peso_por_canonico)
    renombrado = agrupar_por_marca(nombres_canonicos, peso_por_canonico)
    empresa_canonica_final = {r: renombrado[c] for r, c in empresa_canonica.items()}

    empresas_ordenadas = sorted(set(empresa_canonica_final.values()))
    return empresa_canonica_final, empresas_ordenadas


def anio_resolucion(fecha):
    if not fecha:
        return None
    m = re.search(r"\d{4}", fecha)
    return m.group(0) if m else None


def capacidad_clase(kw):
    if kw is None:
        return "desconocida"
    if kw < 500:
        return "pequena"
    if kw <= 1000:
        return "mediana"
    return "grande"


def capacidad_label(kw):
    if kw is None:
        return "Desconocida"
    if kw >= 1000:
        return f"{kw/1000:.2f} MW"
    return f"{kw:.0f} kW"


def main():
    if not os.path.exists(ENTRADA):
        print(f"No se encontró {ENTRADA}. Ejecuta primero: python extraer.py")
        sys.exit(1)

    with open(ENTRADA, encoding="utf-8") as f:
        proyectos = json.load(f)

    con_mapa = [p for p in proyectos if p.get("lat") and p.get("lon")]
    sin_mapa = [p for p in proyectos if not (p.get("lat") and p.get("lon"))]

    total_kw = sum(p.get("capacidad_kw") or 0 for p in proyectos)
    total_mw = total_kw / 1000

    departamentos = sorted(set(p["departamento"] for p in proyectos if p.get("departamento")))
    empresa_canonica, empresas = agrupar_empresas(proyectos)
    hay_sin_empresa = any(not p.get("solicitante") for p in proyectos)
    anios = sorted(set(anio_resolucion(p.get("fecha_resolucion")) for p in proyectos if anio_resolucion(p.get("fecha_resolucion"))), reverse=True)

    # Centro del mapa: Colombia
    lat_centro = 4.5
    lon_centro = -74.0
    if con_mapa:
        lat_centro = round(sum(p["lat"] for p in con_mapa) / len(con_mapa), 4)
        lon_centro = round(sum(p["lon"] for p in con_mapa) / len(con_mapa), 4)

    datos_js = json.dumps(proyectos, ensure_ascii=False)

    opciones_dept = "\n".join(
        f'<option value="{d}">{d}</option>' for d in departamentos
    )
    opciones_anio = "\n".join(
        f'<option value="{a}">{a}</option>' for a in anios
    )

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Proyectos Energéticos Colombia — Mininterior</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f4f6f9; color: #1a1a2e; }}

  header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 60%, #0f3460 100%);
    color: #fff;
    padding: 18px 28px;
    display: flex;
    align-items: center;
    gap: 24px;
    flex-wrap: wrap;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  }}
  header h1 {{ font-size: 1.25rem; font-weight: 700; letter-spacing: 0.3px; }}
  header .subtitulo {{ font-size: 0.8rem; opacity: 0.7; margin-top: 2px; }}

  .stats {{
    display: flex;
    gap: 20px;
    margin-left: auto;
    flex-wrap: wrap;
  }}
  .stat-card {{
    background: rgba(255,255,255,0.1);
    border-radius: 8px;
    padding: 8px 16px;
    text-align: center;
    min-width: 90px;
  }}
  .stat-card .num {{ font-size: 1.5rem; font-weight: 700; color: #4cc9f0; }}
  .stat-card .lbl {{ font-size: 0.7rem; opacity: 0.8; text-transform: uppercase; letter-spacing: 0.5px; }}

  #map {{
    width: 100%;
    height: 520px;
    border-bottom: 3px solid #0f3460;
  }}

  .leyenda-wrapper {{
    display: flex;
    align-items: center;
    gap: 20px;
    padding: 10px 24px;
    background: #fff;
    border-bottom: 1px solid #e0e0e0;
    font-size: 0.8rem;
    flex-wrap: wrap;
  }}
  .leyenda-titulo {{ font-weight: 600; color: #555; }}
  .leyenda-item {{ display: flex; align-items: center; gap: 6px; }}
  .dot {{
    width: 14px; height: 14px; border-radius: 50%;
    border: 2px solid rgba(0,0,0,0.25);
    display: inline-block;
  }}
  .dot.pequena  {{ background: #4cc9f0; }}
  .dot.mediana  {{ background: #4ade80; }}
  .dot.grande   {{ background: #fb923c; }}
  .dot.desconocida {{ background: #94a3b8; }}

  .controles {{
    display: flex;
    gap: 10px;
    padding: 14px 24px;
    background: #fff;
    border-bottom: 1px solid #e0e0e0;
    flex-wrap: wrap;
    align-items: center;
  }}
  .controles label {{ font-size: 0.8rem; font-weight: 600; color: #555; }}
  .controles input, .controles select {{
    padding: 7px 12px;
    border: 1.5px solid #cbd5e1;
    border-radius: 6px;
    font-size: 0.85rem;
    outline: none;
    transition: border-color 0.2s;
  }}
  .controles input:focus, .controles select:focus {{ border-color: #0f3460; }}
  .controles input {{ width: 240px; }}
  #btn-reset {{
    padding: 7px 14px;
    background: #0f3460;
    color: #fff;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.85rem;
  }}
  #btn-reset:hover {{ background: #1a4f8a; }}
  #contador {{
    margin-left: auto;
    font-size: 0.8rem;
    color: #666;
  }}

  .multiselect {{ position: relative; }}
  .multiselect-btn {{
    padding: 7px 12px;
    border: 1.5px solid #cbd5e1;
    border-radius: 6px;
    font-size: 0.85rem;
    background: #fff;
    cursor: pointer;
    min-width: 190px;
    text-align: left;
    color: #1a1a2e;
  }}
  .multiselect-btn:hover, .multiselect-btn:focus {{ border-color: #0f3460; outline: none; }}
  .multiselect-panel {{
    display: none;
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    z-index: 1000;
    background: #fff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.18);
    width: 380px;
    max-width: min(380px, calc(100vw - 48px));
    padding: 10px;
  }}
  .multiselect-panel.abierto {{ display: block; }}
  .multiselect-panel input[type="text"] {{
    width: 100%;
    padding: 6px 10px;
    border: 1.5px solid #cbd5e1;
    border-radius: 6px;
    font-size: 0.82rem;
    margin-bottom: 8px;
    box-sizing: border-box;
  }}
  .multiselect-acciones {{ display: flex; gap: 6px; margin-bottom: 8px; }}
  .multiselect-acciones button {{
    flex: 1;
    padding: 5px 8px;
    font-size: 0.75rem;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #f8fafc;
    cursor: pointer;
    color: #1a1a2e;
  }}
  .multiselect-acciones button:hover {{ background: #eef2f7; }}
  .multiselect-lista {{ max-height: 260px; overflow-y: auto; overflow-x: hidden; }}
  .multiselect-lista label {{
    display: flex;
    align-items: flex-start;
    gap: 8px;
    font-weight: 400;
    font-size: 0.8rem;
    line-height: 1.35;
    padding: 5px 4px;
    cursor: pointer;
    color: #1a1a2e;
    border-radius: 4px;
  }}
  .multiselect-lista label:hover {{ background: #f0f7ff; }}
  .multiselect-lista input[type="checkbox"] {{
    cursor: pointer;
    flex: none;
    width: 14px;
    height: 14px;
    margin: 2px 0 0;
    accent-color: #0f3460;
  }}
  .multiselect-lista label span {{
    flex: 1 1 auto;
    min-width: 0;
    overflow-wrap: anywhere;
  }}
  .multiselect-vacio {{ font-size: 0.8rem; color: #999; padding: 6px 2px; font-style: italic; }}

  .tabla-wrapper {{
    overflow-x: auto;
    padding: 0 0 40px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
    background: #fff;
  }}
  thead th {{
    background: #1a1a2e;
    color: #fff;
    padding: 11px 12px;
    text-align: left;
    font-weight: 600;
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
  }}
  thead th:hover {{ background: #0f3460; }}
  thead th::after {{ content: " ↕"; opacity: 0.4; font-size: 0.7rem; }}
  thead th.asc::after  {{ content: " ↑"; opacity: 1; }}
  thead th.desc::after {{ content: " ↓"; opacity: 1; }}

  tbody tr {{ border-bottom: 1px solid #f0f0f0; transition: background 0.15s; }}
  tbody tr:hover {{ background: #f0f7ff; }}
  tbody td {{ padding: 9px 12px; vertical-align: top; }}

  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 0.72rem;
    font-weight: 600;
    white-space: nowrap;
  }}
  .badge.pequena  {{ background: #e0f7fe; color: #0077a8; }}
  .badge.mediana  {{ background: #dcfce7; color: #166534; }}
  .badge.grande   {{ background: #fff3e0; color: #b45309; }}
  .badge.desconocida {{ background: #f1f5f9; color: #64748b; }}

  .nombre-proyecto {{ font-weight: 600; max-width: 260px; line-height: 1.3; }}
  .link-pdf {{
    color: #0f3460;
    text-decoration: none;
    font-size: 0.75rem;
    display: inline-block;
    margin-top: 4px;
  }}
  .link-pdf:hover {{ text-decoration: underline; }}
  .sin-datos {{ color: #aaa; font-style: italic; }}

  .popup-nombre {{ font-weight: 700; font-size: 0.95rem; margin-bottom: 6px; color: #1a1a2e; max-width: 280px; line-height: 1.3; }}
  .popup-fila {{ font-size: 0.82rem; margin-bottom: 3px; }}
  .popup-fila b {{ color: #0f3460; }}
  .popup-link {{ display: block; margin-top: 8px; color: #0f3460; font-size: 0.8rem; }}
  .popup-badge {{ font-size: 0.75rem; font-weight: 700; padding: 2px 8px; border-radius: 10px; }}

  #sin-coords {{
    padding: 10px 24px;
    font-size: 0.8rem;
    color: #7c3aed;
    background: #f5f3ff;
    border-bottom: 1px solid #ddd6fe;
    display: none;
  }}
</style>
</head>
<body>

<header>
  <div>
    <h1>⚡ Proyectos Energéticos — Colombia</h1>
    <div class="subtitulo">Fuente: Resoluciones de Consulta Previa · Ministerio del Interior</div>
  </div>
  <div class="stats">
    <div class="stat-card">
      <div class="num" id="stat-total">{len(proyectos)}</div>
      <div class="lbl">Proyectos</div>
    </div>
    <div class="stat-card">
      <div class="num">{total_mw:.1f}</div>
      <div class="lbl">MW Total</div>
    </div>
    <div class="stat-card">
      <div class="num">{len(con_mapa)}</div>
      <div class="lbl">En Mapa</div>
    </div>
    <div class="stat-card">
      <div class="num">{len(departamentos)}</div>
      <div class="lbl">Departamentos</div>
    </div>
  </div>
</header>

<div id="map"></div>

<div class="leyenda-wrapper">
  <span class="leyenda-titulo">Capacidad:</span>
  <div class="leyenda-item"><span class="dot pequena"></span> &lt; 500 kW</div>
  <div class="leyenda-item"><span class="dot mediana"></span> 500 kW – 1 MW</div>
  <div class="leyenda-item"><span class="dot grande"></span> &gt; 1 MW</div>
  <div class="leyenda-item"><span class="dot desconocida"></span> Desconocida</div>
</div>

<div class="controles">
  <label>Buscar:</label>
  <input type="text" id="buscar" placeholder="nombre, municipio, empresa...">
  <label>Departamento:</label>
  <select id="filtro-dept">
    <option value="">Todos</option>
    {opciones_dept}
  </select>
  <label>Año Resolución:</label>
  <select id="filtro-anio">
    <option value="">Todos</option>
    {opciones_anio}
  </select>
  <label>Empresa:</label>
  <div class="multiselect" id="multiselect-emp">
    <button type="button" id="btn-emp" class="multiselect-btn">Todas las empresas</button>
    <div class="multiselect-panel" id="panel-emp">
      <input type="text" id="buscar-emp" placeholder="Buscar empresa...">
      <div class="multiselect-acciones">
        <button type="button" id="emp-todas">Marcar todas</button>
        <button type="button" id="emp-ninguna">Desmarcar todas</button>
      </div>
      <div class="multiselect-lista" id="lista-emp"></div>
    </div>
  </div>
  <button id="btn-reset">Limpiar filtros</button>
  <span id="contador"></span>
</div>

<div id="sin-coords"></div>

<div class="tabla-wrapper">
<table id="tabla">
  <thead>
    <tr>
      <th data-col="nombre">Proyecto</th>
      <th data-col="capacidad_kw">Capacidad</th>
      <th data-col="municipio">Municipio</th>
      <th data-col="departamento">Departamento</th>
      <th data-col="solicitante">Empresa</th>
      <th data-col="fecha_resolucion">Fecha Resolución</th>
      <th data-col="resolucion">Resolución</th>
      <th style="width:60px;text-align:center">PDF</th>
    </tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>
</div>

<script>
const DATOS = {datos_js};

const SIN_EMPRESA = "__sin_empresa__";
const EMPRESAS = {json.dumps(empresas, ensure_ascii=False)}{' .concat([SIN_EMPRESA])' if hay_sin_empresa else ''};
const EMPRESA_CANONICA = {json.dumps(empresa_canonica, ensure_ascii=False)};
let empresasSeleccionadas = new Set(EMPRESAS);

const COLORES = {{
  pequena:    {{ fill: "#4cc9f0", border: "#0077a8" }},
  mediana:    {{ fill: "#4ade80", border: "#166534" }},
  grande:     {{ fill: "#fb923c", border: "#b45309" }},
  desconocida:{{ fill: "#94a3b8", border: "#475569" }},
}};

function capClase(kw) {{
  if (kw == null) return "desconocida";
  if (kw < 500)  return "pequena";
  if (kw <= 1000) return "mediana";
  return "grande";
}}
function capLabel(kw) {{
  if (kw == null) return "—";
  if (kw >= 1000) return (kw/1000).toFixed(2) + " MW";
  return kw.toFixed(0) + " kW";
}}
function val(v) {{ return v || '<span class="sin-datos">—</span>'; }}
function anioResolucion(fecha) {{
  if (!fecha) return null;
  const m = fecha.match(/\\d{{4}}/);
  return m ? m[0] : null;
}}

// ── Mapa ──────────────────────────────────────────────────────────────────
const map = L.map("map").setView([{lat_centro}, {lon_centro}], 6);

L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
  attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a>',
  maxZoom: 19,
}}).addTo(map);

const capas = {{
  "Mapa": L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{ attribution: '© OpenStreetMap', maxZoom: 19 }}),
  "Satélite": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}", {{ attribution: '© Esri', maxZoom: 19 }}),
}};
L.control.layers(capas).addTo(map);
capas["Mapa"].addTo(map);

function crearIcono(clase) {{
  const c = COLORES[clase];
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="36" viewBox="0 0 28 36">
    <path d="M14 0C6.27 0 0 6.27 0 14c0 9.33 14 22 14 22S28 23.33 28 14C28 6.27 21.73 0 14 0z"
          fill="${{c.fill}}" stroke="${{c.border}}" stroke-width="2"/>
    <circle cx="14" cy="14" r="6" fill="white" opacity="0.9"/>
  </svg>`;
  return L.divIcon({{
    html: svg,
    className: "",
    iconSize: [28, 36],
    iconAnchor: [14, 36],
    popupAnchor: [0, -36],
  }});
}}

const marcadores = {{}};

DATOS.forEach((p, i) => {{
  if (!p.lat || !p.lon) return;
  const clase = capClase(p.capacidad_kw);
  const m = L.marker([p.lat, p.lon], {{ icon: crearIcono(clase) }});

  const badgeColor = {{
    pequena:"background:#e0f7fe;color:#0077a8",
    mediana:"background:#dcfce7;color:#166534",
    grande:"background:#fff3e0;color:#b45309",
    desconocida:"background:#f1f5f9;color:#64748b"
  }}[clase];

  m.bindPopup(`
    <div class="popup-nombre">${{p.nombre || "Sin nombre"}}</div>
    <span class="popup-badge" style="${{badgeColor}}">${{capLabel(p.capacidad_kw)}}</span>
    <br><br>
    <div class="popup-fila"><b>Municipio:</b> ${{p.municipio || "—"}}, ${{p.departamento || "—"}}</div>
    <div class="popup-fila"><b>Empresa:</b> ${{p.solicitante || "—"}}</div>
    <div class="popup-fila"><b>Resolución:</b> ${{p.fecha_resolucion || "—"}}</div>
    <div class="popup-fila"><b>Resolución:</b> ${{p.resolucion || "—"}} · ${{p.fecha_resolucion || "—"}}</div>
    ${{p.pdf_url ? `<a class="popup-link" href="${{p.pdf_url}}" target="_blank">📄 Ver resolución PDF</a>` : ""}}
  `, {{ maxWidth: 320 }});

  m.addTo(map);
  marcadores[i] = m;
}});

// ── Tabla ──────────────────────────────────────────────────────────────────
let datosActuales = [...DATOS];
let colOrden = null, dirOrden = 1;

function renderTabla(datos) {{
  const tbody = document.getElementById("tbody");
  tbody.innerHTML = "";
  datos.forEach((p, i) => {{
    const clase = capClase(p.capacidad_kw);
    const idxOriginal = DATOS.indexOf(p);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <div class="nombre-proyecto">${{p.nombre || '<span class="sin-datos">Sin nombre</span>'}}</div>
      </td>
      <td><span class="badge ${{clase}}">${{capLabel(p.capacidad_kw)}}</span></td>
      <td>${{val(p.municipio)}}</td>
      <td>${{val(p.departamento)}}</td>
      <td>${{val(p.solicitante)}}</td>
      <td>${{val(p.fecha_resolucion)}}</td>
      <td>${{val(p.resolucion)}}</td>
      <td style="text-align:center">
        ${{p.pdf_url
          ? `<a href="${{p.pdf_url}}" target="_blank" title="Ver resolución PDF"
               style="display:inline-block;padding:4px 8px;background:#0f3460;color:#fff;border-radius:5px;font-size:0.75rem;text-decoration:none;white-space:nowrap">
               📄 PDF</a>`
          : '<span class="sin-datos">—</span>'}}
      </td>
    `;
    if (p.lat && p.lon) {{
      tr.style.cursor = "pointer";
      tr.title = "Ver en mapa";
      tr.addEventListener("click", () => {{
        map.setView([p.lat, p.lon], 13);
        if (marcadores[idxOriginal]) marcadores[idxOriginal].openPopup();
        window.scrollTo({{ top: 0, behavior: "smooth" }});
      }});
    }}
    tbody.appendChild(tr);
  }});
  document.getElementById("contador").textContent = `${{datos.length}} de ${{DATOS.length}} proyectos`;
  actualizarMarcadores(datos);
}}

function actualizarMarcadores(datos) {{
  DATOS.forEach((p, i) => {{
    if (!marcadores[i]) return;
    if (datos.includes(p)) {{
      marcadores[i].setOpacity(1);
    }} else {{
      marcadores[i].setOpacity(0.15);
    }}
  }});
}}

// ── Filtros ────────────────────────────────────────────────────────────────
function aplicarFiltros() {{
  const q = document.getElementById("buscar").value.toLowerCase();
  const dept = document.getElementById("filtro-dept").value;
  const anio = document.getElementById("filtro-anio").value;

  let resultado = DATOS.filter(p => {{
    const texto = [p.nombre, p.municipio, p.departamento, p.solicitante, p.resolucion]
      .filter(Boolean).join(" ").toLowerCase();
    const okQ = !q || texto.includes(q);
    const okD = !dept || p.departamento === dept;
    const okA = !anio || anioResolucion(p.fecha_resolucion) === anio;
    const okE = empresasSeleccionadas.has(EMPRESA_CANONICA[p.solicitante] || SIN_EMPRESA);
    return okQ && okD && okA && okE;
  }});

  if (colOrden) {{
    resultado.sort((a, b) => {{
      const va = a[colOrden] ?? "";
      const vb = b[colOrden] ?? "";
      if (typeof va === "number") return (va - vb) * dirOrden;
      return String(va).localeCompare(String(vb)) * dirOrden;
    }});
  }}

  datosActuales = resultado;
  renderTabla(resultado);
}}

document.getElementById("buscar").addEventListener("input", aplicarFiltros);
document.getElementById("filtro-dept").addEventListener("change", aplicarFiltros);
document.getElementById("filtro-anio").addEventListener("change", aplicarFiltros);

// ── Multiselect de empresas ─────────────────────────────────────────────────
function nombreEmpresa(e) {{
  if (e === SIN_EMPRESA) return "(Sin empresa)";
  return e;
}}

function renderListaEmpresas(filtro = "") {{
  const cont = document.getElementById("lista-emp");
  const q = filtro.toLowerCase();
  cont.innerHTML = "";
  const visibles = EMPRESAS.filter(e => nombreEmpresa(e).toLowerCase().includes(q));
  if (visibles.length === 0) {{
    cont.innerHTML = '<div class="multiselect-vacio">Sin resultados</div>';
    return;
  }}
  visibles.forEach(e => {{
    const label = document.createElement("label");
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.checked = empresasSeleccionadas.has(e);
    chk.addEventListener("change", () => {{
      if (chk.checked) empresasSeleccionadas.add(e); else empresasSeleccionadas.delete(e);
      actualizarBotonEmp();
      aplicarFiltros();
    }});
    const span = document.createElement("span");
    span.textContent = nombreEmpresa(e);
    label.appendChild(chk);
    label.appendChild(span);
    cont.appendChild(label);
  }});
}}

function actualizarBotonEmp() {{
  const btn = document.getElementById("btn-emp");
  const n = empresasSeleccionadas.size;
  const total = EMPRESAS.length;
  if (n === total) btn.textContent = "Todas las empresas";
  else if (n === 0) btn.textContent = "Ninguna empresa";
  else btn.textContent = `${{n}} de ${{total}} empresas`;
}}

renderListaEmpresas();
actualizarBotonEmp();

document.getElementById("btn-emp").addEventListener("click", (e) => {{
  e.stopPropagation();
  document.getElementById("panel-emp").classList.toggle("abierto");
}});
document.getElementById("buscar-emp").addEventListener("input", (e) => renderListaEmpresas(e.target.value));
document.getElementById("emp-todas").addEventListener("click", () => {{
  empresasSeleccionadas = new Set(EMPRESAS);
  renderListaEmpresas(document.getElementById("buscar-emp").value);
  actualizarBotonEmp();
  aplicarFiltros();
}});
document.getElementById("emp-ninguna").addEventListener("click", () => {{
  empresasSeleccionadas = new Set();
  renderListaEmpresas(document.getElementById("buscar-emp").value);
  actualizarBotonEmp();
  aplicarFiltros();
}});
document.addEventListener("click", (e) => {{
  if (!document.getElementById("multiselect-emp").contains(e.target)) {{
    document.getElementById("panel-emp").classList.remove("abierto");
  }}
}});

document.getElementById("btn-reset").addEventListener("click", () => {{
  document.getElementById("buscar").value = "";
  document.getElementById("filtro-dept").value = "";
  document.getElementById("filtro-anio").value = "";
  document.getElementById("buscar-emp").value = "";
  empresasSeleccionadas = new Set(EMPRESAS);
  renderListaEmpresas();
  actualizarBotonEmp();
  colOrden = null; dirOrden = 1;
  document.querySelectorAll("thead th").forEach(th => th.classList.remove("asc","desc"));
  aplicarFiltros();
}});

// ── Ordenar columnas ───────────────────────────────────────────────────────
document.querySelectorAll("thead th").forEach(th => {{
  th.addEventListener("click", () => {{
    const col = th.dataset.col;
    if (colOrden === col) {{
      dirOrden *= -1;
      th.classList.toggle("asc", dirOrden === 1);
      th.classList.toggle("desc", dirOrden === -1);
    }} else {{
      document.querySelectorAll("thead th").forEach(t => t.classList.remove("asc","desc"));
      colOrden = col;
      dirOrden = 1;
      th.classList.add("asc");
    }}
    aplicarFiltros();
  }});
}});

// ── Alerta proyectos sin mapa ──────────────────────────────────────────────
const sinCoords = DATOS.filter(p => !p.lat || !p.lon);
if (sinCoords.length > 0) {{
  const div = document.getElementById("sin-coords");
  div.style.display = "block";
  div.textContent = `⚠️ ${{sinCoords.length}} proyecto(s) no tienen coordenadas y no aparecen en el mapa (sí aparecen en la tabla).`;
}}

// Render inicial
aplicarFiltros();
</script>
</body>
</html>"""

    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✓ {SALIDA} generado ({len(proyectos)} proyectos, {len(con_mapa)} en mapa)")
    print(f"  Abre el archivo en tu navegador: {os.path.abspath(SALIDA)}")


if __name__ == "__main__":
    main()
