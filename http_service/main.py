"""Servicio HTTP de detalle de sismos (CONTRATOS.md, secciones 1 y 3).

Carga ``data/sismos.json`` en memoria al iniciar y expone
``GET /earthquakes/{id}``.

Uso, desde la raíz del repositorio:
    uvicorn http_service.main:app

Respuestas:
- ``200`` con los 10 campos del dataset (los opcionales omitidos van en ``null``).
- ``404 {"detail": "Sismo no encontrado"}`` si el id tiene formato válido pero no existe.
- ``422`` (validación de FastAPI) si el id no cumple ``^(csn|sim)-[0-9]{1,9}$``.
"""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Path as PathParam
from pydantic import BaseModel

RUTA_DATASET = Path(__file__).resolve().parents[1] / "data" / "sismos.json"
PATRON_ID = r"^(csn|sim)-[0-9]{1,9}$"


class Sismo(BaseModel):
    """Registro del dataset con la forma de la respuesta 200 (CONTRATOS.md 1.2 y 3)."""

    id: str
    latitud: float
    longitud: float
    fecha_utc: str
    fecha_local: str | None = None
    profundidad_km: float
    magnitud: float
    tipo_magnitud: str
    referencia: str
    fuente_url: str | None = None


def cargar_sismos(ruta):
    """Lee el dataset y lo indexa por id; lanza ValueError si un id se repite."""
    with open(ruta, encoding="utf-8") as archivo:
        registros = json.load(archivo)
    sismos = {}
    for registro in registros:
        sismo = Sismo(**registro)
        if sismo.id in sismos:
            raise ValueError(f"id repetido en {ruta}: {sismo.id!r}")
        sismos[sismo.id] = sismo
    return sismos


# Datos en memoria: el servicio no modifica el dataset ni usa base de datos.
SISMOS = cargar_sismos(RUTA_DATASET)

app = FastAPI(title="Datos Sismos")


@app.get("/earthquakes/{id}", response_model=Sismo)
def obtener_sismo(id: str = PathParam(pattern=PATRON_ID)):
    sismo = SISMOS.get(id)
    if sismo is None:
        raise HTTPException(status_code=404, detail="Sismo no encontrado")
    return sismo
