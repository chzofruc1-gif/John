import React, { useState, useRef } from 'react';
import { GoogleGenAI } from '@google/genai';
import { Play, Square, Loader2, Mic, FileText, Headphones, Sparkles, Settings2, User, Upload, Users, Wand2, PenLine, AlignLeft, Music, Download, Clock, History, Globe } from 'lucide-react';
import { motion } from 'motion/react';
import { mergeAudioWithMusic } from './lib/audio';

const getAi = () => {
  const apiKey = (import.meta as any).env.VITE_USER_GEMINI_API_KEY || process.env.GEMINI_API_KEY;
  return new GoogleGenAI({ apiKey });
};

const VOICES = [
  // Female
  { id: 'Aoede', baseVoice: 'Aoede', name: 'Aoede (温暖)', gender: 'Female', description: '低沉温和的女声', prefix: '' },
  { id: 'Kore', baseVoice: 'Kore', name: 'Kore (清脆)', gender: 'Female', description: '清脆明快的女声', prefix: '' },
  { id: 'Zephyr', baseVoice: 'Zephyr', name: 'Zephyr (轻柔)', gender: 'Female', description: '温柔细腻的女声', prefix: '' },
  { id: 'Aoede-Energetic', baseVoice: 'Aoede', name: 'Mia (活力)', gender: 'Female', description: '年轻充满活力的女声', prefix: 'Say energetically and cheerfully:' },
  { id: 'Kore-Pro', baseVoice: 'Kore', name: 'Ava (知性)', gender: 'Female', description: '专业从容的知性女声', prefix: 'Say calmly and professionally:' },

  // Male
  { id: 'Charon', baseVoice: 'Charon', name: 'Charon (深沉)', gender: 'Male', description: '沉稳厚实的男声', prefix: '' },
  { id: 'Fenrir', baseVoice: 'Fenrir', name: 'Fenrir (响亮)', gender: 'Male', description: '响亮有力的男声', prefix: '' },
  { id: 'Puck', baseVoice: 'Puck', name: 'Puck (阳光)', gender: 'Male', description: '年轻充满活力的男声', prefix: '' },
  { id: 'Charon-Mature', baseVoice: 'Charon', name: 'Leo (成熟)', gender: 'Male', description: '成熟稳健的男声', prefix: 'Say slowly in a deep, mature voice:' },
  { id: 'Puck-Casual', baseVoice: 'Puck', name: 'Noah (随性)', gender: 'Male', description: '轻松随性的男声', prefix: 'Say casually and relaxed:' }
];

function createWavBlob(data: string | Uint8Array, sampleRate = 24000) {
  let bytes: Uint8Array;
  if (typeof data === 'string') {
    const binaryString = window.atob(data);
    const len = binaryString.length;
    bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
  } else {
    bytes = data;
  }

  const wavBuffer = new ArrayBuffer(44 + bytes.length);
  const view = new DataView(wavBuffer);

  const writeString = (view: DataView, offset: number, string: string) => {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  };

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + bytes.length, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, 'data');
  view.setUint32(40, bytes.length, true);
  new Uint8Array(wavBuffer, 44).set(bytes);

  return new Blob([wavBuffer], { type: 'audio/wav' });
}

interface Speaker {
  id: number;
  name: string;
  voice: string;
}

