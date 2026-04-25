import { GoogleGenAI } from "@google/genai";

async function run() {
  const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
  const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models?key=${process.env.GEMINI_API_KEY}`);
  const json = await response.json();
  const models = json.models.filter((m: any) => m.name.includes("flash"));
  console.log(models.map((m: any) => \`${m.name} - \${m.supportedGenerationMethods.join(',')}\`).join('\n'));
}
run();
