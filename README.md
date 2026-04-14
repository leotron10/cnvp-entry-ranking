# CNVP Entry Ranking Generator

Generador automatico de listas de entrada para torneos del **Circuito Nacional de Voley Playa 2026** (RFEVB).

Extrae los inscritos de [esvoley.es](https://esvoley.es), los cruza con el Ranking Nacional (RNVP) en tiempo real y genera la lista de entrada ordenada por puntos, separada en **Cuadro Final** y **Cuadro Clasificacion**. Exportable a Excel.

---

## Requisitos

- **Python 3.10 o superior** → [descargar aqui](https://www.python.org/downloads/)
  - Al instalarlo, marca la casilla **"Add Python to PATH"**
- Conexion a internet

---

## Instalacion (solo la primera vez)

1. Descarga o clona este repositorio
2. Haz doble clic en **`setup.bat`**
3. Espera a que termine la instalacion

---

## Uso

Haz doble clic en **`CNVP Entry Ranking.bat`**

- Se abrira el navegador automaticamente
- Selecciona el torneo en el menu desplegable
- La lista se genera sola en segundos
- Descarga el resultado en Excel con el boton de la parte inferior

---

## Funcionalidades

- Torneos 2026 detectados automaticamente desde esvoley.es
- Cruce con el ranking nacional por ID de jugador (sin errores de nombres)
- Fuzzy matching como fallback para inconsistencias en nombres
- Separacion configurable entre Cuadro Final y Cuadro Clasificacion
- Exportacion a `.xlsx` con una hoja por cada cuadro
