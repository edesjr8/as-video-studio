"""
Proveedor de imagen Kie.ai (anadido para AS Video Studio en Easypanel).

Responde igual que generar() de imagen.py: devuelve (png_bytes, meta). Asi el
resto del Estudio no sabe ni le importa quien dibuja.

Flujo de Kie (API "Market", asincrona):
  1. las referencias locales se suben a su almacen temporal -> URLs
  2. POST /api/v1/jobs/createTask          -> taskId
  3. GET  /api/v1/jobs/recordInfo?taskId=  -> hasta state=success|fail
  4. se descarga la imagen y se ajusta al tamano que espera el Estudio

Variables de entorno:
  KIE_API_KEY            obligatoria
  KIE_RESOLUCION         1K | 2K | 4K   (por defecto: 1K, o 2K con quality=high)
  KIE_PRECIO_CREDITO     USD por credito para la cuenta de gasto (def. 0.005)
  KIE_MAX_REFS           tope de referencias por llamada (def. segun modelo)
  KIE_UPLOAD_URL         endpoint de subida (def. el de la doc de Kie)
"""
import hashlib
import io
import json
import os
import threading
import time

import requests
from PIL import Image

API = "https://api.kie.ai/api/v1/jobs"
UPLOAD_URL = os.environ.get("KIE_UPLOAD_URL") or \
    "https://kieai.redpandaai.co/api/file-stream-upload"

# Cuantas imagenes de entrada acepta cada modelo, y como se llaman sus campos.
#   familia "nb": nano-banana-2 / nano-banana-pro  -> image_input, aspect_ratio, resolution
#   familia "nb1": google/nano-banana(-edit)        -> image_urls, image_size
MODELOS = {
    "nano-banana-2":           {"familia": "nb",  "max_refs": 14},
    "nano-banana-pro":         {"familia": "nb",  "max_refs": 8},
    "google/nano-banana":      {"familia": "nb1", "max_refs": 0},
    "google/nano-banana-edit": {"familia": "nb1", "max_refs": 10},
}

TAMANOS_PX = {"apaisado": (1536, 1024), "cuadrado": (1024, 1024),
              "vertical": (1024, 1536)}
PROPORCION = {"apaisado": "3:2", "cuadrado": "1:1", "vertical": "2:3"}

_gasto = {"usd": 0.0, "llamadas": 0}
_subidas = {}                      # huella del fichero -> (url, cuando)
_subidas_lock = threading.Lock()
_VIGENCIA_SUBIDA = 6 * 3600        # Kie borra los temporales; se resube pasado esto


class SinSaldoKie(RuntimeError):
    pass


def _clave():
    clave = (os.environ.get("KIE_API_KEY") or "").strip()
    if not clave:
        raise RuntimeError("falta KIE_API_KEY en las variables de entorno de "
                           "Easypanel (Environment) para usar Kie.ai")
    return clave


def _cabeceras():
    return {"Authorization": f"Bearer {_clave()}"}


