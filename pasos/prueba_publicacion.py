"""
Publicacion: el director, las miniaturas y los textos (pasos/publicacion.py).

Sin red y sin CLI: el director y el motor de imagen van DOBLADOS. Lo que se
comprueba es lo que hace el Estudio con lo que le devuelven -- limpiar la
propuesta, colocar los capitulos donde YouTube los acepta, componer el texto
encima del fondo y no volver a pagar una imagen por cambiar una palabra.

    python pasos/prueba_publicacion.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import types

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "pasos"))

from PIL import Image  # noqa: E402

import publicacion  # noqa: E402

CARPETA = os.path.join(tempfile.gettempdir(), "estudio_prueba_publicacion")

_fallos = []
_comprobaciones = [0]


def ok(condicion, mensaje):
    _comprobaciones[0] += 1
    if not condicion:
        _fallos.append(mensaje)
        print(f"    FALLO: {mensaje}")
    else:
        print(f"  ok   {mensaje}")
    return bool(condicion)


def igual(obtenido, esperado, mensaje):
    return ok(obtenido == esperado, f"{mensaje} (obtenido {obtenido!r}, "
                                    f"esperado {esperado!r})")


def titulo(texto):
    print(f"\n[{texto}]")


# ------------------------------------------------------------------ los dobles

BLOQUES = [{"id": f"B0{i}", "texto": f"texto del bloque {i}", "t_in": t}
           for i, t in enumerate([0.4, 12.0, 15.0, 40.0, 75.5], 1)]

CONTEXTO = {
    "titulo": "El puerto", "encargo": "un video sobre un puerto", "idioma": "es",
    "duracion": 95.0, "bloques": BLOQUES,
    "planos": [{"id": "S001", "narracion": "abre el puerto", "ruta": ""},
               {"id": "S002", "narracion": "la lancha", "ruta": ""}],
    "reparto": [{"nombre": "capitana", "ruta": "", "descripcion": ""}],
    "estilo": {}, "params_assets": {}, "estilo_texto": "",
}

PROPUESTA = {
    "miniaturas": [
        {"concepto": "la capitana mira el barco hundido", "porque": "pregunta",
         "emocion": "asombro", "texto": "Nadie lo vio venir",
         "resaltar": ["nadie", "inventada"], "posicion": "derecha",
         "color_texto": "#ffffff", "color_acento": "amarillo",
         "prompt_imagen": "a captain staring at a sinking ship",
         "planos": ["S001", "S999"], "personajes": ["capitana", "fantasma"]},
        {"concepto": "el puerto de noche", "texto": "EL PUERTO",
         "posicion": "en medio"},
    ],
    "titulos": [{"texto": "El puerto que nadie vigilaba", "porque": "curiosidad",
                 "miniatura": 1},
                {"texto": "Otro titulo", "miniatura": 7},
                {"texto": "   "}],
    "descripcion": "Un puerto. Una noche.",
    "capitulos": [{"bloque": "B01", "titulo": "El principio"},
                  {"bloque": "B02", "titulo": "Demasiado pronto"},
                  {"bloque": "B03", "titulo": "Pegado al anterior"},
                  {"bloque": "B04", "titulo": "La lancha"},
                  {"bloque": "B05", "titulo": "El final"},
                  {"bloque": "B99", "titulo": "No existe"}],
    "tags": ["puerto", "historia"],
    "hashtags": ["puerto", "#historia"],
}


class MotorDoble:
    """El motor de imagen, sin red: devuelve un PNG apaisado y apunta la llamada."""

    def __init__(self):
        self.llamadas = []
        self._contexto = None

    def contexto(self, **kwargs):
        motor = self

        class _Ctx:
            def __enter__(self_):
                motor._contexto = kwargs
                return self_

            def __exit__(self_, *exc):
                motor._contexto = None
                return False
        return _Ctx()

    def generar(self, prompt, referencias, **kwargs):
        self.llamadas.append({"prompt": prompt, "refs": list(referencias),
                              "ctx": dict(self._contexto or {}), **kwargs})
        im = Image.new("RGB", (1536, 1024), (30, 60, 110))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue(), {"coste": 0.04, "segundos": 1.0}

    def normalizar(self, ruta, cache):
        return ruta


def main():
    shutil.rmtree(CARPETA, ignore_errors=True)
    os.makedirs(CARPETA)
    proyecto = types.SimpleNamespace(raiz=CARPETA, id="prueba")
    estado = object()

    motor = MotorDoble()
    publicacion.medios.motor = lambda ruta: motor
    publicacion.contexto_del_video = lambda proyecto, estado: json.loads(
        json.dumps(CONTEXTO))
    pedidas = []

    def director(instruccion, **kwargs):
        pedidas.append({"instruccion": instruccion, **kwargs})
        return "```json\n" + json.dumps(PROPUESTA) + "\n```", {}
    publicacion.cli_claude.ejecutar = director

    titulo("el director")
    datos = publicacion.dirigir(proyecto, estado, ajuste={"modelo": "sonnet"})
    instruccion = pedidas[-1]["instruccion"]
    ok("[B04 · 0:40]" in instruccion, "el guion le llega con el reloj de cada bloque")
    ok("S001" in instruccion and "capitana" in instruccion,
       "y la lista de planos y del reparto para mirarlos")
    igual(pedidas[-1]["herramientas_permitidas"], ("Read",),
          "puede abrir imagenes con Read y nada mas")
    igual([m["id"] for m in datos["miniaturas"]], ["M1", "M2"],
          "las miniaturas se numeran M1, M2…")
    m1, m2 = datos["miniaturas"]
    igual(m1["planos"], ["S001"], "un plano que no existe se cae")
    igual(m1["personajes"], ["capitana"], "un personaje que no esta en el reparto se cae")
    igual(m1["resaltar"], ["nadie"], "solo se resaltan palabras que estan en el texto")
    igual(m1["color_acento"], "#FFD400", "un color mal escrito vuelve al de fabrica")
    igual(m2["posicion"], "izquierda", "una posicion inventada vuelve a la izquierda")
    igual([t["miniatura"] for t in datos["titulos"]], ["M1", ""],
          "cada titulo dice su miniatura, y una que no existe se queda en blanco")
    igual(len(datos["titulos"]), 2, "un titulo vacio se descarta")
    igual(datos["titulo_elegido"], "El puerto que nadie vigilaba",
          "el primero queda elegido")
    igual(datos["hashtags"], ["#puerto", "#historia"], "los hashtags llevan su #")

    titulo("los capitulos, como YouTube los acepta")
    capitulos = datos["capitulos"]
    igual(capitulos[0]["t"], 0.0, "el primero en 0:00 aunque el bloque empiece en 0,4 s")
    igual([c["bloque"] for c in capitulos], ["B01", "B02", "B04", "B05"],
          "se cae el que queda a menos de 10 s del anterior y el de un bloque que no existe")
    igual([c["reloj"] for c in capitulos], ["0:00", "0:12", "0:40", "1:15"],
          "con su reloj")
    igual(publicacion.capitulos_con_tiempo(
        [{"bloque": "B01", "titulo": "a"}, {"bloque": "B04", "titulo": "b"}], BLOQUES),
        [], "con menos de tres no hay capitulos: YouTube no los reconoceria")
    completa = publicacion.descripcion_completa(datos)
    ok(completa.startswith("Un puerto. Una noche.") and "0:40 La lancha" in completa
       and completa.rstrip().endswith("#puerto #historia"),
       "la descripcion copiada lleva texto, capitulos y hashtags, en ese orden")

    titulo("diseñar una miniatura")
    ficha = publicacion.disenar_miniatura(proyecto, estado, "M1", motor="openai")
    llamada = motor.llamadas[-1]
    ok("a captain staring at a sinking ship" in llamada["prompt"]
       and "NO text" in llamada["prompt"] and "right side" in llamada["prompt"],
       "el prompt es el del director, sin texto y con el lado libre donde va el titulo")
    igual(llamada["ctx"].get("proveedor"), "openai", "se pide al motor elegido")
    ok(not llamada["ctx"].get("corregir"), "la primera vez no es una correccion")
    igual(ficha["activa"], 1, "queda la version 1")
    final = os.path.join(CARPETA, "publicacion", "miniaturas", "M1_v1.png")
    ok(os.path.exists(final), "con su miniatura compuesta")
    with Image.open(final) as im:
        igual(im.size, (1280, 720), "a la medida de YouTube")
        fondo = Image.open(os.path.join(CARPETA, "publicacion", "miniaturas",
                                        "M1_fondo_v1.png"))
        ok(im.convert("RGB").getpixel((1000, 360)) != fondo.getpixel((1000, 360))
           or im.convert("RGB").tobytes() != fondo.tobytes(),
           "y el texto encima del fondo")

    titulo("cambiar el texto no paga otra imagen")
    llamadas = len(motor.llamadas)
    antes = open(final, "rb").read()
    ficha = publicacion.editar_miniatura(proyecto, "M1", {
        "texto": "OTRO TEXTO", "posicion": "abajo", "color_texto": "#00ff00"})
    igual(len(motor.llamadas), llamadas, "editar el texto no llama al motor")
    ok(open(final, "rb").read() != antes, "pero la miniatura se recompone")
    try:
        publicacion.editar_miniatura(proyecto, "M1", {"posicion": "en medio"})
        ok(False, "una posicion que no existe se rechaza")
    except ValueError:
        ok(True, "una posicion que no existe se rechaza")

    titulo("corregir con nota")
    publicacion.disenar_miniatura(proyecto, estado, "M1", motor="kie:nano-banana-2",
                                  nota="que la capitana sonria")
    llamada = motor.llamadas[-1]
    igual(llamada["prompt"], "que la capitana sonria",
          "con Kie y nota, el prompt es SOLO la nota")
    ok(llamada["ctx"].get("corregir") and llamada["ctx"].get("solo_prompt")
       and str(llamada["ctx"].get("actual")).endswith("M1_fondo_v1.png"),
       "y va como correccion del fondo actual")
    publicacion.disenar_miniatura(proyecto, estado, "M1", motor="openai",
                                  nota="mas luz")
    llamada = motor.llamadas[-1]
    ok("a captain staring" in llamada["prompt"] and "mas luz" in llamada["prompt"],
       "con OpenAI va el prompt completo con la nota dentro")
    datos = publicacion.leer(proyecto)
    m1 = datos["miniaturas"][0]
    igual([v["n"] for v in m1["versiones"]], [1, 2, 3], "cada diseño es una version")
    publicacion.editar_miniatura(proyecto, "M1", {"activa": 1})
    igual(publicacion.leer(proyecto)["miniaturas"][0]["activa"], 1,
          "y se puede volver a una anterior")

    titulo("los textos a mano")
    datos = publicacion.editar_textos(proyecto, {
        "titulo_elegido": "Mi titulo", "tags": ["a", " ", "b"], "otra": "x"})
    igual(datos["titulo_elegido"], "Mi titulo", "el titulo elegido se guarda")
    igual(datos["tags"], ["a", "b"], "los tags vacios se caen")
    ok("otra" not in datos, "un campo que no existe no se guarda")

    print("\n" + "=" * 70)
    if _fallos:
        print(f"FALLOS: {len(_fallos)} de {_comprobaciones[0]} comprobaciones")
        for fallo in _fallos:
            print(f"  - {fallo}")
        return 1
    print(f"PUBLICACION OK: {_comprobaciones[0]} comprobaciones pasan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
