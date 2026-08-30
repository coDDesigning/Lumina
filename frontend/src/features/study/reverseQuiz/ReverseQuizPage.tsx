import { useState } from 'react';
import type { Workspace } from '@/data/workspaces';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { PageHeader } from '@/ui/PageHeader';
import { Button } from '@/ui/Button';
import { ReverseQuizSession } from './ReverseQuizSession';
import styles from './ReverseQuizPage.module.css';

export interface ReverseQuizPageProps {
  workspace: Workspace;
}

export default function ReverseQuizPage({ workspace }: ReverseQuizPageProps) {
  const courseId = Number(workspace.id);
  useDocumentTitle(`${workspace.name} · Reverse Quiz`);
  const [selectedTopic, setSelectedTopic] = useState<string | null>(null);
  const [customTopic, setCustomTopic] = useState('');

  const handleStart = (topic: string) => {
    if (topic.trim()) {
      setSelectedTopic(topic.trim());
    }
  };

  return (
    <div className={styles.container}>
      <PageHeader
        courseId={workspace.id}
        crumbs={[
          { label: workspace.name, to: `/courses/${workspace.id}` },
          { label: 'Reverse Quiz' },
        ]}
      />
      <p className={styles.description}>Explain concepts in your own words to check your understanding.</p>

      <div className={styles.content}>
        {selectedTopic ? (
          <ReverseQuizSession 
            courseId={Number(courseId)} 
            topic={selectedTopic} 
            onRestart={() => setSelectedTopic(null)}
          />
        ) : (
          <div className={styles.topicSelection}>
            <h2>What would you like to explain today?</h2>
            
            {workspace.topics.length > 0 && (
              <div className={styles.topicsList}>
                <h3>Course Topics</h3>
                <div className={styles.topicsGrid}>
                  {workspace.topics.map((topic, index) => (
                    <Button 
                      key={index}
                      variant="secondary"
                      onClick={() => handleStart(topic)}
                      className={styles.topicButton}
                    >
                      {topic}
                    </Button>
                  ))}
                </div>
              </div>
            )}

            <div className={styles.customTopic}>
              <h3>Or enter a specific topic</h3>
              <div className={styles.customTopicInput}>
                <input
                  type="text"
                  placeholder="e.g. Photosynthesis, Newton's Laws"
                  value={customTopic}
                  onChange={(e) => setCustomTopic(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleStart(customTopic)}
                  className={styles.input}
                />
                <Button 
                  disabled={!customTopic.trim()}
                  onClick={() => handleStart(customTopic)}
                >
                  Start
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
