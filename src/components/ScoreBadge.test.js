import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ScoreBadge from './ScoreBadge';

describe('ScoreBadge', () => {
  test('renders 70-74 as a muted green promising bucket', () => {
    const markup = renderToStaticMarkup(<ScoreBadge score={74} showLabel />);

    expect(markup).toContain('text-trading-green-500');
    expect(markup).toContain('bg-trading-green-500/[0.08]');
    expect(markup).toContain('(Promising)');
    expect(markup).not.toContain('text-trading-green-400');
    expect(markup).not.toContain('(Strong)');
  });

  test('keeps opportunity thresholds at 75 for calls and 25 for puts', () => {
    const callThreshold = renderToStaticMarkup(<ScoreBadge score={75} showLabel />);
    expect(callThreshold).toContain('text-trading-green-400');
    expect(callThreshold).toContain('(Strong)');

    const putMiddlingEdge = renderToStaticMarkup(<ScoreBadge score={26} showLabel />);
    expect(putMiddlingEdge).toContain('text-gray-400');
    expect(putMiddlingEdge).toContain('(Neutral)');

    const putThreshold = renderToStaticMarkup(<ScoreBadge score={25} showLabel />);
    expect(putThreshold).toContain('text-trading-red-400');
    expect(putThreshold).toContain('(Weak)');
  });
});
