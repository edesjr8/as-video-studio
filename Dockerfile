# AS Video Studio en un contenedor (para Easypanel)
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    RAIZ=/opt/as-video-studio \
    CARPETA_FUENTES=/usr/local/share/fonts/estudio

# Paquetes base + ffmpeg + python + nginx (portero interno con auth_request)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg tini gosu \
      ffmpeg python3 python3-venv python3-pip \
      nginx fontconfig cabextract xfonts-utils software-properties-common \
      fonts-dejavu-core build-essential \
    && rm -rf /var/lib/apt/lists/*

# Fuentes de Microsoft (Verdana, Georgia, Arial). Si la descarga falla, se usa DejaVu.
RUN add-apt-repository -y multiverse && apt-get update \
    && echo ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true | debconf-set-selections \
    && (apt-get install -y --no-install-recommends ttf-mscorefonts-installer || echo "AVISO: sin fuentes de Microsoft, se usa DejaVu") \
    && rm -rf /var/lib/apt/lists/*

# Navegador que dibuja los planos (Microsoft Edge)
RUN curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-edge.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-edge.gpg] https://packages.microsoft.com/repos/edge stable main" > /etc/apt/sources.list.d/microsoft-edge.list \
    && apt-get update && apt-get install -y --no-install-recommends microsoft-edge-stable \
    && rm -rf /var/lib/apt/lists/* \
    && printf '#!/bin/sh\nexec /usr/bin/microsoft-edge --no-sandbox --disable-dev-shm-usage "$@"\n' > /usr/local/bin/estudio-edge \
    && chmod 755 /usr/local/bin/estudio-edge

# Node 22 + CLI de Claude
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code

# Usuario sin privilegios
RUN useradd --system --create-home --home-dir /data/home --shell /usr/sbin/nologin studio

# El codigo
COPY . ${RAIZ}/app
RUN python3 -m venv ${RAIZ}/venv \
    && ${RAIZ}/venv/bin/pip install --no-cache-dir --upgrade pip wheel \
    && ${RAIZ}/venv/bin/pip install --no-cache-dir -r ${RAIZ}/app/requirements.txt \
    && cp -r ${RAIZ}/app/despliegue/login ${RAIZ}/login \
    && cd ${RAIZ}/login && npm install --omit=dev --no-audit --no-fund \
    && mkdir -p ${RAIZ}/app/cache \
    && chown -R studio:studio ${RAIZ}

# Fuentes enlazadas con los nombres que espera el estudio
RUN bash ${RAIZ}/app/docker/fuentes.sh

COPY docker/nginx.conf /etc/nginx/nginx.conf

EXPOSE 80
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash", "/opt/as-video-studio/app/docker/entrypoint.sh"]
