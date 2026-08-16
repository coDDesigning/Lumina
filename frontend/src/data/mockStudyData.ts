export interface QuizQuestion {
  id: number;
  question: string;
  options: string[];
  correctAnswer: number; // 0-based index
  explanation: string;
  topic: string;
}

export interface QuizConfig {
  questionType: 'multiple_choice' | 'true_false';
  questionCount: number;
  difficulty: 'easy' | 'medium' | 'hard';
  topic: string;
  hasTimer: boolean;
  timeLimitSeconds: number;
}

export interface QuizResult {
  totalQuestions: number;
  correctCount: number;
  incorrectCount: number;
  scorePercentage: number;
  timeSpentSeconds: number;
  userAnswers: { [questionId: number]: number };
  questions: QuizQuestion[];
  completedAt: string;
}

export interface SummaryConfig {
  type: 'overview' | 'comprehensive' | 'key_concepts' | 'exam_tips';
  topic: string;
  sourceCount: number;
}

export interface GeneratedSummary {
  title: string;
  type: string;
  topic: string;
  content: string;
  keyTakeaways: string[];
  definitions: { term: string; definition: string }[];
  examTips: string[];
  createdAt: string;
}

export interface TopicMastery {
  topic: string;
  masteryPercentage: number;
  status: 'Mastered' | 'In Progress' | 'Needs Review';
  quizzesTaken: number;
}

export function generateMockQuestions(
  courseName: string,
  topics: string[],
  config: QuizConfig
): QuizQuestion[] {
  const selectedTopics =
    config.topic === 'All Topics' || !config.topic
      ? topics.length > 0
        ? topics
        : ['Fundamentals', 'Core Concepts', 'Methodology', 'Applications']
      : [config.topic];

  const pool: QuizQuestion[] = [
    {
      id: 1,
      question: `What is the primary mechanism of action discussed regarding ${selectedTopics[0] || 'core concepts'} in ${courseName}?`,
      options: [
        'Cognitive restructuring through continuous iterative exposure',
        'Direct sensory perception bypass in high-stress conditions',
        'External locus attribution to minimize emotional dissonance',
        'Algorithmic computation of optimal heuristic paths',
      ],
      correctAnswer: 2,
      explanation:
        'As highlighted in the primary course source, individuals frequently construct cognitive barriers and shift attribution externally to mitigate emotional liability and preserve psychological equilibrium.',
      topic: selectedTopics[0] || 'Core Concepts',
    },
    {
      id: 2,
      question: `Which of the following best characterizes the relationship between empirical validation and theoretical models in ${selectedTopics[1] || selectedTopics[0] || 'Methodology'}?`,
      options: [
        'Theoretical models precede and strictly dictate empirical observation',
        'Empirical feedback loops continuously calibrate theoretical boundaries',
        'Observations are purely qualitative without structural dependencies',
        'Experimental noise invalidates deductive baseline assumptions',
      ],
      correctAnswer: 1,
      explanation:
        'The literature emphasizes an iterative feedback mechanism where empirical trials refine and constrain initial theoretical assumptions.',
      topic: selectedTopics[1] || selectedTopics[0] || 'Methodology',
    },
    {
      id: 3,
      question: `When analyzing failure cases in ${selectedTopics[0] || courseName}, what is identified as the most frequent bottleneck?`,
      options: [
        'Insufficient sample size in preliminary test batches',
        'Overfitting to localized historical variance',
        'Neglect of boundary conditions during peak load states',
        'Misalignment between user intent and system objective function',
      ],
      correctAnswer: 2,
      explanation:
        'Boundary condition violations during edge states account for the vast majority of degraded performance outcomes described in chapter 3.',
      topic: selectedTopics[0] || 'Analysis',
    },
    {
      id: 4,
      question: `In the context of ${selectedTopics[2] || selectedTopics[0] || 'Applications'}, what distinguishes adaptive strategies from static counterparts?`,
      options: [
        'Static strategies require deterministic runtime guarantees',
        'Adaptive strategies incorporate dynamic feedback to re-weight parameters in real time',
        'Static approaches cannot be evaluated computationally',
        'Adaptive models always guarantee polynomial convergence',
      ],
      correctAnswer: 1,
      explanation:
        'Dynamic parameter recalibration based on incoming signal variance is the defining characteristic of adaptive systems.',
      topic: selectedTopics[2] || selectedTopics[0] || 'Applications',
    },
    {
      id: 5,
      question: `True or False: The principle of conservation of state implies that every transition must be reversible without external entropy cost.`,
      options: [
        'True — All ideal state transitions preserve internal symmetry',
        'False — Irreversible operations generate positive entropy according to fundamental constraints',
      ],
      correctAnswer: 1,
      explanation:
        'State transitions in real systems invariably incur thermodynamic or informational entropy costs, making full reversibility impossible.',
      topic: selectedTopics[0] || 'Fundamentals',
    },
    {
      id: 6,
      question: `What role does heuristic approximation play when exact solutions for ${courseName} become computationally intractable?`,
      options: [
        'It replaces validation testing with random sampling',
        'It provides bounded near-optimal solutions within practical time limits',
        'It guarantees zero margin of error on worst-case inputs',
        'It eliminates the need for foundational axioms',
      ],
      correctAnswer: 1,
      explanation:
        'Heuristic methods trade global optimality guarantees for polynomial execution speed with verifiable approximation bounds.',
      topic: selectedTopics[1] || 'Heuristics',
    },
    {
      id: 7,
      question: `Which diagnostic metric is most suitable for detecting early drift in ${selectedTopics[0] || 'core performance'}?`,
      options: [
        'Cumulative moving average of residual variance',
        'Single-point threshold triggers on baseline outliers',
        'Static percentile snapshots at arbitrary intervals',
        'Normalized mean absolute deviation of raw outputs',
      ],
      correctAnswer: 0,
      explanation:
        'Residual variance tracking across a sliding window offers the highest sensitivity to gradual distribution shifts.',
      topic: selectedTopics[0] || 'Diagnostics',
    },
    {
      id: 8,
      question: `According to the syllabus guidelines, how should conflict between overlapping source definitions be resolved?`,
      options: [
        'Discard all conflicting sources from the study set',
        'Prioritize primary empirical texts over secondary commentary',
        'Average the conflicting claims into a single compromise definition',
        'Rely exclusively on initial intuitions',
      ],
      correctAnswer: 1,
      explanation:
        'Hierarchical source evaluation prioritizes peer-reviewed primary findings over derivative synthesis materials.',
      topic: selectedTopics[0] || 'Methodology',
    },
  ];

  return pool.slice(0, Math.min(config.questionCount, pool.length));
}

