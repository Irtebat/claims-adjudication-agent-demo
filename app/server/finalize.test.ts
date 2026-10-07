import { describe, it, expect } from 'vitest';
import {
  operationalVerdict,
  validateFinalize,
  buildAdjudicatedEvent,
  decisionRecordInsertSql,
  runFinalize,
  type FinalizeRequest,
  type QueryClient,
  type Pool,
} from './finalize';

const rec = {
  recommendedVerdict: 'APPROVE',
  recommendedDisposition: 'CREDIT',
  approvedAmount: 5000,
  claimedAmount: 9000,
};

describe('operationalVerdict', () => {
  it('maps PEND_INVESTIGATE -> PEND, others unchanged', () => {
    expect(operationalVerdict('PEND_INVESTIGATE')).toBe('PEND');
    expect(operationalVerdict('APPROVE')).toBe('APPROVE');
    expect(operationalVerdict('DENY')).toBe('DENY');
  });
});

describe('validateFinalize', () => {
  it('accepts an unchanged confirm with no override needed', () => {
    const v = validateFinalize(
      { finalVerdict: 'APPROVE', finalDisposition: 'CREDIT', approvedAmount: 5000, decidedBy: 'a@x' },
      rec
    );
    expect(v.ok).toBe(true);
    expect(v.overrideFlag).toBe(false);
    expect(v.operationalVerdict).toBe('APPROVE');
  });

  it('requires override_reason when any field differs from the recommendation', () => {
    const v = validateFinalize(
      { finalVerdict: 'APPROVE', finalDisposition: 'CREDIT', approvedAmount: 4000, decidedBy: 'a@x' },
      rec
    );
    expect(v.overrideFlag).toBe(true);
    expect(v.ok).toBe(false);
    expect(v.errors).toContain('override_reason_required');
  });

  it('accepts an override WITH a reason', () => {
    const v = validateFinalize(
      {
        finalVerdict: 'APPROVE',
        finalDisposition: 'CREDIT',
        approvedAmount: 4000,
        overrideReason: 'partial goodwill settlement',
        decidedBy: 'a@x',
      },
      rec
    );
    expect(v.ok).toBe(true);
    expect(v.overrideFlag).toBe(true);
  });

  it('rejects decided_by missing', () => {
    const v = validateFinalize(
      { finalVerdict: 'APPROVE', finalDisposition: 'CREDIT', approvedAmount: 5000, decidedBy: null },
      rec
    );
    expect(v.errors).toContain('decided_by_required');
  });

  it('rejects approved_amount exceeding claimed', () => {
    const v = validateFinalize(
      {
        finalVerdict: 'APPROVE',
        finalDisposition: 'CREDIT',
        approvedAmount: 99999,
        overrideReason: 'x',
        decidedBy: 'a@x',
      },
      rec
    );
    expect(v.errors).toContain('approved_amount_exceeds_claimed');
  });

  it('enforces DENY => zero amount and a deny/duplicate disposition', () => {
    const bad = validateFinalize(
      { finalVerdict: 'DENY', finalDisposition: 'DENY', approvedAmount: 100, overrideReason: 'x', decidedBy: 'a@x' },
      rec
    );
    expect(bad.errors).toContain('deny_must_be_zero_amount');
    const good = validateFinalize(
      { finalVerdict: 'DENY', finalDisposition: 'DUPLICATE', approvedAmount: 0, overrideReason: 'x', decidedBy: 'a@x' },
      rec
    );
    expect(good.ok).toBe(true);
    expect(good.duplicateFlag).toBe(true);
  });

  it('enforces PEND_INVESTIGATE => zero amount + PEND_INVESTIGATE disposition', () => {
    const v = validateFinalize(
      {
        finalVerdict: 'PEND_INVESTIGATE',
        finalDisposition: 'CREDIT',
        approvedAmount: 0,
        overrideReason: 'x',
        decidedBy: 'a@x',
      },
      rec
    );
    expect(v.errors).toContain('invalid_pend_disposition');
  });

  it('rejects an invalid APPROVE disposition', () => {
    const v = validateFinalize(
      { finalVerdict: 'APPROVE', finalDisposition: 'DENY', approvedAmount: 5000, decidedBy: 'a@x' },
      rec
    );
    expect(v.errors).toContain('invalid_approve_disposition');
  });
});

