// Infresh — API pública de facilitación de reuniones (la UI vive en GitHub Pages).
// GET  /companion/status   → {live, model}
// POST /companion/suggest  → intervenciones de facilitación en vivo
// POST /companion/summary  → cierre de reunión (memoria organizacional)
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

const SYSTEM_PROMPT = `Sos Infresh, una inteligencia que facilita reuniones de equipo en vivo. Tu trabajo no es \
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
- El transcript viene de reconocimiento de voz: puede tener errores, interpretá con criterio.`;

const SUMMARY_PROMPT = `Sos Infresh. La reunión terminó. Con el contexto organizacional y el transcript completo, \
generá el cierre que se convierte en memoria organizacional.

Devolvé EXCLUSIVAMENTE un objeto JSON válido, sin markdown ni texto extra:
{"resumen": "...", "decisiones": ["..."], "acciones": [{"que": "...", "quien": "..."}], "pendientes": ["..."], "compartir_con": [{"quien": "...", "motivo": "..."}]}

Reglas:
- resumen: máximo 80 palabras, qué pasó y qué cambió.
- decisiones: solo decisiones realmente tomadas (no ideas sueltas). Si no hubo, lista vacía.
- acciones: compromisos concretos; "quien" con el nombre si se dijo, sino "sin asignar".
- pendientes: temas abiertos que quedaron sin resolver.
- compartir_con: roles o equipos de la organización que deberían ver esto (ej: "Liderazgo/C-level", "Equipo de diseño"), con motivo de una línea. Basate en las prioridades estratégicas y en lo hablado.
- Español rioplatense, directo. Sin inventar nada que no esté en el transcript.`;

const DEMO_SUGGESTIONS = [
  { type: "dato", title: "Modo demo activo", text: "Falta configurar ANTHROPIC_API_KEY como secret de la función. Estas intervenciones son simuladas." },
  { type: "pregunta", title: "¿Cuál es el objetivo?", text: "¿Qué resultado concreto quieren tener al terminar esta reunión?" },
  { type: "decision", title: "Hay una decisión en el aire", text: "El grupo parece estar decidiendo algo sin nombrarlo. Explicitarlo ayuda a confirmarlo o desafiarlo." },
  { type: "equilibrio", title: "Voces desparejas", text: "Una persona lleva la mayor parte de la conversación. ¿Qué opinan los que todavía no hablaron?" },
  { type: "alineacion", title: "Conectar con la estrategia", text: "Lo que discuten toca una prioridad del liderazgo. Vale nombrar esa conexión." },
];

const DEMO_SUMMARY = {
  demo: true,
  resumen: "Modo demo: sin ANTHROPIC_API_KEY el cierre es de ejemplo. El equipo revisó la retención de la beta, acordó probar registro diferido y dejó abierto el modelo de precios.",
  decisiones: ["Probar onboarding con registro diferido guardando progreso local"],
  acciones: [{ que: "Prototipar el registro diferido", quien: "Marito" }],
  pendientes: ["Modelo de precios: único vs. suscripción"],
  compartir_con: [{ quien: "Liderazgo/C-level", motivo: "Impacta el plan de lanzamiento de septiembre" }],
};

interface Line { speaker?: string; text?: string }
interface Config { objetivo?: string; prioridades?: string; contexto?: string; agenda?: string[]; tema_actual?: string }
interface Payload {
  transcript?: Line[]; previous_titles?: string[]; demo_index?: number;
  config?: Config; participacion?: { speaker?: string; palabras?: number }[]; minutos?: number;
}
interface Suggestion { type: string; title: string; text: string }

