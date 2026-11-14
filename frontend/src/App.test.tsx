import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('App shell', () => {
  it('renders the header, the five documented screens and the role switcher', () => {
    const html = renderToStaticMarkup(<App />)
    expect(html).toContain('CredSure')
    expect(html).toContain('Digital Credential Lifecycle Management Platform')
    for (const label of ['Dashboard', 'Issue credential', 'Credential detail', 'Verify', 'Anomalies']) {
      expect(html).toContain(label)
    }
    expect(html).toContain('Academic Administrator')
    expect(html).toContain('aria-current="page"') // dashboard selected by default
    expect(html).toContain('role="status"') // dashboard starts in its loading state
  })
})
