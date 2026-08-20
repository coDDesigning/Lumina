import { describe, expect, it } from 'vitest';
import { formatDelta, reasonLabel, transactionLabel } from './creditLabels';
import type { CreditTransaction } from './types';

function transaction(overrides: Partial<CreditTransaction>): CreditTransaction {
  return {
    id: 1,
    delta: -1,
    balance_after: 49,
    reason: 'generation_charge',
    actor_type: 'user',
    actor_user_id: 2,
    actor_label: null,
    source_type: null,
    source_id: null,
    refunds_transaction_id: null,
    grant_period: null,
    note: null,
    created_at: '2026-08-20T10:00:00Z',
    ...overrides,
  };
}

describe('creditLabels', () => {
  it('names every reason the backend can emit', () => {
    expect(reasonLabel('initial_grant')).toBe('Welcome credits');
    expect(reasonLabel('periodic_grant')).toBe('Monthly credits');
    expect(reasonLabel('generation_charge')).toBe('AI generation');
    expect(reasonLabel('generation_refund')).toBe('Refund');
    expect(reasonLabel('admin_grant')).toBe('Administrator grant');
    expect(reasonLabel('admin_adjustment')).toBe('Administrator adjustment');
    expect(reasonLabel('metering_reset')).toBe('Balance re-baselined');
    expect(reasonLabel('migration_reconciliation')).toBe('Opening balance');
  });

  it('falls back to a readable form for an unknown reason', () => {
    expect(reasonLabel('future_mechanism')).toBe('future mechanism');
  });

  it('prefers the specific feature over the bare category', () => {
    expect(transactionLabel(transaction({ source_type: 'study_guide' }))).toBe(
      'AI generation — Study guide',
    );
  });

  it('omits the feature when the transaction has no source', () => {
    expect(transactionLabel(transaction({ reason: 'admin_grant', delta: 20 }))).toBe(
      'Administrator grant',
    );
  });

  it('signs a delta so a credit reads as a gain', () => {
    expect(formatDelta(20)).toBe('+20');
    expect(formatDelta(-5)).toBe('-5');
    expect(formatDelta(0)).toBe('0');
  });
});
