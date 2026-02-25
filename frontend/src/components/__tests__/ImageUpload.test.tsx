import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ImageUpload } from '../ui/ImageUpload'

describe('ImageUpload', () => {
  it('has capture environment attribute', () => {
    render(<ImageUpload onImageSelected={() => {}} />)
    const input = screen.getByLabelText(/upload photo/i, { selector: 'input' })
    expect(input).toHaveAttribute('capture', 'environment')
  })

  it('renders preview after file selection', () => {
    const onImageSelected = vi.fn()
    const original = globalThis.FileReader

    class MockFileReader {
      result: string | ArrayBuffer | null =
        'data:image/jpeg;base64,ZmFrZS1pbWFnZS1iYXNlNjQ='
      onload: ((this: FileReader, ev: ProgressEvent<FileReader>) => unknown) | null =
        null
      readAsDataURL() {
        this.onload?.call(this as unknown as FileReader, {} as ProgressEvent<FileReader>)
      }
    }

    ;(globalThis as unknown as { FileReader: typeof FileReader }).FileReader =
      MockFileReader as unknown as typeof FileReader

    render(<ImageUpload onImageSelected={onImageSelected} showPreview />)

    const input = screen.getByLabelText(/upload photo/i, { selector: 'input' })
    const file = new File(['content'], 'device.jpg', { type: 'image/jpeg' })
    fireEvent.change(input, { target: { files: [file] } })

    expect(onImageSelected).toHaveBeenCalledWith('ZmFrZS1pbWFnZS1iYXNlNjQ=', 'image/jpeg')
    expect(screen.getByAltText(/upload preview/i)).toBeInTheDocument()

    ;(globalThis as unknown as { FileReader: typeof FileReader }).FileReader = original
  })
})
