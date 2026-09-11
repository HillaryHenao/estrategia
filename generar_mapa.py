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


# Erratas puntuales confirmadas en el texto de alguna resolución (no una regla
# general de fuzzy-matching, que arriesgaría fusionar empresas distintas por
# error): se corrigen palabra por palabra antes de agrupar.
_ERRATAS_CONOCIDAS = {
    "ENERIGIA": "ENERGIA",  # "ERCO ENERIGIA S.A.S" -> "ERCO ENERGIA S.A.S"
}


def corregir_erratas_conocidas(nombre):
    palabras = nombre.split(" ")
    corregidas = [_ERRATAS_CONOCIDAS.get(p.upper(), p) for p in palabras]
    return " ".join(corregidas)


# Los 33 departamentos oficiales de Colombia (32 + Bogotá D.C.), en la
# ortografía que se muestra en el filtro y en la tabla.
DEPARTAMENTOS_OFICIALES = [
    "Amazonas", "Antioquia", "Arauca", "Atlántico", "Bogotá D.C.", "Bolívar",
    "Boyacá", "Caldas", "Caquetá", "Casanare", "Cauca", "Cesar", "Chocó",
    "Córdoba", "Cundinamarca", "Guainía", "Guaviare", "Huila", "La Guajira",
    "Magdalena", "Meta", "Nariño", "Norte de Santander", "Putumayo",
    "Quindío", "Risaralda", "San Andrés y Providencia", "Santander", "Sucre",
    "Tolima", "Valle del Cauca", "Vaupés", "Vichada",
]

# Ruido de extracción de PDF que a veces queda pegado al final del nombre del
# departamento (colillas de otra frase, acotaciones entre paréntesis, etc.).
_RUIDO_DEPTO_RE = re.compile(
    r"\s*(identificad[oa].*|localizad[oa].*|ubicad[oa].*|\(.*\)|:.*)$",
    re.IGNORECASE,
)

# Variantes que no se pueden resolver por normalización de tildes/puntuación
# (typos u otro texto pegado por error) -> forma normalizada del departamento
# oficial al que corresponden. Confirmado contra la fuente_url de cada caso.
_DEPTO_ERRATAS_CONOCIDAS = {
    "LA BOLIVAR": "BOLIVAR",  # fuente_url: "...departamento-bolivar-municipios-san-estanislao"
    "GUAJIRA": "LA GUAJIRA",  # forma corta usual de "La Guajira"
    "VALLE": "VALLE DEL CAUCA",  # forma corta usual de "Valle del Cauca"
}

_DEPTOS_NORM_A_OFICIAL = {
    _quitar_tildes(d.upper()).replace(".", "").strip(): d for d in DEPARTAMENTOS_OFICIALES
}


# Casos donde la extracción del PDF capturó un fragmento de oración en vez del
# nombre de la empresa solicitante. Se corrigen al nombre real cuando es
# identificable, o se tratan como "sin empresa" (None) cuando no lo es, para
# no ensuciar el filtro ni la tabla con basura. Claves normalizadas (sin
# tildes/puntuación) para no depender de cómo vino cada PDF.
_SOLICITANTE_FRAGMENTOS_CONOCIDOS = {
    "PROMOTORA DEL PROYECTO ES LA SOCIEDAD COMERCIAL ABO WIND RENOVABLES PROYECTO TRES SAS ESP":
        "ABO WIND RENOVABLES PROYECTO TRES S.A.S. E.S.P",
    "CUANDO SE ESTABLECIO EN LA REGION TENIENDO EN CUENTA LA CONDICION DE LUGAR ESPECIAL DE ALOJAMIENTO (LEA) QUE": None,
    "EN UN ESPACIO Y": None,
    "EN UN ESPACIO Y TIEMPO": None,
    "TIENE BAJO OPERACION 109 MW EN PLANTAS SOLARES FOTOVOLTAICAS": None,
}


def corregir_solicitante(sol):
    """Corrige o descarta fragmentos de oración mal capturados como si fueran
    el nombre de la empresa (ver _SOLICITANTE_FRAGMENTOS_CONOCIDOS)."""
    if not sol:
        return sol
    clave = normalizar_para_agrupar(limpiar_nombre_empresa(sol))
    if clave in _SOLICITANTE_FRAGMENTOS_CONOCIDOS:
        return _SOLICITANTE_FRAGMENTOS_CONOCIDOS[clave]
    return sol


def canonicalizar_departamento(nombre):
    """Corrige tildes, puntuación de sobra y ruido de extracción para que
    'Atlantico' / 'Atlántico' / 'Atlántico:' caigan en un solo 'Atlántico',
    igual que ocurría con los nombres de empresa."""
    if not nombre:
        return nombre
    limpio = _RUIDO_DEPTO_RE.sub("", nombre.strip()).strip(" .\"'“”")
    norm = _quitar_tildes(limpio.upper()).replace(".", "").strip()
    norm = _DEPTO_ERRATAS_CONOCIDAS.get(norm, norm)
    return _DEPTOS_NORM_A_OFICIAL.get(norm, limpio)


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
            nombre_limpio = corregir_erratas_conocidas(limpiar_nombre_empresa(sol))
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


