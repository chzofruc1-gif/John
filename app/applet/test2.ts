import { GoogleGenAI } from "@google/genai";
async function run() {
  const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
  const response = await ai.models.generateContent({
    model: "gemini-2.0-flash", // or gemini-2.0-flash-exp
    contents: "Say 'Hello world'",
    config: {
      responseModalities: ["AUDIO"],
      speechConfig: {
        voiceConfig: { prebuiltVoiceConfig: { voiceName: "Puck" } }
      }
    }
  });
  console.log("Success with", !!response.candidates);
}
run().catch(e => console.error(e));
