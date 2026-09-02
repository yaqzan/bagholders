import { shouldAutoRefresh, shouldRefreshOnResume } from './timeUtils';

describe('shouldAutoRefresh', () => {
  test('refreshes during a stale intraday score bucket', () => {
    expect(
      shouldAutoRefresh(
        '2026-05-11T09:31:00-04:00',
        new Date('2026-05-11T10:05:00-04:00')
      )
    ).toBe(true);
  });

  test('does not refresh before the intraday pull buffer elapses', () => {
    expect(
      shouldAutoRefresh(
        '2026-05-11T09:31:00-04:00',
        new Date('2026-05-11T10:03:00-04:00')
      )
    ).toBe(false);
  });

  test('does not refresh after the final intraday bucket window', () => {
    expect(
      shouldAutoRefresh(
        '2026-05-11T15:31:00-04:00',
        new Date('2026-05-11T17:05:00-04:00')
      )
    ).toBe(false);
  });

  test('does not refresh on weekends', () => {
    expect(
      shouldAutoRefresh(
        '2026-05-08T15:31:00-04:00',
        new Date('2026-05-09T10:05:00-04:00')
      )
    ).toBe(false);
  });
});

describe('shouldRefreshOnResume', () => {
  test('refreshes when the intraday pull bucket is stale', () => {
    expect(
      shouldRefreshOnResume(
        '2026-05-11T09:31:00-04:00',
        new Date('2026-05-11T10:05:00-04:00')
      )
    ).toBe(true);
  });

  test('refreshes when loaded data exceeds the Updated-column stale window', () => {
    expect(
      shouldRefreshOnResume(
        '2026-05-11T09:00:00-04:00',
        new Date('2026-05-11T12:01:00-04:00')
      )
    ).toBe(true);
  });

  test('does not refresh recently loaded data on resume', () => {
    expect(
      shouldRefreshOnResume(
        '2026-05-11T11:55:00-04:00',
        new Date('2026-05-11T12:01:00-04:00')
      )
    ).toBe(false);
  });
});
