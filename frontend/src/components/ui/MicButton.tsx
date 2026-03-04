import { cva, type VariantProps } from 'class-variance-authority'
import { Mic, MicOff } from 'lucide-react'
import { cn } from '../../lib/utils'

const micButtonVariants = cva(
  'control-mic inline-flex items-center justify-center gap-2 rounded-2xl font-semibold text-sm transition',
  {
    variants: {
      status: {
        idle: 'bg-[color:var(--industry-accent)] text-black hover:brightness-110',
        recording: 'bg-destructive/90 text-white hover:bg-destructive',
        processing: 'bg-warning/90 text-black hover:bg-warning',
      },
      size: {
        default: 'w-full px-4 py-3 sm:w-auto',
        compact: 'w-auto px-3 py-2',
      },
    },
    defaultVariants: {
      status: 'idle',
      size: 'default',
    },
  },
)

interface MicButtonProps extends VariantProps<typeof micButtonVariants> {
  onClick: () => void
  disabled?: boolean
  className?: string
}

export function MicButton({ onClick, disabled = false, className, status, size }: MicButtonProps) {
  const isRecording = status === 'recording'
  const isProcessing = status === 'processing'
  const isDisabled = disabled || isProcessing
  const label = isRecording ? 'End call' : 'Start call'
  return (
    <button
      type="button"
      onClick={() => {
        if (isDisabled) return
        onClick()
      }}
      disabled={isDisabled}
      className={cn(
        micButtonVariants({ status, size }),
        isDisabled && 'cursor-not-allowed opacity-70',
        className,
      )}
      aria-label={label}
    >
      {isRecording ? (
        <>
          <MicOff className="size-4" /> End call
        </>
      ) : (
        <>
          <Mic className="size-4" /> Start call
        </>
      )}
    </button>
  )
}