export function generateMockSummary(
  courseName: string,
  topics: string[],
  config: SummaryConfig
): GeneratedSummary {
  const activeTopic = config.topic === 'All Topics' || !config.topic ? topics.join(', ') || 'Comprehensive Course Material' : config.topic;

  return {
    title: `${courseName}: ${config.type.replace('_', ' ').toUpperCase()}`,
    type: config.type,
    topic: activeTopic,
    createdAt: new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
    content: `This structured study summary synthesizes the core principles and analytical frameworks of **${courseName}**, focusing on **${activeTopic}**.\n\n### Key Conceptual Pillars\n1. **Theoretical Foundations:** Understanding the foundational axioms that govern system behaviors under varying constraints.\n2. **Mechanisms of Action:** How cognitive, computational, and structural interactions produce observed outcomes.\n3. **Application & Boundary Limits:** Recognizing where standard models hold true and identifying conditions that precipitate system divergence.`,
    keyTakeaways: [
      `Master the primary definitions and contrast mechanisms in ${topics[0] || 'Core Concepts'}.`,
      'Recognize how empirical observations continuously constrain and refine theoretical baseline models.',
      'Pay close attention to boundary condition assumptions during edge-case examination scenarios.',
      'Understand the trade-offs between exact deterministic solutions and heuristic approximations.',
    ],
    definitions: [
      {
        term: 'Cognitive Attenuation',
        definition: 'The reduction in perceived urgency or impact resulting from repeated exposure to high-friction stimuli.',
      },
      {
        term: 'State Equilibrium',
        definition: 'A stable condition wherein opposing forces or influence vectors balance out, preventing spontaneous trajectory shifts.',
      },
      {
        term: 'Boundary Divergence',
        definition: 'The phenomenon where an analytical model ceases to yield accurate predictions once parameter thresholds are breached.',
      },
    ],
    examTips: [
      'Questions on the midterm frequently contrast adaptive vs. static methodologies — ensure you can draw comparative tables.',
      'Review formulas related to variance tracking and sliding window derivations.',
      'Remember that primary source evidence always supersedes generalized secondary review notes in multiple-choice ambiguities.',
    ],
  };
}

export function calculateTopicMastery(
  topics: string[],
  quizResults: QuizResult[]
): TopicMastery[] {
  const fallbackTopics = topics.length > 0 ? topics : ['Core Foundations', 'Analytical Methods', 'Applied Studies'];

  return fallbackTopics.map((topic, index) => {
    // Generate calculated or realistic seed mastery
    const baseScore = 65 + ((index * 13 + 7) % 30);
    const hasQuizzes = quizResults.length > 0;
    const masteryPercentage = hasQuizzes
      ? Math.min(100, Math.round((baseScore + quizResults[0].scorePercentage) / 2))
      : baseScore;

    let status: TopicMastery['status'] = 'In Progress';
    if (masteryPercentage >= 80) status = 'Mastered';
    else if (masteryPercentage < 60) status = 'Needs Review';

    return {
      topic,
      masteryPercentage,
      status,
      quizzesTaken: hasQuizzes ? quizResults.length : 1,
    };
  });
}
