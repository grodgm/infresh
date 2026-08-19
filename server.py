#!/usr/bin/env python3
"""Meeting Companion MVP — servidor local.

Sirve la UI estática y expone POST /api/suggest, que manda el transcript
de la reunión a Claude y devuelve sugerencias estructuradas en JSON.

Uso:
    .venv/bin/python server.py          # http://localhost:8787
Clave API:
    export ANTHROPIC_API_KEY=sk-...     # o ponerla en un archivo .env
Sin clave, el frontend funciona en modo demo (sugerencias simuladas).
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
PORT = 8787
MODEL = "claude-opus-5"


def load_env_file() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

client = None
if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
    import anthropic

    client = anthropic.Anthropic()

SYSTEM_PROMPT = """Sos Infresh, una inteligencia que facilita reuniones de equipo en vivo. Tu trabajo no es \
documentar: es mejorar la calidad del pensamiento grupal mientras sucede. Recibís el contexto \
organizacional (prioridades estratégicas del liderazgo, contexto del equipo, objetivo y agenda de la \
reunión), datos de participación por persona, y el transcript parcial (hablantes detectados por voz).

Devolvé EXCLUSIVAMENTE un objeto JSON válido, sin markdown ni texto extra, con esta forma:
{"suggestions": [{"type": "...", "title": "...", "text": "..."}]}

Tipos de intervención (elegí el que más valor aporte AHORA):
- "foco": la conversación se desvió del tema de agenda actual o del objetivo. Nombralo y proponé volver.
- "decision": el grupo está tomando una decisión sin nombrarla. Explicitala para que la confirmen o la desafíen.
- "equilibrio": mirando la participación, alguien domina la conversación o alguien casi no habló. Sugerí dar voz (con nombres).
- "alineacion": conectá lo que están hablando con una prioridad estratégica de la organización (a favor o en tensión).
- "pregunta": una pregunta puntual que destrabaría o profundizaría la conversación estancada.
- "accion": un próximo paso concreto que se desprende de lo hablado (con responsable si se nombró).
- "riesgo": algo que están pasando por alto y puede costarles caro.
- "insight": una conexión o reencuadre que los participantes no están viendo.
- "dato": contexto factual relevante que eleva la discusión.

Reglas:
- Máximo 2 intervenciones por llamada. Si el tramo nuevo no amerita nada valioso, devolvé {"suggestions": []}. Intervenir de más es peor que no intervenir.
- Priorizá foco/decision/equilibrio/alineacion (facilitación) sobre insight/dato (contenido).
- "equilibrio" solo con señal clara (una persona >60% de las palabras, o alguien en silencio con la reunión avanzada). No lo repitas si ya lo dijiste.
- title: máximo 6 palabras. text: máximo 30 palabras. Español rioplatense, directo, sin relleno. Hablale al equipo, no sobre el equipo.
- No repitas intervenciones ya dadas (te paso los títulos previos).
- El transcript viene de reconocimiento de voz: puede tener errores, interpretá con criterio."""

SUMMARY_PROMPT = """Sos Infresh. La reunión terminó. Con el contexto organizacional y el transcript completo, \
generá el cierre que se convierte en memoria organizacional.

Devolvé EXCLUSIVAMENTE un objeto JSON válido, sin markdown ni texto extra:
{"resumen": "...", "decisiones": ["..."], "acciones": [{"que": "...", "quien": "..."}], "pendientes": ["..."], "compartir_con": [{"quien": "...", "motivo": "..."}]}

