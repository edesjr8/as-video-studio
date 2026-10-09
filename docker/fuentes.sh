#!/usr/bin/env bash
# Enlaza las fuentes con los nombres que usa el estudio (misma logica que instalar.sh)
set -e
C="${CARPETA_FUENTES:-/usr/local/share/fonts/estudio}"
mkdir -p "$C"
CONF=/etc/fonts/conf.d/61-as-video-studio.conf
printf '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n' > "$CONF"

enlaza() {
  local destino="$1"; shift
  local c e=""
  for c in "$@"; do
    e="$(find /usr/share/fonts /usr/local/share/fonts -path "$C" -prune -o -iname "$c" -type f -print 2>/dev/null | head -1)"
    [ -n "$e" ] && break
  done
  [ -n "$e" ] && ln -sf "$e" "$C/$destino"
}
alias_f() {
  local fam="$1" fich="$2" real nombre
  real="$(readlink -f "$C/$fich" 2>/dev/null || true)"; [ -n "$real" ] || return 0
  nombre="$(fc-query -f '%{family[0]}' "$real" 2>/dev/null || true)"; [ -n "$nombre" ] || return 0
  [ "$nombre" = "$fam" ] && return 0
  cat >> "$CONF" <<XML
  <match target="pattern">
    <test qual="any" name="family"><string>$fam</string></test>
    <edit name="family" mode="assign" binding="same"><string>$nombre</string></edit>
  </match>
XML
}

enlaza verdana.ttf   Verdana.ttf DejaVuSans.ttf
enlaza verdanab.ttf  Verdana_Bold.ttf DejaVuSans-Bold.ttf
enlaza georgia.ttf   Georgia.ttf DejaVuSerif.ttf
enlaza georgiab.ttf  Georgia_Bold.ttf DejaVuSerif-Bold.ttf
enlaza arial.ttf     Arial.ttf DejaVuSans.ttf
enlaza arialbd.ttf   Arial_Bold.ttf DejaVuSans-Bold.ttf
enlaza tahoma.ttf    Tahoma.ttf Verdana.ttf DejaVuSans.ttf || true
enlaza tahomabd.ttf  Tahoma_Bold.ttf Verdana_Bold.ttf DejaVuSans-Bold.ttf || true
enlaza consola.ttf   Consolas.ttf DejaVuSansMono.ttf || true
enlaza consolab.ttf  Consolas_Bold.ttf DejaVuSansMono-Bold.ttf || true
enlaza segoeui.ttf   Segoe_UI.ttf DejaVuSans.ttf || true
enlaza segoeuib.ttf  Segoe_UI_Bold.ttf DejaVuSans-Bold.ttf || true

for f in "Verdana:verdana.ttf" "Georgia:georgia.ttf" "Arial:arial.ttf" \
         "Tahoma:tahoma.ttf" "Consolas:consola.ttf" "Segoe UI:segoeui.ttf"; do
  alias_f "${f%%:*}" "${f##*:}"
done
printf '</fontconfig>\n' >> "$CONF"
fc-cache -f >/dev/null 2>&1
echo "Fuentes listas: $(ls "$C" | tr '\n' ' ')"
