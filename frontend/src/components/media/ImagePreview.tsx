interface ImagePreviewProps {
  src: string
  status?: 'analyzing' | 'complete'
}

function isValidImageSrc(value: string): boolean {
  return (
    value.startsWith('blob:') ||
    value.startsWith('data:image/') ||
    value.startsWith('https://') ||
    value.startsWith('/')
  )
}

export function ImagePreview({ src, status = 'complete' }: ImagePreviewProps) {
  if (!isValidImageSrc(src)) return null

  return (
    <figure className="animate-slide-up overflow-hidden rounded-2xl border border-border/70 bg-card/65">
      <img src={src} alt="Uploaded device" className="h-44 w-full object-cover" />
      <figcaption className="flex items-center justify-between px-3 py-2 text-xs">
        <span className="text-muted-foreground">Customer image</span>
        <span className="text-primary" aria-live="polite" aria-atomic="true">
          {status === 'analyzing' ? 'Analyzing…' : 'Ready'}
        </span>
      </figcaption>
    </figure>
  )
}
