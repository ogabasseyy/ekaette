import { cva } from 'class-variance-authority'
import { BarChart3, Megaphone, Mic, Settings } from 'lucide-react'
import { type AppPage, NAV_ITEMS, navigateTo } from '../../lib/navigation'
import { cn } from '../../lib/utils'

const ICON_MAP: Record<string, typeof Mic> = {
  Mic,
  BarChart3,
  Megaphone,
  Settings,
}

const navTabVariants = cva(
  'nav-tab relative flex flex-col items-center gap-0.5 px-4 py-2 text-[0.62rem] font-semibold uppercase tracking-[0.18em] transition-colors',
  {
    variants: {
      active: {
        true: 'nav-tab-active text-primary',
        false: 'text-muted-foreground hover:text-foreground',
      },
    },
    defaultVariants: {
      active: false,
    },
  },
)

interface NavBarProps {
  activePage: AppPage
  className?: string
}

export function NavBar({ activePage, className }: NavBarProps) {
  return (
    <nav aria-label="Primary">
      <div className={cn('nav-bar flex items-center justify-center gap-1', className)}>
        {NAV_ITEMS.map(item => {
          const isActive = item.page === activePage
          const Icon = ICON_MAP[item.iconName]
          return (
            <button
              key={item.page}
              type="button"
              aria-current={isActive ? 'page' : undefined}
              className={cn(navTabVariants({ active: isActive }))}
              onClick={() => {
                if (!isActive) navigateTo(item.page)
              }}
            >
              {Icon && <Icon className="size-4" />}
              <span>{item.label}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
