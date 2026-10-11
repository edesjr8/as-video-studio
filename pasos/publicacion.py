"""
Publicacion: el director que empaqueta el video para YouTube.

Lo que hace
-----------
1. `dirigir`: un agente (el CLI de Claude, con la cuenta del canal) lee el
   guion, el encargo, los tiempos de cada bloque y MIRA los planos y las hojas
   de reparto. Devuelve conceptos de miniatura, titulos emparejados con ellas,
   descripcion, capitulos, tags y hashtags.
2. `disenar_miniatura`: dibuja el FONDO de una miniatura con el motor de imagen
   que se elija en la pantalla (OpenAI o Kie), con las laminas del estilo, los
   planos y el reparto del video como referencias. El fondo va SIN TEXTO.
3. `componer`: pone el texto encima con las fuentes del Estudio. Cambiar una
   palabra o un color es recomponer, no volver a pagar una imagen.

Por que NO es un paso del grafo
-------------------------------
Nada de lo que hay aqui alimenta al video: es lo que va ALREDEDOR de el. Como
paso, sus params entrarian en firmas y un titulo retocado podria dejar
obsoleto lo que cuelga. Vive en su propia carpeta del proyecto
(`publicacion/`) con su propio JSON, y no toca el estado.
"""
import io
import os
import re
import threading
import time

from PIL import Image, ImageDraw, ImageFilter

try:
    from . import cli_claude, comun, medios, p6_assets, tipografia
except ImportError:  # ejecutado con la carpeta pasos directamente en sys.path
    import cli_claude
    import comun
    import medios
    import p6_assets
    import tipografia

#: Medida de una miniatura de YouTube.
ANCHO, ALTO = 1280, 720

#: Conceptos que se piden. Mas de cuatro es elegir entre parecidos.
MINIATURAS = 4
TITULOS = 10

#: Lo que se le adjunta al generador como mucho, ademas del estilo.
MAX_REFS_PLANOS = 3
MAX_REFS_REPARTO = 2

#: Calidad de OpenAI para las miniaturas. Una miniatura se mira mas que
#: cualquier plano: la baja no compensa.
CALIDAD_OPENAI = "medium"

POSICIONES = ("izquierda", "derecha", "arriba", "abajo")

TIEMPO_BASE_S = 600
HERRAMIENTAS_VETADAS = ("Bash", "Write", "Edit", "NotebookEdit", "Grep", "Glob",
                        "WebFetch", "WebSearch", "Task", "TodoWrite")
HERRAMIENTAS_PERMITIDAS = ("Read",)

SISTEMA = ("Eres el director de publicacion de un canal de YouTube de videos de "
           "animacion narrada: experto en CTR, miniaturas, titulos y retencion. "
           "Antes de proponer nada abre con Read los planos y las hojas de "
           "reparto que te listan para saber como se ve el video. Contesta SOLO "
           "con el objeto JSON que se pide, sin texto alrededor.")