_RUIDO_SLUG_RE = re.compile(r"-ubicad[oa]-en-el-municipio-de-.*$", re.IGNORECASE)
_CONECTORES_TITULO = {"de", "del", "la", "las", "el", "los", "en", "y", "a"}


def nombre_desde_url(fuente_url):
    """Deriva un título legible del slug de la URL cuando el PDF no traía uno,
    ej. '.../fotovoltaico-solar-melgar-9-9-mw-ubicado-en-el-municipio-de-melgar-
    departamento-del-tolima/' -> 'Fotovoltaico Solar Melgar 9.9 MW' (se recorta
    la cola de municipio/departamento porque ya se muestra en su propia columna)."""
    if not fuente_url:
        return None
    slug = fuente_url.rstrip("/").rsplit("/", 1)[-1]
    slug = _RUIDO_SLUG_RE.sub("", slug)
    slug = re.sub(r"(\d+)-(\d+)-mw\b", r"\1.\2 mw", slug)
    slug = re.sub(r"(\d+)-(\d+)-kw\b", r"\1.\2 kw", slug)
    slug = re.sub(r"(?<=\d)-mw\b", " mw", slug)
    slug = re.sub(r"(?<=\d)-kw\b", " kw", slug)
    palabras = [w for w in re.split(r"[-\s]+", slug) if w]
    if not palabras:
        return None
    resultado = []
    for w in palabras:
        wl = w.lower()
        if wl in ("mw", "kw"):
            resultado.append(w.upper())
        elif wl in _CONECTORES_TITULO and resultado:
            resultado.append(wl)
        else:
            resultado.append(w.capitalize())
    return " ".join(resultado)


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


def escapar(s):
    """Escape HTML mínimo para texto libre (nombres de empresa/departamento)."""
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def contar_por_empresa(proyectos, empresa_canonica):
    """Nº de solicitudes por empresa (nombre canónico) dentro de una lista de proyectos."""
    c = Counter()
    for p in proyectos:
        sol = p.get("solicitante")
        nombre = empresa_canonica.get(sol) if sol else None
        c[nombre or "(Sin empresa)"] += 1
    return c.most_common()


def render_ranking(pares, max_n=15, color="#0f3460"):
    """Lista de barras horizontales 'nombre — cantidad', escalada al máximo del propio ranking."""
    top = pares[:max_n]
    if not top:
        return '<div class="analisis-vacio">Sin datos</div>'
    maximo = max(c for _, c in top)
    filas = []
    for nombre, cnt in top:
        pct = round(cnt / maximo * 100, 1) if maximo else 0
        filas.append(f'''
          <div class="barra-fila">
            <div class="barra-label" title="{escapar(nombre)}">{escapar(nombre)}</div>
            <div class="barra-track"><div class="barra-fill" style="width:{pct}%;background:{color}"></div></div>
            <div class="barra-valor">{cnt}</div>
          </div>''')
    return "\n".join(filas)


def render_zonas(pares, max_n=10):
    return render_ranking(pares, max_n=max_n, color="#7c3aed")


def render_por_anio(anios_clase, anios_ordenados):
    """Barras apiladas por año: proporción pequeña/mediana/grande/desconocida, ancho ~ total del año."""
    colores = {"pequena": "#4cc9f0", "mediana": "#4ade80", "grande": "#fb923c", "desconocida": "#94a3b8"}
    totales = {a: sum(anios_clase[a].values()) for a in anios_ordenados}
    max_total = max(totales.values()) if totales else 1
    filas = []
    for a in anios_ordenados:
        c = anios_clase[a]
        total = totales[a] or 1
        ancho_track = round(totales[a] / max_total * 100, 1) if max_total else 0
        segmentos = "".join(
            f'<div class="segmento" style="width:{c.get(clase, 0) / total * 100:.1f}%;background:{col}" '
            f'title="{clase}: {c.get(clase, 0)}"></div>'
            for clase, col in colores.items() if c.get(clase, 0) > 0
        )
        filas.append(f'''
          <div class="anio-fila" data-anio="{escapar(a)}">
            <div class="anio-label">{escapar(a)}</div>
            <div class="anio-track" style="width:{ancho_track}%">{segmentos}</div>
            <div class="anio-valor">{totales[a]}</div>
          </div>''')
    return "\n".join(filas)


# Rampa secuencial (un solo tono, claro→oscuro = magnitud), tomada de la
# misma familia de azul que ya usa el sitio (fondo "en-mapa" claro / header
# oscuro), para que el heatmap combine con el resto de la página.
_HEATMAP_CLARO = (224, 247, 254)   # #e0f7fe
_HEATMAP_OSCURO = (15, 52, 96)     # #0f3460


