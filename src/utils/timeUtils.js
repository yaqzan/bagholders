/**
 * Utility functions for time formatting
 */

// Pull schedule in minutes since midnight ET — every 30 min during market hours
// (9:30 AM - 4:00 PM ET). Dashboard auto-refresh only tracks intraday score
// freshness, not pre-market, post-close, overnight, or weekend catch-up pulls.
const PULL_TIMES_ET = (() => {
  const times = [];
  for (let m = 570; m <= 960; m += 30) times.push(m); // 9:30 → 16:00 every 30 min
  return times;
})();
const PULL_BUFFER = 4; // minutes after scheduled pull before expecting new data
const STALE_DATA_MAX_AGE_HOURS = 3;

function getETComponents(date) {
  const parts = {};
  new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false
  }).formatToParts(date).forEach(({ type, value }) => {
    parts[type] = value;
  });
  const y = parseInt(parts.year), m = parseInt(parts.month), d = parseInt(parts.day);
  const h = parseInt(parts.hour) % 24, min = parseInt(parts.minute);
  const dow = new Date(y, m - 1, d).getDay();
  return { year: y, month: m, day: d, hour: h, minute: min, dow, dateKey: y * 10000 + m * 100 + d };
}

/**
 * Determines if score data should auto-refresh based on the intraday pull schedule.
 * Returns true only during the current weekday intraday pull bucket when the
 * loaded score timestamp is older than the latest expected pull.
 */
export function shouldAutoRefresh(lastUpdatedISO, now = new Date()) {
  if (!lastUpdatedISO) return false;

  const lastUpdated = new Date(lastUpdatedISO);
  if (Number.isNaN(lastUpdated.getTime())) return false;
  if (now - lastUpdated < PULL_BUFFER * 60 * 1000) return false;

  const nowET = getETComponents(now);
  const lastET = getETComponents(lastUpdated);

  let pullTime = null;

  if (nowET.dow < 1 || nowET.dow > 5) return false;

  const nowMin = nowET.hour * 60 + nowET.minute;
  for (let i = PULL_TIMES_ET.length - 1; i >= 0; i--) {
    const windowStart = PULL_TIMES_ET[i] + PULL_BUFFER;
    const nextPullTime = PULL_TIMES_ET[i + 1] ?? PULL_TIMES_ET[i] + 30;
    const windowEnd = nextPullTime + PULL_BUFFER;
    if (nowMin >= windowStart && nowMin < windowEnd) {
      pullTime = PULL_TIMES_ET[i];
      break;
    }
  }

  if (pullTime === null) return false;

  // Is the loaded score timestamp before the most recent expected intraday pull?
  if (lastET.dateKey < nowET.dateKey) return true;
  if (lastET.dateKey === nowET.dateKey) {
    return (lastET.hour * 60 + lastET.minute) < pullTime;
  }
  return false;
}

export function isStaleByAge(lastUpdatedISO, now = new Date(), maxAgeHours = STALE_DATA_MAX_AGE_HOURS) {
  if (!lastUpdatedISO) return false;

  const lastUpdated = new Date(lastUpdatedISO);
  if (Number.isNaN(lastUpdated.getTime())) return false;

  return now - lastUpdated > maxAgeHours * 60 * 60 * 1000;
}

export function shouldRefreshOnResume(lastUpdatedISO, now = new Date()) {
  return shouldAutoRefresh(lastUpdatedISO, now) || isStaleByAge(lastUpdatedISO, now);
}

export const parseLocalDate = (dateStr) => {
  const s = String(dateStr);
  const dayPart = s.length >= 10 ? s.slice(0, 10) : s;
  const [y, m, d] = dayPart.split('-').map(Number);
  return new Date(y, m - 1, d);
};

/**
 * Format a timestamp into a human-readable string
 * @param {string} timestamp - ISO timestamp string from backend
 * @returns {string} Formatted time string
 */
export const formatUpdatedTime = (timestamp) => {
  if (!timestamp) return null;
  
  const updateDate = new Date(timestamp);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  
  // Reset time to compare dates only
  const updateDateOnly = new Date(updateDate.getFullYear(), updateDate.getMonth(), updateDate.getDate());
  const todayOnly = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const yesterdayOnly = new Date(yesterday.getFullYear(), yesterday.getMonth(), yesterday.getDate());
  
  let dayText;
  if (updateDateOnly.getTime() === todayOnly.getTime()) {
    dayText = "Today";
  } else if (updateDateOnly.getTime() === yesterdayOnly.getTime()) {
    dayText = "Yesterday";
  } else {
    const month = updateDate.toLocaleDateString('en-US', { month: 'long' });
    const day = updateDate.getDate();
    dayText = `${month} ${day}`;
  }
  
  const hour = updateDate.getHours();
  const minute = updateDate.getMinutes();
  const ampm = hour >= 12 ? 'PM' : 'AM';
  const displayHour = hour % 12 || 12;
  const timeText = `${displayHour}:${minute.toString().padStart(2, '0')}${ampm}`;
  
  return `${dayText}, ${timeText}`;
};