describe('validateFinalize — investigate/PEND vocabulary (override detection)', () => {
  // The recommendation as persisted on adjudications: verdict is OPERATIONAL ('PEND'),
  // disposition is 'PEND_INVESTIGATE', amount 0. The client submits the agent verdict
  // 'PEND_INVESTIGATE' for an unchanged accept — the two must NOT read as a change.
  const pendRec = {
    recommendedVerdict: 'PEND',
    recommendedDisposition: 'PEND_INVESTIGATE',
    approvedAmount: 0,
    claimedAmount: 9000,
  };

  it('treats an unchanged PEND/investigate accept as NOT an override (no reason required)', () => {
    const v = validateFinalize(
      { finalVerdict: 'PEND_INVESTIGATE', finalDisposition: 'PEND_INVESTIGATE', approvedAmount: 0, decidedBy: 'a@x' },
      pendRec
    );
    expect(v.ok).toBe(true);
    expect(v.overrideFlag).toBe(false);
    expect(v.operationalVerdict).toBe('PEND');
    expect(v.errors).not.toContain('override_reason_required');
  });

  it('ignores a stray reason on an unchanged PEND accept (still not an override)', () => {
    const v = validateFinalize(
      {
        finalVerdict: 'PEND_INVESTIGATE',
        finalDisposition: 'PEND_INVESTIGATE',
        approvedAmount: 0,
        overrideReason: 'noted',
        decidedBy: 'a@x',
      },
      pendRec
    );
    expect(v.ok).toBe(true);
    expect(v.overrideFlag).toBe(false);
  });

  it('still flags a genuine PEND -> APPROVE change as an override requiring a reason', () => {
    const v = validateFinalize(
      { finalVerdict: 'APPROVE', finalDisposition: 'CREDIT', approvedAmount: 4000, decidedBy: 'a@x' },
      pendRec
    );
    expect(v.overrideFlag).toBe(true);
    expect(v.ok).toBe(false);
    expect(v.errors).toContain('override_reason_required');
  });

  it('accepts a PEND -> APPROVE override when a reason is given', () => {
    const v = validateFinalize(
      {
        finalVerdict: 'APPROVE',
        finalDisposition: 'CREDIT',
        approvedAmount: 4000,
        overrideReason: 'goodwill settlement',
        decidedBy: 'a@x',
      },
      pendRec
    );
    expect(v.ok).toBe(true);
    expect(v.overrideFlag).toBe(true);
  });

  it('flags a genuine PEND -> DENY change as an override requiring a reason', () => {
    const v = validateFinalize(
      { finalVerdict: 'DENY', finalDisposition: 'DENY', approvedAmount: 0, decidedBy: 'a@x' },
      pendRec
    );
    expect(v.overrideFlag).toBe(true);
    expect(v.errors).toContain('override_reason_required');
  });
});

describe('buildAdjudicatedEvent', () => {
  it('reflects the FINAL decision and mirrors the canonical shape', () => {
    const { eventId, sql } = buildAdjudicatedEvent(
      'ADJ-1',
      {
        claim_id: 'CLM-9',
        idempotency_key: 'idem',
        supplier_attributable: true,
        recovery_supplier_id: 'SUP-7',
        duplicate_of_claim_id: null,
      },
      {
        finalVerdict: 'PEND_INVESTIGATE',
        operationalVerdict: 'PEND',
        disposition: 'PEND_INVESTIGATE',
        approvedAmount: 0,
      },
      () => '2026-01-01T00:00:00Z'
    );
    expect(eventId).toBe('adj-ADJ-1');
    expect(sql.text).toContain('INSERT INTO public.outbox');
    expect(sql.text).toContain('ON CONFLICT (event_id) DO NOTHING');
    const payload = JSON.parse(String(sql.params[3])) as Record<string, unknown>;
    expect(payload.event_id).toBe('adj-ADJ-1');
    expect(payload.event_type).toBe('claim.adjudicated');
    expect(payload.schema_version).toBe('claim-event/v1');
    expect(payload.verdict).toBe('PEND'); // operational
    expect(payload.recommended_verdict).toBe('PEND_INVESTIGATE');
    expect(payload.approved_amount).toBe(0);
    expect(payload.supplier_attributable).toBe(true);
    expect(payload.recovery_supplier_id).toBe('SUP-7');
    expect(sql.params[0]).toBe('adj-ADJ-1');
    expect(sql.params[1]).toBe('CLM-9'); // aggregate_id
  });
});

