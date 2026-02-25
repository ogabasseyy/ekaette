import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ImageUpload } from '../ui/ImageUpload'

function withMockFileReader(
  result: string | null,
  fn: () => void,
  triggerError = false,
) {
  const original = globalThis.FileReader

  class MockFileReader {
    result: string | ArrayBuffer | null = result
    onload: ((this: FileReader, ev: ProgressEvent<FileReader>) => unknown) | null = null
    onerror: ((this: FileReader, ev: ProgressEvent<FileReader>) => unknown) | null = null
    readAsDataURL() {
      if (triggerError) {
        this.onerror?.call(this as unknown as FileReader, {} as ProgressEvent<FileReader>)
      } else {
        this.onload?.call(this as unknown as FileReader, {} as ProgressEvent<FileReader>)
      }
    }
  }

  ;(globalThis as unknown as { FileReader: typeof FileReader }).FileReader =
    MockFileReader as unknown as typeof FileReader

  try {
    fn()
  } finally {
    ;(globalThis as unknown as { FileReader: typeof FileReader }).FileReader = original
  }
}

describe('ImageUpload', () => {
  it('has capture environment attribute', () => {
    render(<ImageUpload onImageSelected={() => {}} />)
    const input = screen.getByLabelText(/upload photo/i, { selector: 'input' })
    expect(input).toHaveAttribute('capture', 'environment')
  })

  it('renders preview after file selection', () => {
    const onImageSelected = vi.fn()

    withMockFileReader(
      'data:image/jpeg;base64,ZmFrZS1pbWFnZS1iYXNlNjQ=',
      () => {
        render(<ImageUpload onImageSelected={onImageSelected} showPreview />)

        const input = screen.getByLabelText(/upload photo/i, { selector: 'input' })
        const file = new File(['content'], 'device.jpg', { type: 'image/jpeg' })
        fireEvent.change(input, { target: { files: [file] } })

        expect(onImageSelected).toHaveBeenCalledWith('ZmFrZS1pbWFnZS1iYXNlNjQ=', 'image/jpeg')
        expect(screen.getByAltText(/upload preview/i)).toBeInTheDocument()
      },
    )
  })

  it('rejects unsupported file types', () => {
    const onError = vi.fn()
    render(<ImageUpload onImageSelected={() => {}} onError={onError} />)

    const input = screen.getByLabelText(/upload photo/i, { selector: 'input' })
    const file = new File(['hello'], 'doc.txt', { type: 'text/plain' })
    fireEvent.change(input, { target: { files: [file] } })

    expect(onError).toHaveBeenCalledWith(expect.stringContaining('Unsupported'))
    expect(screen.getByText(/unsupported/i)).toBeInTheDocument()
  })

  it('rejects files larger than 10MB', () => {
    const onError = vi.fn()
    render(<ImageUpload onImageSelected={() => {}} onError={onError} />)

    const input = screen.getByLabelText(/upload photo/i, { selector: 'input' })
    const bigFile = new File([new ArrayBuffer(11 * 1024 * 1024)], 'huge.jpg', {
      type: 'image/jpeg',
    })
    fireEvent.change(input, { target: { files: [bigFile] } })

    expect(onError).toHaveBeenCalledWith(expect.stringContaining('10 MB'))
  })

  it('handles FileReader error', () => {
    const onError = vi.fn()

    withMockFileReader(
      null,
      () => {
        render(<ImageUpload onImageSelected={() => {}} onError={onError} />)

        const input = screen.getByLabelText(/upload photo/i, { selector: 'input' })
        const file = new File(['content'], 'device.jpg', { type: 'image/jpeg' })
        fireEvent.change(input, { target: { files: [file] } })

        expect(onError).toHaveBeenCalledWith(expect.stringContaining('Failed'))
      },
      true,
    )
  })
})
