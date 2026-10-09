#!/usr/bin/env bash
# Arranca el login (Node), el estudio (Python) y nginx dentro del contenedor.
set -euo pipefail
RAIZ=/opt/as-video-studio
D=/data

: "${DOMINIO:?Falta la variable DOMINIO (ej: estudio.midominio.com)}"
CUENTA="${ESTUDIO_CUENTA:-estudio}"

mkdir -p $D/home $D/login $D/datos/{proyectos,secretos,banco/presets}
[ -f $D/datos/tarifas.json ] || cp $RAIZ/app/tarifas.json $D/datos/tarifas.json
[ -f $D/datos/reglas.json ] || cp $RAIZ/app/motores/reglas/reglas.json $D/datos/reglas.json
# Las reglas aprendidas viven en el volumen para que sobrevivan a cada deploy
ln -sf $D/datos/reglas.json $RAIZ/app/motores/reglas/reglas.json
[ -f $D/login/secreto ] || openssl rand -hex 48 > $D/login/secreto
chown -R studio:studio $D $RAIZ/app/cache $RAIZ/app/motores/reglas
chmod 700 $D/datos/secretos $D/login

LOTES="${ESTUDIO_LOTES:-$(nproc)}"; [ "$LOTES" -gt 16 ] && LOTES=16

# ---- Entorno del estudio
export PUERTO=8110 HOME=$D/home ESTUDIO_LOTES=$LOTES \
  ESTUDIO_PROYECTOS=$D/datos/proyectos ESTUDIO_PRESETS=$D/datos/presets.json \
  ESTUDIO_BANCO=$D/datos/banco ESTUDIO_BANCO_PRESETS=$D/datos/banco/presets \
  ESTUDIO_SECRETOS=$D/datos/secretos ESTUDIO_ENV=$D/datos/secretos/.env \
  ESTUDIO_RECETAS=$D/datos/recetas.json ESTUDIO_AJUSTES=$D/datos/ajustes.json \
  ESTUDIO_TARIFAS=$D/datos/tarifas.json ESTUDIO_ESTADISTICAS=$D/datos/estadisticas.json \
  ESTUDIO_COSTE_GLOBAL=$D/datos/coste_global.jsonl ESTUDIO_BITACORA_GLOBAL=$D/datos/bitacora_global.jsonl \
  ESTUDIO_MOTORES=$RAIZ/app/motores ESTUDIO_FUENTES=/usr/local/share/fonts/estudio \
  ESTUDIO_EDGE=/usr/local/bin/estudio-edge

# ---- Entorno del login
export NODE_ENV=production HOST=127.0.0.1 PORT=3000 \
  APP_URL="https://$DOMINIO" APP_ORIGENES="https://$DOMINIO" \
  DATA_DIR=$D/login SESSION_SECRET="$(cat $D/login/secreto)" \
  SECURE_COOKIES=true TRUST_PROXY=2

# ---- Contrasena: se crea la primera vez
cd $RAIZ/login
if gosu studio node bin/user.js list 2>/dev/null | grep -q 'No hay cuentas'; then
  PW="${ESTUDIO_PASSWORD:-$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-20)}"
  printf '%s\n' "$PW" | gosu studio node bin/user.js poner "$CUENTA" --password-stdin >/dev/null
  echo "=================================================="
  echo " Cuenta creada. Contrasena: $PW"
  echo " (Cambiala luego quitando la variable o con: node bin/user.js passwd $CUENTA)"
  echo "=================================================="
fi

# ---- Arranque
gosu studio node $RAIZ/login/server.js &
cd $RAIZ/app
gosu studio $RAIZ/venv/bin/python $RAIZ/app/app.py --puerto 8110 --host 127.0.0.1 &
nginx -g 'daemon off;' &

# Si cualquiera de los tres se cae, el contenedor se reinicia
wait -n
echo "Un proceso se ha parado; reiniciando contenedor."
exit 1
