import { useEffect, useState } from 'react';
import { Check, Copy, Download } from 'lucide-react';
import type { StudyGuideGenerationResult } from '@/api/types';
import { Button } from '@/ui/Button';
import { studyGuideFileName, studyGuideToMarkdown } from './studyGuideMarkdown';

type CopyState = 'idle' | 'copied' | 'failed';

export interface StudyGuideExportActionsProps {
  guide: StudyGuideGenerationResult;
  courseName: string;
}

export function StudyGuideExportActions({ guide, courseName }: StudyGuideExportActionsProps) {
  const [copyState, setCopyState] = useState<CopyState>('idle');

  useEffect(() => {
    if (copyState === 'idle') {
      return;
    }
    const timer = setTimeout(() => setCopyState('idle'), 2000);
    return () => clearTimeout(timer);
  }, [copyState]);

  useEffect(() => {
    setCopyState('idle');
  }, [guide.generated_output_id]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(studyGuideToMarkdown(guide, courseName));
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
  };

  const handleDownload = () => {
    const blob = new Blob([studyGuideToMarkdown(guide, courseName)], {
      type: 'text/markdown',
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = studyGuideFileName(courseName);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  return (
    <>
      <Button
        onClick={() => void handleCopy()}
        icon={copyState === 'copied' ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      >
        {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed' : 'Copy'}
      </Button>
      <Button variant="primary" onClick={handleDownload} icon={<Download aria-hidden="true" />}>
        Download
      </Button>
    </>
  );
}
