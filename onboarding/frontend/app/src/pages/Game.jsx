import { useState } from 'react';
import PhoneScreen from '../components/PhoneScreen';

// Money Verse is a separate, externally-hosted web app -- embedded here via
// iframe so it stays inside the app shell. Some hosts block framing via
// X-Frame-Options/CSP with no client-detectable signal (the iframe just shows
// blank), so an "open in a new tab" escape hatch is always present, not just
// shown on failure.
const GAME_URL = import.meta.env.VITE_GAME_URL || '';

export default function Game() {
  const [loaded, setLoaded] = useState(false);

  if (!GAME_URL) {
    return (
      <PhoneScreen title="Money Verse">
        <div className="flex flex-col items-center text-center gap-4 mt-16">
          <div className="w-24 h-24 rounded-2xl bg-primary-container/15 flex items-center justify-center text-5xl">
            🎮
          </div>
          <h2 className="font-heading font-bold text-lg text-primary">Coming soon</h2>
          <p className="text-on-surface-variant text-[13.5px] max-w-xs">
            The game isn't configured yet — set VITE_GAME_URL to enable it.
          </p>
        </div>
      </PhoneScreen>
    );
  }

  return (
    <PhoneScreen
      title="Money Verse"
      noPad
      right={
        <a
          href={GAME_URL}
          target="_blank"
          rel="noreferrer"
          aria-label="Open in a new tab"
          title="Open in a new tab"
          className="w-9 h-9 rounded-full flex items-center justify-center text-primary hover:bg-surface-container active:scale-95 transition"
        >
          <ExternalLinkIcon />
        </a>
      }
    >
      <div className="relative flex-1">
        {!loaded && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-surface">
            <span className="inline-block w-8 h-8 rounded-full border-4 border-primary border-t-transparent animate-spin" />
            <p className="text-on-surface-variant text-[13px]">Loading the game…</p>
          </div>
        )}
        <iframe
          src={GAME_URL}
          title="Money Verse"
          onLoad={() => setLoaded(true)}
          className="w-full h-full border-0"
          allow="autoplay; fullscreen"
        />
      </div>
    </PhoneScreen>
  );
}

function ExternalLinkIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <path
        d="M14 5h5v5M19 5l-8 8M9 5H6a1 1 0 00-1 1v12a1 1 0 001 1h12a1 1 0 001-1v-3"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
