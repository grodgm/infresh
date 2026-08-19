# Meeting Companion — MVP

Companion de reuniones que escucha el micrófono, transcribe en vivo, detecta a cada
participante por su voz (análisis de pitch, sin servicios externos) y muestra
sugerencias de una superinteligencia (Claude) que nutre la conversación:
insights, preguntas, acciones, riesgos y datos.

## Correr

```bash
.venv/bin/python server.py
```

Abrir **http://localhost:8787** en **Google Chrome** (el reconocimiento de voz
Web Speech API solo funciona bien en Chrome) y tocar **▶ Iniciar reunión**.

- Sin micrófono: botón **Simular** genera una conversación de prueba.
- Click en el nombre de un participante para renombrarlo.

## Clave API (modo live)

Sin clave, corre en modo demo con sugerencias de ejemplo. Para sugerencias
reales de Claude, crear un archivo `.env` en esta carpeta:

```
ANTHROPIC_API_KEY=sk-ant-...
```

y reiniciar el servidor. Usa el modelo `claude-opus-5` con effort bajo para
mantener la latencia del ciclo (una llamada cada ~15 s, solo si hubo
transcript nuevo, con prompt caching en el system prompt).

## Cómo funciona la detección de hablantes

MVP sin diarización externa: se estima la frecuencia fundamental (pitch) de la
voz por autocorrelación sobre el audio del micrófono, y cada frase final del
reconocedor se asigna al cluster de pitch más cercano (~2 semitonos de
tolerancia). Funciona mejor cuando las voces difieren en tono (ej. voces
graves vs. agudas); voces muy similares pueden mezclarse — es el trade-off
del MVP. El siguiente paso natural es reemplazarlo por diarización real
(AssemblyAI / Deepgram) manteniendo el resto igual.

## Archivos

- `server.py` — servidor local (Python stdlib) + llamada a Claude (`/api/suggest`).
- `static/index.html` — toda la UI: audio, pitch, clustering, transcript, tarjetas.
