import { describe, expect, it } from 'vitest';
import { displayFileName } from './documentLabels';

describe('displayFileName', () => {
  it('leaves an ordinary filename untouched', () => {
    expect(displayFileName('week-3-lecture.pdf')).toBe('week-3-lecture.pdf');
  });

  it('strips path separators and .. sequences so it reads as a name, not a path', () => {
    const shown = displayFileName('../../../../../../../../tmp/traversal_probe_x.pdf');
    expect(shown).not.toContain('/');
    expect(shown).not.toContain('\\');
    expect(shown).not.toContain('..');
    expect(shown).toBe('tmp traversal_probe_x.pdf');
  });

  it('replaces control characters with a space', () => {
    expect(displayFileName(`a${String.fromCharCode(1)}bc.txt`)).toBe('a bc.txt');
  });

  it('caps the length with an ellipsis', () => {
    const long = `${'a'.repeat(200)}.pdf`;
    const shown = displayFileName(long, 20);
    expect(shown).toHaveLength(20);
    expect(shown.endsWith('…')).toBe(true);
  });

  it('falls back to a placeholder when nothing printable remains', () => {
    expect(displayFileName('///')).toBe('file');
  });
});