def color_heatmap(ratio):
    """ratio en [0,1] -> color de fondo de la celda (interpolación lineal claro→oscuro)."""
    r = round(_HEATMAP_CLARO[0] + (_HEATMAP_OSCURO[0] - _HEATMAP_CLARO[0]) * ratio)
    g = round(_HEATMAP_CLARO[1] + (_HEATMAP_OSCURO[1] - _HEATMAP_CLARO[1]) * ratio)
    b = round(_HEATMAP_CLARO[2] + (_HEATMAP_OSCURO[2] - _HEATMAP_CLARO[2]) * ratio)
    return f"rgb({r},{g},{b})"


def texto_heatmap(ratio):
    """Texto blanco sobre celdas oscuras, tinta oscura sobre celdas claras (contraste)."""
    return "#ffffff" if ratio > 0.55 else "#1a1a2e"


def render_matriz_empresa_depto(proyectos, empresa_canonica, top_empresas):
    """
    Heatmap departamento × empresa (top N fijo): color = magnitud (secuencial,
    un solo tono, claro→oscuro), número = valor exacto en cada celda. Responde
    preguntas tipo "¿cuántas tiene SUNTACC en Cesar / Bolívar / etc.?". Filas =
    departamentos con al menos una solicitud de alguna empresa del top,
    ordenados por total desc.
    """
    conteo = defaultdict(lambda: defaultdict(int))
    total_por_depto = Counter()
    total_por_empresa = Counter()
    for p in proyectos:
        sol = p.get("solicitante")
        nombre = empresa_canonica.get(sol) if sol else None
        if nombre not in top_empresas:
            continue
        depto = p.get("departamento") or "(Sin departamento)"
        conteo[depto][nombre] += 1
        total_por_depto[depto] += 1
        total_por_empresa[nombre] += 1

    deptos_ordenados = [d for d, _ in total_por_depto.most_common()]
    if not deptos_ordenados:
        return '<div class="analisis-vacio">Sin datos</div>'

    max_celda = max(
        (conteo[d][e] for d in deptos_ordenados for e in top_empresas),
        default=0,
    ) or 1

    encabezados = "".join(
        f'<div class="heatmap-col-header" title="{escapar(e)}">{escapar(e)}</div>' for e in top_empresas
    )

    filas_html = []
    for depto in deptos_ordenados:
        filas_html.append(f'<div class="heatmap-row-header" title="{escapar(depto)}">{escapar(depto)}</div>')
        for e in top_empresas:
            n = conteo[depto][e]
            ratio = n / max_celda if n else 0
            estilo = f"background:{color_heatmap(ratio)};color:{texto_heatmap(ratio)}" if n else ""
            clase = "heatmap-cell" + ("" if n else " heatmap-cell-vacia")
            filas_html.append(
                f'<div class="{clase}" style="{estilo}" '
                f'title="{escapar(e)} · {escapar(depto)}: {n} solicitud{"es" if n != 1 else ""}">{n or "–"}</div>'
            )
        filas_html.append(f'<div class="heatmap-cell heatmap-total-cell">{total_por_depto[depto]}</div>')

    fila_total = "".join(
        f'<div class="heatmap-cell heatmap-total-cell">{total_por_empresa[e]}</div>' for e in top_empresas
    )

    n_cols = len(top_empresas)
    return f'''
    <div class="heatmap-wrapper">
      <div class="heatmap-escala">
        <span>Menos</span>
        <span class="heatmap-escala-barra"></span>
        <span>Más</span>
      </div>
      <div class="heatmap-grid" style="grid-template-columns: minmax(130px,auto) repeat({n_cols}, minmax(64px,1fr)) minmax(56px,auto);">
        <div class="heatmap-corner"></div>
        {encabezados}
        <div class="heatmap-col-header heatmap-total-header">Total</div>
        {"".join(filas_html)}
        <div class="heatmap-row-header heatmap-total-header">Total</div>
        {fila_total}
        <div class="heatmap-cell heatmap-total-cell"></div>
      </div>
    </div>'''


