import { useCallback, useEffect, useState } from 'react';
import { Calendar } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { describeError } from '@/api/errors';
import { examRoadmapAPI } from '@/api/examRoadmap';
import { afterExamRoadmapGenerated } from '@/api/invalidations';
import type { ExamRoadmap } from '@/api/types';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { Dialog } from '@/ui/Dialog';
import { Select } from '@/ui/Input';
import { ExamRoadmapView } from './ExamRoadmapView';
import { GeneratingState } from './GenerationStates';
import styles from './ExamRoadmapModal.module.css';

export interface ExamRoadmapModalProps {
  courseId: number;
  courseName: string;
  examDate?: string | null;
  hasTopics?: boolean;
  onClose: () => void;
  onGenerated?: (outputId: number) => void;
}

type ModalPhase =
  | { phase: 'idle' }
  | { phase: 'generating' }
  | { phase: 'success'; roadmap: ExamRoadmap; outputId: number }
  | { phase: 'error'; message: string };

const TOPIC_PER_DAY_OPTIONS = [
  { value: '1', label: '1 topic per day (Gentle)' },
  { value: '2', label: '2 topics per day' },
  { value: '3', label: '3 topics per day (Recommended)' },
  { value: '4', label: '4 topics per day' },
  { value: '5', label: '5 topics per day (Intensive)' },
  { value: '6', label: '6 topics per day (Max)' },
];

export function ExamRoadmapModal({
  courseId,
  courseName,
  examDate,
  hasTopics = true,
  onClose,
  onGenerated,
}: ExamRoadmapModalProps) {
  const navigate = useNavigate();
  const [maxTopicsPerDay, setMaxTopicsPerDay] = useState(3);
  const [includeMaterials, setIncludeMaterials] = useState(true);
  const [state, setState] = useState<ModalPhase>({ phase: 'idle' });
  const [elapsed, setElapsed] = useState(0);

  const hasExamDate = Boolean(examDate && examDate.trim());

  useEffect(() => {
    if (state.phase !== 'generating') {
      return;
    }
    setElapsed(0);
    const timer = setInterval(() => setElapsed((seconds) => seconds + 1), 1000);
    return () => clearInterval(timer);
  }, [state.phase]);

  const handleGenerate = useCallback(async () => {
    setState({ phase: 'generating' });

    try {
      const result = await examRoadmapAPI.generate(courseId, {
        max_topics_per_day: maxTopicsPerDay,
        include_materials: includeMaterials,
      });

      afterExamRoadmapGenerated(courseId);
      setState({
        phase: 'success',
        roadmap: result.roadmap,
        outputId: result.generated_output_id,
      });
      onGenerated?.(result.generated_output_id);
    } catch (caught) {
      const described = describeError(caught, 'Could not generate exam roadmap.');
      const message =
        caught instanceof Error && caught.message && !('status' in caught)
          ? caught.message
          : described.message;
      setState({ phase: 'error', message });
    }
  }, [courseId, maxTopicsPerDay, includeMaterials, onGenerated]);

  const handleGoToSettings = () => {
    onClose();
    navigate(`/courses/${courseId}/settings`);
  };

  return (
    <Dialog
      open
      onClose={onClose}
      size="xl"
      title="Exam Study Roadmap"
      description={`Plan your study schedule for ${courseName}`}
      mark={<Calendar aria-hidden="true" />}
      footer={
        state.phase === 'success' ? (
          <Button onClick={onClose}>Done</Button>
        ) : (
          <div className={styles.noticeActions}>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            {hasExamDate ? (
              <Button
                variant="primary"
                onClick={handleGenerate}
                isLoading={state.phase === 'generating'}
                loadingLabel="Planning..."
              >
                Generate Roadmap
              </Button>
            ) : null}
          </div>
        )
      }
    >
      <div className={styles.container}>
        {state.phase === 'generating' ? (
          <GeneratingState
            heading="Allocating daily goals..."
            detail="Balancing topic importance, quiz mastery, and prerequisites against your remaining days."
            elapsed={elapsed}
          />
        ) : state.phase === 'success' ? (
          <ExamRoadmapView roadmap={state.roadmap} />
        ) : (
          <>
            {state.phase === 'error' ? (
              <Alert tone="destructive" live="alert">
                {state.message}
              </Alert>
            ) : null}

            {!hasExamDate ? (
              <div className={styles.noticeCard}>
                <h3 className={styles.noticeTitle}>Exam Date Required</h3>
                <p className={styles.noticeText}>
                  To build a personalized day-by-day study schedule, Lumina needs to know when your
                  exam takes place. You can set the exam date in Course Settings.
                </p>
                <div className={styles.noticeActions}>
                  <Button variant="primary" onClick={handleGoToSettings}>
                    Go to Course Settings
                  </Button>
                </div>
              </div>
            ) : (
              <div className={styles.form}>
                {!hasTopics ? (
                  <Alert tone="info">
                    This course has no syllabus topics yet. We recommend adding topics in Course
                    Settings to prioritize what gets tested.
                  </Alert>
                ) : null}

                <div className={styles.fieldGrid}>
                  <Select
                    label="Daily pacing"
                    value={String(maxTopicsPerDay)}
                    onChange={(e) => setMaxTopicsPerDay(Number(e.target.value))}
                    hint="Maximum number of topics to study or review in a single day."
                  >
                    {TOPIC_PER_DAY_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </Select>
                </div>

                <div className={styles.checkboxArea}>
                  <Checkbox
                    label="Attach course materials & citations"
                    description="Link recommended lecture slides, readings, and page numbers to each scheduled topic."
                    checked={includeMaterials}
                    onChange={(e) => setIncludeMaterials(e.target.checked)}
                  />
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </Dialog>
  );
}