export default function App() {
  const [input, setInput] = useState('Welcome to our new podcast episode! Today we are discussing the incredible advancements in artificial intelligence. It\'s a fascinating topic, and I can\'t wait to dive into the details with you all.');
  const [script, setScript] = useState('');
  const [isGeneratingScript, setIsGeneratingScript] = useState(false);
  const [isGeneratingAudio, setIsGeneratingAudio] = useState(false);
  const [generationProgress, setGenerationProgress] = useState<{ current: number; total: number; stage: string } | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [introFile, setIntroFile] = useState<File | null>(null);
  const [outroFile, setOutroFile] = useState<File | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [gapSeconds, setGapSeconds] = useState(0.5);
  const [history, setHistory] = useState<{id: string, timestamp: number, script: string, audioUrl: string | null}[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [language, setLanguage] = useState<'english' | 'chinese'>('chinese');

  const [mode, setMode] = useState<1 | 2 | 3>(2);
  const [genMode, setGenMode] = useState<'auto' | 'custom' | 'direct'>('auto');
  const [customPrompt, setCustomPrompt] = useState('Write a highly engaging, humorous podcast script based on the following material. Make sure the hosts debate the topic slightly before agreeing.');
  const [speakers, setSpeakers] = useState<Speaker[]>([
    { id: 1, name: 'Alex', voice: 'Puck' },
    { id: 2, name: 'Sam', voice: 'Kore' },
    { id: 3, name: 'Jordan', voice: 'Aoede' }
  ]);
  const [previewingVoice, setPreviewingVoice] = useState<string | null>(null);

  const activeSpeakers = speakers.slice(0, mode);

  const updateSpeaker = (id: number, field: keyof Speaker, value: string) => {
    setSpeakers(prev => prev.map(s => s.id === id ? { ...s, [field]: value } : s));
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target?.result;
      if (typeof text === 'string') {
        setInput(text);
      }
    };
    reader.readAsText(file);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const generateWithRetry = async (fn: () => Promise<any>, maxRetries = 5) => {
    let lastError;
    for (let i = 0; i < maxRetries; i++) {
      try {
        return await fn();
      } catch (err: any) {
        lastError = err;
        const errString = typeof err === 'string' ? err : JSON.stringify(err, Object.getOwnPropertyNames(err));
        const isRetryable = errString.includes('500') || 
                            errString.includes('xhr error') || 
                            errString.includes('UNKNOWN') ||
                            errString.includes('429') || 
                            errString.includes('RESOURCE_EXHAUSTED');

        if (isRetryable && i < maxRetries - 1) {
          const isRateLimit = errString.includes('429') || errString.includes('RESOURCE_EXHAUSTED');
          const delay = isRateLimit ? 15000 : (i + 1) * 2000 + (Math.random() * 1000); // 15s for rate limits, else exponential
          console.log(`API Error encountered (${errString.substring(0, 50)}). Retrying in ${Math.round(delay/1000)}s...`);
          setGenerationProgress(prev => prev ? { ...prev, stage: 'Retrying API connection...' } : null);
          await new Promise(resolve => setTimeout(resolve, delay));
          continue;
        }
        throw err;
      }
    }
    throw lastError;
  };

  const handlePreviewVoice = async (voiceId: string) => {
    setPreviewingVoice(voiceId);
    try {
      const voiceDef = VOICES.find(v => v.id === voiceId);
      if (!voiceDef) return;
      
      let baseText = language === 'chinese' ? `你好，我是 ${voiceDef.name}，这是一段${voiceDef.description}测试。` : `Hi, I am ${voiceDef.name}. This is a preview of my voice.`;
      const ttsText = voiceDef.prefix ? `${voiceDef.prefix} ${baseText}` : baseText;
      
      const response = await generateWithRetry(() => getAi().models.generateContent({
        model: "gemini-3.1-flash-tts-preview",
        contents: [{ parts: [{ text: ttsText }] }],
        config: {
          responseModalities: ["AUDIO"],
          speechConfig: {
            voiceConfig: {
              prebuiltVoiceConfig: { voiceName: voiceDef.baseVoice }
            }
          }
        }
      }), 2);
      
      const inlineData = response.candidates?.[0]?.content?.parts?.[0]?.inlineData;
      if (inlineData?.data) {
        const wavBlob = createWavBlob(inlineData.data);
        const url = URL.createObjectURL(wavBlob);
        const audio = new Audio(url);
        audio.onended = () => URL.revokeObjectURL(url);
        await audio.play();
      }
    } catch (err: any) {
      console.error("Preview error", err);
      const errString = typeof err === 'string' ? err : JSON.stringify(err, Object.getOwnPropertyNames(err));
      if (errString.includes('quota') || errString.includes('RESOURCE_EXHAUSTED') || errString.includes('429')) {
         setError("Preview failed: Quota/Rate Limit Exceeded. If you are on the free tier, wait a moment. TTS API may have a 100 requests/day limit.");
      } else {
         setError(`Preview failed: ${err.message || 'Unknown error'}`);
      }
    } finally {
      setPreviewingVoice(null);
    }
  };

  const handleGenerate = async () => {
    if (!input.trim()) {
      setError("请输入一些文本来生成播客。");
      return;
    }
    setError(null);
    setScript('');
    setAudioUrl(null);

    try {
      const speakerNames = activeSpeakers.map(s => s.name).join(', ');
      const formatExample = activeSpeakers.map(s => `${s.name}: [dialogue]`).join('\n');
      
      let generatedScript = '';

      if (genMode === 'direct') {
        generatedScript = input.trim();
        setScript(generatedScript);
      } else {
        setIsGeneratingScript(true);

        let scriptPrompt = '';
        if (genMode === 'auto') {
          scriptPrompt = `You are an expert podcast producer and scriptwriter.
The user has provided a script, outline, or topic below.
Rewrite it into a highly realistic, engaging, and natural ${mode}-person podcast script.
The speakers are: ${speakerNames}.
Make it sound like a real conversation: include filler words, slight interruptions, agreements, and natural emotional tone.
You MUST write the script entirely in ${language === 'chinese' ? 'Chinese' : 'English'}.
For each line of dialogue, you MUST prepend an emotion instruction in brackets, such as [cheerfully], [sadly], [excitedly], [seriously], [laughing], etc.
Do NOT include any other stage directions or sound effects.
Format the output EXACTLY like this:
${activeSpeakers[0].name}: [emotion] dialogue
${activeSpeakers[1]?.name || 'Guest'}: [emotion] dialogue

User Input:
${input}`;
        } else {
          scriptPrompt = `${customPrompt}

The speakers are: ${speakerNames}.
You MUST write the script entirely in ${language === 'chinese' ? 'Chinese' : 'English'}.
For each line of dialogue, you MUST prepend an emotion instruction in brackets, such as [cheerfully], [sadly], [excitedly], [seriously], [laughing], etc.
Do NOT include any other stage directions or sound effects.
Format the output EXACTLY like this:
${activeSpeakers[0].name}: [emotion] dialogue
${activeSpeakers[1]?.name || 'Guest'}: [emotion] dialogue

Source Material:
${input}`;
        }

        const scriptResponse = await generateWithRetry(() => getAi().models.generateContent({
          model: 'gemini-3.1-pro-preview',
          contents: scriptPrompt,
        }));

        generatedScript = scriptResponse.text?.trim() || '';
        setScript(generatedScript);
        setIsGeneratingScript(false);
      }

      if (!generatedScript) {
        throw new Error("Failed to generate script.");
      }

      setIsGeneratingAudio(true);

      // Parse the script into line-by-line dialogue
      const parsedLines: {speaker: string, text: string}[] = [];
      const lines = generatedScript.split('\n');
      for (const line of lines) {
        if (!line.trim()) continue;
        // Clean asterisks to ensure clean matching
        const cleanLine = line.replace(/\*/g, '');
        const match = cleanLine.match(/^\s*([^:：]+?)\s*[:：]\s*(.*)/);
        if (match) {
          parsedLines.push({ speaker: match[1].trim(), text: match[2].trim() });
        } else if (parsedLines.length > 0) {
          parsedLines[parsedLines.length - 1].text += ' ' + line.trim();
        }
      }

      // Fallback if no speaker format is detected
      if (parsedLines.length === 0) {
        parsedLines.push({ speaker: activeSpeakers[0].name, text: generatedScript });
      }

      const audioChunks: Uint8Array[] = [];
      
      setGenerationProgress({ current: 0, total: parsedLines.length, stage: 'Preparing chunks' });

      // Generate audio line-by-line to prevent voice swapping and enhance emotion
      // Check if user has provided a paid API key (which bypasses free tier rate limits)
      const isPaidKey = !!(import.meta as any).env.VITE_USER_GEMINI_API_KEY;

      const results: (Uint8Array | null)[] = new Array(parsedLines.length).fill(null);
      let completedCount = 0;

      // We use a limited concurrency queue to speed up generation significantly while avoiding UI freezes or extreme throttling.
      const concurrencyLimit = isPaidKey ? 3 : 1;
      
      const generateChunk = async (i: number) => {
        const line = parsedLines[i];
        if (!line.text.trim()) return null;

        // Find matching speaker settings, default to first speaker
        const speaker = activeSpeakers.find(s => s.name.toLowerCase() === line.speaker.toLowerCase()) || activeSpeakers[0];
        
        let ttsText = line.text;
        // Extract emotion tag if present
        const emotionMatch = ttsText.match(/^\[(.*?)\]\s*(.*)/);
        if (emotionMatch) {
          ttsText = `Say ${emotionMatch[1]}: ${emotionMatch[2]}`;
        }

        const voiceDef = VOICES.find(v => v.id === speaker.voice);
        const actualVoiceName = voiceDef?.baseVoice || speaker.voice;

        // Apply voice variations via prefix
        if (voiceDef?.prefix && !emotionMatch) {
          ttsText = `${voiceDef.prefix} ${ttsText}`;
        }

        const response = await generateWithRetry(() => getAi().models.generateContent({
          model: "gemini-3.1-flash-tts-preview",
          contents: [{ parts: [{ text: ttsText }] }],
          config: {
            responseModalities: ["AUDIO"],
            speechConfig: {
              voiceConfig: {
                prebuiltVoiceConfig: { voiceName: actualVoiceName }
              }
            }
          }
        }));
        
        const inlineData = response.candidates?.[0]?.content?.parts?.[0]?.inlineData;
        if (inlineData?.data) {
          const binaryString = window.atob(inlineData.data);
          const bytes = new Uint8Array(binaryString.length);
          for (let j = 0; j < binaryString.length; j++) {
            bytes[j] = binaryString.charCodeAt(j);
          }
          
          // Strip WAV header if present (44 bytes) to concatenate raw PCM
          if (bytes.length > 44 && bytes[0] === 82 && bytes[1] === 73 && bytes[2] === 70 && bytes[3] === 70) {
            return bytes.slice(44);
          } else {
            return bytes;
          }
        }
        return null;
      };

      let currentIndex = 0;
      
      const worker = async () => {
        while (currentIndex < parsedLines.length) {
          const i = currentIndex++;
          
          // Small delay for free tier ONLY to avoid hitting 15 RPM (1 request every 4 seconds)
          if (!isPaidKey && i > 0) {
            await new Promise(resolve => setTimeout(resolve, 4200));
          }

          setGenerationProgress(prev => ({ 
             current: prev?.current || 0, 
             total: parsedLines.length, 
             stage: `Synthesizing part ${i + 1}/${parsedLines.length}...`
          }));

          const chunk = await generateChunk(i);
          results[i] = chunk;
          
          completedCount++;
          setGenerationProgress({ 
             current: completedCount, 
             total: parsedLines.length, 
             stage: `Synthesized ${completedCount}/${parsedLines.length}` 
          });
        }
      };

      // Start workers
      const workers = Array.from({ length: Math.min(concurrencyLimit, parsedLines.length) }, () => worker());
      await Promise.all(workers);

      // Assemble final audio chunks
      for (let i = 0; i < results.length; i++) {
        const bytes = results[i];
        if (bytes) {
          audioChunks.push(bytes);
          if (i < parsedLines.length - 1 && gapSeconds > 0) {
            const numSamples = Math.floor(gapSeconds * 24000);
            const silence = new Uint8Array(numSamples * 2);
            audioChunks.push(silence);
          }
        }
      }

      if (audioChunks.length > 0) {
        const totalLength = audioChunks.reduce((acc, chunk) => acc + chunk.length, 0);
        const concatenated = new Uint8Array(totalLength);
        let offset = 0;
        for (const chunk of audioChunks) {
          concatenated.set(chunk, offset);
          offset += chunk.length;
        }
        
        const blob = createWavBlob(concatenated, 24000);
        
        let finalBlob = blob;
        if (introFile || outroFile) {
          setGenerationProgress({ current: parsedLines.length, total: parsedLines.length, stage: 'Merging intro/outro music' });
          finalBlob = await mergeAudioWithMusic(blob, introFile, outroFile);
        }
        
        const finalAudioUrl = URL.createObjectURL(finalBlob);
        setAudioUrl(finalAudioUrl);
        
        setHistory(prev => [{
          id: Math.random().toString(36).substring(2, 9),
          timestamp: Date.now(),
          script: generatedScript,
          audioUrl: finalAudioUrl
        }, ...prev]);
      } else {
        throw new Error("Failed to generate audio chunks.");
      }

    } catch (err: any) {
      console.error(err);
      const errString = typeof err === 'string' ? err : JSON.stringify(err, Object.getOwnPropertyNames(err));
      
      let errorMessage = err.message || "An error occurred during generation.";
      if (errString.includes('429') || errString.includes('RESOURCE_EXHAUSTED')) {
        try {
          const parsed = typeof err === 'object' && err.message ? err.message : JSON.parse(errString);
          const msg = typeof parsed === 'string' ? parsed : (parsed.error?.message || parsed.message || errString);
          if (msg.includes('quota') || msg.includes('limit: 100')) {
            errorMessage = `API Quota Exceeded: ${msg}`;
          } else {
            errorMessage = "API Rate Limit Exceeded. Please wait a minute and try again, or use a shorter script. Paid tier might also have concurrent limits.";
          }
        } catch (e) {
          errorMessage = "API Rate Limit Exceeded. Please wait a minute and try again, or use a shorter script.";
        }
      } else if (errString.includes('xhr error') || errString.includes('500') || errString.includes('UNKNOWN')) {
        errorMessage = "The AI service is currently experiencing high traffic or network instability. Please try again in a few moments.";
      }
      
      setError(errorMessage);
    } finally {
      setIsGeneratingScript(false);
      setIsGeneratingAudio(false);
      setGenerationProgress(null);
    }
  };

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 p-6 font-sans selection:bg-indigo-500/30">
      <div className="max-w-5xl mx-auto space-y-8">
        <header className="flex items-center justify-between border-b border-neutral-800 pb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-500/20 flex items-center justify-center border border-indigo-500/30">
              <Mic className="w-5 h-5 text-indigo-400" />
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">AI Podcast Studio</h1>
              <p className="text-sm text-neutral-400">Generate lifelike conversations</p>
            </div>
          </div>
          
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 bg-neutral-900 p-1 rounded-lg border border-neutral-800">
              <button
                onClick={() => setLanguage('english')}
                className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors flex items-center gap-2 ${
                  language === 'english'
                    ? 'bg-neutral-800 text-white shadow-sm'
                    : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50'
                }`}
              >
                <Globe className="w-4 h-4" /> English
              </button>
              <button
                onClick={() => setLanguage('chinese')}
                className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors flex items-center gap-2 ${
                  language === 'chinese'
                    ? 'bg-neutral-800 text-white shadow-sm'
                    : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50'
                }`}
              >
                <Globe className="w-4 h-4" /> 中文
              </button>
            </div>

            <div className="flex items-center gap-2 bg-neutral-900 p-1 rounded-lg border border-neutral-800">
              <button
                onClick={async () => {
                  if (typeof window !== 'undefined' && (window as any).aistudio?.openSelectKey) {
                    await (window as any).aistudio.openSelectKey();
                  }
                }}
                className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors flex items-center gap-2 text-indigo-400 hover:text-indigo-300 hover:bg-neutral-800/50`}
                title="Select your paid Gemini API Project to bypass free tier limits"
              >
                <Settings2 className="w-4 h-4" /> Paid Key
              </button>
            </div>

            <div className="flex items-center gap-2 bg-neutral-900 p-1 rounded-lg border border-neutral-800">
              {[1, 2, 3].map(m => (
                <button
                  key={m}
                  onClick={() => setMode(m as 1 | 2 | 3)}
                  className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors flex items-center gap-2 ${
                    mode === m 
                      ? 'bg-neutral-800 text-white shadow-sm' 
                      : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50'
                  }`}
                >
                  <Users className="w-4 h-4" />
                  {m} {m === 1 ? 'Person' : 'People'}
                </button>
              ))}
            </div>
          </div>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          <div className="space-y-6">
            
            {/* Speaker Settings */}
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 space-y-4">
              <h3 className="text-sm font-medium text-neutral-300 flex items-center gap-2 mb-2">
                <Settings2 className="w-4 h-4" />
                Speaker Settings
              </h3>
              
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {activeSpeakers.map((speaker, index) => (
                  <div key={speaker.id} className="space-y-3 p-3 bg-neutral-950/50 border border-neutral-800/50 rounded-xl">
                    <div className={`flex items-center gap-2 text-sm font-medium ${index === 0 ? 'text-indigo-400' : index === 1 ? 'text-emerald-400' : 'text-amber-400'}`}>
                      <User className="w-4 h-4" />
                      <input 
                        type="text" 
                        value={speaker.name}
                        onChange={e => updateSpeaker(speaker.id, 'name', e.target.value)}
                        className="bg-transparent border-b border-transparent hover:border-neutral-700 focus:border-current focus:outline-none w-24 px-1"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-neutral-500 block mb-1">Voice</label>
                      <div className="flex items-center gap-2">
                        <select 
                          value={speaker.voice} 
                          onChange={e => updateSpeaker(speaker.id, 'voice', e.target.value)} 
                          className={`flex-1 bg-neutral-900 border border-neutral-800 rounded-lg px-2 py-1.5 text-sm text-neutral-200 focus:outline-none focus:ring-1 ${index === 0 ? 'focus:ring-indigo-500' : index === 1 ? 'focus:ring-emerald-500' : 'focus:ring-amber-500'}`}
                        >
                          {VOICES.map(v => <option key={v.id} value={v.id}>{v.name} ({v.gender === 'Male' ? '男' : '女'} - {v.description})</option>)}
                        </select>
                        <button
                          onClick={() => handlePreviewVoice(speaker.voice)}
                          disabled={previewingVoice === speaker.voice}
                          className="p-1.5 text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800 rounded-md transition-colors shrink-0 disabled:opacity-50"
                          title="Preview voice"
                        >
                          {previewingVoice === speaker.voice ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="pt-3 border-t border-neutral-800 flex items-center justify-between">
                <label className="text-sm text-neutral-400 flex items-center gap-2">
                  <Clock className="w-4 h-4" /> Dialogue Gap <span className="text-xs text-neutral-500">({gapSeconds}s)</span>
                </label>
                <input 
                  type="range" min="0" max="2" step="0.1" 
                  value={gapSeconds} onChange={e => setGapSeconds(parseFloat(e.target.value))} 
                  className="w-1/2 h-1 bg-neutral-800 rounded-lg appearance-none cursor-pointer accent-indigo-500" 
                />
              </div>
            </div>

            {/* Music & Audio FX */}
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-5 space-y-4">
              <h3 className="text-sm font-medium text-neutral-300 flex items-center gap-2 mb-2">
                <Music className="w-4 h-4" />
                Music & Audio FX
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                <div className="bg-neutral-950/50 border border-neutral-800/50 rounded-xl p-3 space-y-2">
                  <label className="text-neutral-400 block font-medium">Intro Music</label>
                  <div className="flex items-center gap-2">
                    <label className="flex-1 cursor-pointer bg-neutral-900 border border-neutral-700 hover:bg-neutral-800 transition py-1.5 px-3 rounded-lg text-center text-xs text-neutral-200 truncate">
                      {introFile ? introFile.name : 'Choose audio file...'}
                      <input 
                        type="file" 
                        accept="audio/*" 
                        className="hidden" 
                        onChange={(e) => setIntroFile(e.target.files?.[0] || null)}
                      />
                    </label>
                    {introFile && (
                      <button onClick={() => setIntroFile(null)} className="text-neutral-500 hover:text-red-400 p-1">
                        &times;
                      </button>
                    )}
                  </div>
                </div>
                <div className="bg-neutral-950/50 border border-neutral-800/50 rounded-xl p-3 space-y-2">
                  <label className="text-neutral-400 block font-medium">Outro Music</label>
                  <div className="flex items-center gap-2">
                    <label className="flex-1 cursor-pointer bg-neutral-900 border border-neutral-700 hover:bg-neutral-800 transition py-1.5 px-3 rounded-lg text-center text-xs text-neutral-200 truncate">
                      {outroFile ? outroFile.name : 'Choose audio file...'}
                      <input 
                        type="file" 
                        accept="audio/*" 
                        className="hidden" 
                        onChange={(e) => setOutroFile(e.target.files?.[0] || null)}
                      />
                    </label>
                    {outroFile && (
                      <button onClick={() => setOutroFile(null)} className="text-neutral-500 hover:text-red-400 p-1">
                        &times;
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium text-neutral-300 flex items-center gap-2">
                  <FileText className="w-4 h-4" />
                  Generation Mode & Input
                </label>
                <div>
                  <input 
                    type="file" 
                    accept=".txt,.md" 
                    className="hidden" 
                    ref={fileInputRef}
                    onChange={handleFileUpload}
                  />
                  <button 
                    onClick={() => fileInputRef.current?.click()}
                    className="text-xs flex items-center gap-1.5 text-neutral-400 hover:text-neutral-200 transition-colors bg-neutral-900 px-2.5 py-1.5 rounded-md border border-neutral-800"
                  >
                    <Upload className="w-3.5 h-3.5" />
                    Import File
                  </button>
                </div>
              </div>

              <div className="flex bg-neutral-900 p-1 rounded-lg border border-neutral-800 w-full">
                <button
                  onClick={() => setGenMode('auto')}
                  className={`flex-1 px-3 py-2 text-xs font-medium rounded-md transition-colors flex items-center justify-center gap-2 ${
                    genMode === 'auto' ? 'bg-neutral-800 text-white shadow-sm' : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50'
                  }`}
                >
                  <Wand2 className="w-3.5 h-3.5" /> Auto Rewrite
                </button>
                <button
                  onClick={() => setGenMode('custom')}
                  className={`flex-1 px-3 py-2 text-xs font-medium rounded-md transition-colors flex items-center justify-center gap-2 ${
                    genMode === 'custom' ? 'bg-neutral-800 text-white shadow-sm' : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50'
                  }`}
                >
                  <PenLine className="w-3.5 h-3.5" /> Custom Prompt
                </button>
                <button
                  onClick={() => setGenMode('direct')}
                  className={`flex-1 px-3 py-2 text-xs font-medium rounded-md transition-colors flex items-center justify-center gap-2 ${
                    genMode === 'direct' ? 'bg-neutral-800 text-white shadow-sm' : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50'
                  }`}
                >
                  <AlignLeft className="w-3.5 h-3.5" /> Direct Script
                </button>
              </div>

              {genMode === 'custom' && (
                <div className="space-y-2">
                  <label className="text-xs text-neutral-500 block">Custom Instructions</label>
                  <textarea
                    value={customPrompt}
                    onChange={(e) => setCustomPrompt(e.target.value)}
                    placeholder="E.g., Make it a debate, focus on the history, keep it under 2 minutes..."
                    className="w-full h-20 bg-neutral-950 border border-neutral-800 rounded-xl p-3 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none focus:ring-1 focus:ring-indigo-500/50 resize-none"
                  />
                </div>
              )}

              <div className="space-y-2">
                <label className="text-xs text-neutral-500 block">
                  {genMode === 'direct' ? 'Formatted Script (e.g., Alex: Hello!)' : 'Source Material / Topic'}
                </label>
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder={
                    genMode === 'direct' 
                      ? "Paste your formatted script here. The AI will read it exactly as written..." 
                      : "Paste your article, notes, or just type a topic here..."
                  }
                  className="w-full h-40 bg-neutral-900 border border-neutral-800 rounded-2xl p-4 text-neutral-200 placeholder-neutral-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 resize-none"
                />
              </div>

              <button
                onClick={handleGenerate}
                disabled={!input.trim() || isGeneratingScript || isGeneratingAudio}
                className="w-full py-3 px-4 bg-indigo-600 hover:bg-indigo-500 disabled:bg-neutral-800 disabled:text-neutral-500 text-white rounded-xl font-medium transition-colors flex items-center justify-center gap-2"
              >
                {isGeneratingScript ? (
                  <><Loader2 className="w-4 h-4 animate-spin" /> Writing Script...</>
                ) : isGeneratingAudio ? (
                  <><Loader2 className="w-4 h-4 animate-spin" /> Generating Audio...</>
                ) : (
                  <><Sparkles className="w-4 h-4" /> {genMode === 'direct' ? 'Generate Audio' : 'Generate Podcast'}</>
                )}
              </button>
              {error && (
                <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-sm">
                  {error}
                </div>
              )}
            </div>
          </div>

          <div className="space-y-6">
            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 space-y-4">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-medium text-neutral-300 flex items-center gap-2">
                  <Headphones className="w-4 h-4" />
                  Audio Output
                </h3>
                {audioUrl && (
                  <a 
                    href={audioUrl} 
                    download="podcast.wav"
                    className="text-xs flex items-center gap-1.5 text-indigo-400 hover:text-indigo-300 transition-colors bg-indigo-500/10 hover:bg-indigo-500/20 px-2.5 py-1.5 rounded-md border border-indigo-500/20"
                  >
                    <Download className="w-3.5 h-3.5" /> Download Audio
                  </a>
                )}
              </div>
              {audioUrl ? (
                <div className="space-y-4">
                  <audio ref={audioRef} src={audioUrl} controls className="w-full" />
                  <div className="flex justify-center">
                    <div className="flex items-center gap-2 text-xs text-neutral-500 bg-neutral-950 px-3 py-1.5 rounded-full border border-neutral-800">
                      <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                      Ready to play
                    </div>
                  </div>
                </div>
              ) : (
                <div className="h-24 flex items-center justify-center border-2 border-dashed border-neutral-800 rounded-xl text-neutral-600 text-sm p-4 w-full">
                  {isGeneratingAudio ? (
                    <div className="flex flex-col items-center justify-center space-y-3 w-full max-w-sm mx-auto">
                      <div className="flex justify-between w-full text-xs text-neutral-400">
                        <span>{generationProgress?.stage || "Synthesizing..."}</span>
                        <span>{generationProgress && generationProgress.total > 0 ? `${Math.round((generationProgress.current / generationProgress.total) * 100)}%` : ""}</span>
                      </div>
                      <div className="w-full bg-neutral-800 rounded-full h-1.5 overflow-hidden">
                        <div 
                          className="bg-indigo-500 h-full transition-all duration-300 ease-out" 
                          style={{ width: `${generationProgress && generationProgress.total > 0 ? (generationProgress.current / generationProgress.total) * 100 : 0}%` }}
                        />
                      </div>
                    </div>
                  ) : isGeneratingScript ? (
                    <div className="flex items-center gap-2">
                       <Loader2 className="w-4 h-4 animate-spin text-indigo-500" />
                       Generating podcast script...
                    </div>
                  ) : (
                    "No audio generated yet"
                  )}
                </div>
              )}
            </div>

            <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 flex flex-col h-[400px]">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-medium text-neutral-300 flex items-center gap-2">
                  <FileText className="w-4 h-4" />
                  Generated Script
                </h3>
                {script && (
                  <button 
                    onClick={() => {
                      const blob = new Blob([script], { type: 'text/plain' });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = 'podcast_script.txt';
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                    className="text-xs flex items-center gap-1.5 text-neutral-400 hover:text-neutral-200 transition-colors bg-neutral-950 hover:bg-neutral-800 px-2.5 py-1.5 rounded-md border border-neutral-800"
                  >
                    <Download className="w-3.5 h-3.5" /> Download Script
                  </button>
                )}
              </div>
              <div className="flex-1 overflow-y-auto pr-2 space-y-4 custom-scrollbar">
                {script ? (
                  script.split('\n').filter(line => line.trim()).map((line, i) => {
                    const cleanLine = line.replace(/\*/g, '');
                    const speakerMatch = cleanLine.match(/^\s*([^:：]+?)\s*[:：]\s*(.*)/);
                    
                    if (!speakerMatch) {
                      return <p key={i} className="text-neutral-400 text-sm italic">{line}</p>;
                    }

                    const speakerName = speakerMatch[1].trim();
                    let text = speakerMatch[2].trim();
                    
                    // Remove the [emotion] tag for cleaner UI display
                    text = text.replace(/^\[.*?\]\s*/, '');
                    
                    const speakerIndex = activeSpeakers.findIndex(s => s.name.toLowerCase() === speakerName.toLowerCase());
                    
                    // If the speaker isn't one of our active speakers, just render as normal text
                    if (speakerIndex === -1) {
                       return <p key={i} className="text-neutral-400 text-sm italic">{line}</p>;
                    }

                    const isFirst = speakerIndex === 0;
                    const isSecond = speakerIndex === 1;
                    const isThird = speakerIndex === 2;

                    return (
                      <motion.div 
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: Math.min(i * 0.1, 2) }}
                        key={i} 
                        className={`flex flex-col ${isFirst ? 'items-start' : isSecond ? 'items-end' : 'items-center'}`}
                      >
                        <span className={`text-xs font-medium mb-1 ${isFirst ? 'text-indigo-400' : isSecond ? 'text-emerald-400' : 'text-amber-400'}`}>
                          {speakerName}
                        </span>
                        <div className={`max-w-[85%] p-3 rounded-2xl text-sm leading-relaxed ${
                          isFirst 
                            ? 'bg-indigo-500/10 border border-indigo-500/20 text-indigo-50 rounded-tl-sm' 
                            : isSecond
                            ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-50 rounded-tr-sm'
                            : 'bg-amber-500/10 border border-amber-500/20 text-amber-50 rounded-t-sm'
                        }`}>
                          {text}
                        </div>
                      </motion.div>
                    );
                  })
                ) : (
                  <div className="h-full flex items-center justify-center text-neutral-600 text-sm italic">
                    {isGeneratingScript ? "Writing script..." : "Script will appear here"}
                  </div>
                )}
              </div>
            </div>

            {history.length > 0 && (
              <div className="bg-neutral-900 border border-neutral-800 rounded-2xl p-6 space-y-4">
                <div className="flex items-center justify-between cursor-pointer" onClick={() => setShowHistory(!showHistory)}>
                  <h3 className="text-sm font-medium text-neutral-300 flex items-center gap-2">
                    <History className="w-4 h-4" />
                    Generation History ({history.length})
                  </h3>
                  <span className="text-neutral-500 text-xs">{showHistory ? "Hide" : "Show"}</span>
                </div>
                
                {showHistory && (
                  <div className="space-y-3 mt-4 max-h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                    {history.map(item => (
                      <div key={item.id} className="bg-neutral-950 border border-neutral-800/50 rounded-xl p-3 flex flex-col gap-2">
                        <div className="flex justify-between items-center text-xs text-neutral-500">
                          <span>{new Date(item.timestamp).toLocaleTimeString()}</span>
                          <div className="flex gap-2">
                            <button 
                              onClick={() => {
                                setScript(item.script);
                                setAudioUrl(item.audioUrl);
                              }}
                              className="text-indigo-400 hover:text-indigo-300 transition-colors"
                            >
                              Load
                            </button>
                            {item.audioUrl && (
                              <button 
                                onClick={() => {
                                  const a = document.createElement('a');
                                  a.href = item.audioUrl!;
                                  a.download = `podcast_${item.id}.wav`;
                                  a.click();
                                }}
                                className="text-emerald-400 hover:text-emerald-300 transition-colors"
                              >
                                Download
                              </button>
                            )}
                          </div>
                        </div>
                        <p className="text-sm text-neutral-300 line-clamp-2 italic">
                          {item.script.split('\n').filter(Boolean)[0] || "No script..."}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