describe('decisionRecordInsertSql', () => {
  it('preserves the deterministic baseline and overrides the human-decision columns', () => {
    const { text, params } = decisionRecordInsertSql('ADJ-1', {
      finalVerdict: 'APPROVE',
      finalDisposition: 'CREDIT',
      approvedAmount: 4000,
      duplicateFlag: false,
      decidedBy: 'a@x',
      overrideReason: 'goodwill',
    });
    // deterministic baseline columns are carried forward from the prior version.
    expect(text).toContain('deterministic_verdict, deterministic_disposition');
    expect(text).toContain('max(record_version) + 1');
    // No ON CONFLICT: a version collision must fail the tx (caller asserts rowCount),
    // never be silently swallowed.
    expect(text).not.toContain('ON CONFLICT');
    expect(params).toEqual(['ADJ-1', 4000, false, 'APPROVE', 'CREDIT', 'a@x', 'goodwill']);
  });
});

// --- runFinalize (transactional) with a scripted fake pg client --------------
type Script = (text: string) => { rows: Record<string, unknown>[]; rowCount: number };

class FakeClient implements QueryClient {
  calls: { text: string; params?: unknown[] }[] = [];
  released = false;
  constructor(private script: Script) {}
  query(text: string, params?: unknown[]) {
    this.calls.push({ text, params });
    if (/^\s*(BEGIN|COMMIT|ROLLBACK)/.test(text)) return Promise.resolve({ rows: [], rowCount: 0 });
    return Promise.resolve(this.script(text));
  }
  release() {
    this.released = true;
  }
  mutations() {
    return this.calls.filter((c) => /INSERT INTO|UPDATE public/.test(c.text));
  }
  didOutbox() {
    return this.calls.some((c) => /INSERT INTO public\.outbox/.test(c.text));
  }
  committed() {
    return this.calls.some((c) => /^\s*COMMIT/.test(c.text));
  }
}

class FakePool implements Pool {
  constructor(public client: FakeClient) {}
  connect() {
    return Promise.resolve(this.client);
  }
}

const RECOMMENDED_ROW = {
  claim_id: 'CLM-9',
  recommended_verdict: 'APPROVE',
  recommended_disposition: 'CREDIT',
  approved_amount: 5000,
  claimed_amount: 9000,
  decision_status: 'RECOMMENDED',
  supplier_attributable: false,
  recovery_supplier_id: null,
  duplicate_of_claim_id: null,
  idempotency_key: 'idem',
};

function scriptHappy(): Script {
  return (text) => {
    if (/FOR UPDATE/.test(text)) return { rows: [RECOMMENDED_ROW], rowCount: 1 };
    if (/UPDATE public\.adjudications/.test(text)) return { rows: [{ ...RECOMMENDED_ROW }], rowCount: 1 };
    return { rows: [], rowCount: 1 }; // record insert + outbox insert
  };
}

const confirmReq: FinalizeRequest = {
  finalVerdict: 'APPROVE',
  finalDisposition: 'CREDIT',
  approvedAmount: 5000,
  decidedBy: 'adjuster@x',
};

