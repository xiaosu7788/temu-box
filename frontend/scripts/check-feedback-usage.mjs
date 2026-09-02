import { readdir, readFile } from 'node:fs/promises'
import { extname, join, relative } from 'node:path'

const root = new URL('../src/', import.meta.url)
const allowed = new Set(['feedback.ts'])
const forbidden = [
  { name: 'Element Plus feedback API', pattern: /\bEl(?:Message|MessageBox|Notification)\b/ },
  { name: 'browser dialog API', pattern: /\b(?:alert|confirm|prompt)\s*\(/ },
]

async function sourceFiles(directory) {
  const files = []
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) files.push(...await sourceFiles(path))
    else if (['.ts', '.vue'].includes(extname(entry.name))) files.push(path)
  }
  return files
}

const sourceRoot = decodeURIComponent(root.pathname).replace(/^\/(?:[A-Za-z]:)/, (value) => value.slice(1))
const violations = []
for (const file of await sourceFiles(sourceRoot)) {
  const name = relative(sourceRoot, file).replaceAll('\\', '/')
  if (allowed.has(name)) continue
  const content = await readFile(file, 'utf8')
  for (const rule of forbidden) {
    if (rule.pattern.test(content)) violations.push(`${name}: ${rule.name} must use src/feedback.ts`)
  }
}

if (violations.length) {
  throw new Error(`Feedback usage check failed:\n${violations.join('\n')}`)
}

console.log('Feedback usage check passed')