def main():
    if not os.path.exists(ENTRADA):
        print(f"No se encontró {ENTRADA}. Ejecuta primero: python extraer.py")
        sys.exit(1)

    with open(ENTRADA, encoding="utf-8") as f:
        proyectos = json.load(f)

    for p in proyectos:
        if p.get("departamento"):
            p["departamento"] = canonicalizar_departamento(p["departamento"])
        if p.get("solicitante"):
            p["solicitante"] = corregir_solicitante(p["solicitante"])
        if not p.get("nombre"):
            p["nombre_inferido"] = nombre_desde_url(p.get("fuente_url"))

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

    # ── Análisis: minigranjas (mediana escala, 0.9-1 MW) vs gran escala (>1 MW) ──
    proyectos_mediana = [p for p in proyectos if capacidad_clase(p.get("capacidad_kw")) == "mediana"]
    proyectos_grande = [p for p in proyectos if capacidad_clase(p.get("capacidad_kw")) == "grande"]

    ranking_empresas_mediana = contar_por_empresa(proyectos_mediana, empresa_canonica)
    ranking_empresas_grande = contar_por_empresa(proyectos_grande, empresa_canonica)

    ranking_zonas = Counter(
        p.get("departamento") or "(Sin departamento)" for p in proyectos
    ).most_common(10)

    anios_clase = defaultdict(Counter)
    for p in proyectos:
        anio = anio_resolucion(p.get("fecha_resolucion")) or "Sin fecha"
        anios_clase[anio][capacidad_clase(p.get("capacidad_kw"))] += 1
    anios_ordenados = sorted(anios_clase.keys(), key=lambda a: (a == "Sin fecha", a))

    # ── Matriz empresa (top N, fijo por total general) × departamento ──
    TOP_N_EMPRESAS_MATRIZ = 8
    ranking_empresas_general = contar_por_empresa(proyectos, empresa_canonica)
    top_empresas_matriz = [
        nombre for nombre, _ in ranking_empresas_general
        if nombre != "(Sin empresa)"
    ][:TOP_N_EMPRESAS_MATRIZ]
    matriz_empresa_depto_html = render_matriz_empresa_depto(proyectos, empresa_canonica, top_empresas_matriz)

    opciones_anio = "\n".join(
        f'<option value="{a}">{a}</option>' for a in anios
    )

    analisis_html = f"""
<section class="analisis">
  <div class="analisis-header">
    <h2 class="analisis-titulo">📊 Sección de Análisis</h2>
    <div class="analisis-filtro">
      <label for="analisis-anio">Año de resolución:</label>
      <select id="analisis-anio">
        <option value="">Todos</option>
        {opciones_anio}
      </select>
    </div>
  </div>
  <div class="analisis-grid">
    <div class="analisis-panel">
      <h3>Minigranjas / Mediana escala <span class="analisis-sub" id="analisis-mediana-count">(500 kW – 1 MW · {len(proyectos_mediana)} solicitudes)</span></h3>
      <p class="analisis-desc">Nº de solicitudes por empresa</p>
      <div id="analisis-mediana-ranking" data-color="#4ade80">
      {render_ranking(ranking_empresas_mediana, color="#4ade80")}
      </div>
    </div>
    <div class="analisis-panel">
      <h3>Gran escala <span class="analisis-sub" id="analisis-grande-count">(&gt; 1 MW · {len(proyectos_grande)} solicitudes)</span></h3>
      <p class="analisis-desc">Nº de solicitudes por empresa</p>
      <div id="analisis-grande-ranking" data-color="#fb923c">
      {render_ranking(ranking_empresas_grande, color="#fb923c")}
      </div>
    </div>
    <div class="analisis-panel">
      <h3>Zonas principales <span class="analisis-sub" id="analisis-zonas-count">(Top 10 departamentos · {len(proyectos)} solicitudes)</span></h3>
      <p class="analisis-desc">Nº de solicitudes por departamento</p>
      <div id="analisis-zonas-ranking" data-color="#7c3aed">
      {render_zonas(ranking_zonas)}
      </div>
    </div>
    <div class="analisis-panel analisis-panel-ancho">
      <h3>Por año de resolución <span class="analisis-sub">(el año seleccionado arriba se resalta)</span></h3>
      <p class="analisis-desc">
        <span class="leyenda-item"><span class="dot pequena"></span> Pequeña</span>
        <span class="leyenda-item"><span class="dot mediana"></span> Mediana / Minigranja</span>
        <span class="leyenda-item"><span class="dot grande"></span> Grande</span>
        <span class="leyenda-item"><span class="dot desconocida"></span> Desconocida</span>
      </p>
      <div id="analisis-por-anio">
      {render_por_anio(anios_clase, anios_ordenados)}
      </div>
    </div>
    <div class="analisis-panel analisis-panel-ancho">
      <h3>Distribución por empresa y departamento <span class="analisis-sub" id="analisis-matriz-count">(Top {TOP_N_EMPRESAS_MATRIZ} empresas · {len(proyectos)} solicitudes)</span></h3>
      <p class="analisis-desc">Nº de solicitudes por departamento, para cada una de las empresas con más solicitudes en total</p>
      <div id="analisis-matriz">
      {matriz_empresa_depto_html}
      </div>
    </div>
  </div>
</section>
"""

    datos_js = json.dumps(proyectos, ensure_ascii=False)

    opciones_dept = "\n".join(
        f'<option value="{d}">{d}</option>' for d in departamentos
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

  .leyenda-item.leyenda-toggle {{
    cursor: pointer;
    user-select: none;
    border: none;
    background: none;
    font: inherit;
    padding: 4px 6px;
    border-radius: 6px;
    color: #1a1a2e;
    transition: background 0.15s, opacity 0.15s;
  }}
  .leyenda-item.leyenda-toggle:hover {{ background: #f0f7ff; }}
  .leyenda-item.leyenda-toggle.inactivo {{ opacity: 0.45; }}
  .leyenda-item.leyenda-toggle.inactivo .dot {{
    background: #d1d5db !important;
    border-color: #cbd5e1 !important;
  }}
  .leyenda-hint {{ font-size: 0.72rem; color: #999; font-style: italic; }}

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
  .badge.en-mapa  {{ background: #e0f2fe; color: #0369a1; }}
  .badge.sin-mapa {{ background: #fef3c7; color: #92400e; }}

  .nombre-proyecto {{ font-weight: 600; max-width: 260px; line-height: 1.3; }}
  .nombre-inferido-marca {{ color: #b45309; font-weight: 400; cursor: help; }}
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

  .analisis {{
    padding: 20px 24px 8px;
    background: #fff;
    border-bottom: 1px solid #e0e0e0;
  }}
  .analisis-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 14px;
  }}
  .analisis-titulo {{ font-size: 1.05rem; color: #1a1a2e; }}
  .analisis-filtro {{ display: flex; align-items: center; gap: 8px; font-size: 0.82rem; color: #334155; }}
  .analisis-filtro label {{ font-weight: 600; }}
  .analisis-filtro select {{
    padding: 6px 10px;
    border: 1.5px solid #cbd5e1;
    border-radius: 6px;
    font-size: 0.82rem;
    outline: none;
  }}
  .analisis-filtro select:focus {{ border-color: #0f3460; }}
  .analisis-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 18px;
  }}
  .analisis-panel {{
    background: #f8fafc;
    border: 1px solid #e5e9f0;
    border-radius: 10px;
    padding: 14px 16px 16px;
  }}
  .analisis-panel-ancho {{ grid-column: 1 / -1; }}
  .analisis-panel h3 {{ font-size: 0.9rem; color: #1a1a2e; margin-bottom: 2px; }}
  .analisis-sub {{ font-weight: 400; font-size: 0.78rem; color: #64748b; }}
  .analisis-desc {{ font-size: 0.75rem; color: #64748b; margin-bottom: 10px; display: flex; gap: 14px; flex-wrap: wrap; }}
  .analisis-vacio {{ font-size: 0.8rem; color: #999; font-style: italic; }}

  .barra-fila {{
    display: grid;
    grid-template-columns: minmax(90px, 1fr) 3fr auto;
    align-items: center;
    gap: 8px;
    padding: 3px 0;
    font-size: 0.78rem;
  }}
  .barra-label {{ color: #334155; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .barra-track {{ background: #e5e9f0; border-radius: 4px; height: 10px; overflow: hidden; }}
  .barra-fill {{ height: 100%; border-radius: 4px; }}
  .barra-valor {{ font-weight: 700; color: #1a1a2e; text-align: right; min-width: 22px; }}

  .anio-fila {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 4px 6px;
    font-size: 0.78rem;
    border-radius: 6px;
    transition: background 0.15s;
  }}
  .anio-label {{ width: 64px; flex: none; color: #334155; font-weight: 600; }}
  .anio-track {{ display: flex; height: 14px; border-radius: 4px; overflow: hidden; background: #e5e9f0; min-width: 20px; }}
  .anio-track .segmento {{ height: 100%; }}
  .anio-valor {{ font-weight: 700; color: #1a1a2e; min-width: 26px; }}
  .anio-fila-activa {{ background: #eef2ff; box-shadow: inset 0 0 0 1.5px #4338ca; }}
  .anio-fila-activa .anio-label {{ color: #4338ca; }}

  .heatmap-wrapper, #analisis-matriz {{ overflow-x: auto; }}
  .heatmap-escala {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.72rem;
    color: #64748b;
    margin-bottom: 10px;
  }}
  .heatmap-escala-barra {{
    width: 90px;
    height: 10px;
    border-radius: 5px;
    background: linear-gradient(to right, rgb(224,247,254), rgb(15,52,96));
  }}

  .heatmap-grid {{
    display: grid;
    gap: 2px;
    background: #f8fafc;
    font-size: 0.78rem;
    min-width: max-content;
  }}
  .heatmap-corner {{ background: #1a1a2e; position: sticky; left: 0; top: 0; z-index: 3; }}
  .heatmap-col-header {{
    background: #1a1a2e;
    color: #fff;
    font-weight: 600;
    padding: 7px 8px;
    text-align: center;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    position: sticky;
    top: 0;
    z-index: 2;
  }}
  .heatmap-row-header {{
    background: #eef1f6;
    color: #334155;
    font-weight: 600;
    padding: 7px 10px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 180px;
    position: sticky;
    left: 0;
  }}
  .heatmap-cell {{
    padding: 7px 6px;
    text-align: center;
    font-variant-numeric: tabular-nums;
    border-radius: 4px;
  }}
  .heatmap-cell-vacia {{ background: #f1f5f9; color: #cbd5e1; }}
  .heatmap-col-header.heatmap-total-header,
  .heatmap-row-header.heatmap-total-header {{ background: #0f3460; color: #fff; }}
  .heatmap-cell.heatmap-total-cell {{
    background: #eef1f6;
    color: #1a1a2e;
    font-weight: 700;
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
  <button type="button" class="leyenda-item leyenda-toggle" data-clase="pequena"><span class="dot pequena"></span> &lt; 500 kW</button>
  <button type="button" class="leyenda-item leyenda-toggle" data-clase="mediana"><span class="dot mediana"></span> 500 kW – 1 MW</button>
  <button type="button" class="leyenda-item leyenda-toggle" data-clase="grande"><span class="dot grande"></span> &gt; 1 MW</button>
  <button type="button" class="leyenda-item leyenda-toggle" data-clase="desconocida"><span class="dot desconocida"></span> Desconocida</button>
  <span class="leyenda-hint">clic para mostrar/ocultar</span>
</div>

{analisis_html}

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
      <th data-col="en_mapa" style="width:110px;text-align:center">Mapa</th>
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
const TOP_EMPRESAS_MATRIZ = {json.dumps(top_empresas_matriz, ensure_ascii=False)};
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
function nombreTexto(p) {{ return p.nombre || p.nombre_inferido || null; }}
function nombreProyectoHtml(p) {{
  if (p.nombre) return p.nombre;
  if (p.nombre_inferido) {{
    return `${{p.nombre_inferido}} <span class="nombre-inferido-marca" title="El PDF no traía un título: este nombre se infirió de la URL de la resolución">✳</span>`;
  }}
  return '<span class="sin-datos">Sin nombre</span>';
}}
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
    <div class="popup-nombre">${{nombreProyectoHtml(p)}}</div>
    <span class="popup-badge" style="${{badgeColor}}">${{capLabel(p.capacidad_kw)}}</span>
    <br><br>
    <div class="popup-fila"><b>Municipio:</b> ${{p.municipio || "—"}}, ${{p.departamento || "—"}}</div>
    <div class="popup-fila"><b>Empresa:</b> ${{p.solicitante || "—"}}</div>
    <div class="popup-fila"><b>Resolución:</b> ${{p.fecha_resolucion || "—"}}</div>
    <div class="popup-fila"><b>Resolución:</b> ${{p.resolucion || "—"}} · ${{p.fecha_resolucion || "—"}}</div>
    ${{p.pdf_url ? `<a class="popup-link" href="${{p.pdf_url}}" target="_blank" rel="noopener">📄 Ver resolución PDF</a>` : ""}}
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
        <div class="nombre-proyecto">${{nombreProyectoHtml(p)}}</div>
      </td>
      <td><span class="badge ${{clase}}">${{capLabel(p.capacidad_kw)}}</span></td>
      <td>${{val(p.municipio)}}</td>
      <td>${{val(p.departamento)}}</td>
      <td>${{val(p.solicitante)}}</td>
      <td>${{val(p.fecha_resolucion)}}</td>
      <td>${{val(p.resolucion)}}</td>
      <td style="text-align:center">
        ${{p.lat && p.lon
          ? '<span class="badge en-mapa" title="Tiene coordenadas y aparece en el mapa">📍 En mapa</span>'
          : '<span class="badge sin-mapa" title="Sin coordenadas: falta revisar la resolución y ubicarlo manualmente">⚠ Sin coords.</span>'}}
      </td>
      <td style="text-align:center">
        ${{p.pdf_url
          ? `<a href="${{p.pdf_url}}" target="_blank" rel="noopener" title="Ver resolución PDF"
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
  const visibles = new Set(datos);
  DATOS.forEach((p, i) => {{
    const m = marcadores[i];
    if (!m) return;
    if (visibles.has(p)) {{
      if (!map.hasLayer(m)) m.addTo(map);
    }} else {{
      if (map.hasLayer(m)) map.removeLayer(m);
    }}
  }});
}}

// ── Leyenda de capacidad (clic para mostrar/ocultar) ────────────────────────
let clasesVisibles = new Set(["pequena", "mediana", "grande", "desconocida"]);

document.querySelectorAll(".leyenda-toggle").forEach(btn => {{
  btn.addEventListener("click", () => {{
    const clase = btn.dataset.clase;
    if (clasesVisibles.has(clase)) {{
      clasesVisibles.delete(clase);
      btn.classList.add("inactivo");
    }} else {{
      clasesVisibles.add(clase);
      btn.classList.remove("inactivo");
    }}
    aplicarFiltros();
  }});
}});

// ── Filtros ────────────────────────────────────────────────────────────────
function aplicarFiltros() {{
  const q = document.getElementById("buscar").value.toLowerCase();
  const dept = document.getElementById("filtro-dept").value;
  const anio = document.getElementById("filtro-anio").value;

  let resultado = DATOS.filter(p => {{
    const texto = [nombreTexto(p), p.municipio, p.departamento, p.solicitante, p.resolucion]
      .filter(Boolean).join(" ").toLowerCase();
    const okQ = !q || texto.includes(q);
    const okD = !dept || p.departamento === dept;
    const okA = !anio || anioResolucion(p.fecha_resolucion) === anio;
    const okE = empresasSeleccionadas.has(EMPRESA_CANONICA[p.solicitante] || SIN_EMPRESA);
    const okC = clasesVisibles.has(capClase(p.capacidad_kw));
    return okQ && okD && okA && okE && okC;
  }});

  if (colOrden) {{
    resultado.sort((a, b) => {{
      let va, vb;
      if (colOrden === "en_mapa") {{
        va = (a.lat && a.lon) ? 1 : 0;
        vb = (b.lat && b.lon) ? 1 : 0;
      }} else if (colOrden === "nombre") {{
        va = nombreTexto(a) ?? "";
        vb = nombreTexto(b) ?? "";
      }} else {{
        va = a[colOrden] ?? "";
        vb = b[colOrden] ?? "";
      }}
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

function empresasVisibles(filtro = "") {{
  const q = filtro.toLowerCase();
  return EMPRESAS.filter(e => nombreEmpresa(e).toLowerCase().includes(q));
}}

function renderListaEmpresas(filtro = "") {{
  const cont = document.getElementById("lista-emp");
  cont.innerHTML = "";
  const visibles = empresasVisibles(filtro);
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
  const filtro = document.getElementById("buscar-emp").value;
  empresasVisibles(filtro).forEach(e => empresasSeleccionadas.add(e));
  renderListaEmpresas(filtro);
  actualizarBotonEmp();
  aplicarFiltros();
}});
document.getElementById("emp-ninguna").addEventListener("click", () => {{
  const filtro = document.getElementById("buscar-emp").value;
  empresasVisibles(filtro).forEach(e => empresasSeleccionadas.delete(e));
  renderListaEmpresas(filtro);
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
  clasesVisibles = new Set(["pequena", "mediana", "grande", "desconocida"]);
  document.querySelectorAll(".leyenda-toggle").forEach(btn => btn.classList.remove("inactivo"));
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

// ── Sección de Análisis: filtro por año ─────────────────────────────────────
function contarPorEmpresaJS(lista) {{
  const c = new Map();
  lista.forEach(p => {{
    const nombre = p.solicitante ? (EMPRESA_CANONICA[p.solicitante] || p.solicitante) : null;
    const clave = nombre || "(Sin empresa)";
    c.set(clave, (c.get(clave) || 0) + 1);
  }});
  return Array.from(c.entries()).sort((a, b) => b[1] - a[1]);
}}

function renderRankingJS(pares, contenedorId, maxN) {{
  const cont = document.getElementById(contenedorId);
  const color = cont.dataset.color || "#0f3460";
  const top = pares.slice(0, maxN || 15);
  if (!top.length) {{
    cont.innerHTML = '<div class="analisis-vacio">Sin datos</div>';
    return;
  }}
  const maximo = Math.max(...top.map(([, c]) => c));
  cont.innerHTML = top.map(([nombre, cnt]) => {{
    const pct = maximo ? (cnt / maximo * 100).toFixed(1) : 0;
    return `<div class="barra-fila">
      <div class="barra-label" title="${{nombre}}">${{nombre}}</div>
      <div class="barra-track"><div class="barra-fill" style="width:${{pct}}%;background:${{color}}"></div></div>
      <div class="barra-valor">${{cnt}}</div>
    </div>`;
  }}).join("");
}}

function actualizarAnalisis() {{
  const anio = document.getElementById("analisis-anio").value;
  const base = anio ? DATOS.filter(p => anioResolucion(p.fecha_resolucion) === anio) : DATOS;

  const mediana = base.filter(p => capClase(p.capacidad_kw) === "mediana");
  const grande = base.filter(p => capClase(p.capacidad_kw) === "grande");

  renderRankingJS(contarPorEmpresaJS(mediana), "analisis-mediana-ranking", 15);
  renderRankingJS(contarPorEmpresaJS(grande), "analisis-grande-ranking", 15);

  const zonasMap = new Map();
  base.forEach(p => {{
    const z = p.departamento || "(Sin departamento)";
    zonasMap.set(z, (zonasMap.get(z) || 0) + 1);
  }});
  const zonas = Array.from(zonasMap.entries()).sort((a, b) => b[1] - a[1]);
  renderRankingJS(zonas, "analisis-zonas-ranking", 10);

  document.getElementById("analisis-mediana-count").textContent = `(500 kW – 1 MW · ${{mediana.length}} solicitudes)`;
  document.getElementById("analisis-grande-count").textContent = `(> 1 MW · ${{grande.length}} solicitudes)`;
  document.getElementById("analisis-zonas-count").textContent = `(Top 10 departamentos · ${{base.length}} solicitudes)`;

  document.querySelectorAll(".anio-fila").forEach(el => {{
    el.classList.toggle("anio-fila-activa", anio !== "" && el.dataset.anio === anio);
  }});

  renderMatrizEmpresaDepto(base);
  document.getElementById("analisis-matriz-count").textContent =
    `(Top ${{TOP_EMPRESAS_MATRIZ.length}} empresas · ${{base.length}} solicitudes)`;
}}

function renderMatrizEmpresaDepto(base) {{
  const cont = document.getElementById("analisis-matriz");
  const conteo = new Map();     // depto -> Map(empresa -> n)
  const totalPorDepto = new Map();
  const totalPorEmpresa = new Map(TOP_EMPRESAS_MATRIZ.map(e => [e, 0]));

  base.forEach(p => {{
    const nombre = p.solicitante ? (EMPRESA_CANONICA[p.solicitante] || p.solicitante) : null;
    if (!TOP_EMPRESAS_MATRIZ.includes(nombre)) return;
    const depto = p.departamento || "(Sin departamento)";
    if (!conteo.has(depto)) conteo.set(depto, new Map());
    const fila = conteo.get(depto);
    fila.set(nombre, (fila.get(nombre) || 0) + 1);
    totalPorDepto.set(depto, (totalPorDepto.get(depto) || 0) + 1);
    totalPorEmpresa.set(nombre, (totalPorEmpresa.get(nombre) || 0) + 1);
  }});

  const deptosOrdenados = Array.from(totalPorDepto.entries()).sort((a, b) => b[1] - a[1]);

  if (!deptosOrdenados.length) {{
    cont.innerHTML = '<div class="analisis-vacio">Sin datos</div>';
    return;
  }}

  let maxCelda = 0;
  deptosOrdenados.forEach(([depto]) => {{
    const fila = conteo.get(depto) || new Map();
    TOP_EMPRESAS_MATRIZ.forEach(e => {{ maxCelda = Math.max(maxCelda, fila.get(e) || 0); }});
  }});
  maxCelda = maxCelda || 1;

  const encabezados = TOP_EMPRESAS_MATRIZ.map(e =>
    `<div class="heatmap-col-header" title="${{e}}">${{e}}</div>`).join("");

  const filasHtml = deptosOrdenados.map(([depto, total]) => {{
    const fila = conteo.get(depto) || new Map();
    const celdas = TOP_EMPRESAS_MATRIZ.map(e => {{
      const n = fila.get(e) || 0;
      const ratio = n ? n / maxCelda : 0;
      const estilo = n ? `background:${{colorHeatmap(ratio)}};color:${{textoHeatmap(ratio)}}` : "";
      const clase = "heatmap-cell" + (n ? "" : " heatmap-cell-vacia");
      const tip = `${{e}} · ${{depto}}: ${{n}} solicitud${{n !== 1 ? "es" : ""}}`;
      return `<div class="${{clase}}" style="${{estilo}}" title="${{tip}}">${{n || "–"}}</div>`;
    }}).join("");
    return `<div class="heatmap-row-header" title="${{depto}}">${{depto}}</div>${{celdas}}`
      + `<div class="heatmap-cell heatmap-total-cell">${{total}}</div>`;
  }}).join("");

  const filaTotal = TOP_EMPRESAS_MATRIZ.map(e =>
    `<div class="heatmap-cell heatmap-total-cell">${{totalPorEmpresa.get(e) || 0}}</div>`).join("");

  const nCols = TOP_EMPRESAS_MATRIZ.length;
  cont.innerHTML = `
    <div class="heatmap-escala">
      <span>Menos</span>
      <span class="heatmap-escala-barra"></span>
      <span>Más</span>
    </div>
    <div class="heatmap-grid" style="grid-template-columns: minmax(130px,auto) repeat(${{nCols}}, minmax(64px,1fr)) minmax(56px,auto);">
      <div class="heatmap-corner"></div>
      ${{encabezados}}
      <div class="heatmap-col-header heatmap-total-header">Total</div>
      ${{filasHtml}}
      <div class="heatmap-row-header heatmap-total-header">Total</div>
      ${{filaTotal}}
      <div class="heatmap-cell heatmap-total-cell"></div>
    </div>`;
}}

function colorHeatmap(ratio) {{
  const claro = [224, 247, 254], oscuro = [15, 52, 96];
  const c = claro.map((v, i) => Math.round(v + (oscuro[i] - v) * ratio));
  return `rgb(${{c[0]}},${{c[1]}},${{c[2]}})`;
}}

function textoHeatmap(ratio) {{
  return ratio > 0.55 ? "#ffffff" : "#1a1a2e";
}}

document.getElementById("analisis-anio").addEventListener("change", actualizarAnalisis);
actualizarAnalisis();

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