function buildUserMessage(payload: Payload, closing = false): string {
  // Límites anti-abuso: la URL es pública
  const lines = (payload.transcript ?? []).slice(-300);
  const previous = (payload.previous_titles ?? []).slice(-20);
  const cfg = payload.config ?? {};
  const participacion = payload.participacion ?? [];
  const parts: string[] = [];
  if (cfg.prioridades) parts.push(`PRIORIDADES ESTRATÉGICAS DE LA ORGANIZACIÓN:\n${String(cfg.prioridades).slice(0, 2000)}`);
  if (cfg.contexto) parts.push(`CONTEXTO DEL EQUIPO:\n${String(cfg.contexto).slice(0, 2000)}`);
  if (cfg.objetivo) parts.push(`OBJETIVO DE LA REUNIÓN: ${String(cfg.objetivo).slice(0, 300)}`);
  if (cfg.agenda?.length) parts.push("AGENDA: " + cfg.agenda.slice(0, 20).map((t) => String(t).slice(0, 120)).join(" / "));
  if (!closing && cfg.tema_actual) parts.push(`TEMA DE AGENDA ACTUAL: ${String(cfg.tema_actual).slice(0, 120)}`);
  if (participacion.length) {
    const total = participacion.reduce((acc, p) => acc + (p.palabras ?? 0), 0) || 1;
    parts.push("PARTICIPACIÓN (palabras): " + participacion.slice(0, 12).map(
      (p) => `${String(p.speaker ?? "?").slice(0, 60)}: ${p.palabras ?? 0} (${Math.round(100 * (p.palabras ?? 0) / total)}%)`,
    ).join(", "));
  }
  if (payload.minutos !== undefined) parts.push(`MINUTOS DE REUNIÓN: ${payload.minutos}`);
  const transcriptText = lines
    .map((l) => `[${String(l.speaker ?? "?").slice(0, 60)}] ${String(l.text ?? "").slice(0, 600)}`)
    .join("\n");
  parts.push((closing ? "TRANSCRIPT COMPLETO DE LA REUNIÓN:" : "TRANSCRIPT DE LA REUNIÓN HASTA AHORA:") + "\n" + (transcriptText || "(vacío)"));
  if (previous.length && !closing) {
    parts.push("INTERVENCIONES YA DADAS (no repetir):\n" + previous.map((t) => `- ${String(t).slice(0, 120)}`).join("\n"));
  }
  return parts.join("\n\n");
}

function extractJson(text: string): unknown {
  const cleaned = text.trim().replace(/^```(?:json)?\s*|\s*```$/g, "");
  const match = cleaned.match(/\{[\s\S]*\}/);
  if (!match) return null;
  try {
    return JSON.parse(match[0]);
  } catch {
    return null;
  }
}

function parseSuggestions(text: string): Suggestion[] {
  const data = extractJson(text) as { suggestions?: unknown[] } | null;
  if (!data) return [];
  const out: Suggestion[] = [];
  for (const item of data.suggestions ?? []) {
    const it = item as Record<string, string>;
    if (it && typeof it === "object" && it.text) {
      out.push({ type: it.type ?? "insight", title: it.title ?? "", text: it.text });
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

async function callClaude(systemPrompt: string, userMessage: string, effort: string, maxTokens: number): Promise<{ text?: string; error?: string }> {
  try {
    const response = await client!.messages.create({
      model: MODEL,
      max_tokens: maxTokens,
      output_config: { effort },
      system: [
        { type: "text", text: systemPrompt, cache_control: { type: "ephemeral" } },
      ],
      messages: [{ role: "user", content: userMessage }],
    });
    let text = "";
    for (const block of response.content) if (block.type === "text") text += block.text;
    return { text };
  } catch (error) {
    if (error instanceof Anthropic.RateLimitError) return { error: "Rate limit — reintentá en un momento." };
    if (error instanceof Anthropic.AuthenticationError) return { error: "Clave API inválida." };
    if (error instanceof Anthropic.APIError) return { error: `Error de API (${error.status}).` };
    return { error: "Sin conexión con la API." };
  }
}

async function getSuggestions(payload: Payload): Promise<Record<string, unknown>> {
  if (!client) {
    const idx = (payload.demo_index ?? 0) % DEMO_SUGGESTIONS.length;
    return { demo: true, suggestions: [DEMO_SUGGESTIONS[idx]] };
  }
  const { text, error } = await callClaude(SYSTEM_PROMPT, buildUserMessage(payload), "low", 2000);
  if (error) return { error, suggestions: [] };
  return { suggestions: parseSuggestions(text!) };
}

async function getSummary(payload: Payload): Promise<Record<string, unknown>> {
  if (!client) return DEMO_SUMMARY;
  const { text, error } = await callClaude(SUMMARY_PROMPT, buildUserMessage(payload, true), "medium", 3000);
  if (error) return { error };
  const data = extractJson(text!);
  return data && typeof data === "object" ? data as Record<string, unknown> : { error: "No se pudo generar el cierre." };
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS });
  }
  const path = new URL(req.url).pathname.replace(/\/$/, "");
  if (req.method === "GET" && path.endsWith("/status")) {
    return json({ live: client !== null, model: MODEL });
  }
  if (req.method === "POST" && (path.endsWith("/suggest") || path.endsWith("/summary"))) {
    let payload: Payload;
    try {
      payload = await req.json();
    } catch {
      return json({ error: "JSON inválido", suggestions: [] }, 400);
    }
    return json(path.endsWith("/summary") ? await getSummary(payload) : await getSuggestions(payload));
  }
  return new Response(null, { status: 302, headers: { Location: UI_URL, ...CORS } });
});
