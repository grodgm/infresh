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

SYSTEM_PROMPT = """Sos una superinteligencia que acompaña reuniones en vivo. Recibís el \
transcript parcial de una reunión (con hablantes detectados automáticamente) y tu único \
trabajo es nutrir la conversación con aportes breves y de alto valor.

Devolvé EXCLUSIVAMENTE un objeto JSON válido, sin markdown ni texto extra, con esta forma:
{"suggestions": [{"type": "insight|pregunta|accion|riesgo|dato", "title": "...", "text": "..."}]}

Reglas:
- Máximo 2 sugerencias por llamada. Si el transcript nuevo no amerita nada valioso, devolvé {"suggestions": []}.
- "insight": una conexión o reencuadre que los participantes no están viendo.
- "pregunta": una pregunta puntual que destrabaría o profundizaría la conversación.
- "accion": un próximo paso concreto que se desprende de lo hablado (con responsable si se nombró).
- "riesgo": algo que están pasando por alto y puede costarles caro.
- "dato": contexto factual relevante que eleva la discusión.
- title: máximo 6 palabras. text: máximo 30 palabras. Español rioplatense, directo, sin relleno.
- No repitas sugerencias ya dadas (te paso los títulos previos).
- El transcript viene de reconocimiento de voz: puede tener errores, interpretá con criterio."""

DEMO_SUGGESTIONS = [
    {"type": "dato", "title": "Modo demo activo", "text": "No hay ANTHROPIC_API_KEY configurada. Estas sugerencias son simuladas; con la clave, Claude analiza la conversación real."},
    {"type": "pregunta", "title": "¿Cuál es el objetivo?", "text": "¿Qué resultado concreto quieren tener al terminar esta reunión?"},
    {"type": "insight", "title": "Están alineados sin saberlo", "text": "Dos participantes proponen lo mismo con palabras distintas. Nombrarlo puede cerrar el debate."},
    {"type": "accion", "title": "Definir un responsable", "text": "Se mencionó una tarea sin dueño. Asignarla ahora evita que se pierda."},
    {"type": "riesgo", "title": "Decisión sin datos", "text": "Están por decidir en base a una suposición no validada. Vale chequearla primero."},
]


def build_user_message(payload: dict) -> str:
    lines = payload.get("transcript", [])
    previous = payload.get("previous_titles", [])
    transcript_text = "\n".join(
        f"[{line.get('speaker', '?')}] {line.get('text', '')}" for line in lines
    )
    parts = ["TRANSCRIPT DE LA REUNIÓN HASTA AHORA:", transcript_text or "(vacío)"]
    if previous:
        parts.append("\nSUGERENCIAS YA DADAS (no repetir):")
        parts.extend(f"- {t}" for t in previous)
    return "\n".join(parts)


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


def get_suggestions(payload: dict) -> dict:
    if client is None:
        idx = payload.get("demo_index", 0) % len(DEMO_SUGGESTIONS)
        return {"demo": True, "suggestions": [DEMO_SUGGESTIONS[idx]]}

    import anthropic

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            output_config={"effort": "low"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": build_user_message(payload)}],
        )
    except anthropic.RateLimitError:
        return {"error": "Rate limit — reintentando en el próximo ciclo.", "suggestions": []}
    except anthropic.AuthenticationError:
        return {"error": "Clave API inválida.", "suggestions": []}
    except anthropic.APIStatusError as exc:
        return {"error": f"Error de API ({exc.status_code}).", "suggestions": []}
    except anthropic.APIConnectionError:
        return {"error": "Sin conexión con la API.", "suggestions": []}

    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        return {"suggestions": parse_suggestions(text)}
    except (json.JSONDecodeError, ValueError):
        return {"suggestions": []}


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
        content_types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(file.suffix, "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/suggest":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "JSON inválido", "suggestions": []}, 400)
            return
        self._send_json(get_suggestions(payload))


if __name__ == "__main__":
    mode = "LIVE (Claude " + MODEL + ")" if client else "DEMO (sin ANTHROPIC_API_KEY)"
    print(f"Meeting Companion → http://localhost:{PORT}  [{mode}]")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
