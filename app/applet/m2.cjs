const https = require('https');
https.get("https://generativelanguage.googleapis.com/v1beta/models?key=" + process.env.GEMINI_API_KEY, (res) => {
  let data = '';
  res.on('data', (chunk) => { data += chunk; });
  res.on('end', () => {
    const json = JSON.parse(data);
    const models = json.models.filter((m) => m.name.includes("flash") || m.name.includes("tts"));
    console.log(models.map((m) => m.name + " - " + m.supportedGenerationMethods.join(",")).join("\n"));
  });
});
