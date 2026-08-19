// Meeting Companion — API pública de sugerencias (la UI vive en GitHub Pages).
// GET  /companion/status   → {live, model}
// POST /companion/suggest  → sugerencias de Claude sobre el transcript
// GET  /companion          → redirige a la UI
import Anthropic from "npm:@anthropic-ai/sdk";

const MODEL = "claude-opus-5";
const UI_URL = "https://grodgm.github.io/infresh/";
const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
const client = apiKey ? new Anthropic({ apiKey }) : null;

const CORS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type",
};

const SYSTEM_PROMPT = `Sos una superinteligencia que acompaña reuniones en vivo. Recibís el \
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
- El transcript viene de reconocimiento de voz: puede tener errores, interpretá con criterio.`;

const DEMO_SUGGESTIONS = [
  { type: "dato", title: "Modo demo activo", text: "Falta configurar ANTHROPIC_API_KEY como secret de la función. Estas sugerencias son simuladas." },
  { type: "pregunta", title: "¿Cuál es el objetivo?", text: "¿Qué resultado concreto quieren tener al terminar esta reunión?" },
  { type: "insight", title: "Están alineados sin saberlo", text: "Dos participantes proponen lo mismo con palabras distintas. Nombrarlo puede cerrar el debate." },
  { type: "accion", title: "Definir un responsable", text: "Se mencionó una tarea sin dueño. Asignarla ahora evita que se pierda." },
  { type: "riesgo", title: "Decisión sin datos", text: "Están por decidir en base a una suposición no validada. Vale chequearla primero." },
];

interface Line { speaker?: string; text?: string }
interface Payload { transcript?: Line[]; previous_titles?: string[]; demo_index?: number }
interface Suggestion { type: string; title: string; text: string }

function buildUserMessage(payload: Payload): string {
  // Límites anti-abuso: la URL es pública
  const lines = (payload.transcript ?? []).slice(-200);
  const previous = (payload.previous_titles ?? []).slice(-20);
  const transcriptText = lines
    .map((l) => `[${String(l.speaker ?? "?").slice(0, 60)}] ${String(l.text ?? "").slice(0, 600)}`)
    .join("\n");
  const parts = ["TRANSCRIPT DE LA REUNIÓN HASTA AHORA:", transcriptText || "(vacío)"];
  if (previous.length) {
    parts.push("\nSUGERENCIAS YA DADAS (no repetir):");
    for (const t of previous) parts.push(`- ${String(t).slice(0, 120)}`);
  }
  return parts.join("\n");
}

function parseSuggestions(text: string): Suggestion[] {
  const cleaned = text.trim().replace(/^```(?:json)?\s*|\s*```$/g, "");
  const match = cleaned.match(/\{[\s\S]*\}/);
  if (!match) return [];
  const data = JSON.parse(match[0]);
  const out: Suggestion[] = [];
  for (const item of data.suggestions ?? []) {
    if (item && typeof item === "object" && item.text) {
      out.push({ type: item.type ?? "insight", title: item.title ?? "", text: item.text });
    }
  }
  return out.slice(0, 3);
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS },
  });
}

async function getSuggestions(payload: Payload): Promise<Record<string, unknown>> {
  if (!client) {
    const idx = (payload.demo_index ?? 0) % DEMO_SUGGESTIONS.length;
    return { demo: true, suggestions: [DEMO_SUGGESTIONS[idx]] };
  }
  try {
    const response = await client.messages.create({
      model: MODEL,
      max_tokens: 2000,
      output_config: { effort: "low" },
      system: [
        { type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } },
      ],
      messages: [{ role: "user", content: buildUserMessage(payload) }],
    });
    let text = "";
    for (const block of response.content) if (block.type === "text") text += block.text;
    try {
      return { suggestions: parseSuggestions(text) };
    } catch {
      return { suggestions: [] };
    }
  } catch (error) {
    if (error instanceof Anthropic.RateLimitError) {
      return { error: "Rate limit — reintentando en el próximo ciclo.", suggestions: [] };
    }
    if (error instanceof Anthropic.AuthenticationError) {
      return { error: "Clave API inválida.", suggestions: [] };
    }
    if (error instanceof Anthropic.APIError) {
      return { error: `Error de API (${error.status}).`, suggestions: [] };
    }
    return { error: "Sin conexión con la API.", suggestions: [] };
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS });
  }
  const path = new URL(req.url).pathname.replace(/\/$/, "");
  if (req.method === "GET" && path.endsWith("/status")) {
    return json({ live: client !== null, model: MODEL });
  }
  if (req.method === "POST" && path.endsWith("/suggest")) {
    let payload: Payload;
    try {
      payload = await req.json();
    } catch {
      return json({ error: "JSON inválido", suggestions: [] }, 400);
    }
    return json(await getSuggestions(payload));
  }
  return new Response(null, { status: 302, headers: { Location: UI_URL, ...CORS } });
});