describe('runFinalize', () => {
  it('happy path: one tx writes UPDATE + new record + outbox, then COMMIT', async () => {
    const client = new FakeClient(scriptHappy());
    const result = await runFinalize(new FakePool(client), 'ADJ-1', confirmReq);
    expect(result.status).toBe('finalized');
    if (result.status === 'finalized') {
      expect(result.decidedBy).toBe('adjuster@x');
      expect(result.overrideFlag).toBe(false);
      expect(result.eventId).toBe('adj-ADJ-1');
    }
    expect(client.committed()).toBe(true);
    expect(client.didOutbox()).toBe(true);
    // exactly the three money-path mutations
    expect(client.mutations()).toHaveLength(3);
    expect(client.released).toBe(true);
    // decided_by is bound on the UPDATE
    const upd = client.calls.find((c) => /UPDATE public\.adjudications/.test(c.text));
    expect(upd?.params).toContain('adjuster@x');
  });

  it('records decided_by and override_reason on an override', async () => {
    const client = new FakeClient(scriptHappy());
    const result = await runFinalize(new FakePool(client), 'ADJ-1', {
      finalVerdict: 'APPROVE',
      finalDisposition: 'CREDIT',
      approvedAmount: 4000,
      overrideReason: 'goodwill',
      decidedBy: 'adjuster@x',
    });
    expect(result.status).toBe('finalized');
    if (result.status === 'finalized') expect(result.overrideFlag).toBe(true);
    const upd = client.calls.find((c) => /UPDATE public\.adjudications/.test(c.text));
    expect(upd?.params).toContain('goodwill');
  });

  it('rejects an override with no reason server-side, writing nothing', async () => {
    const client = new FakeClient(scriptHappy());
    const result = await runFinalize(new FakePool(client), 'ADJ-1', {
      finalVerdict: 'DENY',
      finalDisposition: 'DENY',
      approvedAmount: 0,
      decidedBy: 'adjuster@x',
    });
    expect(result.status).toBe('invalid');
    if (result.status === 'invalid') expect(result.errors).toContain('override_reason_required');
    expect(client.mutations()).toHaveLength(0);
    expect(client.didOutbox()).toBe(false);
    expect(client.committed()).toBe(false);
  });

  it('is idempotent: re-finalizing an already-FINAL adjudication is a no-op', async () => {
    const client = new FakeClient((text) => {
      if (/FOR UPDATE/.test(text)) return { rows: [{ ...RECOMMENDED_ROW, decision_status: 'FINAL' }], rowCount: 1 };
      return { rows: [], rowCount: 1 };
    });
    const result = await runFinalize(new FakePool(client), 'ADJ-1', confirmReq);
    expect(result.status).toBe('already_final');
    expect(client.mutations()).toHaveLength(0); // no UPDATE, no new record version, no outbox
    expect(client.didOutbox()).toBe(false);
    expect(client.committed()).toBe(false);
  });

  it('treats a lost UPDATE race (rowCount 0) as already-final', async () => {
    const client = new FakeClient((text) => {
      if (/FOR UPDATE/.test(text)) return { rows: [RECOMMENDED_ROW], rowCount: 1 };
      if (/UPDATE public\.adjudications/.test(text)) return { rows: [], rowCount: 0 };
      return { rows: [], rowCount: 1 };
    });
    const result = await runFinalize(new FakePool(client), 'ADJ-1', confirmReq);
    expect(result.status).toBe('already_final');
    expect(client.didOutbox()).toBe(false);
  });

  it('returns not_found when the adjudication is absent', async () => {
    const client = new FakeClient(() => ({ rows: [], rowCount: 0 }));
    const result = await runFinalize(new FakePool(client), 'ADJ-x', confirmReq);
    expect(result.status).toBe('not_found');
    expect(client.didOutbox()).toBe(false);
  });

  it('rolls back the whole tx (no outbox, no commit) if the version insert writes zero rows', async () => {
    // Simulate the human-final decision_record insert affecting zero rows (missing
    // baseline or a version collision). The transaction MUST roll back so no FINAL
    // adjudication is left without its audit version and no outbox event is emitted.
    const client = new FakeClient((text) => {
      if (/FOR UPDATE/.test(text)) return { rows: [RECOMMENDED_ROW], rowCount: 1 };
      if (/UPDATE public\.adjudications/.test(text)) return { rows: [{ ...RECOMMENDED_ROW }], rowCount: 1 };
      if (/INSERT INTO public\.adjudication_decision_records/.test(text)) return { rows: [], rowCount: 0 };
      return { rows: [], rowCount: 1 };
    });
    await expect(runFinalize(new FakePool(client), 'ADJ-1', confirmReq)).rejects.toThrow(/version not written/);
    expect(client.didOutbox()).toBe(false); // outbox insert never reached
    expect(client.committed()).toBe(false); // never committed
    expect(client.calls.some((c) => /^\s*ROLLBACK/.test(c.text))).toBe(true);
    expect(client.released).toBe(true);
  });
});