Reglas:
- resumen: máximo 80 palabras, qué pasó y qué cambió.
- decisiones: solo decisiones realmente tomadas (no ideas sueltas). Si no hubo, lista vacía.
- acciones: compromisos concretos; "quien" con el nombre si se dijo, sino "sin asignar".
- pendientes: temas abiertos que quedaron sin resolver.
- compartir_con: roles o equipos de la organización que deberían ver esto (ej: "Liderazgo/C-level", "Equipo de diseño"), con motivo de una línea. Basate en las prioridades estratégicas y en lo hablado.
- Español rioplatense, directo. Sin inventar nada que no esté en el transcript."""

DEMO_SUGGESTIONS = [
    {"type": "dato", "title": "Modo demo activo", "text": "No hay ANTHROPIC_API_KEY configurada. Estas sugerencias son simuladas; con la clave, Claude analiza la conversación real."},
    {"type": "pregunta", "title": "¿Cuál es el objetivo?", "text": "¿Qué resultado concreto quieren tener al terminar esta reunión?"},
    {"type": "insight", "title": "Están alineados sin saberlo", "text": "Dos participantes proponen lo mismo con palabras distintas. Nombrarlo puede cerrar el debate."},
    {"type": "accion", "title": "Definir un responsable", "text": "Se mencionó una tarea sin dueño. Asignarla ahora evita que se pierda."},
    {"type": "riesgo", "title": "Decisión sin datos", "text": "Están por decidir en base a una suposición no validada. Vale chequearla primero."},
]


def build_user_message(payload: dict, closing: bool = False) -> str:
    lines = payload.get("transcript", [])[-300:]
    previous = payload.get("previous_titles", [])[-20:]
    cfg = payload.get("config", {}) or {}
    participacion = payload.get("participacion", []) or []
    parts = []
    if cfg.get("prioridades"):
        parts.append(f"PRIORIDADES ESTRATÉGICAS DE LA ORGANIZACIÓN:\n{cfg['prioridades']}")
    if cfg.get("contexto"):
        parts.append(f"CONTEXTO DEL EQUIPO:\n{cfg['contexto']}")
    if cfg.get("objetivo"):
        parts.append(f"OBJETIVO DE LA REUNIÓN: {cfg['objetivo']}")
    if cfg.get("agenda"):
        parts.append("AGENDA: " + " / ".join(cfg["agenda"]))
    if not closing and cfg.get("tema_actual"):
        parts.append(f"TEMA DE AGENDA ACTUAL: {cfg['tema_actual']}")
    if participacion:
        total = sum(p.get("palabras", 0) for p in participacion) or 1
        parts.append("PARTICIPACIÓN (palabras): " + ", ".join(
            f"{p.get('speaker', '?')}: {p.get('palabras', 0)} ({round(100 * p.get('palabras', 0) / total)}%)"
            for p in participacion
        ))
    if payload.get("minutos") is not None:
        parts.append(f"MINUTOS DE REUNIÓN: {payload['minutos']}")
    transcript_text = "\n".join(
        f"[{line.get('speaker', '?')}] {line.get('text', '')}" for line in lines
    )
    label = "TRANSCRIPT COMPLETO DE LA REUNIÓN:" if closing else "TRANSCRIPT DE LA REUNIÓN HASTA AHORA:"
    parts.append(f"{label}\n{transcript_text or '(vacío)'}")
    if previous and not closing:
        parts.append("INTERVENCIONES YA DADAS (no repetir):\n" + "\n".join(f"- {t}" for t in previous))
    return "\n\n".join(parts)


def parse_suggestions(text: str) -> list:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return []
    data = json.loads(match.group(0))
    out = []
    for item in data.get("suggestions", []):
        if isinstance(item, dict) and item.get("text"):
            out.append(
                {
                    "type": item.get("type", "insight"),
                    "title": item.get("title", ""),
                    "text": item["text"],
                }
            )
    return out[:3]


DEMO_SUMMARY = {
    "demo": True,
    "resumen": "Modo demo: sin ANTHROPIC_API_KEY el cierre es de ejemplo. El equipo revisó la retención de la beta, acordó probar registro diferido y dejó abierto el modelo de precios.",
    "decisiones": ["Probar onboarding con registro diferido guardando progreso local"],
    "acciones": [{"que": "Prototipar el registro diferido", "quien": "Marito"}],
    "pendientes": ["Modelo de precios: único vs. suscripción"],
    "compartir_con": [{"quien": "Liderazgo/C-level", "motivo": "Impacta el plan de lanzamiento de septiembre"}],
}


def call_claude(system_prompt: str, user_message: str, effort: str, max_tokens: int):
    """Devuelve (texto, error). Usa el mismo manejo de errores para suggest y summary."""
    import anthropic

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            output_config={"effort": effort},
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.RateLimitError:
        return None, "Rate limit — reintentá en un momento."
    except anthropic.AuthenticationError:
        return None, "Clave API inválida."
    except anthropic.APIStatusError as exc:
        return None, f"Error de API ({exc.status_code})."
    except anthropic.APIConnectionError:
        return None, "Sin conexión con la API."
    return "".join(block.text for block in response.content if block.type == "text"), None


def get_suggestions(payload: dict) -> dict:
    if client is None:
        idx = payload.get("demo_index", 0) % len(DEMO_SUGGESTIONS)
        return {"demo": True, "suggestions": [DEMO_SUGGESTIONS[idx]]}
    text, error = call_claude(SYSTEM_PROMPT, build_user_message(payload), "low", 2000)
    if error:
        return {"error": error, "suggestions": []}
    try:
        return {"suggestions": parse_suggestions(text)}
    except (json.JSONDecodeError, ValueError):
        return {"suggestions": []}


def get_summary(payload: dict) -> dict:
    if client is None:
        return DEMO_SUMMARY
    text, error = call_claude(SUMMARY_PROMPT, build_user_message(payload, closing=True), "medium", 3000)
    if error:
        return {"error": error}
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return {"error": "No se pudo generar el cierre."}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"error": "No se pudo generar el cierre."}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silenciar logs por request
        pass

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            self._send_json({"live": client is not None, "model": MODEL})
            return
        path = self.path.split("?")[0]
        if path == "/":
            path = "/index.html"
        file = (STATIC / path.lstrip("/")).resolve()
        if not str(file).startswith(str(STATIC)) or not file.is_file():
            self.send_error(404)
            return
        content_types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".mp3": "audio/mpeg"}
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(file.suffix, "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/api/suggest", "/api/summary"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "JSON inválido", "suggestions": []}, 400)
            return
        handler = get_summary if self.path == "/api/summary" else get_suggestions
        self._send_json(handler(payload))


if __name__ == "__main__":
    mode = "LIVE (Claude " + MODEL + ")" if client else "DEMO (sin ANTHROPIC_API_KEY)"
    print(f"Meeting Companion → http://localhost:{PORT}  [{mode}]")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