def _huella(ruta):
    h = hashlib.sha1()
    with open(ruta, "rb") as fh:
        for trozo in iter(lambda: fh.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def _como_png(ruta, lado_max=2048):
    """Las referencias pueden venir en cualquier formato y tamano: se mandan
    como PNG razonable para no subir 10 MB por plano."""
    with Image.open(ruta) as im:
        im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
        im.thumbnail((lado_max, lado_max))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue()


def subir(ruta):
    """Sube una referencia y devuelve su URL. Cachea por contenido: la lamina de
    estilo va en todos los planos y no tiene sentido subirla 200 veces."""
    huella = _huella(ruta)
    with _subidas_lock:
        previa = _subidas.get(huella)
        if previa and time.time() - previa[1] < _VIGENCIA_SUBIDA:
            return previa[0]
    datos = _como_png(ruta)
    ultimo = None
    for intento in range(4):
        try:
            r = requests.post(
                UPLOAD_URL, headers=_cabeceras(),
                files={"file": (f"{huella[:16]}.png", datos, "image/png")},
                data={"uploadPath": "as-video-studio", "fileName": f"{huella[:16]}.png"},
                timeout=120)
            cuerpo = r.json() if r.headers.get("content-type", "").startswith(
                "application/json") else {}
            url = ((cuerpo.get("data") or {}).get("downloadUrl")
                   or (cuerpo.get("data") or {}).get("fileUrl"))
            if r.status_code == 200 and url:
                with _subidas_lock:
                    _subidas[huella] = (url, time.time())
                return url
            ultimo = f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.RequestException as exc:
            ultimo = str(exc)
        time.sleep(2 * (intento + 1))
    raise RuntimeError(f"Kie.ai: no se pudo subir la referencia {os.path.basename(ruta)}: {ultimo}")


def _entrada(modelo, prompt, urls, tamano, resolucion):
    familia = MODELOS.get(modelo, {"familia": "nb"})["familia"]
    if familia == "nb1":
        entrada = {"prompt": prompt, "output_format": "png",
                   "image_size": PROPORCION[tamano]}
        if urls:
            entrada["image_urls"] = urls
        return entrada
    return {"prompt": prompt, "image_input": urls,
            "aspect_ratio": PROPORCION[tamano], "resolution": resolucion,
            "output_format": "png"}


def _urls_del_resultado(data):
    """recordInfo trae resultJson como TEXTO con {"resultUrls": [...]}; se busca
    con tolerancia por si algun modelo lo pone en otro sitio."""
    crudo = data.get("resultJson")
    if isinstance(crudo, str):
        try:
            crudo = json.loads(crudo)
        except ValueError:
            crudo = {}
    for fuente in (crudo or {}, data.get("response") or {}, data):
        for campo in ("resultUrls", "result_urls", "imageUrls", "images"):
            valor = fuente.get(campo) if isinstance(fuente, dict) else None
            if isinstance(valor, list) and valor:
                primero = valor[0]
                return [primero.get("url") if isinstance(primero, dict) else primero]
        for campo in ("imageUrl", "url"):
            if isinstance(fuente, dict) and isinstance(fuente.get(campo), str):
                return [fuente[campo]]
    return []


def _es_sin_saldo(texto, codigo):
    t = (texto or "").lower()
    return codigo == 402 or "insufficient" in t or "credit" in t and "not enough" in t


def generar(prompt, referencias, *, modelo="nano-banana-2", quality="low",
            tamano="apaisado", reintentos=3, espera_max=900):
    referencias = [r for r in (referencias or []) if r]
    faltan = [r for r in referencias if not os.path.exists(r)]
    if faltan:
        raise ValueError("estas imagenes de referencia no existen: "
                         + ", ".join(str(f) for f in faltan[:5]))
    maximo = int(os.environ.get("KIE_MAX_REFS") or
                 MODELOS.get(modelo, {}).get("max_refs", 8))
    # las referencias van en orden de importancia (estilo, estructura,
    # personajes, continuidad): si sobran, se quitan las ultimas
    referencias = referencias[:maximo] if maximo else []
    resolucion = os.environ.get("KIE_RESOLUCION") or ("2K" if quality == "high" else "1K")

    t0 = time.time()
    urls = [subir(r) for r in referencias]
    cuerpo = {"model": modelo,
              "input": _entrada(modelo, prompt, urls, tamano, resolucion)}

    ultimo = None
    for intento in range(reintentos + 1):
        r = requests.post(f"{API}/createTask", headers=_cabeceras(),
                          json=cuerpo, timeout=60)
        try:
            resp = r.json()
        except ValueError:
            resp = {}
        codigo = resp.get("code", r.status_code)
        task = (resp.get("data") or {}).get("taskId")
        if r.status_code == 200 and codigo == 200 and task:
            break
        ultimo = f"HTTP {r.status_code} / code {codigo}: {(resp.get('msg') or r.text)[:200]}"
        if _es_sin_saldo(r.text, codigo):
            raise SinSaldoKie("Kie.ai se ha quedado sin creditos: recarga en "
                              "https://kie.ai/billing y retoma; lo ya generado "
                              f"no se pierde. ({ultimo})")
        if codigo in (401, 403):
            raise RuntimeError(f"Kie.ai rechaza la clave KIE_API_KEY: {ultimo}")
        if codigo in (429, 500, 502, 503, 504) and intento < reintentos:
            time.sleep(5 * (intento + 1))
            continue
        raise RuntimeError(f"Kie.ai no acepto la tarea ({modelo}): {ultimo}")

    # sondeo hasta que termine
    inicio = time.time()
    pausa = 2.0
    data = {}
    while True:
        if time.time() - inicio > espera_max:
            raise RuntimeError(f"Kie.ai: la tarea {task} lleva mas de "
                               f"{espera_max // 60} min sin terminar")
        time.sleep(pausa)
        pausa = min(pausa * 1.3, 8.0)
        try:
            q = requests.get(f"{API}/recordInfo", params={"taskId": task},
                             headers=_cabeceras(), timeout=30)
            data = (q.json() or {}).get("data") or {}
        except (requests.RequestException, ValueError):
            continue
        estado = (data.get("state") or "").lower()
        if estado == "success":
            break
        if estado == "fail":
            motivo = data.get("failMsg") or data.get("failCode") or "sin motivo"
            raise RuntimeError(f"Kie.ai: la imagen fallo ({modelo}): {motivo}")

    urls_res = _urls_del_resultado(data)
    if not urls_res:
        raise RuntimeError(f"Kie.ai: la tarea {task} termino sin URL de imagen")
    img = requests.get(urls_res[0], timeout=120)
    img.raise_for_status()

    # al tamano exacto que usa el Estudio (el render cuenta con estas medidas)
    with Image.open(io.BytesIO(img.content)) as im:
        im = im.convert("RGB")
        ancho, alto = TAMANOS_PX[tamano]
        if im.size != (ancho, alto):
            # recorte al centro para igualar proporcion y luego escala
            objetivo = ancho / alto
            w, h = im.size
            if w / h > objetivo:
                nw = int(h * objetivo)
                im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
            elif w / h < objetivo:
                nh = int(w / objetivo)
                im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
            im = im.resize((ancho, alto), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        png = buf.getvalue()

    creditos = data.get("creditsConsumed")
    precio_credito = float(os.environ.get("KIE_PRECIO_CREDITO") or 0.005)
    coste = round(float(creditos) * precio_credito, 4) if creditos else 0.0
    _gasto["usd"] += coste
    _gasto["llamadas"] += 1
    return png, {"segundos": round(time.time() - t0, 1), "quality": quality,
                 "refs": len(referencias), "coste": coste,
                 "modelo": f"kie:{modelo}", "tamano": f"{ancho}x{alto}",
                 "usage": {}}


def gasto():
    return dict(_gasto, usd=round(_gasto["usd"], 4))
