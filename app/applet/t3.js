import { GoogleGenAI } from "@google/genai";
async function run() {
  try {
      const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
      const response = await ai.models.generateContent({
        model: "gemini-3.1-flash-live-preview",
        contents: "Say 'Hello world'",
        config: {
          responseModalities: ["AUDIO"],
          speechConfig: {
            voiceConfig: { prebuiltVoiceConfig: { voiceName: "Puck" } }
          }
        }
      });
      console.log("Success with", !!response.candidates);
  } catch (err) {
      console.error(err.message);
  }
}
run();
