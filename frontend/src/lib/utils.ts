import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function withStableKeys<T>(
  items: readonly T[],
  getSignature: (item: T) => string,
): Array<{ item: T; key: string }> {
  const occurrences = new Map<string, number>()

  return items.map(item => {
    const signature = getSignature(item)
    const nextOccurrence = (occurrences.get(signature) ?? 0) + 1
    occurrences.set(signature, nextOccurrence)

    return {
      item,
      key: nextOccurrence === 1 ? signature : `${signature}::${nextOccurrence}`,
    }
  })
}
