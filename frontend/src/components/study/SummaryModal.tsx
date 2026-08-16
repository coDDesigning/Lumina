import React, { useState } from 'react';
import {
  BookOpen,
  Check,
  Copy,
  Download,
  FileText,
  Lightbulb,
  Sparkles,
  X,
} from 'lucide-react';
import {
  GeneratedSummary,
  SummaryConfig,
  generateMockSummary,
} from '../../data/mockStudyData';
import './study.css';

interface SummaryModalProps {
  isOpen: boolean;
  onClose: () => void;
  courseName: string;
  topics: string[];
}

export const SummaryModal: React.FC<SummaryModalProps> = ({
  isOpen,
  onClose,
  courseName,
  topics,
}) => {
  const [config, setConfig] = useState<SummaryConfig>({
    type: 'overview',
    topic: 'All Topics',
    sourceCount: 3,
  });
  const [isGenerating, setIsGenerating] = useState(false);
  const [loadingStage, setLoadingStage] = useState('Analyzing uploaded documents...');
  const [summary, setSummary] = useState<GeneratedSummary | null>(null);
  const [copied, setCopied] = useState(false);

  if (!isOpen) return null;

  const handleGenerate = () => {
    setIsGenerating(true);
    setLoadingStage('Scanning document sources & extraction layers...');

    setTimeout(() => {
      setLoadingStage('Synthesizing core conceptual pillars...');
    }, 900);

    setTimeout(() => {
      setLoadingStage('Formatting structured takeaways & exam tips...');
    }, 1800);

    setTimeout(() => {
      const generated = generateMockSummary(courseName, topics, config);
      setSummary(generated);
      setIsGenerating(false);
    }, 2600);
  };

  const handleCopy = () => {
    if (!summary) return;
    const textToCopy = `${summary.title}\nTopic: ${summary.topic}\n\n${summary.content}\n\nKey Takeaways:\n${summary.keyTakeaways.map((t) => `- ${t}`).join('\n')}\n\nDefinitions:\n${summary.definitions.map((d) => `${d.term}: ${d.definition}`).join('\n')}\n\nExam Tips:\n${summary.examTips.map((e) => `- ${e}`).join('\n')}`;
    navigator.clipboard.writeText(textToCopy);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = () => {
    if (!summary) return;
    const textToDownload = `# ${summary.title}\n**Topic:** ${summary.topic}\n**Date:** ${summary.createdAt}\n\n${summary.content}\n\n## Key Takeaways\n${summary.keyTakeaways.map((t) => `- ${t}`).join('\n')}\n\n## Key Definitions\n${summary.definitions.map((d) => `### ${d.term}\n${d.definition}`).join('\n\n')}\n\n## Exam Preparation Tips\n${summary.examTips.map((e) => `- ${e}`).join('\n')}`;
    const blob = new Blob([textToDownload], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${courseName.toLowerCase().replace(/\s+/g, '_')}_summary.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleReset = () => {
    setSummary(null);
  };

  const topicOptions = ['All Topics', ...(topics.length > 0 ? topics : ['Core Concepts', 'Methodology', 'Applications'])];

  return (
    <div className="study-modal-backdrop" role="dialog" aria-modal="true">
      <div className="study-modal large-modal">
        <header className="study-modal-header">
          <div>
            <h2>
              <Sparkles aria-hidden="true" />
              AI Study Summary Generator
            </h2>
            <p>Generate clean, high-retention notes from your course sources</p>
          </div>
          <button
            className="modal-close-button"
            type="button"
            onClick={onClose}
            aria-label="Close summary modal"
          >
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="study-modal-body">
          {isGenerating ? (
            <div className="study-loading-state">
              <div className="study-pulse-spinner" />
              <h3>Generating Structured Study Guide</h3>
              <p>{loadingStage}</p>
            </div>
          ) : !summary ? (
            <div className="summary-container">
              <div className="summary-section-card">
                <h4>
                  <BookOpen aria-hidden="true" />
                  Customize Your Study Summary
                </h4>
                <p style={{ margin: '0 0 16px', fontSize: '13px', color: '#666' }}>
                  Select the target scope and synthesis format tailored for your revision goals.
                </p>

                <div className="study-form-grid">
                  <div className="study-field-group">
                    <label htmlFor="summary-type">Summary Format</label>
                    <select
                      id="summary-type"
                      value={config.type}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          type: e.target.value as SummaryConfig['type'],
                        })
                      }
                    >
                      <option value="overview">Quick Overview & Essentials</option>
                      <option value="comprehensive">Comprehensive Study Guide</option>
                      <option value="key_concepts">Key Definitions & Formulas</option>
                      <option value="exam_tips">High-Yield Exam Cram Sheet</option>
                    </select>
                  </div>

                  <div className="study-field-group">
                    <label htmlFor="summary-topic">Course Topic Focus</label>
                    <select
                      id="summary-topic"
                      value={config.topic}
                      onChange={(e) => setConfig({ ...config, topic: e.target.value })}
                    >
                      {topicOptions.map((topic) => (
                        <option key={topic} value={topic}>
                          {topic}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="summary-container">
              <div className="summary-meta-badge">
                <FileText style={{ width: '14px', height: '14px' }} />
                <span>{summary.topic}</span> • <span>{summary.createdAt}</span>
              </div>

              <div className="summary-section-card">
                <h4>
                  <BookOpen aria-hidden="true" />
                  Core Executive Synthesis
                </h4>
                <div style={{ fontSize: '14px', lineHeight: 1.6, color: '#352e3d', whiteSpace: 'pre-line' }}>
                  {summary.content}
                </div>
              </div>

              <div className="summary-section-card">
                <h4>
                  <Check aria-hidden="true" />
                  Key Takeaways
                </h4>
                <ul className="summary-bullet-list">
                  {summary.keyTakeaways.map((takeaway, idx) => (
                    <li key={idx}>{takeaway}</li>
                  ))}
                </ul>
              </div>

              <div className="summary-section-card">
                <h4>
                  <Lightbulb aria-hidden="true" />
                  Important Definitions & Principles
                </h4>
                <div className="definitions-grid">
                  {summary.definitions.map((def, idx) => (
                    <div className="definition-item" key={idx}>
                      <strong>{def.term}</strong>
                      <p>{def.definition}</p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="summary-section-card">
                <h4>
                  <Sparkles aria-hidden="true" />
                  High-Yield Exam Tips
                </h4>
                <ul className="summary-bullet-list">
                  {summary.examTips.map((tip, idx) => (
                    <li key={idx}>{tip}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </div>

        <footer className="study-modal-footer">
          {!summary ? (
            <>
              <button className="secondary-button" type="button" onClick={onClose} disabled={isGenerating}>
                Cancel
              </button>
              <button className="primary-button" type="button" onClick={handleGenerate} disabled={isGenerating}>
                <Sparkles aria-hidden="true" style={{ width: '16px', height: '16px' }} />
                Generate Summary
              </button>
            </>
          ) : (
            <>
              <button className="secondary-button" type="button" onClick={handleReset}>
                New Summary
              </button>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button className="secondary-button" type="button" onClick={handleCopy}>
                  {copied ? <Check style={{ width: '16px', height: '16px', color: '#10b981' }} /> : <Copy style={{ width: '16px', height: '16px' }} />}
                  {copied ? 'Copied!' : 'Copy'}
                </button>
                <button className="primary-button" type="button" onClick={handleDownload}>
                  <Download aria-hidden="true" style={{ width: '16px', height: '16px' }} />
                  Download MD
                </button>
              </div>
            </>
          )}
        </footer>
      </div>
    </div>
  );
};
