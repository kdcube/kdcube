/**
 * Minimal in-house syntax highlighter.
 *
 *  Moved verbatim from src/App.tsx as part of the Wave 1 modular refactor.
 *  Supports python / javascript / bash / json. Emits a plain HTML string
 *  with `<span class="tok-*">` wrappers — token classes ship in index.css
 *  (`.tok-k|b|s|c|n|f|d|o`) and apply for both light and dark snippet
 *  backgrounds.
 */

import { escapeHtml } from './utils.ts'

export const HL_KEYWORDS: Record<string, Set<string>> = {
  python: new Set([
    'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break',
    'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'finally',
    'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal',
    'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with', 'yield',
    'match', 'case',
  ]),
  javascript: new Set([
    'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue',
    'debugger', 'default', 'delete', 'do', 'else', 'export', 'extends',
    'finally', 'for', 'function', 'if', 'import', 'in', 'instanceof', 'let',
    'new', 'null', 'of', 'return', 'super', 'switch', 'this', 'throw', 'true',
    'false', 'try', 'typeof', 'undefined', 'var', 'void', 'while', 'yield',
  ]),
  bash: new Set([
    'if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'do', 'done', 'case',
    'esac', 'function', 'in', 'select', 'until', 'return', 'export', 'local',
    'readonly', 'set', 'unset', 'echo', 'cd', 'pwd', 'source',
  ]),
  json: new Set(['true', 'false', 'null']),
}

export const HL_BUILTINS: Record<string, Set<string>> = {
  python: new Set([
    'print', 'len', 'range', 'list', 'dict', 'set', 'tuple', 'str', 'int',
    'float', 'bool', 'bytes', 'open', 'isinstance', 'type', 'super',
    'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed', 'any', 'all',
    'min', 'max', 'sum', 'abs', 'round', 'hash', 'id', '__init__', 'self',
    'cls', 'Path',
  ]),
  javascript: new Set([
    'console', 'window', 'document', 'Math', 'JSON', 'Object', 'Array',
    'String', 'Number', 'Boolean', 'Promise', 'Map', 'Set', 'Date',
    'Error', 'parseInt', 'parseFloat',
  ]),
}

export function inferLanguage(hint: string | null | undefined, code: string): keyof typeof HL_KEYWORDS {
  const h = String(hint || '').toLowerCase()
  if (h.startsWith('py')) return 'python'
  if (h === 'js' || h === 'jsx' || h === 'ts' || h === 'tsx' || h === 'javascript' || h === 'typescript') return 'javascript'
  if (h === 'sh' || h === 'bash' || h === 'shell') return 'bash'
  if (h === 'json') return 'json'
  const sample = code.slice(0, 240)
  if (/^\s*(def |class |import |from |if __name__)/m.test(sample)) return 'python'
  if (/^\s*(const |let |var |function |export |import )/m.test(sample)) return 'javascript'
  if (/^\s*(#!\/|echo |cd |export )/m.test(sample)) return 'bash'
  return 'python'
}

export function highlightCode(code: string, lang: keyof typeof HL_KEYWORDS): string {
  if (!code) return ''
  const keywords = HL_KEYWORDS[lang] || new Set<string>()
  const builtins = HL_BUILTINS[lang] || new Set<string>()
  const tokens: Array<{ kind: string; text: string }> = []
  let index = 0
  const length = code.length

  const isPython = lang === 'python'
  const isBash = lang === 'bash'
  const isJs = lang === 'javascript'

  while (index < length) {
    const char = code[index]

    // Comments
    if (isPython && char === '#') {
      const end = code.indexOf('\n', index)
      const stop = end === -1 ? length : end
      tokens.push({ kind: 'c', text: code.slice(index, stop) })
      index = stop
      continue
    }
    if (isBash && char === '#') {
      const end = code.indexOf('\n', index)
      const stop = end === -1 ? length : end
      tokens.push({ kind: 'c', text: code.slice(index, stop) })
      index = stop
      continue
    }
    if (isJs && char === '/' && code[index + 1] === '/') {
      const end = code.indexOf('\n', index)
      const stop = end === -1 ? length : end
      tokens.push({ kind: 'c', text: code.slice(index, stop) })
      index = stop
      continue
    }
    if (isJs && char === '/' && code[index + 1] === '*') {
      const end = code.indexOf('*/', index + 2)
      const stop = end === -1 ? length : end + 2
      tokens.push({ kind: 'c', text: code.slice(index, stop) })
      index = stop
      continue
    }

    // Strings (single, double, triple-quoted python, template literals)
    if (isPython && (code.startsWith('"""', index) || code.startsWith("'''", index))) {
      const quote = code.slice(index, index + 3)
      const end = code.indexOf(quote, index + 3)
      const stop = end === -1 ? length : end + 3
      tokens.push({ kind: 's', text: code.slice(index, stop) })
      index = stop
      continue
    }
    if (char === '"' || char === "'" || (isJs && char === '`')) {
      const quote = char
      let stop = index + 1
      while (stop < length) {
        if (code[stop] === '\\') { stop += 2; continue }
        if (code[stop] === quote) { stop += 1; break }
        if (code[stop] === '\n' && quote !== '`') { break }
        stop += 1
      }
      tokens.push({ kind: 's', text: code.slice(index, stop) })
      index = stop
      continue
    }

    // Decorators (Python)
    if (isPython && char === '@' && /[A-Za-z_]/.test(code[index + 1] || '')) {
      let stop = index + 1
      while (stop < length && /[A-Za-z0-9_.]/.test(code[stop])) stop += 1
      tokens.push({ kind: 'd', text: code.slice(index, stop) })
      index = stop
      continue
    }

    // Numbers
    if (/[0-9]/.test(char)) {
      let stop = index + 1
      while (stop < length && /[0-9._eExXa-fA-F]/.test(code[stop])) stop += 1
      tokens.push({ kind: 'n', text: code.slice(index, stop) })
      index = stop
      continue
    }

    // Identifiers / keywords / builtins / function calls
    if (/[A-Za-z_$]/.test(char)) {
      let stop = index + 1
      while (stop < length && /[A-Za-z0-9_$]/.test(code[stop])) stop += 1
      const word = code.slice(index, stop)
      if (keywords.has(word)) {
        tokens.push({ kind: 'k', text: word })
      } else if (builtins.has(word)) {
        tokens.push({ kind: 'b', text: word })
      } else if (code[stop] === '(') {
        tokens.push({ kind: 'f', text: word })
      } else {
        tokens.push({ kind: 'o', text: word })
      }
      index = stop
      continue
    }

    // Default — accumulate until next interesting char
    let stop = index + 1
    while (
      stop < length &&
      !/[A-Za-z_$0-9"'`#]/.test(code[stop]) &&
      !(isJs && code[stop] === '/' && (code[stop + 1] === '/' || code[stop + 1] === '*')) &&
      !(isPython && code[stop] === '@')
    ) {
      stop += 1
    }
    tokens.push({ kind: 'plain', text: code.slice(index, stop) })
    index = stop
  }

  return tokens
    .map((token) => {
      const safe = escapeHtml(token.text)
      if (token.kind === 'plain' || token.kind === 'o') return safe
      return `<span class="tok-${token.kind}">${safe}</span>`
    })
    .join('')
}
