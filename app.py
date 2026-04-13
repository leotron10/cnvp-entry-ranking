"""
CNVP Entry Ranking Generator
=============================
Aplicacion Streamlit que automatiza la creacion del "Entry Ranking"
(lista de entrada) para torneos del Circuito Nacional de Voley Playa (RFEVB 2026).

Flujo:
1. Scraping de torneos desde esvoley.es/voley-playa/circuito-nacional
2. Extraccion de inscritos via API interna de la RFEVB
3. Cruce con ranking nacional (masculino + femenino)
4. Fuzzy matching con RapidFuzz para resolver inconsistencias de nombres
5. Calculo de puntos por pareja y generacion de la lista de entrada
"""

import re
import io
import json
from datetime import date

import requests
import streamlit as st
import pandas as pd
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process
from openpyxl import Workbook

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
BASE_URL = "https://esvoley.es"
CIRCUITO_URL = f"{BASE_URL}/voley-playa/circuito-nacional"
RANKING_MASC_URL = "https://intranet.rfevb.com/webservices/rfevbcom/vplaya/vp-ranking-masculino.php"
RANKING_FEM_URL = "https://intranet.rfevb.com/webservices/rfevbcom/vplaya/vp-ranking-femenino.php"
INSCRIPCIONES_URL = "https://intranet.rfevb.com/webservices/rfevbcom/vplaya/vp-parejas-torneo.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT = 30  # segundos


