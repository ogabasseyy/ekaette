import type { ConnectionState } from '../../types'
import { ImageUpload } from '../ui/ImageUpload'
import { MicButton } from '../ui/MicButton'
import { TextInput } from '../ui/TextInput'

interface FooterProps {
  connectionState: ConnectionState
  isStarting: boolean
  onToggleCall: () => void
  onSendText: (text: string) => void
  onImageSelected: (base64: string, mimeType: string) => void
}

export function Footer({
  connectionState,
  isStarting,
  onToggleCall,
  onSendText,
  onImageSelected,
}: FooterProps) {
  const isConnected = connectionState === 'connected'
  const status = isConnected ? 'recording' : isStarting ? 'processing' : 'idle'

  return (
    <footer className="panel-glass control-footer mt-3 shrink-0 px-4 py-3 sm:mt-4 sm:px-5 sm:py-4">
      <div className="control-footer__grid grid grid-cols-2 gap-3 sm:flex sm:items-center">
        <ImageUpload onImageSelected={onImageSelected} />

        <MicButton onClick={onToggleCall} disabled={isStarting} status={status} size="default" />

        <div className="col-span-2 min-w-0 sm:flex-1">
          <TextInput connected={isConnected} onSend={onSendText} />
        </div>
      </div>

      <div className="mt-2 flex justify-center gap-3 text-[0.6rem] text-muted-foreground uppercase tracking-[0.12em]">
        <a
          href="/privacy.html"
          target="_blank"
          rel="noopener noreferrer"
          className="transition hover:text-foreground"
        >
          Privacy Policy
        </a>
      </div>
    </footer>
  )
}
