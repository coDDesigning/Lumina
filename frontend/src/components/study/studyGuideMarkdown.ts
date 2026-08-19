import type { StudyGuideGenerationResult } from '../../api/types';

function section(heading: string, lines: string[]): string[] {
  if (lines.length === 0) return [];
  return [`## ${heading}`, '', ...lines, ''];
}

function bullets(items: string[]): string[] {
  return items.map((item) => `- ${item}`);
}

export function studyGuideFileName(courseName: string): string {
  const slug = courseName.trim().toLowerCase().replace(/\s+/g, '_') || 'course';
  return `${slug}_study_guide.md`;
}

export function studyGuideToMarkdown(
  result: StudyGuideGenerationResult,
  courseName: string,
): string {
  const guide = result.study_guide;
  const lines: string[] = [`# ${guide.title}`, ''];

  lines.push(`**Course:** ${courseName}`);
  lines.push(`**Difficulty:** ${guide.difficulty.level} — ${guide.difficulty.reason}`);
  lines.push(`**Estimated study time:** ${guide.estimated_study_time}`);
  lines.push(
    `**Coverage:** ${guide.coverage.status} (${guide.coverage.estimated_completeness}%)`,
  );
  lines.push('');

  if (result.context_truncated) {
    lines.push(
      `> Built from ${result.chunks_used} of ${result.chunks_available} content sections, so some material was left out.`,
      '',
    );
  }

  lines.push('## Summary', '', guide.summary, '');
  lines.push(...section('Learning objectives', bullets(guide.learning_objectives)));
  lines.push(...section('Key points', bullets(guide.key_points)));
  lines.push(
    ...section(
      'Important terms',
      guide.important_terms.flatMap((term) => [`### ${term.term}`, '', term.definition, '']),
    ),
  );
  lines.push(
    ...section(
      'Common mistakes',
      guide.common_mistakes.map(
        (item) => `- **Mistake:** ${item.mistake}\n  **Correction:** ${item.correction}`,
      ),
    ),
  );

  const examTips: string[] = [];
  if (guide.exam_tips.lecture_based.length > 0) {
    examTips.push(
      '### From your lecture material',
      '',
      ...bullets(guide.exam_tips.lecture_based),
      '',
    );
  }
  if (guide.exam_tips.ai_suggestions.length > 0) {
    examTips.push('### AI suggestions', '', ...bullets(guide.exam_tips.ai_suggestions), '');
  }
  lines.push(...section('Exam tips', examTips));

  lines.push(...section('Prerequisites', bullets(guide.prerequisites)));

  if (guide.confidence_notes) {
    lines.push('## Notes on confidence', '', guide.confidence_notes, '');
  }

  return lines.join('\n').trimEnd() + '\n';
}