INSTRUCCION = """\
Vas a empaquetar este video para YouTube: miniaturas, titulos, descripcion,
capitulos y tags. Idioma de todo lo que lea el publico: {idioma}.

# El video
Titulo de trabajo: {titulo}
Duracion: {duracion}

## El encargo (lo que se pidio)
{encargo}

## El guion, por bloques (con el segundo en que empieza cada uno)
{guion}

## Como se ve (abre con Read los que necesites)
Planos:
{planos}
Hojas de reparto:
{reparto}

## La guia de estilo
{estilo}

# Lo que tienes que hacer

## Miniaturas: {n_miniaturas} conceptos DISTINTOS entre si
Cada una es una promesa visual que el video cumple. Un sujeto claro, una
emocion legible a 200 px, contraste fuerte, un solo punto de interes y una
pregunta en la cabeza de quien la ve. Nada de collages.
El fondo lo dibuja un generador de imagen con el estilo del video y el TEXTO
lo pone luego el Estudio, asi que:
- `prompt_imagen` (en INGLES) describe la imagen SIN NINGUN TEXTO, letra ni
  rotulo. Composicion 16:9, sujeto grande, y el lado de `posicion` LIBRE y
  despejado para el texto.
- `texto`: 2 a 4 palabras, en {idioma}, que NO repitan el titulo sino que lo
  completen. `resaltar`: las palabras del texto que van en color de acento.
- `planos`: ids de 1 a {max_planos} planos que sirvan de referencia visual.
- `personajes`: nombres del reparto que salen (como estan en la lista).

## Titulos: {n_titulos}, ordenados del mejor al peor
Menos de 60 caracteres, curiosidad sin mentir, la palabra que se busca al
principio cuando se pueda. Cada uno dice con que miniatura va.

## Descripcion
Dos o tres parrafos: el gancho en las dos primeras lineas (es lo que se ve sin
desplegar), de que va y por que verlo hasta el final. SIN capitulos ni
hashtags: van aparte.

## Capitulos
De 3 a 8, cada uno en un bloque donde empiece algo nuevo. El primero en el
primer bloque. Titulos cortos que den ganas de llegar.

## Tags y hashtags
De 10 a 20 tags (de lo concreto a lo general) y 3 hashtags.

# Formato de respuesta (SOLO este JSON)
{{
  "miniaturas": [
    {{"concepto": "...", "porque": "...", "emocion": "...",
      "texto": "...", "resaltar": ["..."],
      "posicion": "izquierda|derecha|arriba|abajo",
      "color_texto": "#FFFFFF", "color_acento": "#FFD400",
      "color_contorno": "#000000",
      "prompt_imagen": "...", "planos": ["S001"], "personajes": ["..."]}}
  ],
  "titulos": [{{"texto": "...", "porque": "...", "miniatura": 1}}],
  "descripcion": "...",
  "capitulos": [{{"bloque": "B01", "titulo": "..."}}],
  "tags": ["..."],
  "hashtags": ["#..."]
}}
"""

_CERROJO = threading.Lock()


# ------------------------------------------------------------------ almacen

def carpeta(proyecto):
    ruta = os.path.join(proyecto.raiz, "publicacion")
    os.makedirs(os.path.join(ruta, "miniaturas"), exist_ok=True)
    return ruta


def _ruta_json(proyecto):
    return os.path.join(carpeta(proyecto), "publicacion.json")


def leer(proyecto):
    """Lo guardado, o un documento vacio."""
    datos = medios.leer_json(_ruta_json(proyecto), {}) or {}
    datos.setdefault("miniaturas", [])
    datos.setdefault("titulos", [])
    datos.setdefault("capitulos", [])
    datos.setdefault("tags", [])
    datos.setdefault("hashtags", [])
    datos.setdefault("descripcion", "")
    return datos


