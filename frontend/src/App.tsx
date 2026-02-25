import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { useEkaetteSocket } from './hooks/useEkaetteSocket'
import { useAudioWorklet } from './hooks/useAudioWorklet'
import { Mic, MicOff, Camera } from 'lucide-react'
import { cn } from './lib/utils'
import type { Industry } from './types'

function App() {
  const [industry, setIndustry] = useState<Industry>('electronics')
  const userId = 'demo-user'
  const [sessionId] = useState(() => `${industry}-${Date.now()}`)
  const [isStarting, setIsStarting] = useState(false)
  const onAudioChunkRef = useRef<((data: ArrayBuffer) => void) | null>(null)

  const socket = useEkaetteSocket(userId, sessionId)
  const audio = useAudioWorklet(onAudioChunkRef)

  const isConnected = socket.state === 'connected'

  useEffect(() => {
    onAudioChunkRef.current = socket.sendAudio
  }, [socket.sendAudio])

  useEffect(() => {
    socket.onAudioData.current = (data: ArrayBuffer) => {
      audio.playAudioChunk(data)
    }
    return () => {
      socket.onAudioData.current = null
    }
  }, [audio.playAudioChunk, socket.onAudioData])

  useEffect(() => {
    if (isConnected) {
      socket.sendConfig(industry)
    }
  }, [industry, isConnected, socket.sendConfig])

  const handleToggleCall = async () => {
    if (isConnected) {
      audio.stop()
      socket.disconnect()
      return
    }
    if (socket.state === 'connecting' || socket.state === 'reconnecting') {
      return
    }

    setIsStarting(true)
    try {
      socket.connect()
      await audio.initPlayer()
      await audio.startRecording()
    } finally {
      setIsStarting(false)
    }
  }

  const handleImageUpload = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const base64 = (reader.result as string).split(',')[1]
      socket.sendImage(base64, file.type)
    }
    reader.readAsDataURL(file)
  }

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col">
      {/* Header */}
      <header className="p-4 border-b border-border flex items-center justify-between">
        <h1 className="text-xl font-bold tracking-tight">Ekaette</h1>
        <select
          value={industry}
          onChange={e => setIndustry(e.target.value as Industry)}
          className="bg-card text-card-foreground rounded-lg px-3 py-1.5 text-sm border border-border"
        >
          <option value="electronics">Electronics</option>
          <option value="hotel">Hotel</option>
          <option value="automotive">Automotive</option>
          <option value="fashion">Fashion</option>
        </select>
      </header>

      {/* Transcript area */}
      <main className="flex-1 p-6 overflow-y-auto">
        {socket.messages
          .filter(m => m.type === 'transcription')
          .map((msg, i) => (
            <p
              key={i}
              className={cn(
                'text-sm mb-2',
                msg.type === 'transcription' && msg.role === 'user'
                  ? 'text-foreground text-right'
                  : 'text-muted-foreground',
              )}
            >
              {msg.type === 'transcription' ? msg.text : ''}
            </p>
          ))}

        {socket.state === 'disconnected' && socket.messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-muted">
            <p className="text-lg">Tap the mic to start a conversation</p>
          </div>
        )}
      </main>

      {/* Footer controls */}
      <footer className="p-4 border-t border-border flex items-center justify-center gap-4">
        <label className="cursor-pointer p-3 rounded-full bg-card hover:bg-border transition-colors">
          <Camera className="size-5 text-muted-foreground" />
          <input
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            onChange={handleImageUpload}
          />
        </label>

        <button
          onClick={handleToggleCall}
          disabled={isStarting}
          className={cn(
            'size-16 rounded-full flex items-center justify-center transition-all',
            isConnected
              ? 'bg-destructive text-white animate-pulse-ring'
              : 'bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-60',
          )}
        >
          {isConnected ? <MicOff className="size-6" /> : <Mic className="size-6" />}
        </button>

        <span
          className={cn(
            'text-xs px-3 py-1 rounded-full',
            isConnected ? 'bg-primary/20 text-primary' : 'bg-card text-muted',
          )}
        >
          {socket.state}
        </span>
      </footer>
      {audio.error && (
        <p className="px-4 pb-4 text-xs text-destructive">{audio.error}</p>
      )}
    </div>
  )
}

export default App