# ---------------------------------------------------------------------------
# Funciones de scraping y datos
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def obtener_torneos() -> list[dict]:
    """
    Scraping de la pagina del circuito nacional.
    Devuelve lista de dicts: {nombre, url, slug} para torneos 2026.
    """
    resp = requests.get(CIRCUITO_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    torneos = []
    seen_slugs = set()

    # Los torneos estan dentro de <div class="grid-carpetas-competiciones">
    # con tarjetas <div class="tarjeta"> que contienen <a class="coverLink">
    grids = soup.select("div.grid-carpetas-competiciones")
    tarjetas = []
    for grid in grids:
        tarjetas.extend(grid.select("div.tarjeta"))
    # Fallback: si no se encuentran grids, buscar todas las tarjetas
    if not tarjetas:
        tarjetas = soup.select("div.tarjeta")

    for tarjeta in tarjetas:
        link = tarjeta.select_one("a.coverLink")
        if not link:
            continue
        href = link.get("href", "")
        # Solo torneos del circuito nacional 2026
        if "/voley-playa/circuito-nacional/" not in href or "2026" not in href:
            continue
        slug = href.strip("/").split("/")[-1]
        # Deduplicar por slug
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        # Extraer nombre: img alt -> div.h5 -> div.nombre -> slug
        img = tarjeta.select_one("img")
        nombre = ""
        if img:
            nombre = img.get("alt", "").strip()
        if not nombre:
            h5_div = tarjeta.select_one("div.h5")
            if h5_div:
                nombre = h5_div.get_text(strip=True)
        if not nombre:
            nombre_div = tarjeta.select_one("div.nombre")
            if nombre_div:
                nombre = nombre_div.get_text(strip=True)
        if not nombre:
            nombre = slug.replace("-", " ").title()
        torneos.append({
            "nombre": nombre,
            "url": f"{BASE_URL}{href}",
            "slug": slug,
        })
    return torneos


@st.cache_data(ttl=300, show_spinner=False)
def obtener_ids_torneo(url_torneo: str) -> dict:
    """
    Navega a la pagina de inscripciones del torneo y extrae
    IdTorneoMasculino e IdTorneoFemenino del JavaScript embebido.
    """
    url_insc = url_torneo.rstrip("/") + "/inscripciones/"
    resp = requests.get(url_insc, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    ids = {}
    # Buscar: var IdTorneoMasculino = 1723;
    match_masc = re.search(r"var\s+IdTorneoMasculino\s*=\s*(\d+)", resp.text)
    match_fem = re.search(r"var\s+IdTorneoFemenino\s*=\s*(\d+)", resp.text)
    if match_masc:
        ids["masculino"] = int(match_masc.group(1))
    if match_fem:
        ids["femenino"] = int(match_fem.group(1))
    return ids


@st.cache_data(ttl=300, show_spinner=False)
def obtener_inscritos(id_torneo: int) -> list[dict]:
    """
    Llama a la API de parejas inscritas para un torneo dado.
    Devuelve la lista JSON cruda de parejas.
    """
    params = {"orden": "FechaInscripcion", "IdTorneo": id_torneo}
    resp = requests.get(
        INSCRIPCIONES_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    text = resp.text.strip()
    if not text or text == "[]":
        return []
    # A veces la respuesta trae basura antes del JSON
    inicio = text.find("[")
    fin = text.rfind("]")
    if inicio == -1 or fin == -1:
        return []
    return resp.json() if inicio == 0 else json.loads(text[inicio:fin+1])


@st.cache_data(ttl=600, show_spinner=False)
def obtener_ranking(genero: str) -> dict:
    """
    Descarga el ranking nacional completo (masculino o femenino).
    Devuelve dos dicts:
      - por_id: {IdPersona: puntos_float}
      - por_nombre: {nombre_normalizado: (IdPersona, puntos_float)}
    """
    url = RANKING_MASC_URL if genero == "masculino" else RANKING_FEM_URL
    fecha = date.today().strftime("%Y-%m-%d")
    params = {"fechaHasta": fecha, "buscar": ""}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    data = resp.json()
    por_id = {}
    por_nombre = {}

    for entry in data:
        id_persona = str(entry.get("IdPersona", ""))
        apellidos_nombre = entry.get("ApellidosNombre", "")
        puntos_str = entry.get("PuntosSinFormato", "0")
        try:
            puntos = float(puntos_str)
        except (ValueError, TypeError):
            puntos = 0.0

        por_id[id_persona] = puntos
        # Normalizar nombre para fuzzy matching
        nombre_norm = normalizar_nombre(apellidos_nombre)
        por_nombre[nombre_norm] = (id_persona, puntos)

    return {"por_id": por_id, "por_nombre": por_nombre}


# ---------------------------------------------------------------------------
# Matching de nombres
# ---------------------------------------------------------------------------

def normalizar_nombre(nombre: str) -> str:
    """Normaliza un nombre: mayusculas, sin acentos extra, espacios limpios."""
    import unicodedata
    nombre = nombre.upper().strip()
    # Normalizar Unicode (quitar acentos)
    nombre = unicodedata.normalize("NFD", nombre)
    nombre = "".join(c for c in nombre if unicodedata.category(c) != "Mn")
    # Limpiar espacios multiples
    nombre = re.sub(r"\s+", " ", nombre)
    return nombre


def construir_nombre_completo(jug_data: dict, prefix: str) -> str:
    """
    Construye 'APELLIDO1 APELLIDO2, NOMBRE' a partir de los campos del jugador.
    prefix: 'jug1' o 'jug2'
    """
    pa = jug_data.get(f"{prefix}_PA", "").strip()
    sa = jug_data.get(f"{prefix}_SA", "").strip()
    nombre = jug_data.get(f"{prefix}_Nombre", "").strip()
    apellidos = f"{pa} {sa}".strip() if sa and sa != "-" else pa
    return f"{apellidos}, {nombre}"


def buscar_puntos_jugador(
    id_persona: str,
    nombre_completo: str,
    ranking_id: dict,
    ranking_nombre: dict,
) -> tuple[float, str]:
    """
    Busca los puntos de un jugador usando estrategia escalonada:
    a) Busqueda exacta por IdPersona
    b) Busqueda exacta por nombre normalizado
    c) Busqueda por apellidos solamente
    d) Fuzzy matching con RapidFuzz
    Devuelve (puntos, metodo_match).
    """
    # a) Match exacto por ID
    if id_persona in ranking_id:
        return ranking_id[id_persona], "ID exacto"

    nombre_norm = normalizar_nombre(nombre_completo)

    # b) Match exacto por nombre
    if nombre_norm in ranking_nombre:
        return ranking_nombre[nombre_norm][1], "Nombre exacto"

    # c) Busqueda solo por apellidos (parte antes de la coma)
    apellidos = nombre_norm.split(",")[0].strip() if "," in nombre_norm else nombre_norm
    for key, (_, pts) in ranking_nombre.items():
        key_apellidos = key.split(",")[0].strip() if "," in key else key
        if apellidos == key_apellidos:
            return pts, "Apellidos exactos"

    # d) Fuzzy matching con RapidFuzz
    if ranking_nombre:
        nombres_ranking = list(ranking_nombre.keys())
        resultado = process.extractOne(
            nombre_norm,
            nombres_ranking,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=75,
        )
        if resultado:
            match_nombre, score, _ = resultado
            return ranking_nombre[match_nombre][1], f"Fuzzy ({score:.0f}%)"

    # Si no se encuentra, asignar 0 puntos
    return 0.0, "No encontrado (0 pts)"


# ---------------------------------------------------------------------------
# Procesamiento principal
# ---------------------------------------------------------------------------

def procesar_categoria(
    inscritos: list[dict],
    ranking_id: dict,
    ranking_nombre: dict,
    genero: str,
) -> pd.DataFrame:
    """
    Procesa las parejas inscritas de una categoria, cruza con ranking
    y devuelve un DataFrame ordenado por puntos totales descendente.
    """
    if not inscritos:
        return pd.DataFrame()

    filas = []
    for idx, pareja in enumerate(inscritos, start=1):
        # Datos jugador 1
        id_j1 = str(pareja.get("IdPersona1", ""))
        nombre_j1 = construir_nombre_completo(pareja, "jug1")
        pts_j1, metodo_j1 = buscar_puntos_jugador(
            id_j1, nombre_j1, ranking_id, ranking_nombre
        )

        # Datos jugador 2
        id_j2 = str(pareja.get("IdPersona2", ""))
        nombre_j2 = construir_nombre_completo(pareja, "jug2")
        pts_j2, metodo_j2 = buscar_puntos_jugador(
            id_j2, nombre_j2, ranking_id, ranking_nombre
        )

        pts_total = pts_j1 + pts_j2
        alias = pareja.get("AliasPareja", f"{pareja.get('jug1_PA','')}-{pareja.get('jug2_PA','')}")

        filas.append({
            "Orden Inscripcion": idx,
            "Pareja": alias,
            "Jugador 1": nombre_j1,
            "Puntos J1": pts_j1,
            "Match J1": metodo_j1,
            "Jugador 2": nombre_j2,
            "Puntos J2": pts_j2,
            "Match J2": metodo_j2,
            "Puntos Totales": pts_total,
        })

    df = pd.DataFrame(filas)
    # Ordenar por puntos totales descendente; empates mantienen orden de inscripcion
    df = df.sort_values(
        by=["Puntos Totales", "Orden Inscripcion"],
        ascending=[False, True],
    ).reset_index(drop=True)
    # Posicion en la lista de entrada
    df.insert(0, "Pos.", range(1, len(df) + 1))
    return df


def generar_excel(dfs: dict[str, pd.DataFrame]) -> bytes:
    """Genera un archivo Excel con una hoja por cada DataFrame."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for nombre_hoja, df in dfs.items():
            if not df.empty:
                # Nombre de hoja limpio (max 31 chars para Excel)
                sheet_name = nombre_hoja[:31]
                df.to_excel(writer, index=False, sheet_name=sheet_name)
                # Autoajustar ancho de columnas
                ws = writer.sheets[sheet_name]
                for col_idx, col in enumerate(df.columns, 1):
                    max_len = max(
                        len(str(col)),
                        df[col].astype(str).map(len).max() if len(df) > 0 else 0,
                    )
                    ws.column_dimensions[
                        ws.cell(row=1, column=col_idx).column_letter
                    ].width = min(max_len + 3, 40)
    return output.getvalue()


@st.cache_data(ttl=300, show_spinner="Generando lista de entrada...")
def cargar_resultados(torneo: dict) -> tuple[dict, str]:
    """Carga inscritos + ranking y devuelve resultados procesados."""
    try:
        ids = obtener_ids_torneo(torneo["url"])
    except Exception as e:
        st.error(f"Error al acceder a las inscripciones: {e}")
        return {}, ""

    if not ids:
        st.warning("No se encontraron IDs de torneo en la pagina de inscripciones.")
        return {}, ""

    inscritos_fem = []
    inscritos_masc = []
    try:
        if "femenino" in ids:
            inscritos_fem = obtener_inscritos(ids["femenino"])
        if "masculino" in ids:
            inscritos_masc = obtener_inscritos(ids["masculino"])
    except Exception as e:
        st.error(f"Error al obtener inscritos: {e}")
        return {}, ""

    try:
        ranking_fem = obtener_ranking("femenino")
        ranking_masc = obtener_ranking("masculino")
    except Exception as e:
        st.error(f"Error al obtener el ranking nacional: {e}")
        return {}, ""

    resultados = {}
    if inscritos_fem:
        resultados["Femenino"] = procesar_categoria(
            inscritos_fem, ranking_fem["por_id"], ranking_fem["por_nombre"], "femenino",
        )
    if inscritos_masc:
        resultados["Masculino"] = procesar_categoria(
            inscritos_masc, ranking_masc["por_id"], ranking_masc["por_nombre"], "masculino",
        )

    if not resultados:
        st.warning("No hay parejas inscritas en este torneo.")
        return {}, ""

    return resultados, torneo["nombre"]


# ---------------------------------------------------------------------------
# Interfaz Streamlit
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="CNVP Entry Ranking Generator",
        page_icon="🏐",
        layout="wide",
    )

    st.title("🏐 CNVP Entry Ranking Generator")
    st.caption("Generador automatico de listas de entrada — Circuito Nacional de Voley Playa 2026 (RFEVB)")

    # --- Paso 1: Obtener torneos ---
    with st.spinner("Cargando torneos del Circuito Nacional 2026..."):
        try:
            torneos = obtener_torneos()
        except Exception as e:
            st.error(f"Error al obtener los torneos: {e}")
            st.info("Comprueba tu conexion a internet o intentalo de nuevo mas tarde.")
            return

    if not torneos:
        st.warning("No se encontraron torneos del Circuito Nacional 2026.")
        return

    # Dropdown de seleccion
    nombres_torneos = [t["nombre"] for t in torneos]
    seleccion = st.selectbox(
        "Selecciona un torneo:",
        options=range(len(torneos)),
        format_func=lambda i: nombres_torneos[i],
    )
    torneo = torneos[seleccion]

    st.divider()

    # --- Paso 2: Procesamiento automatico al seleccionar torneo ---
    resultados, torneo_nombre = cargar_resultados(torneo)
    if not resultados:
        return

        # Selector de tamanyo del cuadro final
        cuadro_final_size = st.number_input(
            "Parejas en Cuadro Final:",
            min_value=1,
            max_value=64,
            value=12,
            step=1,
            help="Numero de parejas que entran directamente al cuadro final.",
        )

        cols_display = [
            "Pos.", "Pareja", "Jugador 1", "Puntos J1",
            "Jugador 2", "Puntos J2", "Puntos Totales",
        ]
        col_config = {
            "Puntos J1": st.column_config.NumberColumn(format="%.0f"),
            "Puntos J2": st.column_config.NumberColumn(format="%.0f"),
            "Puntos Totales": st.column_config.NumberColumn(format="%.0f"),
        }

        excel_sheets = {}

        for genero, df in resultados.items():
            if df.empty:
                continue

            df_cf = df.iloc[:cuadro_final_size].copy()
            df_cc = df.iloc[cuadro_final_size:].copy()
            if not df_cc.empty:
                df_cc["Pos."] = range(1, len(df_cc) + 1)

            # --- Cuadro Final ---
            st.subheader(f"Cuadro Final {genero} — {torneo_nombre}")
            st.dataframe(
                df_cf[cols_display],
                use_container_width=True,
                hide_index=True,
                column_config=col_config,
            )

            # --- Cuadro Clasificacion ---
            if not df_cc.empty:
                st.subheader(f"Cuadro Clasificacion {genero} — {torneo_nombre}")
                st.dataframe(
                    df_cc[cols_display],
                    use_container_width=True,
                    hide_index=True,
                    column_config=col_config,
                )

            excel_sheets[f"CF {genero}"] = df_cf
            if not df_cc.empty:
                excel_sheets[f"CC {genero}"] = df_cc

        # Boton de descarga Excel
        st.divider()
        excel_data = generar_excel(excel_sheets)
        nombre_archivo = f"Lista_Entrada_{torneo_nombre.replace(' ', '_')}.xlsx"
        st.download_button(
            label="📥 Descargar Lista de Entrada (.xlsx)",
            data=excel_data,
            file_name=nombre_archivo,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.document",
            type="primary",
            use_container_width=True,
        )

    # --- Footer ---
    st.divider()
    st.caption(
        "Datos extraidos de [esvoley.es](https://esvoley.es) — "
        "RFEVB Circuito Nacional de Voley Playa 2026. "
        "Los puntos se consultan en tiempo real desde el Ranking Nacional (RNVP)."
    )


if __name__ == "__main__":
    main()







