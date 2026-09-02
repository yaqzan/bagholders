/**
 * Utility functions for stock-related calculations
 */

/**
 * Calculate EPS growth interpretation: ((forward_eps - eps) / eps) * 100
 */
export const calculateGrowthInterpretation = (eps, forwardEps) => {
  if (!eps || !forwardEps || eps === 0) {
    return { interpretation: null, metric: null };
  }
  const growth = ((forwardEps - eps) / eps) * 100;
  let interpretation;
  if (growth > 40) interpretation = "Explosive";
  else if (growth >= 25) interpretation = "Strong";
  else if (growth >= 15) interpretation = "Moderate";
  else if (growth >= 5) interpretation = "Steady";
  else if (growth >= 0) interpretation = "Stagnant";
  else interpretation = "Declining";
  return { interpretation, metric: growth };
};

/**
 * Calculate growth rate interpretation: ((pe - forward_pe) / pe) * 100
 */
export const calculateGrowthRateInterpretation = (pe, forwardPe) => {
  if (!pe || !forwardPe || pe === 0) {
    return { interpretation: null, metric: null };
  }
  const growthRate = ((pe - forwardPe) / pe) * 100;
  let interpretation;
  if (growthRate > 25) interpretation = "Accelerating";
  else if (growthRate >= 15) interpretation = "Strong Accel";
  else if (growthRate >= 5) interpretation = "Mild Accel";
  else if (growthRate >= 0) interpretation = "Stable";
  else if (growthRate >= -10) interpretation = "Decelerating";
  else interpretation = "Contracting";
  return { interpretation, metric: growthRate };
};

/**
 * Get combined growth score interpretation based on thresholds
 */
export const getGrowthScoreInterpretation = (growthScore) => {
  if (growthScore === null || growthScore === undefined) return null;
  if (growthScore > 35) return "Exceptional";
  if (growthScore >= 25) return "Strong";
  if (growthScore >= 15) return "Good";
  if (growthScore >= 5) return "Fair";
  if (growthScore >= 0) return "Weak";
  return "Poor";
};

/**
 * Format earnings days from raw number to human-readable string
 * @param {number} earningsDaysRaw - Raw number of days until earnings
 * @returns {string} Formatted earnings days string
 */
// Classify EarningsDate.call_time → 'bmo' | 'amc' | null.
// Accepts 'BMO'/'AMC' tokens or 'HH:MM' strings (BMO = before 09:30 ET, AMC = at/after 16:00 ET).
const classifyCallTime = (callTime) => {
  if (!callTime) return null;
  const ct = String(callTime).trim().toLowerCase();
  if (ct === 'bmo') return 'bmo';
  if (ct === 'amc') return 'amc';
  const m = ct.match(/^(\d{1,2}):(\d{2})/);
  if (m) {
    const mins = parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
    if (mins < 9 * 60 + 30) return 'bmo';
    if (mins >= 16 * 60) return 'amc';
  }
  return null;
};

export const formatEarningsDays = (earningsDaysRaw, callTime = null) => {
  if (earningsDaysRaw === null || earningsDaysRaw === undefined) {
    return null;
  }
  if (earningsDaysRaw < 0) {
    return null;
  }
  const tod = classifyCallTime(callTime);
  if (earningsDaysRaw === 0) {
    if (tod === 'bmo') return 'This Morning';
    if (tod === 'amc') return 'Today After Close';
    return 'Today';
  }
  if (earningsDaysRaw === 1) {
    if (tod === 'bmo') return 'Tomorrow Morning';
    if (tod === 'amc') return 'Tomorrow After Close';
    return 'Tomorrow';
  }
  return `${earningsDaysRaw} days`;
};

/**
 * Format target price percentage from raw growth value
 * @param {number} priceTargetGrowth - Raw price target growth percentage
 * @returns {object} Object containing formatted percentage and raw value
 */
export const formatTargetPercentage = (priceTargetGrowth) => {
  if (priceTargetGrowth === null || priceTargetGrowth === undefined) {
    return {
      percentage: null,
      raw: null
    };
  }
  
  const pct = Math.round(priceTargetGrowth);
  const sign = pct > 0 ? '+' : '';
  const percentage = `${sign}${pct}%`;
  
  return {
    percentage,
    raw: priceTargetGrowth
  };
};
