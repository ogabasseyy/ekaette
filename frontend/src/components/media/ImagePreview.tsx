interface ImagePreviewProps {
  src: string
  status?: 'analyzing' | 'complete'
  alt?: string
}

function isSafeSrc(src: string): boolean {
  if (src.startsWith('data:image/')) return true
  if (src.startsWith('blob:')) return true
  if (src.startsWith('https://')) return true
  if (src.startsWith('/')) return true
  return false
}

export function ImagePreview({
  src,
  status = 'complete',
  alt = 'Uploaded device',
}: ImagePreviewProps) {
  if (!isSafeSrc(src)) return null

  return (
    <figure className="animate-slide-up overflow-hidden rounded-2xl border border-border/70 bg-card/65">
      <img src={src} alt={alt} loading="lazy" className="aspect-[16/9] h-44 w-full object-cover" />
      <figcaption
        className="flex items-center justify-between px-3 py-2 text-xs"
        aria-live="polite"
      >
        <span className="text-muted-foreground">Customer image</span>
        <span className="text-primary">{status === 'analyzing' ? 'Analyzing…' : 'Ready'}</span>
      </figcaption>
    </figure>
  )
}