def _guardar(proyecto, datos):
    datos["actualizado"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    medios.escribir_json(_ruta_json(proyecto), datos)


def _miniatura(datos, mid):
    for ficha in datos.get("miniaturas") or []:
        if ficha.get("id") == mid:
            return ficha
    raise KeyError(f"no hay ninguna miniatura {mid!r}")


# --------------------------------------------------------- lo que es el video

def _reloj(segundos):
    # HACIA ABAJO y no redondeando: un capitulo que empieza en 75,5 s marcado
    # como 1:16 se salta medio segundo de su arranque
    segundos = int(max(0.0, float(segundos or 0)))
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _texto(valor, tope):
    valor = re.sub(r"\s+", " ", str(valor or "")).strip()
    return valor if len(valor) <= tope else valor[:tope - 1] + "…"


def contexto_del_video(proyecto, estado):
    """Todo lo que el director necesita saber del video. -> dict"""
    ingesta = estado.params("ingesta") or {}
    assets = estado.params("assets") or {}

    meta = {}
    ruta_meta = medios.salida_de(
        proyecto, "voz", claves=("meta", "audio_meta", "marcas"),
        patrones=(r"audio_meta\.json", r".*_meta\.json"), estado=estado)
    if ruta_meta and os.path.exists(ruta_meta):
        meta = medios.leer_json(ruta_meta, {}) or {}
    tiempos = {str(b.get("id")): b.get("t_in") for b in (meta.get("bloques") or [])
               if isinstance(b, dict)}

    ruta_guion = medios.salida_de(proyecto, "guion", claves=("guion",),
                                  patrones=(r"guion\.json",), estado=estado)
    documento = medios.leer_json(ruta_guion, {}) if ruta_guion else {}
    bloques = []
    for bloque in documento.get("bloques_detalle") or documento.get("guion") or []:
        if not isinstance(bloque, dict):
            continue
        bid = str(bloque.get("id") or "").strip()
        texto = str(bloque.get("texto") or "").strip()
        if bid and texto:
            bloques.append({"id": bid, "texto": texto, "t_in": tiempos.get(bid)})

    plan = p6_assets.plan_actual(proyecto, estado=estado) or {}
    hechos = p6_assets.planos_hechos(proyecto)
    planos = []
    for escena in plan.get("escenas") or []:
        hecho = hechos.get(escena.get("id"))
        if not hecho:
            continue
        planos.append({"id": escena["id"], "narracion": escena.get("narracion") or "",
                       "ruta": os.path.join(proyecto.raiz, hecho["ruta"])})

    reparto = []
    if proyecto.version_activa("assets") is not None:
        base = os.path.join(proyecto.ruta_paso("assets"), "assets", "reparto")
        fichas = ((assets.get("catalogo") or {}).get("reparto") or {})
        if os.path.isdir(base):
            for nombre in sorted(os.listdir(base)):
                if nombre.endswith(".png"):
                    clave = nombre[:-4]
                    ficha = fichas.get(clave) if isinstance(fichas, dict) else None
                    reparto.append({
                        "nombre": clave, "ruta": os.path.join(base, nombre),
                        "descripcion": _texto((ficha or {}).get("descripcion")
                                              if isinstance(ficha, dict) else "", 200)})

    estilo = assets.get("estilo") or {}
    return {
        "titulo": ingesta.get("titulo") or proyecto.config.get("nombre") or proyecto.id,
        "encargo": ingesta.get("texto") or "",
        "idioma": _idioma(proyecto),
        "duracion": meta.get("duracion"),
        "bloques": bloques,
        "planos": planos,
        "reparto": reparto,
        "estilo": estilo,
        "params_assets": assets,
        "estilo_texto": p6_assets.guia_escrita(estilo) if estilo else "",
    }


def _idioma(proyecto):
    """El idioma del video (el del guion), con 'es' si no se sabe."""
    try:
        return p6_assets.p4_voz.idioma_de_salida(proyecto) or "es"
    except Exception:                                            # noqa: BLE001
        return "es"


# ----------------------------------------------------------------- el director

def _instruccion(ctx):
    guion = "\n".join(
        f"[{b['id']} · {_reloj(b['t_in']) if b['t_in'] is not None else '?'}] "
        f"{_texto(b['texto'], 700)}" for b in ctx["bloques"]) or "(sin guion)"
    # no todos los planos: uno de cada pocos basta para ver como es el video
    paso = max(1, len(ctx["planos"]) // 24)
    planos = "\n".join(f"- {p['id']}: {p['ruta']} — «{_texto(p['narracion'], 120)}»"
                       for p in ctx["planos"][::paso]) or "(sin planos todavia)"
    reparto = "\n".join(f"- {r['nombre']}: {r['ruta']}"
                        + (f" — {r['descripcion']}" if r["descripcion"] else "")
                        for r in ctx["reparto"]) or "(sin reparto)"
    return INSTRUCCION.format(
        idioma=ctx["idioma"], titulo=ctx["titulo"],
        duracion=_reloj(ctx["duracion"]) if ctx["duracion"] else "(sin voz todavia)",
        encargo=_texto(ctx["encargo"], 3000) or "(sin encargo escrito)",
        guion=guion, planos=planos, reparto=reparto,
        estilo=_texto(ctx["estilo_texto"], 2500) or "(sin guia escrita)",
        n_miniaturas=MINIATURAS, n_titulos=TITULOS, max_planos=MAX_REFS_PLANOS)


def _color(valor, defecto):
    valor = str(valor or "").strip()
    return valor if re.fullmatch(r"#[0-9A-Fa-f]{6}", valor) else defecto


def _limpiar_miniatura(cruda, indice, ctx):
    ids = {p["id"] for p in ctx["planos"]}
    nombres = {r["nombre"] for r in ctx["reparto"]}
    posicion = str(cruda.get("posicion") or "").strip().lower()
    texto = _texto(cruda.get("texto"), 60)
    palabras = set(texto.lower().split())
    return {
        "id": f"M{indice}",
        "concepto": _texto(cruda.get("concepto"), 400),
        "porque": _texto(cruda.get("porque"), 400),
        "emocion": _texto(cruda.get("emocion"), 80),
        "texto": texto,
        "resaltar": [str(r).strip() for r in (cruda.get("resaltar") or [])
                     if str(r).strip().lower() in palabras][:3],
        "posicion": posicion if posicion in POSICIONES else "izquierda",
        "color_texto": _color(cruda.get("color_texto"), "#FFFFFF"),
        "color_acento": _color(cruda.get("color_acento"), "#FFD400"),
        "color_contorno": _color(cruda.get("color_contorno"), "#000000"),
        "fuente": "arial",
        "prompt_imagen": _texto(cruda.get("prompt_imagen"), 2500),
        "planos": [str(s) for s in (cruda.get("planos") or []) if str(s) in ids][:MAX_REFS_PLANOS],
        "personajes": [str(n) for n in (cruda.get("personajes") or [])
                       if str(n) in nombres][:MAX_REFS_REPARTO],
        "versiones": [],
        "activa": None,
    }


def capitulos_con_tiempo(capitulos, bloques):
    """Los capitulos con su segundo, como YouTube los acepta.

    YouTube solo los reconoce si el primero esta en 0:00, hay al menos tres y
    cada uno dura 10 s o mas. Se arregla aqui en vez de confiar en el modelo.
    """
    tiempos = {b["id"]: b["t_in"] for b in bloques if b.get("t_in") is not None}
    salida = []
    for capitulo in capitulos or []:
        t = tiempos.get(str(capitulo.get("bloque") or ""))
        titulo = _texto(capitulo.get("titulo"), 80)
        if t is None or not titulo:
            continue
        salida.append({"bloque": str(capitulo["bloque"]), "titulo": titulo,
                       "t": float(t)})
    salida.sort(key=lambda c: c["t"])
    if salida:
        salida[0]["t"] = 0.0
    limpios = []
    for capitulo in salida:
        if limpios and capitulo["t"] - limpios[-1]["t"] < 10:
            continue
        limpios.append(capitulo)
    for capitulo in limpios:
        capitulo["reloj"] = _reloj(capitulo["t"])
    return limpios if len(limpios) >= 3 else []


def descripcion_completa(datos):
    """La descripcion tal como se pega en YouTube."""
    partes = [str(datos.get("descripcion") or "").strip()]
    capitulos = datos.get("capitulos") or []
    if capitulos:
        partes.append("\n".join(f"{c['reloj']} {c['titulo']}" for c in capitulos))
    if datos.get("hashtags"):
        partes.append(" ".join(datos["hashtags"]))
    return "\n\n".join(p for p in partes if p)


def dirigir(proyecto, estado, ajuste=None, avisar=None):
    """Pide la propuesta entera al director y la guarda. -> datos

    Las miniaturas ya DISENADAS de una propuesta anterior se pierden de la
    lista, pero sus ficheros se quedan en `publicacion/miniaturas/`.
    """
    avisar = avisar or (lambda *a, **k: None)
    ajuste = ajuste or {}
    ctx = contexto_del_video(proyecto, estado)
    if not ctx["bloques"]:
        raise RuntimeError("no hay guion: el director necesita saber de que va "
                           "el video")
    avisar(0.05, "el director esta viendo el video")
    texto, _sobre = cli_claude.ejecutar(
        _instruccion(ctx),
        modelo=ajuste.get("modelo") or cli_claude.MODELO_POR_DEFECTO,
        esfuerzo=ajuste.get("esfuerzo") or cli_claude.ESFUERZO_POR_DEFECTO,
        cwd=proyecto.raiz, tiempo_max_s=0, base_tiempo_s=TIEMPO_BASE_S,
        sistema=SISTEMA, herramientas_vetadas=HERRAMIENTAS_VETADAS,
        herramientas_permitidas=HERRAMIENTAS_PERMITIDAS,
        extra=["--no-session-persistence"], para="el director de publicacion")
    crudo = comun.extraer_json(texto, "la propuesta del director")
    if not isinstance(crudo, dict):
        raise RuntimeError("el director no ha devuelto un objeto JSON")
    avisar(0.9, "ordenando la propuesta")

    miniaturas = [_limpiar_miniatura(m, i, ctx) for i, m in
                  enumerate([m for m in (crudo.get("miniaturas") or [])
                             if isinstance(m, dict)][:MINIATURAS], 1)]
    titulos = []
    for t in crudo.get("titulos") or []:
        if not isinstance(t, dict) or not str(t.get("texto") or "").strip():
            continue
        try:
            emparejada = int(t.get("miniatura") or 0)
        except (TypeError, ValueError):
            emparejada = 0
        titulos.append({"texto": _texto(t["texto"], 100),
                        "porque": _texto(t.get("porque"), 300),
                        "miniatura": (f"M{emparejada}"
                                      if 1 <= emparejada <= len(miniaturas) else "")})
    datos = {
        "miniaturas": miniaturas,
        "titulos": titulos[:TITULOS],
        "titulo_elegido": titulos[0]["texto"] if titulos else "",
        "descripcion": str(crudo.get("descripcion") or "").strip(),
        "capitulos": capitulos_con_tiempo(crudo.get("capitulos"), ctx["bloques"]),
        "tags": [_texto(t, 60) for t in (crudo.get("tags") or []) if str(t).strip()][:25],
        "hashtags": [("#" + str(h).strip().lstrip("#")).replace(" ", "")
                     for h in (crudo.get("hashtags") or []) if str(h).strip()][:5],
        "ajuste": {"modelo": ajuste.get("modelo"), "esfuerzo": ajuste.get("esfuerzo")},
    }
    with _CERROJO:
        _guardar(proyecto, datos)
    avisar(1.0, f"{len(miniaturas)} miniaturas y {len(titulos)} titulos propuestos")
    return datos


# ------------------------------------------------------------- las miniaturas

def _a_16_9(png_bytes):
    """El fondo, recortado al centro a 16:9 y a 1280x720."""
    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.convert("RGB")
        w, h = im.size
        objetivo = ANCHO / ALTO
        if w / h > objetivo:
            nw = int(h * objetivo)
            im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        elif w / h < objetivo:
            nh = int(w / objetivo)
            im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
        return im.resize((ANCHO, ALTO), Image.LANCZOS)


def _referencias(proyecto, estado, ficha, ctx):
    """Estilo, planos y reparto, en ese orden de importancia."""
    rutas = []
    cache = os.path.join(carpeta(proyecto), "_refs")
    os.makedirs(cache, exist_ok=True)
    # LA MISMA LAMINA DE ESTILO QUE LOS PLANOS: una sola imagen con los
    # fotogramas del estilo (o el moodboard aprobado), no ocho sueltas
    try:
        p = p6_assets._con_defectos(ctx["params_assets"])
        rutas += [r["ruta"] for r in p6_assets._referencias_estilo(p, cache)]
    except Exception:                                            # noqa: BLE001
        pass                     # sin estilo: el prompt y los planos bastan
    por_id = {p["id"]: p["ruta"] for p in ctx["planos"]}
    for sid in ficha.get("planos") or []:
        if por_id.get(sid) and os.path.exists(por_id[sid]):
            rutas.append(por_id[sid])
    por_nombre = {r["nombre"]: r["ruta"] for r in ctx["reparto"]}
    for nombre in ficha.get("personajes") or []:
        if por_nombre.get(nombre) and os.path.exists(por_nombre[nombre]):
            rutas.append(por_nombre[nombre])
    return rutas


AVISO_SIN_TEXTO = ("\n\nYouTube thumbnail background, 16:9. Absolutely NO text, "
                   "letters, numbers, logos or captions anywhere in the image. "
                   "Keep the {lado} side clean and uncluttered: a title will be "
                   "placed there later. Match the drawing style of the reference "
                   "images exactly.")

LADOS = {"izquierda": "left", "derecha": "right", "arriba": "top", "abajo": "bottom"}


def disenar_miniatura(proyecto, estado, mid, motor="openai", nota="",
                      avisar=None):
    """Dibuja (o corrige con `nota`) el fondo de una miniatura y la compone.

    `motor` es el del selector: 'openai' o 'kie:<modelo>'. Con una nota y un
    fondo ya hecho es una CORRECCION: el fondo actual va como ultima
    referencia; con Kie el prompt es SOLO la nota, igual que en los planos.
    """
    avisar = avisar or (lambda *a, **k: None)
    with _CERROJO:
        datos = leer(proyecto)
        ficha = dict(_miniatura(datos, mid))
    ctx = contexto_del_video(proyecto, estado)
    nota = str(nota or "").strip()
    previo = _ruta_version(proyecto, ficha, "fondo")
    corrige = bool(nota and previo and os.path.exists(previo))
    solo_prompt = corrige and str(motor).startswith("kie")

    if solo_prompt:
        prompt, refs = nota, []
    else:
        prompt = (ficha.get("prompt_imagen") or ficha.get("concepto") or "")
        prompt += AVISO_SIN_TEXTO.format(lado=LADOS.get(ficha.get("posicion"), "left"))
        if nota:
            prompt += f"\n\nCHANGE REQUESTED BY THE EDITOR: {nota}"
        refs = _referencias(proyecto, estado, ficha, ctx)

    imagen = medios.motor("imagen_openai/imagen.py")
    avisar(0.1, f"{mid}: dibujando el fondo con {motor}")
    with imagen.contexto(corregir=corrige, actual=previo if corrige else None,
                         proveedor=motor, solo_prompt=solo_prompt):
        png, meta = imagen.generar(prompt, refs, quality=CALIDAD_OPENAI,
                                   tamano="apaisado")
    fondo = _a_16_9(png)

    with _CERROJO:
        datos = leer(proyecto)
        actual = _miniatura(datos, mid)
        numero = max([v["n"] for v in actual.get("versiones") or []] or [0]) + 1
        base = os.path.join(carpeta(proyecto), "miniaturas")
        nombre_fondo = f"{mid}_fondo_v{numero}.png"
        fondo.save(os.path.join(base, nombre_fondo), "PNG")
        actual.setdefault("versiones", []).append({
            "n": numero, "fondo": nombre_fondo, "motor": motor, "nota": nota,
            "coste": meta.get("coste"), "fecha": time.strftime("%Y-%m-%dT%H:%M:%S")})
        actual["activa"] = numero
        componer(proyecto, actual)
        _guardar(proyecto, datos)
    avisar(1.0, f"{mid} lista")
    return actual


def _ruta_version(proyecto, ficha, clave):
    activa = ficha.get("activa")
    for version in ficha.get("versiones") or []:
        if version.get("n") == activa and version.get(clave):
            return os.path.join(carpeta(proyecto), "miniaturas", version[clave])
    return None


# ------------------------------------------------------------------ el texto

def _lineas(palabras, fuente, ancho_max, draw):
    """Reparte las palabras en lineas que quepan. -> [[palabra]]"""
    lineas, actual = [], []
    for palabra in palabras:
        prueba = " ".join(actual + [palabra])
        if actual and draw.textlength(prueba, font=fuente) > ancho_max:
            lineas.append(actual)
            actual = [palabra]
        else:
            actual.append(palabra)
    if actual:
        lineas.append(actual)
    return lineas


def _caja(ficha):
    """El rectangulo donde va el texto, segun la posicion."""
    margen = 48
    posicion = ficha.get("posicion") or "izquierda"
    if posicion == "derecha":
        return (ANCHO * 0.45, margen, ANCHO - margen, ALTO - margen)
    if posicion == "arriba":
        return (margen, margen, ANCHO - margen, ALTO * 0.42)
    if posicion == "abajo":
        return (margen, ALTO * 0.58, ANCHO - margen, ALTO - margen)
    return (margen, margen, ANCHO * 0.55, ALTO - margen)


def componer(proyecto, ficha):
    """Pone el texto de la ficha sobre su fondo y guarda la final. -> nombre

    Las letras van en MAYUSCULAS, gruesas, con contorno y una sombra suave:
    es lo que se lee a 200 px en un movil. El tamano es el mayor que cabe en
    la caja (cuatro lineas en un lateral, dos arriba o abajo).
    """
    ruta_fondo = _ruta_version(proyecto, ficha, "fondo")
    if not ruta_fondo or not os.path.exists(ruta_fondo):
        return None
    lienzo = Image.open(ruta_fondo).convert("RGBA")
    texto = str(ficha.get("texto") or "").strip().upper()
    if texto:
        x0, y0, x1, y1 = _caja(ficha)
        ancho_max, alto_max = x1 - x0, y1 - y0
        palabras = texto.split()
        prueba = ImageDraw.Draw(lienzo)
        # en un lateral la columna es estrecha y caben mas lineas cortas; arriba
        # o abajo la franja es baja y mas de dos se comen la imagen
        max_lineas = 2 if ficha.get("posicion") in ("arriba", "abajo") else 4
        tam, fuente, lineas = 190, None, []
        while tam >= 40:
            fuente = tipografia.fuente(ficha.get("fuente") or "arial", tam, True)
            lineas = _lineas(palabras, fuente, ancho_max, prueba)
            alto = len(lineas) * tam * 1.05
            if (len(lineas) <= max_lineas and alto <= alto_max
                    and all(prueba.textlength(" ".join(l), font=fuente) <= ancho_max
                            for l in lineas)):
                break
            tam -= 6
        interlinea = tam * 1.05
        alto_total = len(lineas) * interlinea
        y = y0 + (alto_max - alto_total) / 2
        resaltar = {r.upper() for r in ficha.get("resaltar") or []}
        contorno = max(4, tam // 11)

        sombra = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
        capa = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
        ds, dc = ImageDraw.Draw(sombra), ImageDraw.Draw(capa)
        for linea in lineas:
            ancho_linea = prueba.textlength(" ".join(linea), font=fuente)
            if ficha.get("posicion") in ("arriba", "abajo"):
                x = x0 + (ancho_max - ancho_linea) / 2
            elif ficha.get("posicion") == "derecha":
                x = x1 - ancho_linea
            else:
                x = x0
            for palabra in linea:
                color = (ficha.get("color_acento") if palabra.strip(".,!?¡¿:;") in resaltar
                         else ficha.get("color_texto")) or "#FFFFFF"
                ds.text((x + contorno, y + contorno), palabra, font=fuente,
                        fill=(0, 0, 0, 170), stroke_width=contorno,
                        stroke_fill=(0, 0, 0, 170))
                dc.text((x, y), palabra, font=fuente, fill=color,
                        stroke_width=contorno,
                        stroke_fill=ficha.get("color_contorno") or "#000000")
                x += prueba.textlength(palabra + " ", font=fuente)
            y += interlinea
        sombra = sombra.filter(ImageFilter.GaussianBlur(contorno))
        lienzo = Image.alpha_composite(Image.alpha_composite(lienzo, sombra), capa)

    activa = ficha.get("activa")
    nombre = f"{ficha['id']}_v{activa}.png"
    lienzo.convert("RGB").save(os.path.join(carpeta(proyecto), "miniaturas", nombre),
                               "PNG")
    for version in ficha.get("versiones") or []:
        if version.get("n") == activa:
            version["final"] = nombre
    return nombre


CAMPOS_EDITABLES = ("texto", "resaltar", "posicion", "color_texto",
                    "color_acento", "color_contorno", "fuente", "prompt_imagen")


def editar_miniatura(proyecto, mid, cambios):
    """Cambia el texto, la posicion o los colores y recompone. Sin pagar nada."""
    with _CERROJO:
        datos = leer(proyecto)
        ficha = _miniatura(datos, mid)
        for clave in CAMPOS_EDITABLES:
            if clave not in cambios:
                continue
            valor = cambios[clave]
            if clave == "posicion" and valor not in POSICIONES:
                raise ValueError(f"posicion es una de {', '.join(POSICIONES)}")
            if clave.startswith("color_"):
                valor = _color(valor, ficha.get(clave) or "#FFFFFF")
            if clave == "fuente" and str(valor).lower() not in tipografia.FUENTES:
                raise ValueError(f"fuente es una de {', '.join(tipografia.FUENTES)}")
            if clave == "resaltar":
                valor = [str(v).strip() for v in (valor or []) if str(v).strip()][:3]
            if clave in ("texto", "prompt_imagen"):
                valor = _texto(valor, 60 if clave == "texto" else 2500)
            ficha[clave] = valor
        if "activa" in cambios:
            numeros = {v["n"] for v in ficha.get("versiones") or []}
            if cambios["activa"] not in numeros:
                raise ValueError("esa version no existe")
            ficha["activa"] = cambios["activa"]
        componer(proyecto, ficha)
        _guardar(proyecto, datos)
        return ficha


CAMPOS_TEXTO = ("titulo_elegido", "descripcion", "tags", "hashtags", "capitulos")


def editar_textos(proyecto, cambios):
    """Guarda lo que se retoca a mano en titulos, descripcion y tags."""
    with _CERROJO:
        datos = leer(proyecto)
        for clave in CAMPOS_TEXTO:
            if clave not in cambios:
                continue
            valor = cambios[clave]
            if clave in ("tags", "hashtags"):
                valor = [str(v).strip() for v in (valor or []) if str(v).strip()]
            elif clave == "capitulos":
                valor = [{"titulo": _texto(c.get("titulo"), 80),
                          "t": float(c.get("t") or 0),
                          "reloj": _reloj(c.get("t") or 0),
                          "bloque": str(c.get("bloque") or "")}
                         for c in (valor or []) if isinstance(c, dict)]
            else:
                valor = str(valor or "")
            datos[clave] = valor
        _guardar(proyecto, datos)
        return datos
