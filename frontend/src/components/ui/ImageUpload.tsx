import { Camera } from 'lucide-react'
import { useId, useState, type ChangeEvent } from 'react'

const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10MB
const ALLOWED_MIME_TYPES = new Set([
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/heic',
  'image/heif',
])

interface ImageUploadProps {
  onImageSelected: (base64: string, mimeType: string) => void
  onError?: (message: string) => void
  className?: string
  showPreview?: boolean
}

export function ImageUpload({
  onImageSelected,
  onError,
  className,
  showPreview = false,
}: ImageUploadProps) {
  const inputId = useId()
  const [previewSrc, setPreviewSrc] = useState<string | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)

  const handleImageUpload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    setValidationError(null)

    if (!ALLOWED_MIME_TYPES.has(file.type)) {
      const msg = 'Unsupported image format. Use JPEG, PNG, WebP, HEIC, or HEIF.'
      setValidationError(msg)
      onError?.(msg)
      return
    }

    if (file.size > MAX_FILE_SIZE) {
      const msg = 'Image too large. Maximum size is 10 MB.'
      setValidationError(msg)
      onError?.(msg)
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      const value = String(reader.result)
      const parts = value.split(',')
      if (parts.length < 2) return
      const base64 = parts[1]
      onImageSelected(base64, file.type)
      if (showPreview) {
        setPreviewSrc(value)
      }
    }
    reader.readAsDataURL(file)
  }

  return (
    <div className={['w-full sm:w-auto', className].filter(Boolean).join(' ')}>
      <label
        htmlFor={inputId}
        className="inline-flex w-full cursor-pointer items-center justify-center gap-2 rounded-2xl border border-border/80 bg-card/40 px-4 py-3 text-sm text-foreground transition hover:border-primary/60 hover:bg-card"
      >
        <Camera className="size-4" />
        <span>Upload photo</span>
      </label>
      <input
        id={inputId}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={handleImageUpload}
      />
      {validationError ? (
        <p className="mt-1.5 text-xs text-destructive">{validationError}</p>
      ) : null}
      {showPreview && previewSrc ? (
        <img
          src={previewSrc}
          alt="Upload preview"
          className="mt-2 h-16 w-16 rounded-lg border border-border/70 object-cover"
        />
      ) : null}
    </div>
  )
}
