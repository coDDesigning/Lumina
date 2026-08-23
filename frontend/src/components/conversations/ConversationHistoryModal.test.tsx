import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { conversationsAPI } from '../../api/conversations';
import type { ConversationDetail, ConversationSummary } from '../../api/types';
import { ConversationHistoryModal } from './ConversationHistoryModal';

vi.mock('../../api/conversations', () => ({
  conversationsAPI: { list: vi.fn(), get: vi.fn() },
}));

const mockList = vi.mocked(conversationsAPI.list);
const mockGet = vi.mocked(conversationsAPI.get);

const QA_SUMMARY: ConversationSummary = {
  id: 12,
  course_id: 7,
  user_id: 3,
  conversation_type: 'course_qa',
  preview: 'What is recursion?',
  message_count: 2,
  created_at: '2026-08-20T09:00:00Z',
  updated_at: '2026-08-20T09:04:00Z',
};

const TUTOR_SUMMARY: ConversationSummary = {
  id: 18,
  course_id: 7,
  user_id: 3,
  conversation_type: 'ai_tutor',
  preview: 'Teach me graph traversal.',
  message_count: 2,
  created_at: '2026-08-20T10:00:00Z',
  updated_at: '2026-08-20T10:06:00Z',
};

const TUTOR_DETAIL: ConversationDetail = {
  ...TUTOR_SUMMARY,
  messages: [
    {
      id: 51,
      role: 'user',
      content: 'Teach me graph traversal.',
      created_at: '2026-08-20T10:00:00Z',
    },
    {
      id: 52,
      role: 'assistant',
      content: 'Start with breadth-first and depth-first search.',
      created_at: '2026-08-20T10:00:01Z',
    },
  ],
};

describe('ConversationHistoryModal', () => {
  beforeEach(() => {
    mockList.mockResolvedValue([QA_SUMMARY, TUTOR_SUMMARY]);
  });

  it('lists both conversation types and loads the selected detail', async () => {
    mockGet.mockResolvedValue(TUTOR_DETAIL);

    render(
      <ConversationHistoryModal
        courseId={7}
        courseName="Algorithms"
        onClose={vi.fn()}
        onResume={vi.fn()}
      />,
    );

    expect(await screen.findByText('Course Q&A')).toBeInTheDocument();
    expect(screen.getByText('AI Tutor')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Conversation 18/ }));

    expect(
      await screen.findAllByText('Teach me graph traversal.'),
    ).toHaveLength(2);
    expect(
      screen.getByText('Start with breadth-first and depth-first search.'),
    ).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith(7, 18, expect.anything());
  });

  it('resumes the loaded typed conversation', async () => {
    const onResume = vi.fn();
    mockGet.mockResolvedValue(TUTOR_DETAIL);

    render(
      <ConversationHistoryModal
        courseId={7}
        courseName="Algorithms"
        onClose={vi.fn()}
        onResume={onResume}
      />,
    );

    await userEvent.click(
      await screen.findByRole('button', { name: /Conversation 18/ }),
    );
    await userEvent.click(
      await screen.findByRole('button', { name: 'Resume conversation' }),
    );

    expect(onResume).toHaveBeenCalledWith(TUTOR_DETAIL);
  });

  it('closes with Escape and restores focus', async () => {
    const onClose = vi.fn();
    const opener = document.createElement('button');
    document.body.append(opener);
    opener.focus();

    const { unmount } = render(
      <ConversationHistoryModal
        courseId={7}
        courseName="Algorithms"
        onClose={onClose}
        onResume={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledOnce();

    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });

  it('does not offer resume for a read-only course', async () => {
    mockGet.mockResolvedValue(TUTOR_DETAIL);
    render(
      <ConversationHistoryModal
        courseId={7}
        courseName="Algorithms"
        canResume={false}
        onClose={vi.fn()}
        onResume={vi.fn()}
      />,
    );

    await userEvent.click(
      await screen.findByRole('button', { name: /Conversation 18/ }),
    );
    expect(await screen.findByText('Read-only access')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Resume conversation' }),
    ).not.toBeInTheDocument();
  });
});
