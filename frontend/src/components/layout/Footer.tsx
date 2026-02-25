import { ImageUpload } from '../ui/ImageUpload'
import { MicButton } from '../ui/MicButton'
import { TextInput } from '../ui/TextInput'
import type { ConnectionState } from '../../types'

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
  const status = isConnected
    ? 'recording'
    : isStarting || connectionState === 'reconnecting'
      ? 'processing'
      : 'idle'

  return (
    <footer className="panel-glass mt-3 shrink-0 px-4 py-3 sm:mt-4 sm:px-5 sm:py-4">
      <div className="grid grid-cols-2 gap-3 sm:flex sm:items-center">
        <ImageUpload onImageSelected={onImageSelected} />

        <MicButton
          onClick={onToggleCall}
          disabled={isStarting}
          status={status}
          size="default"
        />

        <div className="col-span-2 min-w-0 sm:flex-1">
          <TextInput connected={isConnected} onSend={onSendText} />
        </div>
      </div>
    </footer>
  )
}
