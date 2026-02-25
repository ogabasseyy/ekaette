interface ImagePreviewProps {
  src: string
  status?: 'analyzing' | 'complete'
}

export function ImagePreview({ src, status = 'complete' }: ImagePreviewProps) {
  return (
    <figure className="animate-slide-up overflow-hidden rounded-2xl border border-border/70 bg-card/65">
      <img src={src} alt="Uploaded device" className="h-44 w-full object-cover" />
      <figcaption className="flex items-center justify-between px-3 py-2 text-xs">
        <span className="text-muted-foreground">Customer image</span>
        <span className="text-primary">{status === 'analyzing' ? 'Analyzing…' : 'Ready'}</span>
      </figcaption>
    </figure>
  )
}
